# -*- coding: utf-8 -*-
"""Auditoria dura del modo terreno: piezas dentro de la hoja, sin encimarse y
sin que ninguna se haya quedado fuera por no caber.

    python3 verificar.py                        # el terreno de prueba
    python3 verificar.py terreno.stl --escala 100 --espesor 3 --hoja 500x700
"""
import argparse
import sys

import trimesh
from shapely.geometry import box
from shapely.strtree import STRtree

from despiece import (Config, solidificar, rebanar, armar_piezas, acomodar,
                      partir_grandes, cargar_modelo)

ap = argparse.ArgumentParser()
ap.add_argument('modelo', nargs='?', default='out/terreno_prueba.stl')
ap.add_argument('--escala', type=float, default=500)
ap.add_argument('--espesor', type=float, default=3.0)
ap.add_argument('--kerf', type=float, default=0.15)
ap.add_argument('--hoja', default='500x700')
ap.add_argument('--unidades', default='m', choices=['m', 'cm', 'mm'])
ap.add_argument('--solido', action='store_true', help='no vaciar interiores')
ap.add_argument('--tolerancia', type=float, default=0.01,
                help='mm2 de traslape que se dejan pasar')
a = ap.parse_args()

w, h = [float(v) for v in a.hoja.lower().split('x')]
cfg = Config(a.escala, a.espesor, a.kerf, (w, h), unidades_modelo=a.unidades,
             vaciar=not a.solido)

m = cargar_modelo(a.modelo)
if not m.is_watertight:
    m = solidificar(m)
capas = rebanar(m, cfg)
if not capas:
    sys.exit('no salieron capas: revisa unidades/escala/espesor')
piezas = armar_piezas(capas, vaciar=cfg.vaciar)
n_antes = len(piezas)
piezas, partidas = partir_grandes(piezas, cfg)
hojas, grandes = acomodar(piezas, cfg)

fallas = 0
marco = box(0, 0, cfg.hoja[0], cfg.hoja[1])
for h_i, colocadas in enumerate(hojas):
    geos = [(c['pieza']['id'], c['geo']) for c in colocadas]
    for pid, g in geos:
        if not marco.contains(g):
            print('FUERA DE HOJA  h%d %s  bounds=%s'
                  % (h_i + 1, pid, [round(v, 1) for v in g.bounds]))
            fallas += 1
    # el grabado tiene que caer sobre material: si la huella se sale de su pieza,
    # el laser marca la cama; si se sale de la hoja, marca la mesa
    for c in colocadas:
        gu = c.get('guia')
        if gu is None or gu.is_empty:
            continue
        pid = c['pieza']['id']
        if not marco.contains(gu):
            print('GRABADO FUERA DE HOJA  h%d %s' % (h_i + 1, pid))
            fallas += 1
        elif not c['geo'].buffer(1e-6).contains(gu):
            print('GRABADO FUERA DE PIEZA h%d %s  sobra %.1f mm2'
                  % (h_i + 1, pid, gu.difference(c['geo']).area))
            fallas += 1

    # el par a par es O(n^2) y una hoja real trae cientos de piezas
    arbol = STRtree([g for _, g in geos])
    for i, (pid, g) in enumerate(geos):
        for j in arbol.query(g):
            j = int(j)
            if j <= i:
                continue
            inter = g.intersection(geos[j][1])
            if inter.area > a.tolerancia:
                print('ENCIMADAS      h%d %s x %s  area=%.2f mm2'
                      % (h_i + 1, pid, geos[j][0], inter.area))
                fallas += 1

# una pieza que no cabe se quedaba fuera sin decir agua va: la maqueta salia
# sin base y el alumno se enteraba hasta el pegado
if grandes:
    print('SE QUEDAN FUERA (no caben ni partidas): %s' % ', '.join(grandes))
    fallas += len(grandes)

colocadas = sum(len(x) for x in hojas)
if colocadas != len(piezas):
    print('SE PERDIERON PIEZAS: %d armadas, %d colocadas' % (len(piezas), colocadas))
    fallas += 1

print('---')
if partidas:
    print('partidas por no caber: %d piezas -> %d trozos'
          % (len(partidas), sum(n for _, n in partidas)))
print('piezas %d (de %d capas) | hojas %d | colocadas %d | fallas %d'
      % (len(piezas), len(capas), len(hojas), colocadas, fallas))
sys.exit(1 if fallas else 0)
