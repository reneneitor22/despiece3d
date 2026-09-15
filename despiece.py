# -*- coding: utf-8 -*-
"""
Despiece 3D - motor de corte para maquetas.
Entra un modelo 3D (IFC/STL/OBJ/PLY/GLB/DAE/SKP/FBX), salen piezas planas
numeradas listas para corte laser (DXF) o impresion + corte a mano (SVG).

Modo actual: CURVAS DE NIVEL (apilado de rebanadas horizontales).
"""
import math
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union
from rectpack import newPacker, PackingMode, PackingBin


# ---------------------------------------------------------------- parametros
class Config:
    def __init__(self,
                 escala=100,            # 1:100
                 espesor_mm=3.0,        # espesor real de la lamina (carton pluma, MDF)
                 kerf_mm=0.15,          # ancho de quemado del laser
                 hoja=(500.0, 700.0),   # carton ilustracion estandar MX
                 margen_mm=10.0,
                 sep_mm=4.0,
                 unidades_modelo='m',   # m | cm | mm
                 vaciar=True):          # recortar interior tapado
        self.escala = float(escala)
        self.espesor_mm = float(espesor_mm)
        self.kerf_mm = float(kerf_mm)
        self.hoja = (float(hoja[0]), float(hoja[1]))
        self.margen_mm = float(margen_mm)
        self.sep_mm = float(sep_mm)
        self.unidades_modelo = unidades_modelo
        self.vaciar = bool(vaciar)

    @property
    def a_mm(self):
        """Factor: unidad del modelo -> mm de maqueta."""
        base = {'m': 1000.0, 'cm': 10.0, 'mm': 1.0}[self.unidades_modelo]
        return base / self.escala


def cargar_modelo(ruta):
    """Abre el modelo diciendo QUE paso cuando no se puede.

    Un alumno baja lo que sea: la pagina de error del sitio guardada con
    extension .glb, un .zip sin descomprimir, un STL a medio bajar. Lo que no
    puede es toparse con un traceback de trimesh. El .skp de SketchUp, el .fbx
    de Autodesk y el .ifc de ArchiCAD/Revit se desvian a su propio lector:
    trimesh no sabe ni que existen. El .rvt no se desvia a ningun lado --no hay
    lector libre que lo abra-- pero si se reconoce, para contestar con la
    version del archivo y como sacarle el IFC.
    """
    import os
    import trimesh

    if not os.path.exists(ruta):
        raise SystemExit('no existe el archivo: %s' % ruta)
    if os.path.getsize(ruta) < 64:
        raise SystemExit('el archivo esta vacio o se bajo a medias: %s' % ruta)

    import rvt as _rvt
    if _rvt.es_rvt(ruta):
        # Formato cerrado de Autodesk: aqui no se lee, se explica (ver rvt.py).
        raise SystemExit(_rvt.rechazo(ruta))

    import ifc as _ifc
    if _ifc.es_ifc(ruta) and not es_step(ruta):
        # El unico que entra con semantica: trae escrito que es cada elemento y
        # a que planta va, y ya viene en metros con Z arriba (ver ifc.py).
        return _ifc.cargar_ifc(ruta)

    import skp as _skp
    if _skp.es_skp(ruta):
        # El .skp no lo lee trimesh: tiene lector propio, que ademas lo entrega
        # en metros y con Z arriba (ver skp.py).
        return _skp.cargar_skp(ruta)

    import fbx as _fbx
    if _fbx.es_fbx(ruta):
        # Igual el .fbx, que ademas trae anotado su eje de arriba y su unidad
        # (ver fbx.py).
        return _fbx.cargar_fbx(ruta)

    import dxf as _dxf
    if _dxf.es_dwg(ruta) or _dxf.es_dxf(ruta):
        # AutoCAD: mallas bloque por bloque, y los solidos con AcCoreConsole si
        # hay AutoCAD en la maquina (ver dxf.py). Cambio local, 11 sep 2026.
        return _dxf.cargar_dxf(ruta)

    import rhino as _rhino
    if _rhino.es_3dm(ruta):
        # Rhino: mallas de render; las caras planas sin malla se triangulan
        # aqui (ver rhino.py). Cambio local, 11 sep 2026.
        return _rhino.cargar_3dm(ruta)

    ext = os.path.splitext(ruta)[1].lower()
    crudo = open(ruta, 'rb').read(16384)
    cabeza = crudo[:400].lstrip()
    if ext != '.3mf' and es_comprimido(crudo):
        # El .zip o .rar tal como lo baja el alumno: se abre y se usa el modelo de
        # adentro (el .3mf tambien es ZIP, pero ese lo lee trimesh).
        # Se desempaca en una carpeta temporal que se borra en cuanto el modelo
        # esta en memoria (cambio local, 12 sep 2026: antes se quedaba en $TMPDIR).
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp(prefix='despiece_zip_')
        try:
            interior, aviso = desempacar(ruta, tmp)
            m = cargar_modelo(interior)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if aviso:
            m.metadata.setdefault('despiece_avisos', []).append(aviso)
        return m
    minus = crudo.lower()
    # Todo .dae es XML y empieza con "<": antes se tomaba por pagina web y el DAE
    # de SketchUp nunca entro (casa del amigo, 10 sep).
    es_collada = b'<collada' in minus or ext == '.dae'
    if not es_collada and (cabeza[:1] == b'<' or b'<!doctype html' in minus
                           or b'<html' in minus):
        if b'<svg' in minus:
            raise SystemExit('esto es un dibujo 2D (SVG), no un modelo 3D: %s' % ruta)
        raise SystemExit('esto no es un modelo 3D, es una pagina web guardada con '
                         'nombre de modelo. Vuelve a bajarlo desde el boton de '
                         'descarga del sitio: %s' % ruta)

    try:
        if ext in ('.step', '.stp'):
            m = trimesh.load(ruta, force='mesh', **_tolerancia_step(ruta))
        else:
            m = trimesh.load(ruta, force='mesh')
    except Exception as e:
        if ext == '.gltf' and (isinstance(e, FileNotFoundError) or 'No such file' in str(e)):
            raise SystemExit('el .gltf guarda su geometria en un archivo aparte (.bin) que no '
                             'llego. Sube el .glb (en Blender: Exportar > glTF Binary) o un ZIP '
                             'con el .gltf y su .bin: %s' % ruta)
        raise SystemExit('no se pudo leer %s (%s). Formatos que si lee: IFC, SKP, 3DM, '
                         'DWG, DXF, FBX, DAE, OBJ, STL, PLY, GLB, STEP, 3MF y ZIP.' % (ruta, e))
    if m is None or m.is_empty or len(m.faces) == 0:
        raise SystemExit('el archivo se leyo pero no trae geometria: %s' % ruta)
    if es_collada:
        m = _ajustar_collada(m, ruta)
    elif ext == '.obj':
        m = _parar_si_viene_acostado(m)
    elif ext in ('.glb', '.gltf'):
        # glTF va SIEMPRE con Y arriba y en metros (lo dice el estandar), y
        # trimesh no lo gira: la caja de 4 x 2 x 10 de Blender llegaba de 4 x 10 x 2.
        import numpy as np
        v = m.vertices.copy()
        m.vertices = np.column_stack((v[:, 0], -v[:, 2], v[:, 1]))
        # Metros, dice el estandar, pero hay exportadores que escriben milimetros.
        # Si el tamaño no es de edificio se prueba otra unidad y se avisa (cambio
        # local, 12 sep 2026: una casa de 10 m escrita en mm salia de 10 km).
        from dxf import adivinar_unidad
        factor, aviso = adivinar_unidad(1.0, float(max(m.extents)), os.path.basename(ruta))
        if aviso:
            m.apply_scale(factor)
            m.metadata.setdefault('despiece_avisos', []).append(aviso)
        m.metadata['despiece_metros'] = True
    elif ext in ('.step', '.stp'):
        # cascadio entrega glTF, que siempre va en metros: la Iglesia de la Luz
        # en FreeCAD mide 83407 mm y sale de aqui con 83.407.
        m.metadata['despiece_metros'] = True
    return m


