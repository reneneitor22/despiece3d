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
import json
import os
import sys

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache_skp')

# Cambio local (12 sep 2026): "pip3 install --user" instalaba fuera del .venv
# donde corre el programa, y el error seguia igual.
_AYUDA_INSTALAR = (
    'para leer .skp falta la libreria openskp. Instalala en el entorno del programa:\n'
    '    "%s" -m pip install openskp' % sys.executable)


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
    """Huella del contenido. Era ruta + tamaño + fecha, y en la pagina cada subida
    vive en su carpeta: el cache nunca se reusaba y crecia sin fin. Cambio local,
    12 sep 2026."""
    h = hashlib.sha1()
    with open(ruta, 'rb') as f:
        for trozo in iter(lambda: f.read(1 << 20), b''):
            h.update(trozo)
    return h.hexdigest()[:12]


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


# Tope de geometria (15 sep 2026). Sin _por_componente (abajo), openskp gastaba
# ~30x el model.dat en RAM: "Ya ahora si el final.skp" (422 MB) pedia ~13 GB y la
# Mac de 8 GB se quedaba paginando. Con _por_componente lee en 3 min con 1.7 GB
# de pico (~4x). 1000 MB ~ 4 GB: arriba de eso, mejor avisar que tumbar la Mac.
# ponytail: tope por MB de model.dat, no por caras reales; con mas RAM, subirlo.
MAX_GEOMETRIA_MB = float(os.environ.get('DESPIECE_MAX_SKP_MB', '1000'))


def _geometria_mb(ruta):
    """MB de model.dat sin descomprimirlo: el .skp 2021+ es un ZIP con prefijo y
    el tamaño viene en su directorio (4 ms). None en el formato viejo."""
    import zipfile
    try:
        with zipfile.ZipFile(ruta) as z:
            return z.getinfo('model.dat').file_size / 1e6
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


# Leer componente por componente (15 sep 2026). openskp ya lee "un registro de
# arriba a la vez", pero SketchUp mete TODAS las definiciones de componentes en
# F901/7017/7117: en "Ya ahora si el final.skp" ese registro es el 100% de los
# 422 MB y el arbol entero se armaba de golpe. Abriendo esas tres envolturas el
# pico queda en el componente mas grande (25 MB de 700). Esas etiquetas no las
# busca nadie en openskp (solo estan en CONTAINER_TAGS) y todos sus recorridos
# bajan a los hijos igual, en el mismo orden: el resultado no cambia.
_ENVOLTURAS = frozenset({'F901', '7017', '7117'})
_ITER_OPENSKP = None      # el de openskp, guardado al parchar (lo usan las pruebas)


def _por_componente(data, start, end, container_tags=None):
    from openskp import _core
    if container_tags is None:
        container_tags = _core.CONTAINER_TAGS
    arriba = _core._flat_headers(data, start, end)
    if len(arriba) == 1 and arriba[0][0] == 'F401':
        _, o, s = arriba[0]
        arriba = _core._flat_headers(data, o + 6, o + 6 + s)
    registros = []

    def abrir(headers):
        for t, o, s in headers:
            if t in _ENVOLTURAS and s > 0:
                abrir(_core._flat_headers(data, o + 6, o + 6 + s))
            else:
                registros.append((t, o, s))

    abrir(arriba)
    for i, (t, o, s) in enumerate(registros):
        nodos = _core.parse_tlv_recursive(data, o, o + 6 + s, container_tags)
        if nodos:
            yield i, len(registros), nodos[0]


def _abrir(ruta):
    mb = _geometria_mb(ruta)
    if mb and mb > MAX_GEOMETRIA_MB:
        raise SystemExit(
            'el modelo trae %.0f MB de geometria y el tope en esta computadora es de %.0f MB: '
            'con mas, la memoria no alcanza. Casi siempre son muebles, arboles, gente o '
            'coches de 3D Warehouse. Borralos, purga lo que no se usa (Ventana > Informacion '
            'del modelo > Estadisticas > Purgar elementos no usados) y vuelve a guardar: '
            'para el despiece solo hacen falta muros, losas y techos.' % (mb, MAX_GEOMETRIA_MB))
    try:
        from openskp import SkpFile, _core
    except ImportError:
        raise SystemExit(_AYUDA_INSTALAR)
    global _ITER_OPENSKP
    if _core.iter_top_level_lazy is not _por_componente:     # full_parse la busca ahi
        _ITER_OPENSKP = _core.iter_top_level_lazy
        _core.iter_top_level_lazy = _por_componente
    try:
        return SkpFile.open(ruta)
    except Exception as e:
        raise SystemExit('no se pudo abrir el .skp (%s). Si lo guardaste con una '
                         'version muy vieja de SketchUp, vuelve a guardarlo como '
                         '2013 o mas nuevo: %s' % (e, ruta))


