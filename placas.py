# -*- coding: utf-8 -*-
"""Despiece ESTRUCTURAL: saca muros, losas y techos de un modelo como placas planas.

Idea central: cada cuerpo tipo lamina se corta por su PLANO MEDIO. Esa seccion es
exactamente la pieza a cortar, con ventanas y puertas ya recortadas, sin booleanas.
"""
import warnings

import numpy as np
import trimesh
from trimesh.graph import connected_components
import shapely.affinity as aff
from shapely.geometry import (Polygon, MultiPolygon, LineString,
                              Point as ShapelyPoint)
from shapely.ops import unary_union

# los cuerpos degenerados de un modelo real (astillas, caras dobles) hacen que
# trimesh divida entre cero al calcular su centro de masa: es esperado, se filtra
warnings.filterwarnings('ignore', category=RuntimeWarning, module='trimesh')

# relacion espesor/ancho para considerar que un cuerpo es una placa
RAZON_PLACA = 0.34
MIN_AREA_REAL = 0.05          # m2, por debajo de esto es basura

# --- modelos hechos de caras sin espesor (SketchUp y companía) ---
TOL_NORMAL = 0.02             # 1-|cos| para dar dos caras por coplanares
TOL_PLANO = 0.02              # m: que tan lejos del plano cae un vertice
MURO_MAX = 0.80               # m: separacion maxima entre las dos caras de un muro
MIN_AREA_SUP = 1.0            # m2: parche mas chico que esto es moldura, no placa
COSTURA = 1e-4                # m: cierra las costuras entre triangulos vecinos


def _obb(cuerpo):
    """Ejes y extensiones de la caja orientada, ordenados de menor a mayor."""
    T = cuerpo.bounding_box_oriented.primitive.transform
    ext = np.array(cuerpo.bounding_box_oriented.primitive.extents, dtype=float)
    ejes = np.array([T[:3, 0], T[:3, 1], T[:3, 2]])
    orden = np.argsort(ext)
    return ejes[orden], ext[orden], T[:3, 3]


def clasificar(normal):
    nz = abs(float(normal[2]))
    if nz > 0.97:          # horizontal: piso o losa
        return 'losa'
    if nz < 0.20:          # vertical: muro
        return 'muro'
    return 'techo'         # inclinada: faldon


def _marco(ejes, centro):
    """4x4 del plano de la placa: u = eje mas largo, v = medio, n = espesor.
    Marco propio y determinista; el de to_planar sale girado al azar."""
    n, v, u = ejes[0], ejes[1], ejes[2]
    n = n / np.linalg.norm(n)
    u = u / np.linalg.norm(u)
    v = np.cross(n, u)
    v = v / np.linalg.norm(v)
    F = np.eye(4)
    F[:3, 0], F[:3, 1], F[:3, 2], F[:3, 3] = u, v, n, centro
    return F


def _frame_desde_normal(n, centro):
    """Marco 4x4 con Z = n y un U cualquiera perpendicular (la rotacion en el
    plano no afecta el corte; el acomodo ya rota la pieza)."""
    n = np.asarray(n, float); n = n / np.linalg.norm(n)
    ref = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(ref, n); u = u / np.linalg.norm(u)
    v = np.cross(n, u)
    F = np.eye(4)
    F[:3, 0], F[:3, 1], F[:3, 2], F[:3, 3] = u, v, n, centro
    return F


def _soldar(mesh):
    """Un OBJ de SketchUp parte el vertice por cada coordenada de textura y por
    cada normal: la misma esquina aparece 3 o 4 veces y la malla queda hecha
    confeti (1262 'cuerpos' en la casa Bauhaus, que en realidad son 334)."""
    m = mesh.copy()
    try:
        m.merge_vertices(merge_tex=True, merge_norm=True)
    except TypeError:                      # trimesh viejo
        try:
            m.merge_vertices()
        except Exception:
            pass
    except Exception:
        pass
    return m


# el umbral de area mas permisivo de los dos que piden los clientes de esta
# funcion: extraer_placas tira a 1e-6 y cuerpos_macizos a 1e-9. Se prefiltra con
# el flojo y cada quien aplica el suyo en su propio loop, para no cambiarle el
# conjunto de cuerpos a ninguno de los dos.
AREA_ASTILLA = 1e-9


def _borde_exterior(g):
    """Las coordenadas del contorno, venga como venga la geometria.

    `poly` casi siempre es un Polygon, pero las operaciones de recorte pueden
    dejar un MultiPolygon o una GeometryCollection con lineas sueltas adentro.
    Pedirle `.exterior` a eso truena. Devuelve None cuando no hay contorno que
    valga, para que quien llame lo salte en vez de morirse.
    """
    if g is None or g.is_empty:
        return None
    if g.geom_type != 'Polygon':
        partes = [p for p in (g.geoms if hasattr(g, 'geoms') else [])
                  if p.geom_type == 'Polygon' and not p.is_empty]
        if not partes:
            return None
        g = max(partes, key=lambda x: x.area)
    coords = list(g.exterior.coords)
    return coords or None


