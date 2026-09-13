# -*- coding: utf-8 -*-
"""Leer archivos .fbx (Autodesk) y entregarlos como los quiere el despiece.

trimesh tampoco lee FBX. Aqui la conversion la hace `assimp`, que si lo lee en
sus dos formas --binario y ASCII-- y se instala con `brew install assimp`.

Lo que assimp hace y lo que no, medido con cajas de prueba y no supuesto (ver
`prueba_fbx.py`, que lo vuelve a comprobar en cualquier maquina):

* **El eje de arriba lo arregla assimp.** El FBX trae anotado cual es
  (`UpAxis`), porque cada programa usa uno: 3ds Max y SketchUp escriben Z
  arriba, Maya y Unity Y arriba. Con una cabecera de Max --Z arriba-- una caja
  de 4 x 2 x 10 sale de assimp como 4 x 10 x 2: la giro. Con cabecera Y arriba
  la deja igual. O sea que **la salida siempre viene Y arriba**, y de ahi hay
  que pasarla a Z arriba, que es lo que suponen `placas.py` (decide muro o losa
  por la componente Z de la normal) y `niveles_de_piso`.
* **La unidad NO la toca.** Una caja de 400 x 1000 x 200 con
  `UnitScaleFactor` = 1 --o sea, centimetros-- sale con esos mismos numeros.
  `UnitScaleFactor` dice cuantos centimetros mide una unidad del archivo: 1 son
  centimetros (lo normal saliendo de Max), 100 metros, 2.54 pulgadas. Sin
  aplicarlo, una casa de 10 m entra al despiece como si midiera 10 cm y no
  queda ni una placa cortable.

Si la cabecera no trae unidad se supone centimetros, que es lo que manda el
formato. Cuando el exportador la anota mal --le pasa hasta al propio assimp
cuando escribe FBX: declara centimetros sobre datos en metros-- el aviso que se
imprime al cargar enseña el tamaño que quedo, y se puede releer con
`crudo=True` para verlo como viene.
"""
import hashlib
import os
import re
import struct
import sys

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache_skp')

FIRMA_BIN = b'Kaydara FBX Binary'

# Cambio local (12 sep 2026): el mensaje mandaba a "brew install assimp", que
# no es lo que falta cuando no hay Homebrew (se usa assimp_py) ni sirve en Windows.
_AYUDA_INSTALAR = (
    'para leer .fbx falta assimp_py. Instalalo en el entorno del programa:\n'
    '    "%s" -m pip install assimp_py\n'
    '   (o exporta el modelo como OBJ o DAE, que se leen sin nada extra)' % sys.executable)


def es_fbx(ruta):
    """¿Es un .fbx? Por extension o por la firma, si le cambiaron el nombre."""
    if os.path.splitext(ruta)[1].lower() == '.fbx':
        return True
    try:
        with open(ruta, 'rb') as f:
            cabeza = f.read(4096)
    except OSError:
        return False
    return FIRMA_BIN in cabeza or b'FBXHeaderExtension' in cabeza


# --------------------------------------------------------------- cabecera
def _prop_binaria(datos, nombre, tipo):
    """Saca una propiedad de GlobalSettings de un FBX binario.

    Una propiedad se guarda como cuatro cadenas seguidas --nombre, tipo,
    subtipo y banderas-- y luego el valor. Cada cadena es 'S' + largo de 4
    bytes + texto, asi que se busca el nombre CON su encabezado (para no pegarle
    a la misma palabra dentro de otra, como `OriginalUnitScaleFactor`) y se
    camina hacia adelante.
    """
    aguja = b'S' + struct.pack('<I', len(nombre)) + nombre
    i = datos.find(aguja)
    if i < 0:
        return None
    p = i + len(aguja)
    for _ in range(3):                       # tipo, subtipo, banderas
        if p + 5 > len(datos) or datos[p:p + 1] != b'S':
            return None
        n = struct.unpack('<I', datos[p + 1:p + 5])[0]
        p += 5 + n
    marca = datos[p:p + 1]
    if tipo == 'I' and marca == b'I':
        return struct.unpack('<i', datos[p + 1:p + 5])[0]
    if tipo == 'D' and marca == b'D':
        return struct.unpack('<d', datos[p + 1:p + 9])[0]
    if tipo == 'D' and marca == b'I':        # hay quien escribe el 1 como entero
        return float(struct.unpack('<i', datos[p + 1:p + 5])[0])
    return None