def es_step(ruta):
    """STEP de CAD, no IFC. Los dos son ISO-10303-21 por dentro y `ifc.es_ifc`
    agarraba el STEP de la Iglesia de la Luz (FILE_SCHEMA AUTOMOTIVE_DESIGN) y
    ifcopenshell tronaba. Manda lo que diga FILE_SCHEMA."""
    import os
    if os.path.splitext(ruta)[1].lower() in ('.step', '.stp'):
        return True
    import re
    try:
        with open(ruta, 'rb') as f:
            cabeza = f.read(1 << 16).upper()
    except OSError:
        return False
    # Hasta 64 KB y con regex: con 4 KB, un IFC de cabecera larga (FILE_SCHEMA
    # cerca del byte 4096) se tomaba por STEP. Cambio local, 12 sep 2026.
    m = re.search(rb"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", cabeza)
    return bool(b'ISO-10303-21' in cabeza and m and not m.group(1).startswith(b'IFC'))


def _parar_si_viene_acostado(m):
    """El OBJ no dice cual eje va arriba, y Blender, Maya y 3ds Max lo exportan
    con Y arriba. El Pabellon de Barcelona (OBJ de Blender) llegaba de 59.9 x
    3.6 x 25.7 m: acostado, y cada muro se habria tomado por losa. Solo se para
    cuando es claro, si Y mide menos de la mitad que X y que Z; una torre que
    llegue acostada no se detecta, por eso se avisa lo que se hizo.
    """
    import numpy as np
    e = m.extents
    if e[1] < 0.5 * min(e[0], e[2]):
        v = m.vertices.copy()
        m.vertices = np.column_stack((v[:, 0], -v[:, 2], v[:, 1]))
        m.metadata.setdefault('despiece_avisos', []).append(
            'El OBJ venia acostado (con Y arriba, como exporta Blender) y se paro con Z '
            'arriba. Si tu modelo ya estaba bien, exportalo con "Z arriba" y vuelve a subirlo.')
    return m


def _tolerancia_step(ruta):
    """Triangular el STEP a 1 mm real.

    Sin tolerancia cascadio tritura: la Farnsworth de FreeCAD salia en 15.6
    millones de triangulos y 85 s; a 1 mm, 113 mil y 1.8 s, con las mismas
    medidas. A 1:50 un milimetro real es 0.02 mm de maqueta, menos que el haz.

    cascadio toma tol_linear en milimetros sin importar la unidad del archivo:
    la misma columna guardada en mm, m y pulgadas da 396 caras con 1.0. Antes se
    escalaba por la unidad y un STEP en metros recibia 1 micra, 32 veces mas
    caras (cambio local, 12 sep 2026).
    """
    return {'tol_linear': 1.0, 'tol_angular': 0.5}