def preparar_cuerpos(mesh, min_caras=4, min_area=AREA_ASTILLA):
    """Suelda la malla y devuelve (soldada, cuerpos, astillas).

    Reemplaza a `mesh.split(only_watertight=False)`, que arma un Trimesh completo
    por cada componente conectado. En un modelo de SketchUp eso son decenas de
    miles: en el de Irving, 26 361 componentes, de los que 25 724 (97.6%) son
    astillas de una o dos caras que el loop de quien llama tira en su primera
    linea. Construirlas cuesta ~52 s y tanta memoria que el proceso se muere de
    un SIGKILL antes de entregar nada.

    El grafo de conectividad, en cambio, cuesta 0.03 s y ya trae cuantas caras
    tiene cada componente: se filtra ahi y se construye solo lo que sobrevive
    (637 de 26 361 en ese modelo). Medido: 52 s -> 1.03 s, misma salida.

    `astillas` son pares (indice, n_caras) de lo que se filtro. Van de vuelta
    porque el conteo de descartados se le enseña al alumno y tiene que seguir
    dando el mismo numero.
    """
    m = _soldar(mesh)
    if len(m.faces) == 0:
        return m, [], []
    comps = connected_components(m.face_adjacency,
                                 nodes=np.arange(len(m.faces)), min_len=1)
    # un solo cuerpo (o ninguno): el mismo repliegue que traia el codigo con split
    if len(comps) <= 1:
        return m, [m], []
    areas = m.area_faces
    vivos, astillas = [], []
    for i, c in enumerate(comps):
        if len(c) < min_caras or areas[c].sum() < min_area:
            astillas.append((i, len(c)))
        else:
            vivos.append(c)
    if not vivos:
        return m, [], astillas
    # repair=True es lo que le pasa trimesh.graph.split a submesh: se conserva
    # para que la geometria salga identica a la de antes
    cuerpos = m.submesh(vivos, only_watertight=False, repair=True)
    if not isinstance(cuerpos, list):
        cuerpos = [cuerpos]
    return m, cuerpos, astillas


def tabla_obb(cuerpos):
    """El OBB de cada cuerpo, calculado UNA sola vez.

    `extraer_placas` y `cuerpos_macizos` piden la caja orientada de EXACTAMENTE
    los mismos cuerpos, y cada una es un casco convexo: medido, 1.99 s para 1525
    cuerpos, pagados dos veces. Se calcula aqui y se les pasa hecha.

    Cada entrada es la tupla (ejes, ext, centro) o la excepcion que solto
    `oriented_bounds` con un cuerpo degenerado, para que quien llama pueda
    reportarla con el mismo texto de siempre.
    """
    salida = []
    for c in cuerpos:
        try:
            salida.append(_obb(c))
        except Exception as e:      # vertices colineales: no hay caja que sacar
            salida.append(e)
    return salida


def _canonizar(N):
    """Normales con signo fijo: las dos caras de un muro apuntan al reves y son
    el mismo plano. Se voltea la que tenga negativa su componente dominante."""
    dom = np.argmax(np.abs(N), axis=1)
    signo = np.sign(N[np.arange(len(N)), dom])
    signo[signo == 0] = 1.0
    return N * signo[:, None]


def _agrupar_por_plano(mesh, tol_n=TOL_NORMAL, tol_d=TOL_PLANO):
    """Agrupa las caras por el plano que ocupan (normal + distancia al origen).

    Se agrupa por plano y NO por contigüidad: dos muros lejanos del mismo plano
    caen en el mismo grupo, pero al unir los triangulos salen como poligonos
    separados y se reparten despues. Al reves no tiene arreglo: 'facets' pide
    aristas compartidas y en un modelo real cada tramo de muro viene suelto.
    """
    N = _canonizar(np.asarray(mesh.face_normals, dtype=float))
    C = np.asarray(mesh.triangles_center, dtype=float)
    D = np.einsum('ij,ij->i', N, C)

    # cubetas por normal y distancia redondeadas: baja de O(caras^2) a O(cubetas^2)
    llaves = np.column_stack([np.round(N / tol_n), np.round(D / tol_d)]).astype(np.int64)
    cubetas = {}
    for i, k in enumerate(map(tuple, llaves)):
        cubetas.setdefault(k, []).append(i)

    # las cubetas vecinas son el mismo plano partido por el redondeo: se juntan
    grupos = []
    for caras in cubetas.values():
        idx = np.asarray(caras)
        peso = mesh.area_faces[idx]
        if peso.sum() <= 0:
            continue
        n = _canonizar((N[idx] * peso[:, None]).sum(axis=0)[None, :])[0]
        norma = np.linalg.norm(n)
        if norma < 1e-9:
            continue
        n /= norma
        grupos.append([n, float(np.average(D[idx], weights=peso)), list(idx),
                       float(peso.sum())])

    grupos.sort(key=lambda g: -g[3])
    # El mismo recorrido de antes (el primer fundido que empate se queda con el
    # grupo), pero la comparacion contra todos los fundidos va en un solo
    # producto de numpy. Con el doble ciclo, un .skp de 100 mil caras eran 180
    # millones de np.dot y minuto y medio.
    fundidos = []
    FN = np.empty((len(grupos), 3)); FD = np.empty(len(grupos))
    for g in grupos:
        k = len(fundidos)
        if k:
            ok = np.nonzero((FN[:k] @ g[0] > 1 - tol_n)
                            & (np.abs(FD[:k] - g[1]) < tol_d))[0]
            if len(ok):
                f = fundidos[ok[0]]
                f[2].extend(g[2]); f[3] += g[3]
                continue
        FN[k] = g[0]; FD[k] = g[1]
        fundidos.append(g)
    return fundidos


def _poly_de_caras(mesh, idx, F):
    """Une los triangulos del grupo proyectados al plano. El buffer de ida y
    vuelta cierra las costuras de grosor cero que deja la triangulacion."""
    inv = np.linalg.inv(F)
    tri = mesh.faces[np.asarray(idx)]
    vids = np.unique(tri)
    sub = np.full(len(mesh.vertices), -1, dtype=np.int64)
    sub[vids] = np.arange(len(vids))
    V = mesh.vertices[vids]
    P = (inv @ np.hstack([V, np.ones((len(V), 1))]).T).T[:, :2]
    caras2d = []
    for t in tri:
        g = Polygon(P[sub[t]])
        if not g.is_valid:
            g = g.buffer(0)
        if not g.is_empty and g.area > 1e-12:
            caras2d.append(g)
    if not caras2d:
        return None, 0.0
    try:
        u = unary_union(caras2d)
        u = u.buffer(COSTURA).buffer(-COSTURA)
    except Exception:
        return None, 0.0
    if u.is_empty:
        return None, 0.0
    return u, float(V[:, 2].min())


