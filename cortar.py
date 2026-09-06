# -*- coding: utf-8 -*-
"""CLI: python3 cortar.py modelo.stl --escala 200 --espesor 3 --hoja 500x700"""
import argparse, os, sys, time
import trimesh
from despiece import Config, solidificar, rebanar, armar_piezas, acomodar
import exportar


def correr(ruta, cfg, salida, nombre=None):
    t0 = time.time()
    m = trimesh.load(ruta, force='mesh')
    if m.is_empty:
        sys.exit('modelo vacio o formato no leido: ' + ruta)
    ext = m.extents
    print('modelo: %.1f x %.1f x %.1f %s | %d caras | watertight=%s'
          % (ext[0], ext[1], ext[2], cfg.unidades_modelo, len(m.faces), m.is_watertight))

    if not m.is_watertight:
        m = solidificar(m)
        print('  -> solidificado (faldon + fondo), watertight=%s' % m.is_watertight)

    capas = rebanar(m, cfg)
    if not capas:
        sys.exit('no salieron capas: revisa unidades/escala/espesor')
    piezas = armar_piezas(capas, vaciar=cfg.vaciar)
    hojas, grandes = acomodar(piezas, cfg)

    os.makedirs(salida, exist_ok=True)
    nombre = nombre or os.path.splitext(os.path.basename(ruta))[0]
    svgs, area_usada = [], 0.0
    for i, colocadas in enumerate(hojas):
        titulo = '%s  hoja %d/%d  1:%d  lamina %.1fmm' % (nombre, i + 1, len(hojas),
                                                          int(cfg.escala), cfg.espesor_mm)
        exportar.hoja_a_dxf(colocadas, cfg, os.path.join(salida, '%s_hoja%02d.dxf' % (nombre, i + 1)), titulo)
        svg = exportar.hoja_a_svg(colocadas, cfg, titulo)
        svgs.append(svg)
        open(os.path.join(salida, '%s_hoja%02d.svg' % (nombre, i + 1)), 'w').write(svg)
        for col in colocadas:
            area_usada += col['geo'].area

    for pz in piezas:
        pz['z_real_m'] = pz['z_real'] * {'m': 1.0, 'cm': 0.01, 'mm': 0.001}[cfg.unidades_modelo]
        pz.setdefault('hoja', 0)

    area_hojas = cfg.hoja[0] * cfg.hoja[1] * max(1, len(hojas))
    stats = {'n_piezas': len(piezas), 'n_hojas': len(hojas),
             'alto_mm': len(capas) * cfg.espesor_mm,
             'aprov': 100.0 * area_usada / area_hojas,
             'material_cm2': area_usada / 100.0}

    html = exportar.guia_html(hojas, sorted(piezas, key=lambda p: p['id']), cfg, svgs,
                              nombre, grandes, stats)
    ruta_html = os.path.join(salida, '%s_guia.html' % nombre)
    open(ruta_html, 'w').write(html)

    print('capas %d | piezas %d | hojas %d | alto maqueta %.0f mm | aprovechamiento %.0f%%'
          % (len(capas), len(piezas), len(hojas), stats['alto_mm'], stats['aprov']))
    if grandes:
        print('  OJO: no caben en hoja -> %s' % ', '.join(grandes))
    print('%.1fs -> %s' % (time.time() - t0, ruta_html))
    return ruta_html


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('modelo')
    ap.add_argument('--escala', type=float, default=200)
    ap.add_argument('--espesor', type=float, default=3.0)
    ap.add_argument('--kerf', type=float, default=0.15)
    ap.add_argument('--hoja', default='500x700')
    ap.add_argument('--unidades', default='m', choices=['m', 'cm', 'mm'])
    ap.add_argument('--salida', default='out')
    ap.add_argument('--solido', action='store_true', help='no vaciar interiores')
    a = ap.parse_args()
    w, h = [float(v) for v in a.hoja.lower().split('x')]
    correr(a.modelo, Config(a.escala, a.espesor, a.kerf, (w, h), unidades_modelo=a.unidades, vaciar=not a.solido), a.salida)
