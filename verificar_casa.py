# -*- coding: utf-8 -*-
"""Prueba de ensamble: reconstruye en 3D las piezas ya cortadas (extruidas al
espesor del carton, sobre su plano medio) y busca choques entre ellas.

Si dos piezas ocupan el mismo volumen, la maqueta no cierra: el diente de una
pega contra la otra. Se mide por muestreo de voxeles.
"""
import sys
import numpy as np
import trimesh
import shapely
from shapely.geometry import Polygon

from despiece import Config, cargar_modelo
from placas import extraer_placas, nombrar
from uniones import detectar_contactos, aplicar_uniones, recortar_choques
from estructura import _cortable, MAX_PLACAS


def probar(ruta, escala=100.0, carton_mm=2.0, paso_mm=0.4, unidades='m', roce_mm=0.05,
           tope_voxeles=25e6):
    cfg = Config(escala, carton_mm, 0.0, (600, 900), unidades_modelo=unidades)
    m = cargar_modelo(ruta)
    placas, _ = extraer_placas(m)
    # el mismo filtro que aplica el despiece: si una placa no se corta a esta
    # escala, tampoco tiene por que aparecer en la prueba de ensamble
    placas = [p for p in placas if _cortable(p, cfg)]
    if len(placas) > MAX_PLACAS:
        placas.sort(key=lambda p: -p['area'])
        placas = placas[:MAX_PLACAS]
    nombrar(placas)
    t_mod = carton_mm / cfg.a_mm
    cont = detectar_contactos(placas, t_mod)
    aplicar_uniones(placas, cont, t_mod, 12.0 / cfg.a_mm, 0.06 / cfg.a_mm)
    nr, av = recortar_choques(placas, cont, t_mod)
    print('recortes de choque: %d' % nr)
    [print('   aviso:', a) for a in av]

    lo, hi = m.bounds[0] - t_mod, m.bounds[1] + t_mod
    # Un edificio real a 1:100 son cientos de millones de voxeles a 0.4 mm y la
    # maquina se queda sin memoria. Se afloja el paso hasta caber en el tope,
    # avisando: la medicion pierde resolucion, no validez.
    caja_mm = (hi - lo) * cfg.a_mm
    paso_min = float(np.cbrt(max(caja_mm.prod(), 1.0) / float(tope_voxeles)))
    if paso_min > paso_mm:
        print('OJO: la caja mide %.0f x %.0f x %.0f mm de maqueta; el paso sube de '
              '%.2f a %.2f mm para no pasar de %d voxeles'
              % (caja_mm[0], caja_mm[1], caja_mm[2], paso_mm, paso_min, tope_voxeles))
        paso_mm = paso_min
    roce_mm = min(roce_mm, paso_mm / 2.0)          # el roce no puede comerse el voxel

    paso = paso_mm / cfg.a_mm                      # paso del muestreo en unidades del modelo
    ejes = [np.arange(lo[k], hi[k] + paso, paso) for k in range(3)]
    G = np.stack(np.meshgrid(*ejes, indexing='ij'), -1).reshape(-1, 3)
    print('muestreo: %d puntos (%.2f mm de maqueta por voxel)' % (len(G), paso_mm))

    ocupa = {}
    for p in placas:
        # Antes se transformaban los 25 millones de puntos contra CADA placa. Una
        # placa ocupa una esquina de la caja: primero se recortan los puntos a su
        # caja envolvente en el mundo y solo esos se transforman. En un edificio
        # de 166 placas eso es la diferencia entre nueve minutos y medio.
        b = p['poly'].bounds
        esquinas = np.array([[b[0], b[1], 0.0], [b[2], b[1], 0.0],
                             [b[2], b[3], 0.0], [b[0], b[3], 0.0]])
        holgura = t_mod / 2.0 + paso
        caja = np.hstack([esquinas, np.ones((4, 1))])
        W = (p['a_mundo'] @ caja.T).T[:, :3]
        lo_p, hi_p = W.min(axis=0) - holgura, W.max(axis=0) + holgura
        cerca = np.nonzero(np.all((G >= lo_p) & (G <= hi_p), axis=1))[0]
        if len(cerca) == 0:
            ocupa[p['id']] = np.zeros(0, dtype=np.int64); continue

        inv = np.linalg.inv(p['a_mundo'])
        Gc = G[cerca]
        L = (inv @ np.hstack([Gc, np.ones((len(Gc), 1))]).T).T[:, :3]
        dentro_w = np.abs(L[:, 2]) <= t_mod / 2.0 - roce_mm / cfg.a_mm
        idx = np.nonzero(dentro_w)[0]
        if len(idx) == 0:
            ocupa[p['id']] = np.zeros(0, dtype=np.int64); continue
        # se encoge un pelo: dos caras que se BESAN no son un choque
        g = p['poly'].buffer(-roce_mm / cfg.a_mm)
        if g.is_empty:
            ocupa[p['id']] = np.zeros(0, dtype=np.int64); continue
        m2 = shapely.contains_xy(g, L[idx, 0], L[idx, 1])
        ocupa[p['id']] = cerca[idx[m2]]      # de vuelta al indice global

    conteo = np.zeros(len(G), dtype=np.int16)
    for v in ocupa.values():
        conteo[v] += 1
    choques = int((conteo >= 2).sum())
    vol_voxel = paso_mm ** 3
    print('piezas: %d | voxeles ocupados: %d | en choque: %d (%.1f mm3 de maqueta)'
          % (len(placas), int((conteo >= 1).sum()), choques, choques * vol_voxel))

    if choques:
        ids = list(ocupa)
        pares = {}
        malos = set(np.nonzero(conteo >= 2)[0])
        for i in range(len(ids)):
            si = set(ocupa[ids[i]].tolist()) & malos
            if not si:
                continue
            for j in range(i + 1, len(ids)):
                inter = si & set(ocupa[ids[j]].tolist())
                if inter:
                    pares[(ids[i], ids[j])] = len(inter)
        for (a, b), n in sorted(pares.items(), key=lambda kv: -kv[1])[:12]:
            print('   CHOCAN %-3s x %-3s  %d voxeles (%.1f mm3)' % (a, b, n, n * vol_voxel))

    # piezas partidas por los cortes
    for p in placas:
        if p['poly_original'].area > 0 and p['poly'].area < p['poly_original'].area * 0.5:
            print('   OJO %s perdio mas de la mitad del area al cortar uniones' % p['id'])
    return choques


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('modelo', nargs='?', default='out/casa_prueba.stl')
    ap.add_argument('escala', nargs='?', type=float, default=100.0)
    ap.add_argument('--espesor', type=float, default=2.0)
    ap.add_argument('--unidades', default='m', choices=['m', 'cm', 'mm'])
    ap.add_argument('--paso', type=float, default=0.4, help='mm de maqueta por voxel')
    ap.add_argument('--tope-voxeles', type=float, default=25e6)
    a = ap.parse_args()
    r = probar(a.modelo, escala=a.escala, carton_mm=a.espesor, paso_mm=a.paso,
               unidades=a.unidades, tope_voxeles=a.tope_voxeles)
    sys.exit(1 if r else 0)