def _placas_de_superficies(mesh, min_area, muro_max=MURO_MAX, t_modelo=0.0):
    """Modelo hecho de CARAS SIN ESPESOR (SketchUp, croquis exportado): no hay
    cuerpo que seccionar, asi que la placa es la propia cara.

    Un muro asi viene dibujado con sus DOS caras, una por lado. Si cada una sale
    como pieza, el alumno corta el doble de carton y la maqueta queda con muros
    dobles. Se buscan los pares paralelos separados menos que `muro_max` cuyas
    siluetas se encimen, y se funden en una sola placa sobre el plano medio, con
    el espesor real del muro medido entre sus dos caras.
    """
    m = _soldar(mesh)
    grupos = _agrupar_por_plano(m)
    if not grupos:
        return [], 'sin parches coplanares'

    # silueta de cada grupo, en un marco que solo depende de la normal: asi dos
    # planos paralelos comparten ejes y sus siluetas se pueden comparar directo
    planos = []
    for n, d, idx, _ in grupos:
        F = _frame_desde_normal(n, n * d)
        poly, z_min = _poly_de_caras(m, idx, F)
        if poly is None:
            continue
        planos.append({'n': n, 'd': d, 'poly': poly, 'z_min': z_min, 'idx': idx})

    # Se agrupa, no se emparejan de dos en dos. Un muro real no siempre trae dos
    # caras limpias: trae la de afuera, la de adentro y a veces el paño de un
    # aplanado o de una particion pegada. Tomandolas por pares, la tercera se
    # queda suelta y termina como una placa aparte a 7 cm de la otra -> en la
    # maqueta son dos cartones que ocupan el mismo lugar.
    tomados = set()
    placas, sueltas = [], 0
    # La prueba de normales solo depende de a y b: se hace de un golpe contra
    # todos los planos y el ciclo de abajo ya solo recorre los paralelos. En un
    # .skp de 100 mil caras son 10 mil planos y 50 millones de pares.
    normales = np.array([p['n'] for p in planos], dtype=float).reshape(-1, 3)
    for i, a in enumerate(planos):
        if i in tomados:
            continue
        grupo = [i]
        paralelos = np.nonzero(normales[i + 1:] @ a['n'] >= 1 - TOL_NORMAL)[0] + i + 1
        for j in paralelos.tolist():
            if j in tomados:
                continue
            b = planos[j]
            # contra el grupo entero, no solo contra la primera: asi entra la
            # tercera cara aunque quede lejos de la de arranque
            if min(abs(b['d'] - planos[k]['d']) for k in grupo) > muro_max:
                continue
            try:
                comun = max(b['poly'].intersection(planos[k]['poly']).area for k in grupo)
            except Exception:
                comun = 0.0
            menor = min(b['poly'].area, min(planos[k]['poly'].area for k in grupo))
            cerca = min(abs(b['d'] - planos[k]['d']) for k in grupo)
            # Dos criterios, y basta con uno:
            #   - se tapan mas de la mitad: son la misma pared por los dos lados;
            #   - o estan mas juntas que el propio carton: aunque sean paredes
            #     distintas, a esta escala NO CABEN las dos. Un muro y su vecino a
            #     6 cm son 0.6 mm a 1:100, y el carton mide 2. Si salen como dos
            #     piezas, el alumno tiene dos cartones peleando el mismo lugar.
            if comun < 0.5 * menor and not (t_modelo > 0 and cerca < t_modelo
                                            and comun > 0.05 * menor):
                continue
            grupo.append(j)
            tomados.add(j)

        ds = [planos[k]['d'] for k in grupo]
        n = a['n']
        espesor = max(ds) - min(ds)          # de la cara de afuera a la de adentro
        d = (max(ds) + min(ds)) / 2.0        # plano medio del muro
        z_min = min(planos[k]['z_min'] for k in grupo)
        if len(grupo) == 1:
            poly = a['poly']
        else:
            try:
                poly = unary_union([planos[k]['poly'] for k in grupo])
                poly = poly.buffer(COSTURA).buffer(-COSTURA)
            except Exception:
                poly = a['poly']

        F = _frame_desde_normal(n, n * d)
        partes = [poly] if poly.geom_type == 'Polygon' else list(getattr(poly, 'geoms', []))
        for g in partes:
            if g.is_empty or g.area < min_area:
                sueltas += 1
                continue
            minx, miny, _, _ = g.bounds
            g = Polygon([(x - minx, y - miny) for x, y in g.exterior.coords],
                        [[(x - minx, y - miny) for x, y in r.coords] for r in g.interiors])
            Fp = F.copy()
            Fp[:3, 3] = F[:3, 3] + F[:3, 0] * minx + F[:3, 1] * miny
            placas.append({
                'i': len(placas),
                'tipo': clasificar(n),
                'normal': n,
                'espesor_real': float(espesor),   # 0 = suelta, se usa el del carton
                'poly': g,
                'a_mundo': Fp,
                'centro': np.asarray(Fp[:3, 3], dtype=float),
                'area': float(g.area),
                'z_min': float(z_min),
                'vanos': len(g.interiors),
                'sintetica': True,
            })

    n_fundidas = len(tomados)
    nota = '%d placas; %d caras se fundieron con otra por ser el mismo muro' % (len(placas), n_fundidas)
    if sueltas:
        nota += '; %d parches por debajo de %.2f m2 ignorados' % (sueltas, min_area)
    return placas, nota


