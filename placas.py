# -*- coding: utf-8 -*-
"""Despiece ESTRUCTURAL: saca muros, losas y techos de un modelo como placas planas.

Idea central: cada cuerpo tipo lamina se corta por su PLANO MEDIO. Esa seccion es
exactamente la pieza a cortar, con ventanas y puertas ya recortadas, sin booleanas.
"""
import warnings

import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon
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
    fundidos = []
    for g in grupos:
        for f in fundidos:
            if float(np.dot(f[0], g[0])) > 1 - tol_n and abs(f[1] - g[1]) < tol_d:
                f[2].extend(g[2]); f[3] += g[3]
                break
        else:
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
    for i, a in enumerate(planos):
        if i in tomados:
            continue
        grupo = [i]
        for j in range(i + 1, len(planos)):
            if j in tomados:
                continue
            b = planos[j]
            if float(np.dot(a['n'], b['n'])) < 1 - TOL_NORMAL:
                continue
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


def fundir_pegadas(placas, t_modelo, min_encime=0.05):
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
    for i in orden:
        if i in tomadas:
            continue
        ni, di = datos[i]
        F = _frame_desde_normal(ni, ni * di)
        base = _proyectar_a_marco(placas[i], F)
        grupo, ds = [i], [di]
        for j in orden:
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
            try:
                base = unary_union([base, otra]).buffer(COSTURA).buffer(-COSTURA)
                if base.geom_type != 'Polygon':
                    base = max(base.geoms, key=lambda g: g.area)
            except Exception:
                pass

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


def extraer_placas(mesh, min_area=MIN_AREA_REAL, min_area_sup=MIN_AREA_SUP,
                   t_modelo=0.0):
    """Devuelve (placas, descartados). Cada placa: normal, espesor real,
    poligono 2D (m), marco 3D."""
    # sin soldar, un OBJ real se parte en miles de cuerpos de dos triangulos y
    # ninguno llega a placa
    mesh = _soldar(mesh)
    cuerpos = mesh.split(only_watertight=False)
    if len(cuerpos) <= 1:
        cuerpos = [mesh]

    placas, descartados = [], []
    for idx, c in enumerate(cuerpos):
        if len(c.faces) < 4 or c.area < 1e-6:
            descartados.append((idx, 'astilla degenerada (%d caras)' % len(c.faces)))
            continue
        try:
            ejes, ext, centro = _obb(c)
        except Exception as e:
            # cuerpos degenerados (vertices colineales) revientan oriented_bounds
            descartados.append((idx, 'caja orientada no calculable: %s' % e))
            continue
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
            'tipo': clasificar(n),
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
    if len(placas) < 3 or area_solida < 0.15 * float(mesh.area):
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
    m = trimesh.load(sys.argv[1] if len(sys.argv) > 1 else 'out/casa_prueba.stl', force='mesh')
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