def _triangular(vertices_3d, loops, normal):
    """`openskp._core.triangulate_face_3d` con el mismo resultado, sin sus tres
    ciclos cuadraticos (1 oct 2026: el modelo de Irving pasaba minutos en una
    sola cara grande):
    1. deduplicar ids con `not in` sobre una lista -> dict.fromkeys (mismo orden);
    2. `poly.contains(centroide)` triangulo por triangulo -> uno solo, vectorizado
       y con el poligono preparado (mismo predicado);
    3. por cada vertice de cada triangulo buscaba el id mas cercano entre TODOS
       los de la cara. Delaunay devuelve las coordenadas de entrada tal cual, asi
       que se busca la coordenada exacta en un dict (el primer id con esa
       coordenada, igual que el `<` estricto de la busqueda); si no esta, la
       busqueda de siempre."""
    import numpy as np
    import shapely
    from shapely.geometry import MultiPoint, Point, Polygon
    if not loops or not loops[0] or len(loops[0]) < 3:
        return []
    if len(loops) == 1 and len(loops[0]) == 3:
        return [loops[0]]
    if len(loops) == 1 and len(loops[0]) == 4:
        v = loops[0]
        return [[v[0], v[1], v[2]], [v[0], v[2], v[3]]]

    normal = np.array(normal)
    norm_val = np.linalg.norm(normal)
    normal = normal / norm_val if norm_val > 1e-6 else np.array([0.0, 0.0, 1.0])
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u_axis = np.cross(normal, helper)
    u_axis = u_axis / np.linalg.norm(u_axis)
    v_axis = np.cross(normal, u_axis)

    v_id_to_2d = {}
    for v_id in dict.fromkeys(v for loop in loops for v in loop):
        if v_id not in vertices_3d:
            return []
        p3d = np.array(vertices_3d[v_id])
        v_id_to_2d[v_id] = (np.dot(p3d, u_axis), np.dot(p3d, v_axis))

    outer = [v_id_to_2d[v] for v in loops[0]]
    if outer[0] != outer[-1]:
        outer.append(outer[0])
    holes = []
    for hl in loops[1:]:
        h = [v_id_to_2d[v] for v in hl]
        if h[0] != h[-1]:
            h.append(h[0])
        holes.append(h)

    try:
        poly = Polygon(outer, holes)
        if not poly.is_valid:
            poly = poly.buffer(0)
        pts = [Point(c) for coords in [outer] + holes for c in coords[:-1]]
        if not pts:
            return []
        tris = shapely.ops.triangulate(MultiPoint(pts))
        shapely.prepare(poly)
        dentro = shapely.contains(poly, shapely.centroid(np.asarray(tris, dtype=object))) if tris else []
        exacto = {}
        for v_id, c2d in v_id_to_2d.items():
            exacto.setdefault((float(c2d[0]), float(c2d[1])), v_id)

        def mas_cercano(tc):
            v_id = exacto.get((tc[0], tc[1]))
            if v_id is not None:
                return v_id
            best, dmin = None, float('inf')
            for k, c2d in v_id_to_2d.items():
                d = (tc[0] - c2d[0]) ** 2 + (tc[1] - c2d[1]) ** 2
                if d < dmin:
                    dmin, best = d, k
            return best

        inside = []
        for tri, ok in zip(tris, dentro):
            if not ok:
                continue
            ids = [i for i in (mas_cercano(tc) for tc in list(tri.exterior.coords)[:3]) if i is not None]
            if len(ids) == 3 and len(set(ids)) == 3:
                inside.append(ids)
        if inside:
            return inside
    except Exception:
        pass
    outer_loop = loops[0]
    if len(outer_loop) < 3:
        return []
    return [[outer_loop[0], outer_loop[i], outer_loop[i + 1]] for i in range(1, len(outer_loop) - 1)]


