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
           tope_voxeles=25e6, tolerancia=0.005):
    cfg = Config(escala, carton_mm, 0.0, (600, 900), unidades_modelo=unidades)
    m = cargar_modelo(ruta)
    placas, _ = extraer_placas(m, t_modelo=carton_mm / cfg.a_mm)
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
    ocupados = int((conteo >= 1).sum())
    vol_voxel = paso_mm ** 3
    # Lo que importa no es si hay UN voxel en choque, es cuanto material se
    # estorba. La casa de prueba lleva 0.02% desde siempre y arma bien; exigir
    # cero era exigir lo imposible en cualquier modelo real.
    frac = (choques / ocupados) if ocupados else 0.0
    print('piezas: %d | voxeles ocupados: %d | en choque: %d (%.1f mm3, %.2f%% del material)'
          % (len(placas), ocupados, choques, choques * vol_voxel, 100 * frac))

    if choques:
        # Cada voxel en choque sabe quien lo ocupa: se recorre UNA vez por placa
        # y se anota. El par a par con conjuntos no aguanta 400 placas.
        malos = np.zeros(len(G), dtype=bool)
        malos[np.nonzero(conteo >= 2)[0]] = True
        duenios = {}
        for pid, v in ocupa.items():
            if len(v) == 0:
                continue
            for k in v[malos[v]].tolist():
                duenios.setdefault(k, []).append(pid)
        pares = {}
        for lista in duenios.values():
            for i in range(len(lista)):
                for j in range(i + 1, len(lista)):
                    k = (lista[i], lista[j]) if lista[i] < lista[j] else (lista[j], lista[i])
                    pares[k] = pares.get(k, 0) + 1
        for (a, b), n in sorted(pares.items(), key=lambda kv: -kv[1])[:12]:
            print('   CHOCAN %-3s x %-3s  %d voxeles (%.1f mm3)' % (a, b, n, n * vol_voxel))

    # piezas partidas por los cortes
    destruidas = [p['id'] for p in placas
                  if p['poly_original'].area > 0
                  and p['poly'].area < p['poly_original'].area * 0.5]
    for pid in destruidas[:12]:
        print('   OJO %s perdio mas de la mitad del area al cortar uniones' % pid)
    if len(destruidas) > 12:
        print('   ... y %d placas mas' % (len(destruidas) - 12))

    veredicto = frac <= tolerancia and not destruidas
    print('%s  interferencia %.2f%% (tope %.2f%%) | placas destruidas %d'
          % ('PASA' if veredicto else 'NO PASA', 100 * frac, 100 * tolerancia,
             len(destruidas)))
    return {'ok': veredicto, 'frac': frac, 'choques': choques,
            'ocupados': ocupados, 'destruidas': destruidas, 'placas': len(placas)}


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('modelo', nargs='?', default='out/casa_prueba.stl')
    ap.add_argument('escala', nargs='?', type=float, default=100.0)
    ap.add_argument('--espesor', type=float, default=2.0)
    ap.add_argument('--unidades', default='m', choices=['m', 'cm', 'mm'])
    ap.add_argument('--paso', type=float, default=0.4, help='mm de maqueta por voxel')
    ap.add_argument('--tope-voxeles', type=float, default=25e6)
    ap.add_argument('--tolerancia', type=float, default=0.005,
                    help='fraccion del material que puede quedar en choque (0.005 = 0.5%)')
    a = ap.parse_args()
    r = probar(a.modelo, escala=a.escala, carton_mm=a.espesor, paso_mm=a.paso,
               unidades=a.unidades, tope_voxeles=a.tope_voxeles, tolerancia=a.tolerancia)
    sys.exit(0 if r['ok'] else 1)
