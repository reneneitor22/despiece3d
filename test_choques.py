# -*- coding: utf-8 -*-
"""Choques entre piezas que la prueba de ensamble encontraba (16 sep 2026).

Cada caso arma placas a mano, corre uniones + recortes igual que el despiece y
mide por muestreo que ya no ocupen el mismo volumen.

    python -m unittest test_choques
"""
import unittest

import numpy as np
import shapely
from shapely.geometry import box

import uniones as U

T = 0.2                      # carton de 2 mm a 1:100, en metros de modelo
A_MM = 10.0

X, Y, Z = np.eye(3)


def placa(pid, poly, u, v, origen, tipo='muro'):
    F = np.eye(4)
    F[:3, 0], F[:3, 1], F[:3, 2] = u, v, np.cross(u, v)
    F[:3, 3] = origen
    return {'id': pid, 'tipo': tipo, 'poly': poly, 'a_mundo': F, 'normal': F[:3, 2].copy(),
            'centro': np.array(origen, float), 'espesor_real': 0.0, 'area': poly.area,
            'z_min': 0.0, 'vanos': 0}


def losa(pid, poly, z, dx=0.0, dy=0.0):
    return placa(pid, poly, X, Y, [dx, dy, z], 'losa')


def muro_xz(pid, poly, y, dx=0.0):
    """Muro en el plano y = cte: u es x, v es z."""
    return placa(pid, poly, X, Z, [dx, y, 0.0])


def despiezar(placas):
    cont = U.detectar_contactos(placas, T)
    U.aplicar_uniones(placas, cont, T, 12.0 / A_MM, 0.06 / A_MM)
    U.recortar_choques(placas, cont, T)
    return cont


def encimado(a, b, paso=T / 8):
    """Volumen (m3 de modelo) que ocupan las dos placas a la vez."""
    def caja(p):
        x0, y0, x1, y1 = p['poly'].bounds
        L = np.array([[x, y, s, 1] for x in (x0, x1) for y in (y0, y1) for s in (-T / 2, T / 2)])
        W = (p['a_mundo'] @ L.T).T[:, :3]
        return W.min(0), W.max(0)
    (la, ha), (lb, hb) = caja(a), caja(b)
    lo, hi = np.maximum(la, lb), np.minimum(ha, hb)
    if np.any(hi <= lo):
        return 0.0
    G = np.stack(np.meshgrid(*[np.arange(lo[k], hi[k] + paso, paso) for k in range(3)],
                             indexing='ij'), -1).reshape(-1, 3)
    dentro = np.ones(len(G), bool)
    roce = T * 0.05
    for p in (a, b):
        L = (np.linalg.inv(p['a_mundo']) @ np.hstack([G, np.ones((len(G), 1))]).T).T
        g = p['poly'].buffer(-roce)
        dentro &= (np.abs(L[:, 2]) <= T / 2 - roce) & shapely.contains_xy(g, L[:, 0], L[:, 1])
    return float(dentro.sum()) * paso ** 3


class Choques(unittest.TestCase):
    def test_contacto_lejos_del_origen(self):
        # _tramo centraba su segmento en el punto de la recta mas cercano al
        # ORIGEN: un muro chico a 60 m de ahi no tocaba a nadie
        for lejos in (0.0, 60.0):
            L = losa('L', box(0, 0, 4, 4), 0.0, dx=lejos)
            M = muro_xz('M', box(0.5, 0, 3.5, 2), 2.0, dx=lejos)
            self.assertEqual(len(U.detectar_contactos([L, M], T)), 1, 'a %.0f m' % lejos)

    def test_muro_que_atraviesa_la_losa(self):
        # muro de dos pisos que pasa por en medio de la losa: ni dientes ni
        # dedos (le borraban medio muro); la losa recibe una ranura pasante
        L = losa('L', box(0, 0, 6, 6), 1.5)
        M = muro_xz('M', box(1, 0, 5, 3), 3.0)
        despiezar([L, M])
        self.assertLess(encimado(L, M), 1e-6)
        self.assertGreater(M['poly'].area, 0.95 * 12.0)
        self.assertGreater(L['poly'].area, 0.95 * 36.0)

    def test_remate_angosto_sobre_el_muro(self):
        # el muro asoma 4 cm arriba del remate y el remate 13 cm del otro lado:
        # antes contaban las dos como "atraviesan" y no se recortaba ninguna
        R = losa('R', box(0, 2.0 - 0.22, 12, 2.0 + 0.23), 3.0)
        M = muro_xz('M', box(0, 0, 12, 3.14), 2.0)
        despiezar([R, M])
        self.assertLess(encimado(R, M), 1e-6)
        self.assertGreater(R['poly'].area, 0.95 * 12 * 0.45)

    def test_cruce_de_orilla_a_orilla(self):
        # se cruzan de lado a lado: media ranura en cada una, abiertas a su orilla
        L = losa('L', box(0, 0, 4, 4), 1.0)
        M = muro_xz('M', box(0, 0, 4, 2), 2.0)
        U.recortar_choques([L, M], [], T)
        self.assertLess(encimado(L, M), 1e-6)
        for p in (L, M):
            self.assertEqual(p['poly'].geom_type, 'Polygon')
            self.assertEqual(len(p['poly'].interiors), 0, '%s con hueco cerrado' % p['id'])

    def test_paralelas_encimadas(self):
        # losa inclinada 3 grados que atraviesa el plano de la plana
        a = np.radians(3)
        P = losa('P', box(0, 0, 10, 10), 0.0)
        I = placa('I', box(0, 0, 8, 4), np.array([np.cos(a), 0, -np.sin(a)]), Y, [1, 3, 0.1], 'losa')
        U.recortar_choques([P, I], [], T)
        self.assertLess(encimado(P, I), 1e-6)
        self.assertGreater(I['poly'].area, 0.99 * 32.0)


if __name__ == '__main__':
    unittest.main()