def _hornear(parsed):
    """Lo mismo que `openskp.scene.build_scene(parsed)` para el despiece, en
    minutos y no en media hora (1 oct 2026, modelo de Irving: 137 MB, 31 min).

    build_scene tenia dos cosas que crecen con el numero de instancias:
    1. volvia a triangular la definicion en CADA instancia (una silla puesta 300
       veces = 300 triangulaciones);
    2. por cada instancia recorria TODO mesh_index buscando sus mallas para
       ponerles nombre y propiedades: instancias x mallas, cuadratico.
    Aqui (1) se triangula una vez por (definicion, color heredado) con las mismas
    funciones de openskp, y (2) no hace falta: el despiece no usa esos nombres.

    Regresa [(v, f, etiqueta, ruta, nodos)] en el MISMO orden que glb_primitives: v en
    metros, Z arriba, redondeado a float32 como lo guarda openskp (asi la malla
    sale identica bit a bit, ver test_arreglos.HornearDaLoMismo); etiqueta y ruta
    para poder descartar lo que no es construccion; nodos = ids de las instancias
    de la raiz a la pieza (el arbol, para decidir por componente). La etiqueta va aparte de la
    capa que usa openskp para el color: en un .skp viejo (2013-2020) openskp no
    lee el `layer_id` de la instancia, y si se usara para el color cambiaria la
    malla."""
    import numpy as np
    from openskp import _core
    from openskp import scene as S
    from openskp.errors import SkpParseError

    defs = parsed['defs_dict']
    layer_colors = parsed['layer_colors']
    layer_id_to_name = parsed['layer_id_to_name']
    mat_names = parsed.get('material_id_to_name', {})
    materials = parsed['materials']
    by_folder = parsed.get('materials_by_folder', {})
    triangulado = {}
    out = []
    activas = set()
    ids = [0]

    def grupos(def_id, builder, fallback):
        llave = (def_id, fallback)
        g = triangulado.get(llave)
        if g is not None:
            return g
        face_groups = {}
        for f_data in builder.faces.values():
            front_mat = S._resolve_material(f_data.get('material_id'), mat_names, materials, by_folder)
            back_mat = S._resolve_material(f_data.get('back_material_id'), mat_names, materials, by_folder)
            front = S._resolve_color(front_mat) or fallback
            back = S._resolve_color(back_mat) or fallback
            loops = [lv for lv in (S._reconstruct_loop_vertices(lp, builder.edges)
                                   for lp in f_data['loops']) if lv]
            if not loops:
                continue
            try:
                tris = _triangular(builder.vertices, loops, f_data["normal"])
            except Exception as e:
                raise SkpParseError('Failed to triangulate face: %s' % e,
                                    stage='build_scene', definition_id=def_id) from e
            fn = f_data['normal']
            xr, yr = S._face_uv_basis(fn)
            if front == back:
                S._add_face_side(face_groups, builder, tris, fn, front, True, False, front_mat,
                                 f_data.get('uv_transform'), xr, yr)
            else:
                S._add_face_side(face_groups, builder, tris, fn, front, False, False, front_mat,
                                 f_data.get('uv_transform'), xr, yr)
                S._add_face_side(face_groups, builder, tris, fn, back, False, True, back_mat,
                                 f_data.get('uv_transform_back'), xr, yr)
        g = [(np.asarray(gr['local_verts'], dtype=np.float64).reshape(-1, 3),
              np.asarray(gr['local_faces'], dtype=np.int64).reshape(-1, 3))
             for gr in face_groups.values() if gr['local_faces']]
        triangulado[llave] = g
        return g

    def instanciar(def_id, m, capa, ruta, color, etiqueta, nodos):
        d = defs.get(def_id)
        if d is None:
            return
        builder = d['builder']
        if builder.faces:
            fallback = color if color is not None else layer_colors.get(capa, (136, 136, 136))
            for lv, caras in grupos(def_id, builder, fallback):
                x, y, z = lv[:, 0], lv[:, 1], lv[:, 2]
                # transform_point de openskp, mismo orden de operaciones
                v = np.column_stack((m[0] * x + m[1] * y + m[2] * z + m[9],
                                     m[3] * x + m[4] * y + m[5] * z + m[10],
                                     m[6] * x + m[7] * y + m[8] * z + m[11]))
                v = (v * S.INCHES_TO_M).astype(np.float32).astype(np.float64)
                out.append((v, caras, etiqueta, ruta, nodos))
        for inst in builder.instances:
            ref = inst['ref_idx']
            nueva = _core.multiply_matrices(m, inst['matrix'])
            l_name, inst_color = capa, color
            et = layer_id_to_name.get(inst.get('layer_id'), etiqueta)
            d007 = next((c for c in inst['children'] if c['tag'] == 'D007'), None)
            if d007:
                d207 = next((c for c in d007['children'] if c['tag'] == 'D207'), None)
                if d207 and d207['payload']:
                    p = d207['payload']
                    l_id = p[0] if len(p) == 1 else _core.parse_var_int(p, 0, len(p))
                    l_name = et = layer_id_to_name.get(l_id, capa)
                d107 = next((c for c in d007['children'] if c['tag'] == 'D107'), None)
                if d107:
                    mid = _core.parse_var_int(d107['payload'], 0, len(d107['payload']))
                    mat = materials.get(mat_names.get(mid)) or by_folder.get(mat_names.get(mid))
                    if mat:
                        c = mat['color']
                        inst_color = (c['r'], c['g'], c['b'])
            if ref in activas:
                raise SkpParseError('Recursive component definition',
                                    stage='build_scene', definition_id=ref)
            activas.add(ref)
            nombre = inst['name'] or (defs.get(ref) or {}).get('name') or 'Component_%s' % ref
            ids[0] += 1
            instanciar(ref, nueva, l_name, '%s / %s' % (ruta, nombre), inst_color, et, nodos + (ids[0],))
            activas.discard(ref)

    instanciar('ROOT', [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1.0], 'Layer0', 'ROOT', None, 'Layer0', ())
    return out


