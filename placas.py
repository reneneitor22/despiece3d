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


def _placas_de_superficies(mesh, min_area):
    """Modelo hecho de CARAS SIN ESPESOR (SketchUp, export de croquis): no hay
    cuerpo que seccionar. Agrupamos las caras coplanares y conexas (facets) y
    tomamos cada parche como una placa con espesor sintetico (el del carton).
    """
    m = mesh.copy()
    try:
        m.merge_vertices()
    except Exception:
        pass
    facets = list(getattr(m, 'facets', []))
    if not facets:
        return [], 'sin parches coplanares'

    areas = m.facets_area
    normales = m.facets_normal
    placas, sueltas = [], 0
    for k, caras in enumerate(facets):
        area = float(areas[k])
        n = np.asarray(normales[k], float)
        if area < min_area or not np.isfinite(n).all() or np.linalg.norm(n) < 0.5:
            sueltas += 1
            continue
        n = n / np.linalg.norm(n)
        tri = m.faces[caras]
        vids = np.unique(tri)
        sub = np.full(m.vertices.shape[0], -1)
        sub[vids] = np.arange(len(vids))
        V = m.vertices[vids]
        centro = V.mean(axis=0)
        F = _frame_desde_normal(n, centro)
        inv = np.linalg.inv(F)
        P = (inv @ np.hstack([V, np.ones((len(V), 1))]).T).T[:, :2]
        tris2d = [Polygon(P[sub[t]]) for t in tri]
        try:
            poly = unary_union([g.buffer(0) for g in tris2d if g.is_valid and g.area > 1e-12])
        except Exception:
            continue
        cand = [poly] if poly.geom_type == 'Polygon' else list(getattr(poly, 'geoms', []))
        for g in cand:
            if g.is_empty or g.area < min_area:
                continue
            minx, miny, _, _ = g.bounds
            g = Polygon([(x - minx, y - miny) for x, y in g.exterior.coords],
                        [[(x - minx, y - miny) for x, y in r.coords] for r in g.interiors])
            Fp = F.copy(); Fp[:3, 3] = centro + F[:3, 0] * minx + F[:3, 1] * miny
            placas.append({
                'i': len(placas),
                'tipo': clasificar(n),
                'normal': n,
                'espesor_real': 0.0,          # sintetico: se usa el del carton
                'poly': g,
                'a_mundo': Fp,
                'centro': np.asarray(Fp[:3, 3]),
                'area': float(g.area),
                'z_min': float(m.vertices[vids][:, 2].min()),
                'vanos': len(g.interiors),
                'sintetica': True,
            })
    return placas, ('%d parches sueltos ignorados' % sueltas if sueltas else '')


def extraer_placas(mesh, min_area=MIN_AREA_REAL):
    """Devuelve (placas, descartados). Cada placa: normal, espesor real,
    poligono 2D (m), marco 3D."""
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
        sup, motivo = _placas_de_superficies(mesh, min_area)
        if len(sup) > len(placas):
            for i, p in enumerate(sup):
                p['i'] = i
            nota = 'modelo de caras sin espesor: %d placas con grosor sintetico' % len(sup)
            if motivo:
                nota += ' (%s)' % motivo
            descartados.append((-1, nota))
            return sup, descartados

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