def _proyectar_a_marco(placa, F):
    """La silueta de la placa vista en el marco F (2D)."""
    inv = np.linalg.inv(F)
    salida = []
    for anillo in [placa['poly'].exterior] + list(placa['poly'].interiors):
        pts = np.array(anillo.coords)
        L3 = np.hstack([pts, np.zeros((len(pts), 1)), np.ones((len(pts), 1))])
        W = (placa['a_mundo'] @ L3.T).T[:, :3]
        salida.append((inv @ np.hstack([W, np.ones((len(W), 1))]).T).T[:, :2])
    g = Polygon(salida[0], salida[1:])
    return g if g.is_valid else g.buffer(0)


_HOLGURA = 1e-6               # m: margen del prefiltro de fundir contra el redondeo


def _caja_mundo(placa):
    """(min, max) en el mundo de la silueta de la placa; con el contorno basta."""
    pts = np.array(placa['poly'].exterior.coords)
    if not len(pts):
        return np.full(3, np.nan), np.full(3, np.nan)     # nan: nunca pasa el filtro
    L3 = np.hstack([pts, np.zeros((len(pts), 1)), np.ones((len(pts), 1))])
    W = (placa['a_mundo'] @ L3.T).T[:, :3]
    return W.min(axis=0), W.max(axis=0)


def _fundir_una_vuelta(placas, t_modelo, min_encime=0.05):
    """Dos placas paralelas mas juntas que el propio carton son UNA placa.

    Pasa en los dos caminos. En un STL de verdad la losa viene como dos solidos
    apilados (la estructural y su firme) y cada uno da su placa: en la maqueta son
    dos cartones ocupando el mismo lugar. Main Street Place tenia asi el 1.9% de
    su material, todo en choques losa contra losa.

    Se funden sobre el plano medio del conjunto, con el espesor medido de la cara
    de arriba a la de abajo.
    """
    if t_modelo <= 0 or len(placas) < 2:
        return placas, 0

    # normal con signo fijo y distancia al origen: dos placas paralelas comparten
    # normal aunque apunten al reves
    datos = []
    for p in placas:
        n = np.array(p['normal'], dtype=float)
        n = n / np.linalg.norm(n)
        dom = int(np.argmax(np.abs(n)))
        if n[dom] < 0:
            n = -n
        datos.append((n, float(np.dot(n, np.asarray(p['centro'], dtype=float)))))

    # Se decide agrupando de la placa mas grande a la mas chica (la grande manda),
    # pero se ENTREGA en el orden original: detectar_contactos reparte los papeles
    # de ranura y espiga segun el orden de la lista, y reordenarla cambia el
    # despiece aunque no se funda nada.
    tomadas, fundidas = set(), 0
    hechas = {}
    orden = sorted(range(len(placas)), key=lambda k: -placas[k]['area'])

    # Prefiltro en numpy (14 sep 2026). Mandar cada placa contra TODAS a shapely era
    # O(n^2): "Mediana Irving Parte 1" (3.7 km, miles de losas al mismo nivel) se
    # quedaba horas aqui y tapaba la fila del servidor. Ahora a shapely solo llegan
    # las que PUEDEN encimarse: casi paralelas, a menos de un carton y con su caja
    # tocando la de la silueta del grupo. El filtro es holgado y las pruebas de
    # siempre corren igual sobre lo que pasa, asi que la salida no cambia.
    n_pl = len(placas)
    pos = np.empty(n_pl, dtype=np.int64)
    pos[orden] = np.arange(n_pl)
    N = np.array([d[0] for d in datos])
    D = np.array([d[1] for d in datos])
    cajas = np.array([_caja_mundo(p) for p in placas])          # (n, 2, 3)
    C, H = (cajas[:, 0] + cajas[:, 1]) / 2, (cajas[:, 1] - cajas[:, 0]) / 2
    for i in orden:
        if i in tomadas:
            continue
        ni, di = datos[i]
        F = _frame_desde_normal(ni, ni * di)
        base = _proyectar_a_marco(placas[i], F)
        grupo, ds = [i], [di]
        # caja de cada placa vista en el plano de i: centro y medio ancho en 2D
        A = np.linalg.inv(F)[:2]
        c2 = C @ A[:, :3].T + A[:, 3]
        r2 = H @ np.abs(A[:, :3]).T
        libre = N @ ni >= 1 - TOL_NORMAL - _HOLGURA
        libre[list(tomadas)] = False
        libre[i] = False
        desde = -1
        while True:
            # cada vez que el grupo crece se vuelve a filtrar: con la silueta y los
            # planos nuevos pueden alcanzar placas que antes quedaban lejos
            bx0, by0, bx1, by1 = base.bounds
            cerca = np.abs(D[:, None] - np.array(ds)[None, :]).min(axis=1) < t_modelo + _HOLGURA
            encima = ((c2[:, 0] + r2[:, 0] >= bx0 - _HOLGURA) & (c2[:, 0] - r2[:, 0] <= bx1 + _HOLGURA)
                      & (c2[:, 1] + r2[:, 1] >= by0 - _HOLGURA) & (c2[:, 1] - r2[:, 1] <= by1 + _HOLGURA))
            cand = np.nonzero(libre & cerca & encima & (pos > desde))[0]
            crecio = False
            for j in cand[np.argsort(pos[cand])].tolist():
                if j == i or j in tomadas:
                    continue
                nj, dj = datos[j]
                if float(np.dot(ni, nj)) < 1 - TOL_NORMAL:
                    continue
                if min(abs(dj - d) for d in ds) >= t_modelo:
                    continue
                otra = _proyectar_a_marco(placas[j], F)
                try:
                    comun = base.intersection(otra).area
                except Exception:
                    comun = 0.0
                if comun <= min_encime * min(base.area, otra.area):
                    continue
                grupo.append(j); ds.append(dj); tomadas.add(j)
                libre[j], desde, crecio = False, pos[j], True
                try:
                    base = unary_union([base, otra]).buffer(COSTURA).buffer(-COSTURA)
                    if base.geom_type != 'Polygon':
                        base = max(base.geoms, key=lambda g: g.area)
                except Exception:
                    pass
                break
            if not crecio:
                break

        if len(grupo) == 1:
            hechas[i] = placas[i]
            continue

        fundidas += len(grupo) - 1
        d_medio = (max(ds) + min(ds)) / 2.0
        Fm = _frame_desde_normal(ni, ni * d_medio)
        minx, miny, _, _ = base.bounds
        g = Polygon([(x - minx, y - miny) for x, y in base.exterior.coords],
                    [[(x - minx, y - miny) for x, y in r.coords] for r in base.interiors])
        Fp = Fm.copy()
        Fp[:3, 3] = Fm[:3, 3] + Fm[:3, 0] * minx + Fm[:3, 1] * miny
        # el grupo ocupa el lugar de su placa mas temprana
        hechas[min(grupo)] = {
            'i': i,
            'tipo': clasificar(ni),
            'normal': ni,
            'espesor_real': float(max(max(ds) - min(ds),
                                      max(placas[k]['espesor_real'] for k in grupo))),
            'poly': g,
            'a_mundo': Fp,
            'centro': np.asarray(Fp[:3, 3], dtype=float),
            'area': float(g.area),
            'z_min': float(min(placas[k]['z_min'] for k in grupo)),
            'vanos': len(g.interiors),
            'sintetica': any(placas[k].get('sintetica') for k in grupo),
            'fundida_de': len(grupo),
        }

    salida = [hechas[k] for k in sorted(hechas)]
    for k, p in enumerate(salida):
        p['i'] = k
    return salida, fundidas


