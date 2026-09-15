# -*- coding: utf-8 -*-
"""Casa de prueba: muros con espesor y vanos, losa, techo a dos aguas y frontones."""
import os, numpy as np, trimesh
from shapely.geometry import Polygon, box

L, A, H = 8.0, 6.0, 2.7        # largo (X), ancho (Y), altura de muro (Z), en metros
T = 0.15                        # espesor de muro
TL = 0.20                       # espesor de losa
CUM = 1.6                       # cumbrera sobre el muro
TT = 0.12                       # espesor del faldon


def placa(poly2d, esp, M):
    """Extruye un poligono (u,v) un espesor en w y lo lleva al mundo con M."""
    m = trimesh.creation.extrude_polygon(poly2d, esp)
    m.apply_transform(M)
    return m


def marco(*cols):
    """Matriz 4x4 a partir de las imagenes de u,v,w y el origen."""
    M = np.eye(4)
    for i, c in enumerate(cols[:3]):
        M[:3, i] = c
    M[:3, 3] = cols[3]
    return M


def con_vanos(largo, alto, vanos):
    p = box(0, 0, largo, alto)
    for x, y, w, h in vanos:
        p = p.difference(box(x, y, x + w, y + h))
    return p


X, Y, Z = [1, 0, 0], [0, 1, 0], [0, 0, 1]
cuerpos = {}

cuerpos['losa'] = placa(box(0, 0, L, A), TL, marco(X, Y, Z, [0, 0, -TL]))

cuerpos['muro_frente'] = placa(
    con_vanos(L, H, [(3.2, 0.0, 0.95, 2.10), (1.0, 0.95, 1.40, 1.10)]), T,
    marco(X, Z, Y, [0, 0, 0]))
cuerpos['muro_atras'] = placa(
    con_vanos(L, H, [(1.4, 0.95, 1.20, 1.10), (5.0, 0.95, 1.20, 1.10)]), T,
    marco(X, Z, Y, [0, A - T, 0]))
cuerpos['muro_izq'] = placa(
    con_vanos(A - 2 * T, H, [(2.0, 0.95, 1.30, 1.10)]), T,
    marco(Y, Z, X, [0, T, 0]))
cuerpos['muro_der'] = placa(
    con_vanos(A - 2 * T, H, []), T,
    marco(Y, Z, X, [L - T, T, 0]))

# frontones triangulares (planos x = const)
tri = Polygon([(0, 0), (A, 0), (A / 2, CUM)])
cuerpos['fronton_izq'] = placa(tri, T, marco(Y, Z, X, [0, 0, H]))
cuerpos['fronton_der'] = placa(tri, T, marco(Y, Z, X, [L - T, 0, H]))

# faldones del techo
pend = np.arctan2(CUM, A / 2.0)
largo_faldon = float(np.hypot(CUM, A / 2.0))
for nombre, signo, y0 in (('techo_a', +1, 0.0), ('techo_b', -1, A)):
    dir_v = [0, signo * np.cos(pend), np.sin(pend)]          # sube hacia la cumbrera
    nrm = [0, -signo * np.sin(pend), np.cos(pend)]
    cuerpos[nombre] = placa(box(0, 0, L, largo_faldon), TT,
                            marco(X, dir_v, nrm, [0, y0, H]))

casa = trimesh.util.concatenate(list(cuerpos.values()))
os.makedirs('out', exist_ok=True)
casa.export('out/casa_prueba.stl'); casa.export('out/casa_prueba.obj')
print('casa %.2f x %.2f x %.2f m | %d cuerpos | %d caras'
      % (*casa.extents, len(cuerpos), len(casa.faces)))
for k, v in cuerpos.items():
    b = v.bounds
    print('  %-12s  %5.2f x %5.2f x %5.2f m   z %.2f..%.2f'
          % (k, *(b[1] - b[0]), b[0][2], b[1][2]))