# Muebles, equipo, gente y plantas fuera (1 oct 2026). El SKP de Irving traia
# 7.2 M caras y la maqueta (terreno, cimentacion, muros, techo) eran 93 mil: el
# resto era un escenario con guitarras (una sola: 843 mil caras), mesas de bar,
# bocinas y luces de 3D Warehouse. Lo que se corta es ralo (un muro: ~1 triangulo
# por m2, un terreno: ~2); un mueble anda en miles. Se decide por DENSIDAD y no
# por el nombre de la etiqueta: cada alumno las nombra distinto.
# ponytail: umbral fijo a escala real; un modelo a escala equivocada lo cuida
# MAX_QUITAR_AREA (no se filtra). Si algo arquitectonico muy detallado se va,
# subir DENSO.
DENSO = 1000.0          # triangulos por m2: arriba de esto es mueble/equipo/planta
RALO = 20.0             # abajo de esto es superficie de maqueta
MAX_QUITAR_AREA = 0.6   # si se iria mas de esta parte del area, no se quita nada
CHICO = 3.0             # m: un componente denso de este tamaño es un mueble, se va entero


def _quitar_detalle(prims):
    """(prims que se quedan, resumen) a partir de lo que entrega _hornear.

    De la raiz hacia abajo, por instancia: si su subarbol es denso y es chico
    (una mesa de bar, aunque su tabla sea plana) o casi no trae superficie rala,
    se va entero. Si es denso, grande y trae superficie rala (un grupo con el
    piso del escenario Y las guitarras), se baja un nivel y se decide hijo por
    hijo. Las caras sueltas de
    cada definicion se juzgan por su propia densidad.
    resumen = {'caras': quitadas, 'area': m2 quitados, 'nombres': Counter} o None
    si no se quito nada (o se iba demasiado y se dejo todo)."""
    import collections
    import numpy as np
    info = []
    nodo = collections.defaultdict(lambda: [0, 0.0, 0.0, None, None])   # caras, area, rala, min, max
    hijos = collections.defaultdict(list)
    nombre = {}
    for v, f, et, ruta, nodos in prims:
        if v.size < 9 or f.size < 3:
            info.append((0, 0.0))
            continue
        t = v[f]
        a = 0.5 * float(np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum())
        info.append((len(f), a))
        rala = a if len(f) < RALO * a else 0.0
        mn, mx = v.min(axis=0), v.max(axis=0)
        partes = ruta.split(' / ')
        for k, n in enumerate(nodos):
            s = nodo[n]
            s[0] += len(f)
            s[1] += a
            s[2] += rala
            s[3] = mn if s[3] is None else np.minimum(s[3], mn)
            s[4] = mx if s[4] is None else np.maximum(s[4], mx)
            if n not in nombre:
                nombre[n] = partes[k + 1]
                hijos[nodos[k - 1] if k else 0].append(n)

    fuera = set()

    def ver(n):
        for h in hijos.get(n, ()):
            t, a, rala, mn, mx = nodo[h]
            if t >= DENSO * a and (float((mx - mn).max()) <= CHICO or
                                   (rala < 0.25 * a and rala < 10.0)):
                fuera.add(h)        # mueble, o denso sin superficie de maqueta: entero
            else:
                ver(h)              # ralo o mezclado: se decide hijo por hijo

    ver(0)
    quitar = []
    for (v, f, et, ruta, nodos), (t, a) in zip(prims, info):
        if t and (any(n in fuera for n in nodos) or
                  (t >= DENSO * a and len(f) >= 50)):   # caras sueltas densas (no 1 triangulo)
            quitar.append(True)
        else:
            quitar.append(False)
    caras = sum(t for (t, a), q in zip(info, quitar) if q)
    area = sum(a for (t, a), q in zip(info, quitar) if q)
    total = sum(a for t, a in info)
    if not caras or (total and area > MAX_QUITAR_AREA * total):
        return prims, None
    nombres = collections.Counter(nombre[n] for n in fuera)
    return [p for p, q in zip(prims, quitar) if not q], {'caras': caras, 'area': area,
                                                        'nombres': nombres}