def _ajustar_collada(m, ruta):
    """Unidad y eje de arriba del .dae, que trimesh lee pero no aplica.

    Medido: el DAE de SketchUp declara <unit meter="0.0254"/> y trimesh lo
    entrega en pulgadas (la escalera de 4 m salia de 157); y un Y_UP sale igual
    que un Z_UP. SketchUp duplica cada cara (anverso y reverso): 3.25 millones
    de triangulos contra 1.63 del mismo modelo en .skp. Se quitan las repetidas.
    """
    import re
    import numpy as np
    # El <asset> completo, no los primeros 16 KB; comillas dobles o simples; y
    # X_UP tambien (cambio local, 12 sep 2026).
    with open(ruta, 'rb') as f:
        cabeza = f.read(1 << 20)
    fin = cabeza.find(b'</asset>')
    if fin > 0:
        cabeza = cabeza[:fin]
    u = re.search(rb'<unit[^>]*meter\s*=\s*["\']([0-9.eE+-]+)["\']', cabeza)
    metros = float(u.group(1)) if u else 1.0
    arriba = re.search(rb'<up_axis>\s*([XYZ])_UP', cabeza)
    v = m.vertices.copy()
    if arriba and arriba.group(1) == b'Y':       # derecha +X, arriba +Y, hacia ti +Z
        m.vertices = np.column_stack((v[:, 0], -v[:, 2], v[:, 1]))
    elif arriba and arriba.group(1) == b'X':     # derecha -Y, arriba +X, hacia ti +Z
        m.vertices = np.column_stack((-v[:, 1], -v[:, 2], v[:, 0]))
    if metros > 0 and abs(metros - 1.0) > 1e-12:
        m.apply_scale(metros)
    m.merge_vertices()
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    m.metadata['despiece_metros'] = True
    return m


# Del que mas informacion trae al que menos: el IFC dice que es cada cosa, el
# SKP/3DM/DWG traen la geometria completa y ya en unidades, el STL no trae nada.
# El .3mf va al final: en Thingiverse y Printables es la plataforma de impresion
# (la Fallingwater trae uno junto a su ReferenceModel.stl, que es el edificio).
# El .dxf va antes que el .dwg: trae lo mismo y se lee sin AutoCAD ni ODA (un ZIP
# con los dos tronaba en una maquina sin convertidor). Cambio local, 12 sep 2026.
PRIORIDAD = ('.ifc', '.skp', '.3dm', '.dxf', '.dwg', '.fbx', '.dae', '.glb', '.gltf',
             '.step', '.stp', '.obj', '.ply', '.stl', '.off', '.3mf')


def es_comprimido(cabeza):
    return (cabeza[:2] == b'PK' or cabeza[:4] == b'Rar!'
            or cabeza[:6] == b"7z\xbc\xaf'\x1c")