def _prop_ascii(texto, nombre):
    m = re.search(r'"%s"\s*,\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*"[^"]*"\s*,\s*'
                  r'(-?[\d.eE+-]+)' % re.escape(nombre), texto)
    return float(m.group(1)) if m else None


def unidad_fbx(ruta):
    """Centimetros que mide una unidad del archivo (1 = cm, 100 = m, 2.54 = in).

    Solo se lee el principio: GlobalSettings va en la cabecera, y el resto del
    archivo pueden ser cientos de megas de vertices.
    """
    with open(ruta, 'rb') as f:
        cabeza = f.read(1 << 20)

    if FIRMA_BIN in cabeza[:32]:
        u = _prop_binaria(cabeza, b'UnitScaleFactor', 'D')
    else:
        u = _prop_ascii(cabeza.decode('utf-8', 'replace'), 'UnitScaleFactor')
    return 1.0 if not u or u <= 0 else float(u)


def _firma(ruta):
    """Huella del contenido. Era ruta + tamaño + fecha, y en la pagina cada subida
    vive en su carpeta: el cache nunca se reusaba y crecia sin fin (12 sep 2026)."""
    h = hashlib.sha1()
    with open(ruta, 'rb') as f:
        for trozo in iter(lambda: f.read(1 << 20), b''):
            h.update(trozo)
    return h.hexdigest()[:12]


def _con_assimp_py(ruta, intermedia):
    """Lo mismo que `assimp export ruta intermedia.stl`, con assimp_py.

    Medido con una caja de 4 x 2 x 10 m exportada de Blender: sale de 400 x
    1000 x 200, o sea Y arriba y en la unidad del archivo, igual que la version
    de linea de comandos. Por eso el resto de cargar_fbx no cambia.
    """
    try:
        import assimp_py as a
    except ImportError:
        raise SystemExit(_AYUDA_INSTALAR)
    import numpy as np
    import trimesh
    try:
        escena = a.import_file(ruta, a.Process_Triangulate | a.Process_PreTransformVertices
                               | a.Process_JoinIdenticalVertices)
    except Exception as e:
        txt = str(e)
        # Los dos casos que se vieron, dichos en castellano (12 sep 2026): antes el
        # alumno leia "Mesh processing assumes triangulated faces" o un error vacio.
        if 'triangulated' in txt:
            raise SystemExit('el .fbx trae lineas, curvas o puntos sueltos junto con las mallas '
                             'y el lector se detiene ahi. En tu programa exporta solo las mallas '
                             '(sin curvas, lineas ni splines) y vuelve a subirlo: %s' % ruta)
        if re.search(r"loading '[^']*':\s*$", txt) or not txt.strip():
            raise SystemExit('el .fbx no trae ninguna malla (¿solo huesos, camaras o luces?) o '
                             'se bajo a medias: %s' % ruta)
        raise SystemExit('assimp no pudo leer %s: %s' % (ruta, e))
    V, F, n = [], [], 0
    for m in escena.meshes:
        idx = np.asarray(m.indices, dtype=np.int64)
        if m.num_faces == 0 or idx.size != 3 * m.num_faces:
            continue                                 # lineas o puntos sueltos
        v = np.asarray(m.vertices, dtype=np.float64).reshape(-1, 3)
        V.append(v)
        F.append(idx.reshape(-1, 3) + n)
        n += len(v)
    if not V:
        raise SystemExit('el .fbx se leyo pero no trae caras (¿solo huesos, camaras o '
                         'luces?): %s' % ruta)
    trimesh.Trimesh(vertices=np.vstack(V), faces=np.vstack(F),
                    process=False).export(intermedia)


