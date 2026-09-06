# -*- coding: utf-8 -*-
"""CLI del modo estructural: modelo 3D -> muros, losas y techos cortables."""
import argparse, os, sys, time
import trimesh
from despiece import Config, acomodar, partir_grandes, cargar_modelo, ROTACIONES_ORTO
from estructura import despiece_estructural
from isometrica import vista
import exportar

ap = argparse.ArgumentParser()
ap.add_argument('modelo')
ap.add_argument('--escala', type=float, default=100)
ap.add_argument('--espesor', type=float, default=2.0)
ap.add_argument('--kerf', type=float, default=0.15)
ap.add_argument('--hoja', default='600x900')
ap.add_argument('--unidades', default='m')
ap.add_argument('--salida', default='out_casa')
ap.add_argument('--sin-uniones', action='store_true')
a = ap.parse_args()
w, h = [float(v) for v in a.hoja.lower().split('x')]
cfg = Config(a.escala, a.espesor, a.kerf, (w, h), unidades_modelo=a.unidades)

t0 = time.time()
m = cargar_modelo(a.modelo)
piezas, info = despiece_estructural(m, cfg, con_uniones=not a.sin_uniones)
if 'error' in info:
    sys.exit(info['error'])

piezas, partidas = partir_grandes(piezas, cfg, rotaciones=ROTACIONES_ORTO)
hojas, grandes = acomodar(piezas, cfg, rotaciones=ROTACIONES_ORTO)
os.makedirs(a.salida, exist_ok=True)
nombre = os.path.splitext(os.path.basename(a.modelo))[0]
svgs, area = [], 0.0
for i, col in enumerate(hojas):
    tit = '%s  hoja %d/%d  1:%d  lamina %.1fmm' % (nombre, i + 1, len(hojas),
                                                   int(a.escala), a.espesor)
    exportar.hoja_a_dxf(col, cfg, os.path.join(a.salida, '%s_hoja%02d.dxf' % (nombre, i + 1)), tit)
    svg = exportar.hoja_a_svg(col, cfg, tit); svgs.append(svg)
    open(os.path.join(a.salida, '%s_hoja%02d.svg' % (nombre, i + 1)), 'w').write(svg)
    area += sum(c['geo'].area for c in col)

for p in piezas:
    p.setdefault('hoja', 0)
stats = {'n_piezas': len(piezas), 'n_hojas': len(hojas), 'material_cm2': area / 100.0}
iso_a = vista(info['placas'])
iso_e = vista(info['placas'], explotar=1.4 * (cfg.espesor_mm / cfg.a_mm) * 7)
html = exportar.guia_estructural(hojas, piezas, cfg, svgs, nombre, grandes, stats, info,
                                 iso_a, iso_e)
open(os.path.join(a.salida, '%s_guia.html' % nombre), 'w').write(html)

print('placas %d %s | uniones %d | recortes %d | hojas %d | %.1fs'
      % (info['n_placas'], info['por_tipo'], info['n_uniones'], info['n_recortes'],
         len(hojas), time.time() - t0))
for r, e_, lg, d, modo in info['contactos']:
    print('   %-3s + %-3s  %5.2f m  %s x%d' % (r, e_, lg, modo, d))
for pid, n in partidas:
    print('   pieza %s no cabia en la hoja: va partida en %d, se pegan a tope' % (pid, n))
for av in info.get('avisos', []):
    print('   aviso: %s' % av)
if grandes:
    print('   NO CABEN: %s' % ', '.join(grandes))
print('-> %s/%s_guia.html' % (a.salida, nombre))
