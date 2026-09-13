# -*- coding: utf-8 -*-
"""Leer .dxf y .dwg de AutoCAD como malla 3D, en metros y con Z arriba.

Cambio local (11 y 12 sep 2026), no viene en el zip del hermano.

Lo que un alumno trae en DWG/DXF es una de tres cosas, y cada una se lee
distinto (medido con archivos reales, ver test_formatos.py):

* **Mallas.** Lo que exportan SketchUp, Rhino y Revit: POLYLINE de caras
  (polyface), 3DFACE o MESH, casi siempre dentro de bloques anidados. La
  escalera de Arquitek3D (SketchUp 22 -> DWG) son 682 polilineas en 22
  bloques que, ya insertadas, dan 885 mil. `ezdxf.disassemble` las recorre
  una por una y tardo 329 s; aqui cada bloque se vuelve malla UNA vez y se
  coloca con su matriz en numpy.
* **Solidos de AutoCAD** (3DSOLID). Van en ACIS, que ninguna libreria libre
  sabe triangular: el lector ACIS de ezdxf no pudo ni con una caja guardada en
  DXF 2018. Si la maquina tiene AutoCAD, su AcCoreConsole los saca con STLOUT
  sin abrir ventana; si no, se explica que hacer. SURFACE, REGION y BODY
  tambien son ACIS pero STLOUT no los toma: se cuentan y se avisa.
* **Planos 2D** (lineas, polilineas, textos). No traen altura: no hay de donde
  sacar volumen y se dice asi, en vez de entregar una maqueta vacia.

El DWG es cerrado. Se pasa a DXF con AutoCAD (AcCoreConsole) o con el ODA File
Converter, lo que haya en la maquina.

Unidades: $INSUNITS dice en que se dibujo, pero la plantilla acadiso.dwt trae
milimetros de fabrica y mucha gente dibuja en metros sin cambiarla. Si la
unidad declarada da un tamaño absurdo se prueba con las otras y se avisa.

Lo que en AutoCAD no se ve (capas apagadas o congeladas, entidades invisibles)
tampoco se corta, y se avisa cuantas se dejaron fuera.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile

import numpy as np

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache_skp')

# $INSUNITS -> metros por unidad de dibujo
INSUNITS_M = {1: 0.0254, 2: 0.3048, 3: 1609.344, 4: 0.001, 5: 0.01, 6: 1.0,
              7: 1000.0, 10: 0.9144, 14: 0.1, 21: 1200.0 / 3937.0}
NOMBRE_UNIDAD = {0.0254: 'pulgadas', 0.3048: 'pies', 0.001: 'milimetros',
                 0.01: 'centimetros', 1.0: 'metros', 0.1: 'decimetros', 0.9144: 'yardas',
                 1609.344: 'millas', 1000.0: 'kilometros', 1200.0 / 3937.0: 'pies (US survey)'}
# Lo unico ACIS que STLOUT sabe sacar.
SOLIDOS = {'3DSOLID'}
# ACIS que STLOUT no toma y ninguna libreria libre triangula: se cuenta y se avisa.
OTROS_ACIS = {'BODY', 'REGION', 'SURFACE', 'PLANESURFACE', 'EXTRUDEDSURFACE',
              'LOFTEDSURFACE', 'REVOLVEDSURFACE', 'SWEPTSURFACE', 'NURBSURFACE'}
# Tope de caras al colocar bloques: un DXF de 18 KB con MINSERT anidados daba
# 187 mil caras con 3 niveles y ~117 millones con 5, y la Mac de 8 GB mata el
# servidor mucho antes.
MAX_CARAS = 4000000
# Segundos por llamada a AcCoreConsole (la escalera de 1.6 millones tarda ~55).
TIEMPO_ACAD = 480
VERSION_DWG = {b'AC1009': 'R12', b'AC1012': 'R13', b'AC1014': 'R14', b'AC1015': '2000',
               b'AC1018': '2004', b'AC1021': '2007', b'AC1024': '2010',
               b'AC1027': '2013', b'AC1032': '2018'}

_ACCORE = ('/Applications/Autodesk/AutoCAD 20*/AutoCAD 20*.app/Contents/Helpers/'
           'AcCoreConsole.app/Contents/MacOS/AcCoreConsole',
           'C:/Program Files/Autodesk/AutoCAD 20*/accoreconsole.exe')


def es_dxf(ruta):
    if os.path.splitext(ruta)[1].lower() == '.dxf':
        return True
    try:
        with open(ruta, 'rb') as f:
            cabeza = f.read(2048)
    except OSError:
        return False
    if cabeza.startswith(b'AutoCAD Binary DXF'):
        return True
    return bool(re.match(rb'\s*(999\s*\r?\n[^\n]*\n\s*)?0\s*\r?\nSECTION', cabeza))


def es_dwg(ruta):
    if os.path.splitext(ruta)[1].lower() == '.dwg':
        return True
    try:
        with open(ruta, 'rb') as f:
            return f.read(4) in (b'AC10', b'AC1.')
    except OSError:
        return False


def accore():
    """AcCoreConsole de AutoCAD si esta instalado (Mac o Windows)."""
    exe = os.environ.get('DESPIECE_ACCORE')
    if exe and os.path.exists(exe):
        return exe
    for patron in _ACCORE:
        hallados = sorted(glob.glob(patron))
        if hallados:
            return hallados[-1]          # el AutoCAD mas nuevo
    return None


def _firma(ruta):
    """Huella del CONTENIDO del archivo.

    Antes era ruta + tamaño + fecha: en la pagina cada subida vive en su propia
    carpeta, asi que el cache nunca se reusaba pero siempre se escribia, y un
    archivo cambiado con el mismo tamaño y fecha regresaba el modelo viejo.
    """
    h = hashlib.sha1()
    with open(ruta, 'rb') as f:
        for trozo in iter(lambda: f.read(1 << 20), b''):
            h.update(trozo)
    return h.hexdigest()[:12]


# ------------------------------------------------------------------ unidades
def _nombre(f):
    for k, v in NOMBRE_UNIDAD.items():
        if abs(k - f) <= 1e-9 * max(1.0, k):
            return v
    return '%g m por unidad' % f


def adivinar_unidad(declarada_m, tam_mayor, nombre_archivo='', minimo=0.3):
    """Metros por unidad y, si hace falta, el aviso para el alumno.

    Se acepta la declarada si deja el modelo entre `minimo` (30 cm: un mueble)
    y 2 km (un plan maestro). Si no, o si el archivo no dice nada, se toma la
    que deje el modelo mas cerca de 20 m, el tamaño tipico de un edificio de
    taller. Rhino siempre guarda su unidad bien y pasa minimo=0.01: con 0.3 una
    pieza real de 14.5 cm (blocks.3dm de McNeel) se volvia de 3.69 m.

    12 sep 2026: la declarada que deja el modelo fuera de 2 a 500 m se respeta
    pero se avisa (una casa dibujada en cm con la plantilla en mm salia de 1.2 m
    sin decir nada), y al adivinar ya no se prueban pulgadas (un campus de 200 m
    sin unidad salia de 5 m).
    """
    nombre = nombre_archivo or 'el archivo'
    if tam_mayor <= 0:
        return declarada_m or 1.0, None
    if declarada_m and minimo <= tam_mayor * declarada_m <= 2000:
        tam = tam_mayor * declarada_m
        if minimo >= 0.3 and not 2.0 <= tam <= 500.0:
            return declarada_m, ('%s dice estar en %s y asi mide %.3g m. Si tu modelo no mide '
                                 'eso, revisa las unidades en tu programa y vuelve a exportar.'
                                 % (nombre, _nombre(declarada_m), tam))
        return declarada_m, None
    opciones = (1.0, 0.01, 0.001)
    mejor = min(opciones, key=lambda f: abs(np.log10(tam_mayor * f / 20.0)))
    if declarada_m:
        aviso = ('%s dice estar en %s, pero asi mediria %.4g m. Lo tome en %s: '
                 'mide %.3g m. Si no es asi, cambia las unidades en tu programa y '
                 'vuelve a exportar.' % (nombre, _nombre(declarada_m),
                                         tam_mayor * declarada_m, _nombre(mejor),
                                         tam_mayor * mejor))
    else:
        aviso = ('%s no dice en que unidades se dibujo. Lo tome en %s: mide %.3g m. '
                 'Si no es asi, pon las unidades en tu programa y vuelve a exportar.'
                 % (nombre, _nombre(mejor), tam_mayor * mejor))
    return mejor, aviso


def _tam_robusto(V):
    """El lado mayor sin las caras sueltas lejanas: del percentil 0.5 al 99.5.

    Con el lado mayor a secas, una cara perdida a 3 km encogia la casa entera a
    centimetros al adivinar la unidad.
    """
    V = np.asarray(V, dtype=np.float64)
    if len(V) == 0:
        return 0.0
    if len(V) < 200:
        return float(np.ptp(V, axis=0).max())
    lo, hi = np.percentile(V, [0.5, 99.5], axis=0)
    return float((hi - lo).max())


# ------------------------------------------------------- DXF -> malla (rapido)
def _matriz(ins):
    """Matriz 4x4 de ezdxf (vector renglon: p' = p @ A) como numpy."""
    return np.array([list(r) for r in ins.matrix44().rows()], dtype=np.float64)


def _abanico(caras):
    """Caras de n vertices -> triangulos (abanico; las de SketchUp son convexas)."""
    tris = []
    for c in caras:
        c = [i for k, i in enumerate(c) if k == 0 or i != c[k - 1]]
        if len(c) > 2 and c[0] == c[-1]:
            c = c[:-1]
        for k in range(1, len(c) - 1):
            tris.append((c[0], c[k], c[k + 1]))
    return np.array(tris, dtype=np.int64).reshape(-1, 3)


def _con_altura(e, t):
    """¿Linea o polilinea 2D con Thickness (muro dibujado como linea alta)?"""
    if t not in ('LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'CIRCLE', 'SOLID', 'TRACE'):
        return False
    try:
        return abs(float(e.dxf.get('thickness', 0) or 0)) > 0
    except Exception:
        return False


def _malla_de(entidades, doc, memo, pila, cuenta):
    """(V, F) de un grupo de entidades, en las coordenadas de su contenedor."""
    from ezdxf.render import MeshBuilder
    V, F, n = [], [], 0
    ocultas = cuenta['_capas_ocultas']

    def poner(v, f):
        nonlocal n
        if len(v) and len(f):
            cuenta['caras'] += len(f)
            if cuenta['caras'] > MAX_CARAS:
                raise SystemExit(
                    'el dibujo pasa de %d millones de caras al colocar sus bloques (hay '
                    'bloques repetidos dentro de bloques). Simplifica el modelo o exporta '
                    'solo la parte que vas a cortar.' % (MAX_CARAS // 1000000))
            V.append(v)
            F.append(f + n)
            n += len(v)

    for e in entidades:
        t = e.dxftype()
        # Lo que en AutoCAD no se ve tampoco se corta: capa apagada o congelada, o
        # entidad invisible. Dentro de un bloque, la capa "0" toma la del INSERT.
        try:
            capa = (e.dxf.get('layer', '0') or '0').lower()
            oculta = bool(e.dxf.get('invisible', 0)) or (
                capa in ocultas and not (pila and capa == '0'))
        except Exception:
            oculta = False
        if oculta:
            if t not in ('ATTRIB', 'ATTDEF', 'TEXT', 'MTEXT', 'DIMENSION'):
                cuenta['ocultas'] += 1
            continue
        try:
            if t == 'INSERT':
                nombre = e.dxf.name
                if nombre in pila:
                    continue                       # bloque que se inserta a si mismo
                if nombre not in memo:
                    blk = doc.blocks.get(nombre)
                    bandera = 0
                    if blk is not None and blk.block is not None:
                        bandera = int(blk.block.dxf.get('flags', 0) or 0)
                    if bandera & 12:               # 4 = xref, 8 = xref superpuesta
                        cuenta['xref'] += 1
                        memo[nombre] = None
                    else:
                        pila.add(nombre)
                        memo[nombre] = (_malla_de(blk, doc, memo, pila, cuenta)
                                        if blk is not None else None)
                        pila.discard(nombre)
                hecho = memo[nombre]
                if hecho is None or len(hecho[1]) == 0:
                    continue
                vb, fb = hecho
                copias = e.multi_insert() if getattr(e, 'mcount', 1) > 1 else (e,)
                for ins in copias:
                    A = _matriz(ins)
                    poner(vb @ A[:3, :3] + A[3, :3], fb)
            elif t == '3DFACE':
                v = np.array([tuple(e.dxf.get(k)) for k in ('vtx0', 'vtx1', 'vtx2', 'vtx3')],
                             dtype=np.float64)
                f = [[0, 1, 2]] if np.allclose(v[2], v[3]) else [[0, 1, 2], [0, 2, 3]]
                poner(v, np.array(f, dtype=np.int64))
            elif t == 'POLYLINE':
                if e.is_poly_face_mesh or e.is_polygon_mesh:
                    mb = MeshBuilder.from_polyface(e)
                    poner(np.array([tuple(p) for p in mb.vertices], dtype=np.float64),
                          _abanico(mb.faces))
                else:
                    cuenta['2d'] += 1
                    cuenta['con_altura'] += _con_altura(e, t)
            elif t == 'MESH':
                vm = np.array([tuple(p) for p in e.vertices], dtype=np.float64).reshape(-1, 3)
                fm = _abanico(e.faces)
                if len(fm) and (fm.min() < 0 or fm.max() >= len(vm)):
                    # indices que apuntan fuera de su propia malla: antes daba
                    # IndexError (error 500) o pegaba la cara a otra entidad
                    cuenta['rotas'] += 1
                    continue
                poner(vm, fm)
            elif t in SOLIDOS:
                cuenta['solidos'] += 1
                if pila:
                    cuenta['solidos_en_bloque'] = True
            elif t in OTROS_ACIS:
                cuenta['acis'] += 1
            elif t in ('LINE', 'LWPOLYLINE', 'ARC', 'CIRCLE', 'SPLINE', 'ELLIPSE', 'HATCH',
                       'SOLID', 'TRACE'):
                cuenta['2d'] += 1
                cuenta['con_altura'] += _con_altura(e, t)
        except SystemExit:
            raise
        except Exception as ex:                    # una entidad rota no tumba el archivo
            cuenta['rotas'] += 1
            cuenta.setdefault('error', repr(ex)[:200])
    if not V:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(V), np.vstack(F)


def leer_dxf(ruta):
    """(V, F, cuenta, insunits) del DXF, en unidades de dibujo."""
    import ezdxf
    from ezdxf import recover
    try:
        doc = ezdxf.readfile(ruta)
    except Exception:
        try:
            doc, _ = recover.readfile(ruta)        # DXF de programas que lo escriben mal
        except Exception as e:
            raise SystemExit('no se pudo leer el DXF (%s): %s' % (e, ruta))
    ocultas = set()
    for capa in doc.layers:
        try:
            if capa.is_off() or capa.is_frozen():
                ocultas.add(capa.dxf.name.lower())
        except Exception:
            pass
    cuenta = {'solidos': 0, 'acis': 0, '2d': 0, 'con_altura': 0, 'xref': 0, 'ocultas': 0,
              'rotas': 0, 'caras': 0, 'solidos_en_bloque': False, '_capas_ocultas': ocultas}
    V, F = _malla_de(doc.modelspace(), doc, {}, set(), cuenta)
    return V, F, cuenta, int(doc.header.get('$INSUNITS', 0) or 0)


# ------------------------------------------------------------ AutoCAD / ODA
def _matar(p):
    """Mata el proceso y todo su grupo (AcCoreConsole puede quedar girando)."""
    try:
        if os.name != 'nt':
            os.killpg(p.pid, signal.SIGKILL)
        else:
            p.kill()
    except OSError:
        pass
    try:
        p.communicate(timeout=10)
    except Exception:
        pass


def _correr_accore(exe, entrada, lisp, timeout=TIEMPO_ACAD):
    """Corre un LISP en AcCoreConsole y regresa lo que imprimio.

    Va en su propio grupo de procesos y, si se pasa del tiempo, se mata el grupo
    entero: un AcCoreConsole que se queda esperando respuesta gira al 100 % sin
    fin (se encontro uno con 13 h). Si el servidor muere antes, app.py barre los
    que queden al volver a arrancar. El .scr se borra al terminar.
    """
    carpeta = tempfile.mkdtemp(prefix='despiece_acad_')
    try:
        scr = os.path.join(carpeta, 'hacer.scr')
        with open(scr, 'w', encoding='utf-8') as f:
            f.write('(setvar "FILEDIA" 0)\n(setvar "CMDECHO" 0)\n')
            f.write(lisp)
            f.write('(command "_.QUIT" "_Y")\n')
        p = subprocess.Popen([exe, '/i', entrada, '/s', scr, '/l', 'en-US'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=(os.name != 'nt'))
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _matar(p)
            raise SystemExit('AutoCAD tardo mas de %d s con %s y se detuvo.'
                             % (timeout, os.path.basename(entrada)))
        # AcCoreConsole escribe lo que imprime el LISP por stderr, no por stdout.
        return ((out or b'') + (err or b'')).replace(b'\x00', b'').decode('utf-8', 'replace')
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


def _lisp_str(ruta):
    return '"%s"' % ruta.replace('\\', '/').replace('"', '')


def _motivo(salida):
    """La linea de AcCoreConsole que dice por que no pudo, si la hay."""
    for linea in salida.splitlines():
        linea = linea.strip()
        if linea and re.search(r'no v[aá]lid|invalid|error|cannot|no se puede|not a valid',
                               linea, re.I):
            return linea[:160]
    return ''


def dwg_a_dxf(ruta_dwg, salida_dxf):
    """Convierte con lo que haya: AutoCAD primero, luego ODA.

    Regresa (quien, None) si salio, o (None, por que no). El por que es None
    solo cuando no hay ningun convertidor: si hay y fallo, se dice que fallo
    (antes el alumno leia "no hay AutoCAD" con AutoCAD instalado y un DWG
    dañado). Las carpetas del ODA se borran al terminar.
    """
    fallas = []
    exe = accore()
    if exe:
        salida = _correr_accore(exe, ruta_dwg, '(command "_.SAVEAS" "_DXF" "_V" "2018" "16" %s)\n'
                                % _lisp_str(salida_dxf))
        if os.path.exists(salida_dxf) and os.path.getsize(salida_dxf) > 0:
            return 'AutoCAD', None
        m = _motivo(salida)
        fallas.append('AutoCAD no pudo abrirlo' + (' (dice: "%s")' % m if m else ''))
    import exportar
    oda = exportar._oda_convertidor()
    if oda:
        ent = tempfile.mkdtemp(prefix='despiece_oda_')
        sal = tempfile.mkdtemp(prefix='despiece_oda_')
        try:
            shutil.copyfile(ruta_dwg, os.path.join(ent, 'modelo.dwg'))
            try:
                subprocess.run([oda, ent, sal, 'ACAD2018', 'DXF', '0', '1', '*.DWG'],
                               capture_output=True, timeout=TIEMPO_ACAD)
            except subprocess.TimeoutExpired:
                fallas.append('el ODA File Converter tardo mas de %d s' % TIEMPO_ACAD)
            except OSError as e:
                fallas.append('el ODA File Converter no arranco (%s)' % e)
            hecho = os.path.join(sal, 'modelo.dxf')
            if os.path.exists(hecho):
                shutil.move(hecho, salida_dxf)
                return 'ODA File Converter', None
            if not any(f.startswith('el ODA') for f in fallas):
                fallas.append('el ODA File Converter no pudo abrirlo')
        finally:
            shutil.rmtree(ent, ignore_errors=True)
            shutil.rmtree(sal, ignore_errors=True)
    return None, ('; '.join(fallas) or None)


def solidos_con_autocad(ruta, explotar):
    """(STL de los 3DSOLID en sus coordenadas originales, cuantos salieron).

    (None, 0) si AutoCAD no pudo. STLOUT pide los solidos en el octante
    positivo, asi que se mueven a partir de EXTMIN y a la salida se regresan.
    Los que viven dentro de bloques solo salen si antes se explotan los bloques
    (8 pasadas por si vienen anidados); cuantos salieron sirve para avisar de
    los que se quedaron en bloques que AutoCAD no pudo explotar.
    """
    exe = accore()
    if not exe:
        return None, 0
    carpeta = tempfile.mkdtemp(prefix='despiece_stl_')
    try:
        stl = os.path.join(carpeta, 'solidos.stl')
        lisp = ''
        if explotar:
            lisp += ('(setq i 0)\n(while (and (< i 8) (setq ss (ssget "_X" \'((0 . "INSERT"))))) '
                     '(setq j 0) (repeat (sslength ss) (command "_.EXPLODE" (ssname ss j)) '
                     '(setq j (1+ j))) (setq i (1+ i)))\n')
        lisp += ('(setq ss (ssget "_X" \'((0 . "3DSOLID"))))\n'
                 '(if ss (progn (princ (strcat "\\nDESPIECE_N=" (itoa (sslength ss)) "\\n")) '
                 '(command "_.ZOOM" "_E") (setq lo (getvar "EXTMIN")) '
                 '(princ (strcat "\\nDESPIECE_EXTMIN=" (rtos (car lo) 2 8) "," (rtos (cadr lo) 2 8) '
                 '"," (rtos (caddr lo) 2 8) "\\n")) '
                 '(command "_.MOVE" ss "" lo "0,0,0") '
                 '(command "_.STLOUT" ss "" "_Y" %s)))\n' % _lisp_str(stl))
        salida = _correr_accore(exe, ruta, lisp)
        m = re.search(r'DESPIECE_EXTMIN=([-\d.eE+]+),([-\d.eE+]+),([-\d.eE+]+)', salida)
        n = re.search(r'DESPIECE_N=(\d+)', salida)
        if not m or not os.path.exists(stl):
            return None, 0
        import trimesh
        malla = trimesh.load(stl, force='mesh')
        if malla is None or malla.is_empty:
            return None, 0
        malla.apply_translation([float(m.group(k)) for k in (1, 2, 3)])
        return malla, (int(n.group(1)) if n else 0)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)


# ------------------------------------------------------------------- entrada
def _borrar(*rutas):
    for r in rutas:
        try:
            os.remove(r)
        except OSError:
            pass


def cargar_dxf(ruta, usar_cache=True, avisar=True):
    """Malla de un .dxf o .dwg, en metros, con metadata['despiece_avisos']."""
    import trimesh

    nombre = os.path.basename(ruta)
    base = os.path.splitext(nombre)[0].replace(' ', '_')
    guardada = os.path.join(CACHE, '%s-%s-cad.ply' % (base, _firma(ruta)))
    notas = guardada[:-4] + '.json'
    if usar_cache and os.path.exists(guardada):
        # Un PLY a medias (el servidor murio escribiendolo) tronaba en cada carga
        # siguiente: ahora se tira y se vuelve a sacar.
        try:
            m = trimesh.load(guardada, force='mesh', process=False)
        except Exception:
            m = None
        if m is not None and not m.is_empty:
            m.metadata['despiece_metros'] = True
            try:
                with open(notas) as f:
                    m.metadata['despiece_avisos'] = json.load(f)
            except Exception:
                m.metadata['despiece_avisos'] = []
            return m
        _borrar(guardada, notas)

    avisos = []
    fuente, temporal = ruta, None
    try:
        if es_dwg(ruta) and not es_dxf(ruta):
            with open(ruta, 'rb') as f:
                ver = VERSION_DWG.get(f.read(6), 'desconocida')
            if avisar:
                print('convirtiendo %s (DWG %s) a DXF...' % (nombre, ver))
            temporal = tempfile.mkdtemp(prefix='despiece_dwg_')
            fuente = os.path.join(temporal, base + '.dxf')
            quien, falla = dwg_a_dxf(ruta, fuente)
            if not quien and falla:
                raise SystemExit(
                    'no se pudo abrir el .dwg: %s. Casi siempre es un archivo dañado o que se '
                    'bajo a medias: vuelve a guardarlo en AutoCAD (o a bajarlo) y subelo otra '
                    'vez; tambien sirve Guardar como > DXF. (DWG version %s)' % (falla, ver))
            if not quien:
                raise SystemExit(
                    'para abrir un .dwg hace falta AutoCAD o el ODA File Converter (gratis, '
                    'opendesign.com) en esta computadora, y aqui no hay ninguno. Mientras: en '
                    'AutoCAD usa Guardar como > DXF y sube el .dxf. (DWG version %s)' % ver)
        V, F, cuenta, insunits = leer_dxf(fuente)
    finally:
        if temporal:
            shutil.rmtree(temporal, ignore_errors=True)

    partes = []
    completo = True
    if len(F):
        partes.append(trimesh.Trimesh(vertices=V, faces=F, process=False))
    if cuenta['solidos']:
        hay_acad = accore() is not None
        s, sacados = solidos_con_autocad(ruta, cuenta['solidos_en_bloque']) if hay_acad \
            else (None, 0)
        if s is not None:
            partes.append(s)
            if sacados and sacados < cuenta['solidos']:
                completo = False
                avisos.append(
                    'AutoCAD saco %d de %d solidos (3DSOLID): los demas estan dentro de bloques '
                    'que no se pudieron explotar (xref, MINSERT o bloques protegidos). En AutoCAD '
                    'explotalos o usa Enlazar (BIND) y vuelve a exportar.'
                    % (sacados, cuenta['solidos']))
        else:
            completo = False
            if hay_acad:
                por_que = ('AutoCAD no pudo convertir los %d solidos (3DSOLID) del archivo'
                           % cuenta['solidos'])
            else:
                por_que = ('el archivo trae %d solidos de AutoCAD (3DSOLID) y para convertirlos '
                           'en caras hace falta AutoCAD en esta computadora' % cuenta['solidos'])
            if not partes:
                raise SystemExit(por_que + '. En AutoCAD: escribe STLOUT, selecciona todo, '
                                 'Enter, y sube el .stl que sale.')
            avisos.append(por_que + '; se quedaron fuera.')
    if not partes:
        # Por que no hay volumen, dicho con lo que si trae el dibujo.
        if cuenta['con_altura']:
            raise SystemExit(
                'los muros de este dibujo son lineas con altura (propiedad Altura/Thickness), '
                'no volumen: %d de ellas. En AutoCAD conviertelos en solido (EXTRUSION sobre '
                'polilineas cerradas) o exporta el 3D desde tu programa de modelado y vuelve a '
                'subirlo.' % cuenta['con_altura'])
        if cuenta['xref']:
            raise SystemExit(
                'el modelo de este dibujo esta en %d referencia(s) externa(s) (xref) que no '
                'vienen dentro del archivo. En AutoCAD usa Referencias externas > Enlazar (BIND) '
                'y vuelve a guardar.' % cuenta['xref'])
        if cuenta['acis']:
            raise SystemExit(
                'el archivo trae %d superficies o regiones de AutoCAD (SURFACE, REGION, BODY), '
                'que aqui no se pueden leer. En AutoCAD conviertelas a solido o a malla y vuelve '
                'a subirlo.' % cuenta['acis'])
        if cuenta['ocultas']:
            raise SystemExit(
                'todo lo 3D de este dibujo esta en capas apagadas o congeladas (%d objetos). '
                'Prende esas capas en AutoCAD y vuelve a guardar.' % cuenta['ocultas'])
        raise SystemExit(
            'este dibujo es 2D (%d lineas, polilineas y arcos): no trae alturas, asi que no '
            'hay volumen de donde sacar piezas. Sube el modelo 3D: desde SketchUp, Rhino o '
            'Revit exporta DWG/DXF 3D, OBJ o IFC.' % cuenta['2d'])
    m = trimesh.util.concatenate(partes) if len(partes) > 1 else partes[0]
    if cuenta['rotas']:
        avisos.append('%d entidades del dibujo no se pudieron leer y se saltaron.'
                      % cuenta['rotas'])
    if cuenta['ocultas']:
        avisos.append('%d objetos en capas apagadas o congeladas (o invisibles) se dejaron '
                      'fuera, igual que no se ven en AutoCAD. Si los querias, prende sus capas '
                      'y vuelve a exportar.' % cuenta['ocultas'])
    if cuenta['acis']:
        avisos.append('%d superficies o regiones de AutoCAD (SURFACE, REGION, BODY) se quedaron '
                      'fuera: aqui no se pueden leer.' % cuenta['acis'])
    if cuenta['xref']:
        avisos.append('%d referencia(s) externa(s) (xref) no vienen dentro del archivo y se '
                      'quedaron fuera. En AutoCAD usa Enlazar (BIND).' % cuenta['xref'])

    factor, aviso = adivinar_unidad(INSUNITS_M.get(insunits), _tam_robusto(m.vertices), nombre)
    if aviso:
        avisos.append(aviso)
    if abs(factor - 1.0) > 1e-12:
        m.apply_scale(factor)
    # SketchUp y Rhino parten el vertice por cara: sin soldar, separar cuerpos da
    # miles de islas de dos triangulos (ver README). Y las caras dobles (anverso y
    # reverso) se quitan: pesan el doble y no aportan nada al despiece.
    m.merge_vertices()
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    m.metadata['despiece_metros'] = True
    m.metadata['despiece_avisos'] = avisos
    if avisar:
        print('   %s: %d caras, %.2f x %.2f x %.2f m' % (nombre, len(m.faces),
                                                    m.extents[0], m.extents[1], m.extents[2]))
    # Al cache solo lo completo (con solidos que faltan, instalar AutoCAD despues
    # debe servir), y escrito de un golpe: primero a .tmp y luego se renombra.
    if usar_cache and completo:
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(notas + '.tmp', 'w') as f:
                json.dump(avisos, f)
            os.replace(notas + '.tmp', notas)
            m.export(guardada + '.tmp', file_type='ply')
            os.replace(guardada + '.tmp', guardada)
        except Exception:
            _borrar(guardada + '.tmp', notas + '.tmp')
    return m