def fundir_pegadas(placas, t_modelo, min_encime=0.05, vueltas=6):
    """Repite hasta que no quede nada por fundir.

    Con una sola pasada no basta: al fundir dos placas nace una tercera sobre el
    plano medio, y esa puede quedar pegada a otra que antes estaba lejos de las
    dos. En Main Street Place la primera vuelta funde 117 y la segunda otras 11.
    """
    total = 0
    for _ in range(vueltas):
        placas, n = _fundir_una_vuelta(placas, t_modelo, min_encime)
        total += n
        if not n:
            break
    return placas, total


def extraer_placas(mesh, min_area=MIN_AREA_REAL, min_area_sup=MIN_AREA_SUP,
                   t_modelo=0.0, preparado=None, obbs=None, tipos=None,
                   superficies=True):
    """Devuelve (placas, descartados). Cada placa: normal, espesor real,
    poligono 2D (m), marco 3D.

    `preparado` es la salida de preparar_cuerpos() y `obbs` la de tabla_obb():
    quien procesa el modelo completo los calcula una vez y los comparte con
    cuerpos_macizos, que si no repite el soldado, la division y los cascos
    convexos enteros sobre los mismos cuerpos.

    `tipos` es {indice de cuerpo: 'muro'} para cuando el archivo lo DICE en vez
    de haber que deducirlo de la normal: un .ifc trae `IFCWALL` escrito (ver
    ifc.py). Solo se pisa lo que venga en el diccionario; el resto sigue
    saliendo de `clasificar`.

    `superficies=False` apaga el repliegue de "modelo de caras sin espesor".
    Ese repliegue existe para rescatar modelos donde deducir las placas por
    geometria fallo, y **con un archivo que trae la semantica escrita hay que
    apagarlo**: en el `cira`, los 311 solidos del IFC daban 237 placas cuyo
    area suma menos del 15% del area de la malla --normal, la malla cuenta las
    DOS caras de cada solido y la placa es una sola seccion-- asi que el
    repliegue se disparaba, tiraba las 237 y las cambiaba por 678 parches de
    superficie sin tipo ni planta. O sea que el .ifc terminaba adivinando igual
    que un STL.
    """
    # sin soldar, un OBJ real se parte en miles de cuerpos de dos triangulos y
    # ninguno llega a placa
    mesh, cuerpos, astillas = (preparado if preparado is not None
                               else preparar_cuerpos(mesh))

    placas, descartados = [], []
    # las que ni se construyeron: el alumno ve este conteo, tiene que cuadrar
    descartados.extend((i, 'astilla degenerada (%d caras)' % n) for i, n in astillas)
    for idx, c in enumerate(cuerpos):
        if len(c.faces) < 4 or c.area < 1e-6:
            descartados.append((idx, 'astilla degenerada (%d caras)' % len(c.faces)))
            continue
        caja = obbs[idx] if obbs is not None else None
        if caja is None:
            try:
                caja = _obb(c)
            except Exception as e:
                caja = e
        if isinstance(caja, Exception):
            # cuerpos degenerados (vertices colineales) revientan oriented_bounds
            descartados.append((idx, 'caja orientada no calculable: %s' % caja))
            continue
        ejes, ext, centro = caja
        esp, medio, largo = ext
        if largo <= 1e-9 or esp / max(largo, 1e-9) > RAZON_PLACA or medio < 1e-6:
            descartados.append((idx, 'no es lamina (%.2f x %.2f x %.2f m)' % (esp, medio, largo)))
            continue

        n = ejes[0] / np.linalg.norm(ejes[0])
        F = _marco(ejes, centro)
        try:
            sec = c.section(plane_origin=centro, plane_normal=n)
            if sec is None:
                raise ValueError('sin seccion')
            plano, T3 = sec.to_2D(to_2D=np.linalg.inv(F))
        except Exception as e:
            descartados.append((idx, 'no se pudo seccionar: %s' % e))
            continue

        polis = [p for p in plano.polygons_full if p.area >= min_area]
        if not polis:
            descartados.append((idx, 'seccion vacia'))
            continue
        poly = max(polis, key=lambda p: p.area)

        placas.append({
            'i': len(placas),
            'cuerpo': idx,
            'tipo': (tipos or {}).get(idx) or clasificar(n),
            'normal': n,
            'espesor_real': float(esp),
            'poly': poly,               # metros, en el plano local
            'a_mundo': F,               # 4x4: local 2D -> mundo 3D
            'centro': centro,
            'area': float(poly.area),
            'z_min': float(c.bounds[0][2]),
            'vanos': len(poly.interiors),
        })

    # Fallback: si casi no salio nada como solido, el modelo son caras sin
    # espesor. Se agrupan los parches coplanares y se les da espesor sintetico.
    area_solida = sum(p['area'] for p in placas)
    if superficies and (len(placas) < 3 or area_solida < 0.15 * float(mesh.area)):
        sup, motivo = _placas_de_superficies(mesh, min_area_sup, t_modelo=t_modelo)
        if len(sup) > len(placas):
            for i, p in enumerate(sup):
                p['i'] = i
            nota = 'modelo de caras sin espesor: %d placas con grosor sintetico' % len(sup)
            if motivo:
                nota += ' (%s)' % motivo
            descartados.append((-1, nota))
            placas = sup

    # La regla vale para los DOS caminos: en un STL la losa viene como dos
    # solidos apilados y cada uno da su placa.
    placas, fundidas = fundir_pegadas(placas, t_modelo)
    if fundidas:
        descartados.append((-1, '%d placas se fundieron con otra por estar mas juntas '
                                'que el carton' % fundidas))
    return placas, descartados


