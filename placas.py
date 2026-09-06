# -*- coding: utf-8 -*-
"""Despiece ESTRUCTURAL: saca muros, losas y techos de un modelo como placas planas.

Idea central: cada cuerpo tipo lamina se corta por su PLANO MEDIO. Esa seccion es
exactamente la pieza a cortar, con ventanas y puertas ya recortadas, sin booleanas.
"""
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

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


def extraer_placas(mesh, min_area=MIN_AREA_REAL):
    """Devuelve lista de placas: normal, espesor real, poligono 2D (m), marco 3D."""
    cuerpos = mesh.split(only_watertight=False)
    if len(cuerpos) <= 1:
        cuerpos = [mesh]

    placas, descartados = [], []
    for idx, c in enumerate(cuerpos):
        if len(c.faces) < 4 or c.area < 1e-6:
            descartados.append((idx, 'astilla degenerada (%d caras)' % len(c.faces)))
            continue
        ejes, ext, centro = _obb(c)
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