def cargar_fbx(ruta, usar_cache=True, avisar=True, crudo=False):
    """Devuelve la malla del .fbx en metros y con Z arriba.

    Con `crudo=True` se entrega como la solto assimp --Y arriba y en las
    unidades del archivo--, para cuando hay que averiguar por que un modelo
    salio del tamaño equivocado.
    """
    import shutil
    import subprocess
    import numpy as np
    import trimesh

    exe = shutil.which('assimp')

    base = os.path.splitext(os.path.basename(ruta))[0].replace(' ', '_')
    # El paso intermedio va en STL y no en PLY: el PLY que escribe assimp para
    # un modelo con materiales trae la tabla de colores incompleta (declara red
    # y green sin blue) y trimesh truena con KeyError: 'blue'. El STL no lleva
    # color, que es justo lo que aqui no se ocupa.
    intermedia = os.path.join(CACHE, '%s-%s-fbx.stl' % (base, _firma(ruta)))

    if not (usar_cache and os.path.exists(intermedia)):
        if avisar:
            print('leyendo %s (FBX)...' % os.path.basename(ruta))
        os.makedirs(CACHE, exist_ok=True)
        if not exe:
            # Cambio local (11 sep 2026): sin Homebrew no hay `assimp` de linea de
            # comandos y el FBX no entraba; assimp_py (pip) trae el mismo assimp.
            _con_assimp_py(ruta, intermedia)
        else:
            try:
                r = subprocess.run([exe, 'export', ruta, intermedia],
                                   capture_output=True, timeout=900)
            except Exception as e:
                raise SystemExit('assimp no pudo con el FBX (%s): %s' % (e, ruta))
            if r.returncode != 0 or not os.path.exists(intermedia):
                raise SystemExit('assimp no pudo leer %s: %s'
                                 % (ruta, (r.stderr or r.stdout or b''
                                           ).decode('utf-8', 'replace').strip()[:300]))

    try:
        m = trimesh.load(intermedia, force='mesh')
    except Exception:
        # STL intermedio a medias (el servidor murio escribiendolo): se tira para
        # que el siguiente intento lo vuelva a sacar (12 sep 2026).
        try:
            os.remove(intermedia)
        except OSError:
            pass
        raise SystemExit('el .fbx se leyo a medias; vuelve a intentarlo: %s' % ruta)
    if m is None or m.is_empty or len(m.faces) == 0:
        raise SystemExit('el .fbx se leyo pero no trae caras (¿solo huesos, '
                         'camaras o luces?): %s' % ruta)
    # El STL repite cada vertice en cada triangulo; sin soldar, separar cuerpos
    # daria miles de islas de dos triangulos (ver README).
    m.merge_vertices()
    if crudo:
        return m

    # assimp entrega Y arriba pase lo que pase (ver el comentario de arriba).
    v = m.vertices.copy()
    m.vertices = np.column_stack((v[:, 0], -v[:, 2], v[:, 1]))

    unidad_cm = unidad_fbx(ruta)
    # La unidad anotada pasa por la misma revision de sensatez que DXF y 3DM, y
    # lo que se supuso va a la pantalla (12 sep 2026: una cabecera en cm sobre
    # datos en metros dejaba la casa de 4 cm sin decir nada).
    from dxf import adivinar_unidad
    a_metros, aviso = adivinar_unidad(unidad_cm / 100.0, float(max(m.extents)),
                                      os.path.basename(ruta))
    avisos = [aviso] if aviso else []
    if abs(a_metros - 1.0) > 1e-9:
        m.apply_scale(a_metros)
    # El FBX pasa por float32 (assimp y el STL intermedio): lejos del origen se
    # pierde precision y un muro de 12 cm con coordenadas UTM salia de 25 cm.
    lejos = float(np.abs(m.bounds).max())
    if lejos > 20000 and lejos > 1000 * float(max(m.extents)):
        avisos.append('el modelo esta a %.0f km del origen (coordenadas de sitio, como UTM). '
                      'A esa distancia el FBX pierde precision y los espesores delgados salen '
                      'mal. En tu programa mueve el modelo al origen (0, 0, 0) y vuelve a '
                      'exportar.' % (lejos / 1000.0))
    m.metadata['despiece_avisos'] = avisos

    if avisar:
        nombres = {1.0: 'centimetros', 100.0: 'metros', 0.1: 'milimetros',
                   2.54: 'pulgadas', 30.48: 'pies'}
        print('   el FBX viene en %s -> %.1f x %.1f x %.1f m'
              % (nombres.get(unidad_cm, '%g cm por unidad' % unidad_cm),
                 m.extents[0], m.extents[1], m.extents[2]))
    return m