def _puntos_de_muestra(poly, n=6):
    """Unos cuantos puntos repartidos DENTRO del poligono (no en el borde)."""
    pts = [poly.representative_point()]
    minx, miny, maxx, maxy = poly.bounds
    for fx in (0.25, 0.5, 0.75):
        for fy in (0.33, 0.66):
            p = ShapelyPoint(minx + fx * (maxx - minx), miny + fy * (maxy - miny))
            if poly.contains(p):
                pts.append(p)
            if len(pts) >= n:
                return pts
    return pts


def marcar_envolvente(placas, mesh, holgura=None):
    """Marca cada placa como de fuera o de dentro tirando un rayo hacia afuera.

    Un alumno que baja un edificio de cinco pisos casi nunca quiere las losas de
    entrepiso ni los muros interiores: quiere la caja. Desde varios puntos de la
    placa se tira un rayo en direccion de su normal, para los dos lados. Si por
    algun lado el rayo se va sin chocar con nada, esa cara mira a la calle.

    Funciona igual para muros (normal horizontal: el muro interior choca contra el
    de fachada) que para losas (normal vertical: el entrepiso choca contra el
    techo, el techo no choca con nada).
    """
    if not placas or mesh is None or len(mesh.faces) == 0:
        return 0
    if holgura is None:
        holgura = float(np.max(mesh.extents)) * 1e-3

    origenes, direcciones, dueno = [], [], []
    for k, p in enumerate(placas):
        n = np.asarray(p['normal'], dtype=float)
        n = n / np.linalg.norm(n)
        for sp in _puntos_de_muestra(p['poly']):
            L = np.array([sp.x, sp.y, 0.0, 1.0])
            w = (p['a_mundo'] @ L)[:3]
            for signo in (1.0, -1.0):
                origenes.append(w + n * signo * holgura)
                direcciones.append(n * signo)
                dueno.append(k)
    if not origenes:
        return 0

    try:
        pega = mesh.ray.intersects_any(ray_origins=np.array(origenes),
                                       ray_directions=np.array(direcciones))
    except Exception:
        for p in placas:
            p['exterior'] = True
        return len(placas)

    dueno = np.asarray(dueno)
    libre = ~np.asarray(pega)
    n_ext = 0
    for k, p in enumerate(placas):
        p['exterior'] = bool(libre[dueno == k].any())
        n_ext += 1 if p['exterior'] else 0
    return n_ext


def niveles_de_piso(placas, junta_m=0.60):
    """Las alturas donde hay losa, de abajo hacia arriba.

    Se agrupan las losas por su z: en un modelo real la losa de un nivel viene
    partida en varios pedazos (el pasillo, el volado, cada crujia) y todos estan
    a la misma altura salvo centimetros.

    NO se intenta adivinar cuales son "los pisos de verdad". Se probo de dos
    maneras y las dos fallan: agrupar con mas holgura encadena (en la casa
    Bauhaus -1.8, -0.4, 1.3 y 2.2 se pegan de uno en uno y quedan 4 niveles para
    un edificio de cinco), y filtrar por area tumba las plantas de una torre
    porque su plancha de terreno es mas grande que todas juntas (Main Street
    Place pasa de 33 niveles a 2). Se entregan los niveles que hay y el alumno
    escoge: `--pisos` los lista.
    """
    zs = sorted(float(p['z_min']) for p in placas if p['tipo'] == 'losa')
    if not zs:
        return []
    niveles, actual = [], [zs[0]]
    for z in zs[1:]:
        if z - actual[-1] <= junta_m:
            actual.append(z)
        else:
            niveles.append(sum(actual) / len(actual))
            actual = [z]
    niveles.append(sum(actual) / len(actual))
    return niveles


def _banda(placa, z0, z1, holgura=1e-6):
    """La franja de alturas [z0, z1] llevada al plano local de la placa.

    La placa es plana, asi que la z del mundo es una funcion afin de sus
    coordenadas locales: la franja de alturas es una banda recta en su plano.
    Devuelve el Polygon de la banda, o True/False si la placa es horizontal
    (entra entera o no entra).
    """
    F = placa['a_mundo']
    a, b, c0 = float(F[2, 0]), float(F[2, 1]), float(F[2, 3])
    norma = float(np.hypot(a, b))
    if norma < 1e-9:                       # placa horizontal: entra o no entra
        return bool(z0 - holgura <= c0 <= z1 + holgura)

    n = np.array([a, b]) / norma           # hacia donde sube la z, en el plano
    e = np.array([-n[1], n[0]])
    t0, t1 = (z0 - c0) / norma, (z1 - c0) / norma
    b_ = placa['poly'].bounds
    L = (abs(b_[2] - b_[0]) + abs(b_[3] - b_[1])) * 2 + 10.0
    return Polygon([n * t0 + e * (-L), n * t1 + e * (-L),
                    n * t1 + e * L, n * t0 + e * L])


