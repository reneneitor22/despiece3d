# -*- coding: utf-8 -*-
"""Auditoria dura: piezas dentro de la hoja y sin encimarse."""
import sys, trimesh
from despiece import Config, solidificar, rebanar, armar_piezas, acomodar
from shapely.geometry import box

cfg = Config(escala=500, espesor_mm=3.0, hoja=(500, 700))
m = solidificar(trimesh.load('out/terreno_prueba.stl', force='mesh'))
piezas = armar_piezas(rebanar(m, cfg))
hojas, grandes = acomodar(piezas, cfg)

fallas = 0
for h, colocadas in enumerate(hojas):
    marco = box(0, 0, cfg.hoja[0], cfg.hoja[1])
    geos = [(c['pieza']['id'], c['geo']) for c in colocadas]
    for pid, g in geos:
        if not marco.contains(g):
            print('FUERA DE HOJA  h%d %s  bounds=%s' % (h+1, pid, [round(v,1) for v in g.bounds])); fallas += 1
    for i in range(len(geos)):
        for j in range(i+1, len(geos)):
            inter = geos[i][1].intersection(geos[j][1])
            if inter.area > 0.01:
                print('ENCIMADAS      h%d %s x %s  area=%.2f mm2'
                      % (h+1, geos[i][0], geos[j][0], inter.area)); fallas += 1
print('---')
print('hojas=%d piezas=%d fallas=%d' % (len(hojas), sum(len(x) for x in hojas), fallas))
sys.exit(1 if fallas else 0)
