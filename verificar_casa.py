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

from despiece import Config
from placas import extraer_placas, nombrar
from uniones import detectar_contactos, aplicar_uniones, recortar_choques


def probar(ruta, escala=100.0, carton_mm=2.0, paso_mm=0.4, unidades='m', roce_mm=0.05):
    cfg = Config(escala, carton_mm, 0.0, (600, 900), unidades_modelo=unidades)
    m = trimesh.load(ruta, force='mesh')
    placas, _ = extraer_placas(m)
    nombrar(placas)
    t_mod = carton_mm / cfg.a_mm
    cont = detectar_contactos(placas, t_mod)
    aplicar_uniones(placas, cont, t_mod, 12.0 / cfg.a_mm, 0.06 / cfg.a_mm)
    nr, av = recortar_choques(placas, cont, t_mod)
    print('recortes de choque: %d' % nr)
    [print('   aviso:', a) for a in av]

    paso = paso_mm / cfg.a_mm                      # paso del muestreo en unidades del modelo
    lo, hi = m.bounds[0] - t_mod, m.bounds[1] + t_mod
    ejes = [np.arange(lo[k], hi[k] + paso, paso) for k in range(3)]
    G = np.stack(np.meshgrid(*ejes, indexing='ij'), -1).reshape(-1, 3)
    print('muestreo: %d puntos (%.1f mm de maqueta por voxel)' % (len(G), paso_mm))

    ocupa = {}
    for p in placas:
        inv = np.linalg.inv(p['a_mundo'])
        L = (inv @ np.hstack([G, np.ones((len(G), 1))]).T).T[:, :3]
        dentro_w = np.abs(L[:, 2]) <= t_mod / 2.0 - roce_mm / cfg.a_mm
        idx = np.nonzero(dentro_w)[0]
        if len(idx) == 0:
            ocupa[p['id']] = np.zeros(0, dtype=np.int64); continue
        # se encoge un pelo: dos caras que se BESAN no son un choque
        g = p['poly'].buffer(-roce_mm / cfg.a_mm)
        if g.is_empty:
            ocupa[p['id']] = np.zeros(0, dtype=np.int64); continue
        m2 = shapely.contains_xy(g, L[idx, 0], L[idx, 1])
        ocupa[p['id']] = idx[m2]

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
    r = probar(sys.argv[1] if len(sys.argv) > 1 else 'out/casa_prueba.stl',
               escala=float(sys.argv[2]) if len(sys.argv) > 2 else 100.0)
    sys.exit(1 if r else 0)
