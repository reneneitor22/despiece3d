# -*- coding: utf-8 -*-
"""Rasteriza una hoja a PNG con PIL, para revisar el corte sin depender del navegador."""
from PIL import Image, ImageDraw, ImageFont
import numpy as np


def _anillos(g):
    partes = g.geoms if g.geom_type.startswith('Multi') else [g]
    for p in partes:
        if p.geom_type == 'Polygon' and not p.is_empty:
            yield list(p.exterior.coords), [list(r.coords) for r in p.interiors]


def hoja_png(colocadas, cfg, ruta, px_por_mm=1.6, etiquetas=True):
    W, H = cfg.hoja
    w, h = int(W * px_por_mm), int(H * px_por_mm)
    img = Image.new('RGB', (w, h), '#ffffff')
    dr = ImageDraw.Draw(img)
    try:
        fnt = ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', int(11 * px_por_mm))
    except Exception:
        fnt = ImageFont.load_default()

    def T(x, y):
        return (x * px_por_mm, (H - y) * px_por_mm)

    dr.rectangle([0, 0, w - 1, h - 1], outline='#cccccc')
    for col in colocadas:
        for ext, ints in _anillos(col['geo']):
            dr.polygon([T(x, y) for x, y in ext], fill='#fde7ec', outline='#e11d48')
            for r in ints:
                dr.polygon([T(x, y) for x, y in r], fill='#ffffff', outline='#e11d48')
        if col.get('guia') is not None:
            for ext, _ in _anillos(col['guia']):
                dr.line([T(x, y) for x, y in ext], fill='#2563eb', width=1)
        if etiquetas:
            rp = col['geo'].representative_point()
            x, y = T(rp.x, rp.y)
            t = col['pieza']['id']
            try:
                bb = dr.textbbox((0, 0), t, font=fnt)
                dr.text((x - (bb[2] - bb[0]) / 2, y - (bb[3] - bb[1]) / 2), t, font=fnt, fill='#111')
            except Exception:
                dr.text((x, y), t, fill='#111')
    img.save(ruta)
    return ruta