def _aviso_detalle(r, quedan):
    top = ', '.join('%s%s' % (n, ' x%d' % k if k > 1 else '') for n, k in r['nombres'].most_common(6))
    return ('quitamos %s caras de muebles, equipo, gente o plantas (%s%s): eso no se corta. '
            'Quedan %s caras de construccion y terreno.'
            % (format(r['caras'], ','), top, '...' if len(r['nombres']) > 6 else '',
               format(quedan, ',')))


def cargar_skp(ruta, usar_cache=True, avisar=True):
    """Devuelve la malla del .skp en metros y con Z arriba.

    Junta todas las piezas de la escena ya colocadas en su lugar (los
    componentes vienen instanciados con su transformacion aplicada), tal como
    las ve `trimesh.load(..., force='mesh')` con cualquier otro formato.
    """
    import numpy as np
    import trimesh

    if usar_cache:
        # -v2: desde el 1 oct 2026 la malla guardada ya viene sin muebles ni equipo
        # (_quitar_detalle); una de antes no sirve. Los avisos van al lado, en .json.
        guardada = os.path.join(CACHE, '%s-%s-v2.ply'
                                % (os.path.splitext(os.path.basename(ruta))[0].replace(' ', '_'),
                                   _firma(ruta)))
        notas = guardada[:-4] + '.json'
        if os.path.exists(guardada):
            # Un PLY a medias (el corte se mato o se quedo sin memoria escribiendolo)
            # tronaba en CADA subida siguiente del mismo modelo: se tira y se relee,
            # igual que en dxf.py. 24 sep 2026.
            try:
                m = trimesh.load(guardada, force='mesh')
            except Exception:
                m = None
            if m is not None and not m.is_empty:
                try:
                    with open(notas, encoding='utf-8') as f:
                        m.metadata['despiece_avisos'] = json.load(f)
                except Exception:
                    m.metadata['despiece_avisos'] = []
                return m
            for f in (guardada, notas):
                try:
                    os.remove(f)
                except OSError:
                    pass

    skp = _abrir(ruta)
    if avisar:
        print('leyendo %s (SketchUp, primera vez tarda)...' % os.path.basename(ruta))
    try:
        from openskp import _core
        prims = _hornear(_core.full_parse(str(skp.path)))
    except Exception as e:
        raise SystemExit('el .skp se abrio pero no se pudo armar la geometria (%s): %s'
                         % (e, ruta))
    prims, quitado = _quitar_detalle(prims)

    verts, caras, base = [], [], 0
    for v, f, *_ in prims:
        if v.size < 9 or f.size < 3:
            continue
        verts.append(v)
        caras.append(f + base)
        base += len(v)

    if not verts:
        raise SystemExit('el .skp no trae caras: solo lineas, texto o guias. '
                         'Necesito superficies: %s' % ruta)

    m = trimesh.Trimesh(vertices=np.vstack(verts), faces=np.vstack(caras),
                        process=False)
    # SketchUp parte el vertice por material y por cara; sin soldar, separar
    # cuerpos da miles de islas de dos triangulos (el mismo problema que el OBJ
    # exportado desde SketchUp, ver README).
    m.merge_vertices()
    avisos = [_aviso_detalle(quitado, len(m.faces))] if quitado else []
    m.metadata['despiece_avisos'] = avisos

    if usar_cache:
        # De un golpe: a .tmp y luego se renombra, para que nunca quede uno a medias.
        try:
            os.makedirs(CACHE, exist_ok=True)
            with open(notas + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(avisos, f, ensure_ascii=False)
            os.replace(notas + '.tmp', notas)
            m.export(guardada + '.tmp', file_type='ply')
            os.replace(guardada + '.tmp', guardada)
        except Exception:
            for f in (guardada + '.tmp', notas + '.tmp'):
                try:
                    os.remove(f)
                except OSError:
                    pass
    return m
