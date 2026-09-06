# -*- coding: utf-8 -*-
"""Despiece estructural completo: modelo 3D -> placas con uniones -> piezas en mm."""
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon
import shapely.affinity as aff

from placas import extraer_placas, nombrar
from uniones import detectar_contactos, aplicar_uniones, recortar_choques

DIENTE_OBJ_MM = 12.0      # ancho buscado del diente, en mm de maqueta
HOLGURA_MM = 0.06         # juego de la ranura


def despiece_estructural(mesh, cfg, con_uniones=True):
    """Devuelve (piezas_mm, info). Las piezas traen 'poly' en mm de maqueta."""
    placas, descartados = extraer_placas(mesh)
    if not placas:
        return [], {'error': 'no se encontraron muros ni losas. '
                             '¿El modelo trae cuerpos con espesor?',
                    'descartados': descartados}
    nombrar(placas)

    # espesor del carton llevado a unidades del modelo
    t_mod = cfg.espesor_mm / cfg.a_mm
    contactos, n_uniones = [], 0
    n_recortes, avisos_recorte = 0, []
    if con_uniones:
        contactos = detectar_contactos(placas, t_mod)
        n_uniones = aplicar_uniones(placas, contactos, t_mod,
                                    diente_obj=DIENTE_OBJ_MM / cfg.a_mm,
                                    holgura_modelo=HOLGURA_MM / cfg.a_mm)
        n_recortes, avisos_recorte = recortar_choques(placas, contactos, t_mod)

    piezas = []
    for p in placas:
        g = aff.scale(p['poly'], cfg.a_mm, cfg.a_mm, origin=(0, 0))
        minx, miny, _, _ = g.bounds
        g = aff.translate(g, -minx, -miny)
        marca = p.get('marcas')
        if marca is not None and not marca.is_empty:
            marca = aff.translate(aff.scale(marca, cfg.a_mm, cfg.a_mm, origin=(0, 0)),
                                  -minx, -miny)
        else:
            marca = None
        if cfg.kerf_mm:
            g = g.buffer(cfg.kerf_mm / 2.0, join_style=2)
            if g.geom_type == 'MultiPolygon':
                g = max(g.geoms, key=lambda x: x.area)
        piezas.append({
            'id': p['id'],
            'tipo': p['tipo'],
            'poly': g,
            'guia': marca,
            'z_real': p['z_min'],
            'espesor_real_cm': p['espesor_real'] * 100,
            'vanos': p['vanos'],
            'dientes': p.get('n_dientes', 0),
            'ranuras': p.get('n_ranuras', 0),
        })

    info = {
        'n_placas': len(placas),
        'n_uniones': n_uniones,
        'n_recortes': n_recortes,
        'avisos': avisos_recorte,
        'descartados': descartados,
        'por_tipo': {t: sum(1 for p in placas if p['tipo'] == t)
                     for t in ('muro', 'losa', 'techo')},
        'sin_union': [p['id'] for p in placas
                      if p.get('n_dientes', 0) == 0 and p.get('n_ranuras', 0) == 0],
        'placas': placas,
        'contactos': [(placas[c['ranura']]['id'], placas[c['espiga']]['id'],
                       round(c['largo'], 2), c.get('dientes', 0),
                       c.get('modo','?')) for c in contactos],
        'modos': {m: sum(1 for c in contactos if c.get('modo') == m) for m in ('ranura','dedos')},
    }
    return piezas, info
