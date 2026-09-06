# -*- coding: utf-8 -*-
"""Vistas isometricas del modelo ya despiezado: armado y explotado.

Sirve para que el alumno sepa que pieza es cual antes de pegar nada.
Se dibuja con pintor (de atras hacia adelante), sin librerias 3D.
"""
import numpy as np

COS30, SIN30 = np.cos(np.pi / 6), np.sin(np.pi / 6)
COLOR = {'muro': ('#fde7ec', '#e11d48'),
         'losa': ('#e7effd', '#2563eb'),
         'techo': ('#fdf3e7', '#b45309')}


def _proyectar(P):
    """3D -> 2D isometrico. Y crece hacia arriba en el dibujo."""
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    return np.column_stack([(x - y) * COS30, (x + y) * SIN30 + z])


def _puntos_3d(placa, desplazar=0.0):
    """Contorno de la placa en 3D (opcionalmente separado sobre su normal)."""
    salida = []
    g = placa['poly']
    partes = g.geoms if g.geom_type.startswith('Multi') else [g]
    for p in partes:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        for anillo in [p.exterior] + list(p.interiors):
            c = np.array(anillo.coords)
            L = np.column_stack([c[:, 0], c[:, 1], np.zeros(len(c)), np.ones(len(c))])
            W = (placa['a_mundo'] @ L.T).T[:, :3]
            if desplazar:
                W = W + placa['normal'] * desplazar
            salida.append(W)
    return salida


def _visible(p, anillos):
    """En la vista armada solo se etiqueta lo que mira a la camara."""
    return float(np.dot(p['normal'], [0.577, 0.577, 0.577])) > 0.05 or \
           float(np.dot(p['normal'], [-0.577, -0.577, -0.577])) > 0.05


def vista(placas, ancho=760, explotar=0.0, etiquetas=True, titulo=''):
    """SVG isometrico. `explotar` separa cada placa sobre su normal (en unidades del modelo)."""
    cuerpos = []
    for p in placas:
        d = explotar * (1.0 if float(np.dot(p['normal'], [0.4, 0.5, 0.77])) >= 0 else -1.0)
        anillos = _puntos_3d(p, d)
        if not anillos:
            continue
        centro = np.mean(np.vstack(anillos), axis=0)
        prof = float(np.dot(centro, [-1.0, -1.0, -1.0]))      # de atras hacia adelante
        # el peso de z va completo: si se subestima, el techo se dibuja antes
        # que los muros y la casa se ve por dentro
        cuerpos.append((prof, p, anillos))
    if not cuerpos:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
    cuerpos.sort(key=lambda c: c[0], reverse=True)

    todos = np.vstack([a for _, _, ans in cuerpos for a in ans])
    P = _proyectar(todos)
    minx, miny = P.min(axis=0)
    maxx, maxy = P.max(axis=0)
    esc = (ancho - 40) / max(maxx - minx, 1e-9)
    alto = int((maxy - miny) * esc + 40)

    def tx(W):
        q = _proyectar(W)
        return [((v[0] - minx) * esc + 20, alto - 20 - (v[1] - miny) * esc) for v in q]

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="100%%">'
           % (ancho, alto)]
    for _, p, anillos in cuerpos:
        relleno, borde = COLOR.get(p['tipo'], ('#eee', '#666'))
        d = []
        for W in anillos:
            pts = tx(W)
            d.append('M ' + ' L '.join('%.1f %.1f' % xy for xy in pts) + ' Z')
        out.append('<path d="%s" fill="%s" fill-rule="evenodd" stroke="%s" '
                   'stroke-width="1.1" stroke-linejoin="round" fill-opacity="%s"/>'
                   % (' '.join(d), relleno, borde, '0.55' if explotar else '1'))
        if etiquetas and (explotar or _visible(p, anillos)):
            c = np.mean(np.vstack(anillos), axis=0).reshape(1, 3)
            x, y = tx(c)[0]
            out.append('<text x="%.1f" y="%.1f" font-family="Helvetica,Arial" font-size="12" '
                       'font-weight="600" fill="#111" text-anchor="middle" '
                       'dominant-baseline="central" paint-order="stroke" stroke="#fff" '
                       'stroke-width="3">%s</text>' % (x, y, p['id']))
    if titulo:
        out.append('<text x="14" y="18" font-family="Helvetica,Arial" font-size="12" '
                   'fill="#888">%s</text>' % titulo)
    out.append('</svg>')
    return '\n'.join(out)