def _recortar_a_franja(placa, z0, z1, holgura=1e-6):
    """La parte de la placa que cae entre las alturas z0 y z1."""
    banda = _banda(placa, z0, z1, holgura)
    if banda is True:
        return placa['poly']
    if banda is False:
        return None
    try:
        g = placa['poly'].intersection(banda)
    except Exception:
        return None
    if g.is_empty:
        return None
    if g.geom_type != 'Polygon':
        partes = [x for x in getattr(g, 'geoms', []) if x.geom_type == 'Polygon']
        if not partes:
            return None
        g = max(partes, key=lambda x: x.area)
    return g


def cortar_por_piso(placas, piso, junta_m=0.60):
    """Se queda con un solo nivel: su losa y el tramo de muro que le toca.

    Un edificio no se arma de una pieza, se arma planta por planta. Ademas
    resuelve solo el problema del muro medianero: un muro de un solo nivel recibe
    un punado de ranuras en vez de sesenta, y ya no hay que cancelarle uniones.

    Los muros se CORTAN a la altura del nivel, no se descartan: la placa es plana,
    asi que la z del mundo es una funcion afin de sus coordenadas locales y la
    franja de alturas es una banda recta en su plano.

    `piso` va desde 1. Devuelve (placas_del_piso, niveles, aviso).
    """
    niveles = niveles_de_piso(placas, junta_m)
    if not niveles:
        return placas, [], 'no se hallaron losas: no hay de donde sacar los pisos'
    if piso < 1 or piso > len(niveles):
        return [], niveles, ('el modelo tiene %d pisos y se pidio el %d'
                             % (len(niveles), piso))

    z0 = niveles[piso - 1]
    if piso < len(niveles):
        z1 = niveles[piso]
    else:
        # Ultimo piso: no hay losa de arriba, asi que la franja llega hasta lo mas
        # alto del modelo. Cambio local (12 sep 2026): con z1 = la z mas alta, el
        # corte de abajo (z1 - junta/2) caia 30 cm debajo de ella y el ultimo
        # piso perdia su remate: muros cortos y el techo fuera.
        z1 = max(max(float((p['a_mundo'] @ np.array(
            [c[0], c[1], 0.0, 1.0]))[2]) for c in p['poly'].exterior.coords)
            for p in placas) + junta_m

    salida = []
    for p in placas:
        g = _recortar_a_franja(p, z0 - junta_m / 2.0, z1 - junta_m / 2.0)
        if g is None or g.area <= 0:
            continue
        q = dict(p)
        q['poly'] = g
        q['area'] = float(g.area)
        q['vanos'] = len(g.interiors)
        salida.append(q)
    for k, p in enumerate(salida):
        p['i'] = k
    return salida, niveles, ''


# ------------------------------------------- la planta grabada sobre la losa
def _mundo_xy(placa):
    """Afin 2D local (u,v) -> mundo (x,y), en el formato de shapely."""
    F = placa['a_mundo']
    return [float(F[0, 0]), float(F[0, 1]),
            float(F[1, 0]), float(F[1, 1]),
            float(F[0, 3]), float(F[1, 3])]


def _desde_mundo_xy(placa):
    """La inversa del anterior. None si la placa no es horizontal (no invierte)."""
    F = placa['a_mundo']
    A = np.array([[F[0, 0], F[0, 1]], [F[1, 0], F[1, 1]]], dtype=float)
    if abs(float(np.linalg.det(A))) < 1e-12:
        return None
    Ai = np.linalg.inv(A)
    t = Ai @ np.array([float(F[0, 3]), float(F[1, 3])])
    return [Ai[0, 0], Ai[0, 1], Ai[1, 0], Ai[1, 1], -t[0], -t[1]]