def desempacar(ruta, destino=None):
    """Abre un .zip/.rar/.7z y regresa (modelo de adentro, aviso o None).

    Asi baja el alumno de 3D Warehouse, Drive o Thingiverse: comprimido, con
    texturas, y a veces el mismo modelo en tres formatos (la escalera de
    Arquitek3D llega en tres .rar). Se toma el formato que mas informacion trae
    y, de ese formato, el archivo mas pesado. Cambio local, 11 sep 2026.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    import time
    import zipfile
    import zlib

    with open(ruta, 'rb') as f:
        cabeza = f.read(8)
    # El .3mf tambien es ZIP por dentro, pero lo lee trimesh entero: abrirlo aqui
    # daba "el comprimido no trae ningun modelo" (cambio local, 12 sep 2026).
    if not es_comprimido(cabeza) or os.path.splitext(ruta)[1].lower() == '.3mf':
        return ruta, None
    destino = os.path.join(destino or tempfile.mkdtemp(prefix='despiece_zip_'),
                           'desempacado')
    os.makedirs(destino, exist_ok=True)
    otros = set()
    if cabeza[:2] == b'PK':
        try:
            z = zipfile.ZipFile(ruta)
        except zipfile.BadZipFile:
            raise SystemExit('el ZIP esta dañado o se bajo a medias: %s' % ruta)
        # Solo lo que sirve para leer el modelo (texturas, PDFs y renders se
        # quedan adentro), y con el tope revisado antes de escribir nada.
        utiles = set(PRIORIDAD) | {'.bin', '.mtl'}
        elegidas, total = [], 0
        for info in z.infolist():
            partes = info.filename.replace('\\', '/').split('/')
            if info.is_dir() or info.filename.startswith(('/', '\\')) or '..' in partes:
                continue                             # nada fuera de la carpeta
            e = os.path.splitext(partes[-1])[1].lower()
            if e not in utiles or partes[-1].startswith('._') or '__MACOSX' in partes:
                otros.add(e or partes[-1])
                continue
            elegidas.append(info)
            total += info.file_size
        if total > TOPE_DESEMPACAR:
            raise SystemExit('lo de adentro del ZIP pesa %.1f GB descomprimido y el tope es de '
                             '%.0f GB. Sube solo el modelo: %s'
                             % (total / 2 ** 30, TOPE_DESEMPACAR / 2 ** 30, ruta))
        _hay_espacio(destino, total)
        # Cada falla de zipfile con su explicacion: antes salia como error 500
        # con el texto de Python (cambio local, 12 sep 2026).
        for info in elegidas:
            if info.flag_bits & 0x1:
                raise SystemExit('el ZIP tiene contraseña. Descomprimelo y sube el modelo de '
                                 'adentro: %s' % ruta)
            try:
                z.extract(info, destino)
            except NotImplementedError:
                raise SystemExit('el ZIP usa una compresion que aqui no se abre (Deflate64 o AES '
                                 'de 7-Zip o WinZip). Vuelve a comprimirlo con el compresor de tu '
                                 'computadora o sube el modelo solo: %s' % ruta)
            except (zipfile.BadZipFile, zlib.error, EOFError):
                raise SystemExit('el ZIP esta dañado o se bajo a medias (%s no se pudo sacar): %s'
                                 % (info.filename, ruta))
            except RuntimeError:
                raise SystemExit('el ZIP tiene contraseña. Descomprimelo y sube el modelo de '
                                 'adentro: %s' % ruta)
            except OSError as e:
                raise SystemExit('no se pudo sacar %s del ZIP (%s). Descomprimelo y sube el '
                                 'modelo solo: %s' % (info.filename, e.strerror or e, ruta))
    else:
        tipo = 'RAR' if cabeza[:4] == b'Rar!' else '7z'
        tar = shutil.which('bsdtar') or shutil.which('tar')
        if not tar:
            raise SystemExit('no se pudo abrir el %s. Descomprimelo y sube el modelo de '
                             'adentro: %s' % (tipo, ruta))
        _hay_espacio(destino, 0)
        # bsdtar no dice cuanto va a escribir antes de hacerlo: se vigila la
        # carpeta mientras trabaja y se corta si pasa del tope (un .7z de 474 KB
        # saco 3.2 GB). Cambio local, 12 sep 2026.
        p = subprocess.Popen([tar, '-xf', ruta, '-C', destino],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0, motivo = time.time(), None
        while p.poll() is None:
            time.sleep(0.3)
            if _peso(destino) > TOPE_DESEMPACAR:
                motivo = ('lo de adentro del %s pasa de %.0f GB descomprimido. Sube solo el '
                          'modelo: %s' % (tipo, TOPE_DESEMPACAR / 2 ** 30, ruta))
            elif time.time() - t0 > 600:
                motivo = 'el %s tardo mas de 10 minutos en abrirse: %s' % (tipo, ruta)
            if motivo:
                p.kill()
                p.wait()
                shutil.rmtree(destino, ignore_errors=True)
                raise SystemExit(motivo)
        if p.returncode != 0:
            raise SystemExit('no se pudo abrir el %s. Descomprimelo y sube el modelo de '
                             'adentro: %s' % (tipo, ruta))
        # Y otra vez al final: si bsdtar acaba antes de la primera medicion, lo que
        # paso del tope se colaba.
        if _peso(destino) > TOPE_DESEMPACAR:
            shutil.rmtree(destino, ignore_errors=True)
            raise SystemExit('lo de adentro del %s pasa de %.0f GB descomprimido. Sube solo el '
                             'modelo: %s' % (tipo, TOPE_DESEMPACAR / 2 ** 30, ruta))
    # Nada de enlaces: un symlink dentro del .7z/.rar hacia otro archivo del disco
    # hacia que se despiezara ese archivo (cambio local, 12 sep 2026).
    real = os.path.realpath(destino)
    candidatos = []
    for raiz, dirs, archivos in os.walk(destino):
        for d in list(dirs):
            if os.path.islink(os.path.join(raiz, d)):
                os.unlink(os.path.join(raiz, d))
                dirs.remove(d)
        if '__MACOSX' in raiz:
            continue
        for a in archivos:
            p_ = os.path.join(raiz, a)
            if os.path.islink(p_):
                os.unlink(p_)
                continue
            if (not os.path.isfile(p_) or a.startswith('._')
                    or not os.path.realpath(p_).startswith(real + os.sep)):
                continue
            e = os.path.splitext(a)[1].lower()
            if e in PRIORIDAD:
                candidatos.append((PRIORIDAD.index(e), -os.path.getsize(p_), p_))
            else:
                otros.add(e or a)
    if not candidatos:
        raise SystemExit('el comprimido no trae ningun modelo 3D que se pueda leer '
                         '(trae: %s): %s' % (', '.join(sorted(otros)[:8]) or 'nada', ruta))
    candidatos.sort()
    elegido = candidatos[0][2]
    aviso = None
    if len(candidatos) > 1:
        aviso = ('el comprimido traia %d modelos; se uso %s.'
                 % (len(candidatos), os.path.basename(elegido)))
    return elegido, aviso


TOPE_DESEMPACAR = 2 << 30        # bytes que se sacan de un comprimido, a lo mas


def _peso(carpeta):
    import os
    total = 0
    for raiz, _, archivos in os.walk(carpeta):
        for a in archivos:
            try:
                total += os.lstat(os.path.join(raiz, a)).st_size
            except OSError:
                pass
    return total


def _hay_espacio(carpeta, necesita):
    """Que abrir un comprimido no llene el disco: queda al menos 1 GB libre."""
    import shutil
    libre = shutil.disk_usage(carpeta).free
    if libre - necesita < (1 << 30):
        raise SystemExit('no hay espacio en el disco de esta computadora para abrir el '
                         'comprimido (quedan %.1f GB libres).' % (libre / 2 ** 30))


# ------------------------------------------------------------- solidificar
def _loops_de_frontera(mesh):
    """Devuelve listas de indices de vertice que forman los bordes abiertos."""
    from trimesh import grouping
    idx = grouping.group_rows(mesh.edges_sorted, require_count=1)
    if len(idx) == 0:
        return []
    aristas = mesh.edges_sorted[idx]

    vecinos = {}
    for a, b in aristas:
        vecinos.setdefault(a, []).append(b)
        vecinos.setdefault(b, []).append(a)

    vistas = set()
    loops = []
    for inicio in vecinos:
        if inicio in vistas:
            continue
        loop = [inicio]
        vistas.add(inicio)
        actual, previo = inicio, None
        while True:
            sig = [v for v in vecinos.get(actual, []) if v != previo and v not in vistas]
            if not sig:
                break
            previo, actual = actual, sig[0]
            vistas.add(actual)
            loop.append(actual)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def solidificar(mesh, holgura=1e-6):
    """
    Una superficie de terreno es una malla ABIERTA: rebanarla da curvas abiertas,
    no poligonos. Le cosemos faldon vertical + fondo plano para volverla solida.
    """
    if mesh.is_watertight:
        return mesh

    loops = _loops_de_frontera(mesh)
    if not loops:
        return mesh

    z_base = float(mesh.bounds[0][2]) - max(holgura, mesh.extents[2] * 0.01)
    V = list(mesh.vertices)
    F = list(mesh.faces)

    for loop in loops:
        # vertices espejo en la base
        mapa = {}
        for vi in loop:
            x, y, _ = mesh.vertices[vi]
            mapa[vi] = len(V)
            V.append([x, y, z_base])

        # faldon
        n = len(loop)
        for i in range(n):
            a, b = loop[i], loop[(i + 1) % n]
            a2, b2 = mapa[a], mapa[b]
            F.append([a, b, b2])
            F.append([a, b2, a2])

        # fondo
        pts = np.array([[mesh.vertices[v][0], mesh.vertices[v][1]] for v in loop])
        try:
            poly = Polygon(pts).buffer(0)
            if poly.is_empty:
                continue
            vv, ff = trimesh.creation.triangulate_polygon(poly, engine='earcut')
            base_off = len(V)
            for p in vv:
                V.append([p[0], p[1], z_base])
            for t in ff:
                F.append([base_off + t[2], base_off + t[1], base_off + t[0]])
        except Exception:
            pass

    solido = trimesh.Trimesh(vertices=np.array(V), faces=np.array(F), process=True)
    trimesh.repair.fix_normals(solido)
    return solido


# ---------------------------------------------------------------- rebanado
def _limpiar(geom, min_area):
    """Tira astillas y devuelve lista de Polygon validos."""
    if geom is None or geom.is_empty:
        return []
    geom = geom.buffer(0)
    partes = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    return [p for p in partes if p.area >= min_area and p.is_valid]


def rebanar(mesh, cfg, min_area_mm2=4.0):
    """
    Corta el solido en planos horizontales cada `espesor_mm` de maqueta.
    Devuelve lista de capas: {n, z_modelo, z_real_m, polys[mm de maqueta]}
    """
    paso_modelo = cfg.espesor_mm / cfg.a_mm       # cuanto sube cada lamina, en unidades del modelo
    z0, z1 = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    n_capas = max(1, int(math.floor((z1 - z0) / paso_modelo)))

    alturas = [(i + 0.5) * paso_modelo for i in range(n_capas)]  # relativas a z0
    # section_multiplane recorre el BVH una sola vez: ~10x mas rapido que
    # llamar section() en un for con mallas de cientos de miles de caras.
    secciones = None
    try:
        secciones = mesh.section_multiplane(plane_origin=[0, 0, z0],
                                            plane_normal=[0, 0, 1],
                                            heights=alturas)
    except Exception:
        secciones = None

    capas, capas_malas = [], []
    for i in range(n_capas):
        z = z0 + alturas[i]
        plano = None
        if secciones is not None:
            plano = secciones[i]
        else:
            try:
                sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
                plano = sec.to_planar(to_2D=np.eye(4))[0] if sec is not None else None
            except Exception:
                plano = None
        if plano is None:
            continue

        # Con una malla sucia, la rebanada sale como contorno que se cruza a si
        # mismo y trimesh se rinde ("unable to recover polygon"). Antes eso
        # tumbaba TODO el despiece por una sola capa mala. poligonos_llenos
        # salta el contorno roto y conserva los huecos del resto; se avisa.
        from placas import poligonos_llenos
        anillos, rota = poligonos_llenos(plano)
        if rota:
            capas_malas.append(i + 1)

        polys = []
        for p in anillos:
            if p is None or p.is_empty:
                continue
            polys.extend(_limpiar(p, min_area_mm2 / (cfg.a_mm ** 2)))
        if not polys:
            continue

        # a milimetros de maqueta, origen en la esquina del modelo
        ox, oy = float(mesh.bounds[0][0]), float(mesh.bounds[0][1])
        polys_mm = []
        for p in polys:
            pmm = _escalar_poly(p, cfg.a_mm, ox, oy)
            if cfg.kerf_mm:
                pmm = pmm.buffer(cfg.kerf_mm / 2.0, join_style=2)
                pmm = pmm if pmm.geom_type == 'Polygon' else max(pmm.geoms, key=lambda g: g.area)
            polys_mm.append(pmm)

        capas.append({
            'n': i + 1,
            'z_modelo': z,
            'z_real': (z - z0),
            'polys': polys_mm,
        })
    if capas_malas:
        print('  OJO: la malla esta sucia en %d capa(s) (%s...): el contorno se cruza '
              'a si mismo y se reconstruyo como se pudo'
              % (len(capas_malas), ', '.join(str(x) for x in capas_malas[:5])))
    return capas


def _escalar_poly(p, f, ox, oy):
    def tx(coords):
        return [((x - ox) * f, (y - oy) * f) for x, y in coords]
    return Polygon(tx(p.exterior.coords), [tx(r.coords) for r in p.interiors])


# ---------------------------------------------------------------- piezas
def _sufijo_alfa(j):
    """0->a, 25->z, 26->aa, 27->ab...  Nunca se sale del alfabeto (bug: chr(97+j)
    pasaba a '{','|','}' con capas de mas de 26 islas, tipico en terreno real)."""
    s = ''
    j += 1
    while j:
        j, r = divmod(j - 1, 26)
        s = chr(97 + r) + s
    return s


def _id_pieza(n_capa, j, n_polys):
    return '%02d%s' % (n_capa, _sufijo_alfa(j) if n_polys > 1 else '')


def armar_piezas(capas, vaciar=True, ceja_mm=7.0, min_hueco_mm2=900.0):
    """Cada poligono de cada capa es una pieza. Guarda la silueta de la capa
    de ARRIBA para grabarla encima -> asi el alumno sabe donde apilar."""
    piezas = []
    for i, capa in enumerate(capas):
        arriba = unary_union(capas[i + 1]['polys']) if i + 1 < len(capas) else None
        for j, poly in enumerate(capa['polys']):
            guia = None
            if arriba is not None:
                try:
                    g = arriba.intersection(poly.buffer(0.5))
                    if not g.is_empty and g.area > 1.0:
                        guia = g
                except Exception:
                    pass
            # Vaciado: lo que queda tapado por la capa de arriba no se ve.
            # Se recorta dejando una ceja para pegar -> menos material y hueco
            # donde el acomodo mete piezas chicas.
            recorte = None
            if vaciar and guia is not None:
                try:
                    h = guia.buffer(-ceja_mm, join_style=2)
                    if not h.is_empty:
                        h = h if h.geom_type == 'Polygon' else max(h.geoms, key=lambda g: g.area)
                        if h.area >= min_hueco_mm2:
                            resto = poly.difference(h)
                            if resto.geom_type == 'Polygon' and not resto.is_empty:
                                recorte, poly = h, resto
                except Exception:
                    pass

            # al vaciar, la huella de la capa de arriba cruza el hueco recien
            # abierto: ese tramo se grabaria sobre el aire (y sobre la cama del
            # laser). La huella solo vale donde queda material.
            if guia is not None:
                try:
                    guia = guia.intersection(poly)
                    if guia.is_empty:
                        guia = None
                except Exception:
                    pass

            piezas.append({
                'id': _id_pieza(capa['n'], j, len(capa['polys'])),
                'capa': capa['n'],
                'z_real': capa['z_real'],
                'poly': poly,
                'guia': guia,
                'vaciada': recorte is not None,
            })
    return piezas


# ---------------------------------------------------------------- acomodo
# Nesting por geometria real (mascara raster + correlacion FFT), no por caja
# envolvente: las curvas de nivel son blobs y la caja desperdicia ~40%.
import shapely.affinity as aff
from PIL import Image, ImageDraw
from scipy.signal import fftconvolve
from scipy.ndimage import binary_dilation

ROTACIONES = (0, 45, 90, 135, 180, 225, 270, 315)
ROTACIONES_ORTO = (0, 90, 180, 270)


def _mascara(geom, res):
    """Rasteriza a bool. Devuelve (arr, minx, miny) del bbox de la geometria."""
    minx, miny, maxx, maxy = geom.bounds
    w = int(np.ceil((maxx - minx) / res)) + 2
    h = int(np.ceil((maxy - miny) / res)) + 2
    img = Image.new('1', (w, h), 0)
    dr = ImageDraw.Draw(img)
    partes = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
    for p in partes:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        dr.polygon([((x - minx) / res, (y - miny) / res) for x, y in p.exterior.coords], fill=1)
        for r in p.interiors:
            dr.polygon([((x - minx) / res, (y - miny) / res) for x, y in r.coords], fill=0)
    arr = binary_dilation(np.array(img, dtype=bool), iterations=1)
    return arr, minx, miny


def _cabe(poly, cfg, res, rotaciones):
    """True si la pieza entra en la hoja util con alguna de las rotaciones."""
    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    holgura = cfg.sep_mm + 2 * res
    for ang in (rotaciones or ROTACIONES):
        g = aff.rotate(poly, ang, origin='centroid') if ang else poly
        minx, miny, maxx, maxy = g.bounds
        if (maxx - minx) + holgura <= W and (maxy - miny) + holgura <= H:
            return True
    return False


def partir_grandes(piezas, cfg, res=2.0, rotaciones=None, max_trozos=64):
    """Una pieza mas grande que la hoja no se puede cortar: hasta ahora se tiraba
    en silencio y la maqueta salia sin base (el terreno de Marte perdia 23 de 235
    piezas, entre ellas TODAS las capas de abajo).

    Se parte con una reja en trozos que si caben. Los trozos van a tope: en un
    terreno cada capa se pega plana sobre la de abajo, que es la que amarra la
    junta, asi que no necesitan diente. Se numeran <id>.1, <id>.2 ... y quedan
    marcados con 'partida_de' para que la guia diga de donde salio cada uno.
    """
    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    holgura = cfg.sep_mm + 2 * res
    util_w, util_h = W - holgura, H - holgura
    if util_w <= 0 or util_h <= 0:
        return piezas, []

    salida, partidas = [], []
    for pz in piezas:
        poly = pz['poly']
        if poly.is_empty or _cabe(poly, cfg, res, rotaciones):
            salida.append(pz)
            continue

        # la reja se traza sobre la orientacion que menos cortes necesita
        mejor = None
        for ang in (rotaciones or ROTACIONES):
            g = aff.rotate(poly, ang, origin='centroid') if ang else poly
            minx, miny, maxx, maxy = g.bounds
            nx = int(math.ceil((maxx - minx) / util_w))
            ny = int(math.ceil((maxy - miny) / util_h))
            if nx * ny < 1:
                continue
            if mejor is None or nx * ny < mejor[0]:
                mejor = (nx * ny, ang, g, nx, ny)
        if mejor is None or mejor[0] > max_trozos:
            salida.append(pz)                     # ni partiendola cabe: que avise acomodar
            continue

        _, ang, g, nx, ny = mejor
        minx, miny, maxx, maxy = g.bounds
        ancho = (maxx - minx) / nx
        alto = (maxy - miny) / ny
        guia = pz.get('guia')
        if guia is not None and ang:
            guia = aff.rotate(guia, ang, origin=poly.centroid)

        trozos = []
        for iy in range(ny):
            for ix in range(nx):
                celda = box(minx + ix * ancho - 1e-6, miny + iy * alto - 1e-6,
                            minx + (ix + 1) * ancho + 1e-6, miny + (iy + 1) * alto + 1e-6)
                try:
                    corte = g.intersection(celda)
                except Exception:
                    continue
                if corte.is_empty:
                    continue
                for parte in (corte.geoms if corte.geom_type.startswith('Multi') else [corte]):
                    if parte.geom_type != 'Polygon' or parte.area < 1.0:
                        continue
                    trozos.append((parte, celda))

        if len(trozos) < 2:
            salida.append(pz)
            continue

        for k, (parte, celda) in enumerate(trozos, 1):
            hijo = dict(pz)
            hijo['id'] = '%s.%d' % (pz['id'], k)
            hijo['poly'] = parte
            hijo['partida_de'] = pz['id']
            hijo['trozo'] = (k, len(trozos))
            if guia is not None:
                # OJO: se recorta contra el TROZO, no contra la celda de la reja.
                # Contra la celda, la huella grabada se sale de la pieza (y de la
                # hoja) y ademas los bordes de la reja quedan grabados como lineas
                # que no significan nada.
                try:
                    gg = guia.intersection(parte)
                    hijo['guia'] = None if gg.is_empty else gg
                except Exception:
                    hijo['guia'] = None
            salida.append(hijo)
        partidas.append((pz['id'], len(trozos)))

    return salida, partidas


SEP_ZONA_MM = 6.0         # aire entre dos zonas de la misma hoja
ROTULO_MM = 8.0           # franja reservada arriba de cada zona para su nombre


def _acomodar_por_grupo(piezas, cfg, agrupar, res, rotaciones):
    """Acomoda cada grupo por separado y luego empaca los bloques en las hojas.

    Nestear todo junto revuelve los muros de la planta baja con los de la alta y
    la hoja no hay quien la arme; darle una hoja entera a cada planta desperdicia
    material (un edificio de 13 niveles pedia 13 hojas casi vacias). El punto
    medio es el del arquitecto: cada planta es un BLOQUE compacto y los bloques
    se acomodan en estantes dentro de la hoja, cada uno con su rotulo y su marco.
    """
    claves = sorted({pz.get(agrupar) for pz in piezas},
                    key=lambda v: (v is None, v))
    # Con un solo grupo no hay nada que separar: rotular "PLANTA 1" toda la hoja
    # es ruido, y el acomodo normal aprovecha mejor el material.
    if len(claves) < 2:
        return acomodar(piezas, cfg, res=res, rotaciones=rotaciones)
    bloques, grandes = [], []
    for cl in claves:
        sub = [pz for pz in piezas if pz.get(agrupar) == cl]
        hs, gr = acomodar(sub, cfg, res=res, rotaciones=rotaciones)
        grandes.extend(gr)
        for h in hs:
            cajas = [c['geo'].bounds for c in h]
            x0 = min(b[0] for b in cajas)
            y0 = min(b[1] for b in cajas)
            bloques.append({'clave': cl, 'col': h, 'x0': x0, 'y0': y0,
                            'etiqueta': sub[0].get('rotulo', cl),
                            'w': max(b[2] for b in cajas) - x0,
                            'h': max(b[3] for b in cajas) - y0})

    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    # En orden de planta, no por tamano: el alumno arma de abajo hacia arriba y
    # tener la planta 6 en la hoja 2 y la 3 en la 3 es peor que gastar una hoja.
    bloques.sort(key=lambda b: ((b['clave'] is None, b['clave']), -b['h']))
    hojas, estantes = [], []
    for b in bloques:
        bw, bh = b['w'], b['h'] + ROTULO_MM
        destino = None
        for k, st in enumerate(estantes):
            if bw <= W - st['x'] and bh <= st['alto']:
                destino = (k, st['x'], st['y'])
                st['x'] += bw + SEP_ZONA_MM
                break
            y = st['y'] + st['alto'] + SEP_ZONA_MM
            if bw <= W and y + bh <= H:
                st['y'], st['alto'], st['x'] = y, bh, bw + SEP_ZONA_MM
                destino = (k, 0.0, y)
                break
        if destino is None:
            hojas.append([])
            estantes.append({'y': 0.0, 'alto': bh, 'x': bw + SEP_ZONA_MM})
            destino = (len(hojas) - 1, 0.0, 0.0)

        k, dx, dy = destino
        ddx = cfg.margen_mm + dx - b['x0']
        ddy = cfg.margen_mm + dy - b['y0']
        for c in b['col']:
            c['geo'] = aff.translate(c['geo'], ddx, ddy)
            if c['guia'] is not None:
                c['guia'] = aff.translate(c['guia'], ddx, ddy)
            c['zona'] = b['etiqueta']
            hojas[k].append(c)

    hojas = [h for h in hojas if h]
    for i, h in enumerate(hojas):
        for col in h:
            col['pieza']['hoja'] = i + 1
    return hojas, grandes


def acomodar(piezas, cfg, res=2.0, rotaciones=None, agrupar=None):
    """Bottom-left-fill sobre malla. Devuelve (hojas, ids_que_no_caben).
    Cada colocada trae ya la geometria final: {'pieza','geo','guia'}.

    `agrupar` es el nombre de un campo de la pieza (p.ej. 'planta') que NO se
    puede mezclar en una hoja: se acomoda cada grupo por separado y cada hoja
    sale de un solo grupo. Cuesta material --un grupo chico se lleva una hoja
    entera-- y aun asi es lo correcto: una hoja con los muros de la planta baja
    revueltos con los de la alta no hay quien la arme.
    """
    if agrupar:
        return _acomodar_por_grupo(piezas, cfg, agrupar, res, rotaciones)

    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    nw, nh = int(W / res), int(H / res)

    orden = sorted(range(len(piezas)), key=lambda k: -piezas[k]['poly'].area)
    hojas, ocupacion = [], []
    grandes = []

    for k in orden:
        pz = piezas[k]
        # variantes rotadas, con su holgura de separacion ya incorporada
        variantes = []
        for ang in (rotaciones or ROTACIONES):
            g = aff.rotate(pz['poly'], ang, origin='centroid') if ang else pz['poly']
            buf = g.buffer(cfg.sep_mm / 2.0 + res * 0.5, join_style=2)
            m, bx, by = _mascara(buf, res)
            if m.shape[0] <= nh and m.shape[1] <= nw:
                variantes.append((ang, g, buf, m, bx, by))
        if not variantes:
            grandes.append(pz['id'])
            continue

        colocada = False
        for idx_hoja in range(len(hojas) + 1):
            if idx_hoja == len(hojas):
                hojas.append([])
                ocupacion.append(np.zeros((nh, nw), dtype=bool))
            occ = ocupacion[idx_hoja]
            mejor = None
            for ang, g, buf, m, bx, by in variantes:
                libre = fftconvolve(occ.astype(np.float32),
                                    m[::-1, ::-1].astype(np.float32), mode='valid') < 0.5
                if not libre.any():
                    continue
                filas, cols = np.nonzero(libre)
                j = int(np.lexsort((cols, filas))[0])          # el mas abajo, luego el mas a la izq
                fila, col = int(filas[j]), int(cols[j])
                puntaje = (fila, col)
                if mejor is None or puntaje < mejor[0]:
                    mejor = (puntaje, ang, g, buf, bx, by, fila, col, m)
            if mejor is None:
                if not hojas[idx_hoja]:      # hoja recien creada y aun asi no cabe
                    hojas.pop(); ocupacion.pop()
                    grandes.append(pz['id'])
                    colocada = True
                continue

            _, ang, g, buf, bx, by, fila, col, m = mejor
            dx = cfg.margen_mm + col * res - bx
            dy = cfg.margen_mm + fila * res - by
            geo = aff.translate(g, dx, dy)
            guia = None
            if pz['guia'] is not None:
                gg = aff.rotate(pz['guia'], ang, origin=pz['poly'].centroid) if ang else pz['guia']
                guia = aff.translate(gg, dx, dy)
            hojas[idx_hoja].append({'pieza': pz, 'geo': geo, 'guia': guia, 'ang': ang})
            occ[fila:fila + m.shape[0], col:col + m.shape[1]] |= m
            colocada = True
            break
        if not colocada:
            grandes.append(pz['id'])

    hojas = [h for h in hojas if h]
    for i, h in enumerate(hojas):
        for col in h:
            col['pieza']['hoja'] = i + 1
    return hojas, grandes
