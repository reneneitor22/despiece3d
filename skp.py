# -*- coding: utf-8 -*-
"""Leer archivos .skp de SketchUp sin tener SketchUp instalado.

El .skp es formato cerrado. No existe libreria de mallas que lo lea: trimesh
tira `ValueError: unsupported file type: skp`. El SDK oficial de Trimble pide
cuenta de desarrollador y compilar contra un framework de C, o sea que no es
algo que un alumno pueda instalar.

Lo que si se puede: `openskp`, un lector hecho por ingenieria inversa que se
instala con pip y lee los dos contenedores que existen -- VFF (SketchUp 2021 en
adelante) y el viejo CArchive de MFC (2013-2020).

Dos conversiones que hay que hacer a la salida y que no son cosmeticas:

* **Ejes.** openskp entrega Y arriba (convencion glTF). Todo el despiece supone
  Z arriba: `niveles_de_piso` agrupa losas por su Z y `placas.py` decide si algo
  es muro o losa por la componente Z de su normal. Con Y arriba, cada muro se
  clasifica como losa y la casa sale en rebanadas horizontales.
* **Unidades.** SketchUp guarda todo en pulgadas por dentro sin importar lo que
  diga la regla en pantalla; openskp ya lo pasa a metros, que es justo el
  `--unidades m` de omision. La unidad que trae el archivo (`Inches` en este
  caso) es nomas como se le enseña al usuario y no se debe usar para escalar.

Parsear y hornear la escena de una casa completa toma ~15 s, y el CLI lo hace
otra vez en cada corrida (`--pisos`, luego el corte, luego verificar). Por eso
se guarda la malla ya convertida en `.cache_skp/`, con la firma del archivo
(tamaño + fecha) en el nombre: si el alumno vuelve a exportar desde SketchUp, la
firma cambia y se relee solo.
"""
import hashlib
import os

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache_skp')

_AYUDA_INSTALAR = (
    'para leer .skp falta la libreria openskp:\n'
    '    pip3 install --user openskp')


def es_skp(ruta):
    """¿Es un .skp? Por extension o por la firma, si le cambiaron el nombre."""
    if os.path.splitext(ruta)[1].lower() == '.skp':
        return True
    try:
        with open(ruta, 'rb') as f:
            cabeza = f.read(64)
    except OSError:
        return False
    # 0xFF 0xFE 0xFF + largo + "SketchUp Model" en UTF-16LE
    return cabeza[:3] == b'\xff\xfe\xff' and b'S\x00k\x00e\x00t\x00c\x00h\x00' in cabeza


def _firma(ruta):
    st = os.stat(ruta)
    crudo = '%s|%d|%d' % (os.path.abspath(ruta), st.st_size, int(st.st_mtime))
    return hashlib.sha1(crudo.encode('utf-8')).hexdigest()[:12]


def info_skp(ruta):
    """Version, unidades y capas del archivo, sin hornear la geometria."""
    skp = _abrir(ruta)
    m = skp.parse()
    return {
        'version': str(m.version).strip('{}'),
        'unidades_archivo': m.units,
        'capas': [l.name for l in m.layers],
        'n_definiciones': len(m.definitions),
        'n_materiales': len(m.materials),
    }


def _abrir(ruta):
    try:
        from openskp import SkpFile
    except ImportError:
        raise SystemExit(_AYUDA_INSTALAR)
    try:
        return SkpFile.open(ruta)
    except Exception as e:
        raise SystemExit('no se pudo abrir el .skp (%s). Si lo guardaste con una '
                         'version muy vieja de SketchUp, vuelve a guardarlo como '
                         '2013 o mas nuevo: %s' % (e, ruta))


def cargar_skp(ruta, usar_cache=True, avisar=True):
    """Devuelve la malla del .skp en metros y con Z arriba.

    Junta todas las piezas de la escena ya colocadas en su lugar (los
    componentes vienen instanciados con su transformacion aplicada), tal como
    las ve `trimesh.load(..., force='mesh')` con cualquier otro formato.
    """
    import numpy as np
    import trimesh

    if usar_cache:
        guardada = os.path.join(CACHE, '%s-%s.ply'
                                % (os.path.splitext(os.path.basename(ruta))[0].replace(' ', '_'),
                                   _firma(ruta)))
        if os.path.exists(guardada):
            m = trimesh.load(guardada, force='mesh')
            if m is not None and not m.is_empty:
                return m

    skp = _abrir(ruta)
    if avisar:
        print('leyendo %s (SketchUp, primera vez tarda)...' % os.path.basename(ruta))
    try:
        escena = skp.build_scene()
    except Exception as e:
        raise SystemExit('el .skp se abrio pero no se pudo armar la geometria (%s): %s'
                         % (e, ruta))

    verts, caras, base = [], [], 0
    for prim in escena.glb_primitives:
        p = np.asarray(prim.positions, dtype=np.float64)
        idx = np.asarray(prim.indices, dtype=np.int64)
        if p.size < 9 or idx.size < 3:
            continue
        p = p.reshape(-1, 3)
        # Y arriba (glTF) -> Z arriba (SketchUp, y lo que espera el despiece)
        verts.append(np.column_stack((p[:, 0], -p[:, 2], p[:, 1])))
        caras.append(idx.reshape(-1, 3) + base)
        base += len(p)

    if not verts:
        raise SystemExit('el .skp no trae caras: solo lineas, texto o guias. '
                         'Necesito superficies: %s' % ruta)

    m = trimesh.Trimesh(vertices=np.vstack(verts), faces=np.vstack(caras),
                        process=False)
    # SketchUp parte el vertice por material y por cara; sin soldar, separar
    # cuerpos da miles de islas de dos triangulos (el mismo problema que el OBJ
    # exportado desde SketchUp, ver README).
    m.merge_vertices()

    if usar_cache:
        try:
            os.makedirs(CACHE, exist_ok=True)
            m.export(guardada)
        except Exception:
            pass
    return m