def _huella_en_planta(placa, z0, z1, t_min):
    """Lo que la placa ocupa EN PLANTA entre las alturas z0 y z1, en mundo XY.

    Una placa es plana: si es vertical, su sombra en planta es un segmento de
    recta, y el muro de verdad es ese segmento engordado su espesor. Se rebana a
    la altura pedida (no el muro entero) para que las PUERTAS salgan como huecos:
    a 15 cm del piso el vano ya no tiene material y la huella se parte en dos.
    """
    banda = _banda(placa, z0, z1)
    if banda is True or banda is False:
        return []
    try:
        g = placa['poly'].intersection(banda)
    except Exception:
        return []
    if g.is_empty:
        return []
    partes = [x for x in (g.geoms if g.geom_type.startswith('Multi') else [g])
              if x.geom_type == 'Polygon' and not x.is_empty]
    if not partes:
        return []

    F = placa['a_mundo']
    nx, ny = float(F[0, 2]), float(F[1, 2])       # normal de la placa, en planta
    ln = float(np.hypot(nx, ny))
    if ln < 1e-9:                                 # horizontal: no deja huella
        return []
    dx, dy = -ny / ln, nx / ln                    # por aqui corre el muro
    M = _mundo_xy(placa)
    t = max(float(placa.get('espesor_real') or 0.0), t_min)

    salida = []
    for p in partes:
        xy = [(M[0] * u + M[1] * v + M[4], M[2] * u + M[3] * v + M[5])
              for u, v in p.exterior.coords]
        ts = [dx * x + dy * y for x, y in xy]
        # el pie de la recta: le quito a un punto cualquiera su avance sobre d y
        # me queda su desplazamiento perpendicular, que es igual para todos
        px, py = xy[0][0] - dx * ts[0], xy[0][1] - dy * ts[0]
        a = (px + dx * min(ts), py + dy * min(ts))
        b = (px + dx * max(ts), py + dy * max(ts))
        if (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 < 1e-12:
            continue
        salida.append(LineString([a, b]).buffer(t / 2.0, cap_style=2,
                                                join_style=2))
    return salida


def huellas_en_losas(placas, t_min, junta_m=0.60, alto_m=0.15):
    """Graba en cada losa la PLANTA de los muros que se paran encima.

    Es lo que separa un monton de rectangulos anonimos de algo que se puede
    armar: el alumno ve dibujado en la losa donde va cada muro, como en el
    archivo que hace a mano un arquitecto. Se suma a las marcas de ensamble que
    ya trae la placa. Devuelve cuantas losas quedaron marcadas.
    """
    losas = [p for p in placas if p['tipo'] == 'losa']
    muros = [p for p in placas if p['tipo'] in ('muro', 'techo')]
    if not losas or not muros:
        return 0

    niveles = niveles_de_piso(placas, junta_m)
    if not niveles:
        return 0
    # Segunda red: aunque _figura ya entrega siempre un Polygon, una sola placa
    # rara no debe apagar el grabado de TODAS las losas. Antes cualquier
    # geometria sin `.exterior` reventaba aqui y, como la llamada va dentro de un
    # try/except, la planta se perdia entera con un aviso gris.
    z_tope = None
    for p in placas:
        borde = _borde_exterior(p['poly'])
        if borde is None:
            continue
        z = float(np.max((p['a_mundo'] @ np.array(
            [[c[0], c[1], 0.0, 1.0] for c in borde]).T)[2]))
        z_tope = z if z_tope is None else max(z_tope, z)
    if z_tope is None:
        return 0

    marcadas = 0
    for losa in losas:
        z = float(losa['z_min'])
        i = min(range(len(niveles)), key=lambda k: abs(niveles[k] - z))
        z_sig = niveles[i + 1] if i + 1 < len(niveles) else z_tope
        za = z + float(losa.get('espesor_real') or 0.0) / 2.0 + 0.01
        zb = min(za + alto_m, z_sig - 0.02)
        if zb <= za:
            zb = za + 0.01
        inv = _desde_mundo_xy(losa)
        if inv is None:
            continue

        dibujo = []
        for w in muros:
            for h in _huella_en_planta(w, za, zb, t_min):
                try:
                    g = aff.affine_transform(h, inv).intersection(losa['poly'])
                except Exception:
                    continue
                if not g.is_empty:
                    dibujo.append(g)
        if not dibujo:
            continue
        previo = losa.get('marcas')
        if previo is not None and not previo.is_empty:
            dibujo.append(previo)
        g = unary_union(dibujo)
        partes = [x for x in (g.geoms if g.geom_type.startswith('Multi') else [g])
                  if x.geom_type == 'Polygon' and x.area > 1e-9]
        if not partes:
            continue
        losa['marcas'] = partes[0] if len(partes) == 1 else MultiPolygon(partes)
        marcadas += 1
    return marcadas


def asignar_planta(placas, junta_m=0.60, plantas=None):
    """Marca cada placa con la planta a la que pertenece (1 = la de mas abajo).

    Sirve para no revolver las plantas en la misma hoja: una hoja con los muros
    de la baja y de la alta mezclados no hay quien la arme.

    `plantas` es {indice de cuerpo: numero de planta} cuando el archivo lo trae
    escrito --el .ifc mete cada elemento en su `IfcBuildingStorey`-- y entonces
    no hay que agrupar losas por su Z. Es mejor dato: agrupar por Z encadena
    los niveles cuando el edificio tiene medios pisos o rampas.
    """
    if plantas:
        usados = set()
        for p in placas:
            k = plantas.get(p.get('cuerpo'))
            p['planta'] = k if k else 1
            usados.add(p['planta'])
        return max(usados) if usados else 1
    niveles = niveles_de_piso(placas, junta_m)
    if not niveles:
        for p in placas:
            p['planta'] = 1
        return 1
    for p in placas:
        z = float(p['z_min'])
        k = 0
        for i, nz in enumerate(niveles):
            if z >= nz - junta_m / 2.0:
                k = i
        p['planta'] = k + 1
    return len(niveles)


def nombrar(placas):
    """IDs legibles: M1..Mn muros, L1.. losas, T1.. techos. De abajo hacia arriba."""
    pref = {'muro': 'M', 'losa': 'L', 'techo': 'T'}
    cont = {}
    for p in sorted(placas, key=lambda p: (p['tipo'], p['z_min'], -p['area'])):
        k = pref[p['tipo']]
        cont[k] = cont.get(k, 0) + 1
        p['id'] = '%s%d' % (k, cont[k])
    return placas


if __name__ == '__main__':
    import sys
    from despiece import cargar_modelo
    m = cargar_modelo(sys.argv[1] if len(sys.argv) > 1 else 'out/casa_prueba.stl')
    placas, desc = extraer_placas(m)
    nombrar(placas)
    print('cuerpos separados: %d | placas: %d | descartados: %d'
          % (len(m.split(only_watertight=False)), len(placas), len(desc)))
    print('%-5s %-7s %8s %8s %8s %6s' % ('id', 'tipo', 'ancho m', 'alto m', 'esp cm', 'vanos'))
    for p in sorted(placas, key=lambda p: p['id']):
        b = p['poly'].bounds
        print('%-5s %-7s %8.2f %8.2f %8.1f %6d'
              % (p['id'], p['tipo'], b[2] - b[0], b[3] - b[1], p['espesor_real'] * 100, p['vanos']))
    for i, r in desc:
        print('  descartado cuerpo %d: %s' % (i, r))
