# -*- coding: utf-8 -*-
"""Despiece estructural completo: modelo 3D -> placas con uniones -> piezas en mm."""
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon
import shapely.affinity as aff

from placas import (extraer_placas, nombrar, marcar_envolvente,
                    niveles_de_piso, cortar_por_piso)
from uniones import detectar_contactos, aplicar_uniones, recortar_choques

DIENTE_OBJ_MM = 12.0      # ancho buscado del diente, en mm de maqueta
HOLGURA_MM = 0.06         # juego de la ranura
MIN_LADO_MM = 2.0         # mm de maqueta: mas angosto que esto no se corta ni se pega
MIN_AREA_MM2 = 20.0       # mm2 de maqueta: menos que esto es confeti
MAX_PLACAS = 400          # arriba de esto ya no es maqueta escolar


def _cortable(placa, cfg):
    """True si la placa, llevada a la escala pedida, se puede cortar de verdad."""
    g = placa['poly']
    if g.is_empty:
        return False
    minx, miny, maxx, maxy = g.bounds
    lado = min(maxx - minx, maxy - miny) * cfg.a_mm
    return lado >= MIN_LADO_MM and g.area * cfg.a_mm * cfg.a_mm >= MIN_AREA_MM2


def despiece_estructural(mesh, cfg, con_uniones=True, solo_envolvente=False,
                         piso=None):
    """Devuelve (piezas_mm, info). Las piezas traen 'poly' en mm de maqueta."""
    # el espesor del carton llevado a unidades del modelo: lo necesita el camino
    # de superficies para saber que dos caras ya no caben separadas
    placas, descartados = extraer_placas(mesh, t_modelo=cfg.espesor_mm / cfg.a_mm)

    # Lo que manda no es el tamano en el modelo, es el de la MAQUETA: una placa
    # de 1 m2 es una pieza de 10x10 mm a 1:100 y de 2x2 mm a 1:500. Abajo de
    # MIN_LADO_MM no hay tijera ni dedos que la corten y peguen.
    antes = len(placas)
    placas = [p for p in placas if _cortable(p, cfg)]
    incortables = antes - len(placas)

    # Un piso a la vez: la maqueta se arma planta por planta y cada muro recibe
    # un punado de ranuras en vez de todas las del edificio.
    niveles, aviso_piso = niveles_de_piso(placas), ''
    if piso is not None and placas:
        placas, niveles, aviso_piso = cortar_por_piso(placas, piso)
        if aviso_piso and not placas:
            return [], {'error': aviso_piso, 'descartados': descartados,
                        'niveles': niveles}

    # Un edificio de cinco pisos trae losas de entrepiso y muros interiores que
    # el alumno casi nunca quiere: pidiendo solo la envolvente se queda la caja.
    n_dentro = 0
    if solo_envolvente and placas:
        marcar_envolvente(placas, mesh)
        n_dentro = sum(1 for p in placas if not p.get('exterior'))
        placas = [p for p in placas if p.get('exterior')]

    # Un distrito urbano entero da miles de placas. No es un error del modelo:
    # es que no cabe en una maqueta escolar. Se cortan las grandes y se dice
    # cuantas quedaron fuera, en vez de rendirse y no entregar nada.
    fuera_por_tope = []
    if len(placas) > MAX_PLACAS:
        placas.sort(key=lambda p: -p['area'])
        fuera_por_tope = placas[MAX_PLACAS:]
        placas = placas[:MAX_PLACAS]

    if not placas:
        return [], {'error': 'no se encontraron muros ni losas. '
                             '¿El modelo trae cuerpos con espesor?',
                    'descartados': descartados}
    nombrar(placas)

    # espesor del carton llevado a unidades del modelo
    t_mod = cfg.espesor_mm / cfg.a_mm
    contactos, n_uniones = [], 0
    n_recortes, avisos_recorte = 0, []
    avisos_previos = []
    if piso is not None:
        avisos_previos.append('cortado el piso %d de %d (losas a %s m)'
                              % (piso, len(niveles),
                                 ', '.join('%.1f' % z for z in niveles)))
    if aviso_piso:
        avisos_previos.append(aviso_piso)
    if n_dentro:
        avisos_previos.append('%d placas eran de adentro (entrepisos y muros '
                              'interiores) y se dejaron fuera' % n_dentro)
    if incortables:
        avisos_previos.append('%d placas quedan mas chicas que %.0f mm a 1:%d y no se '
                              'pueden cortar: no van en las hojas'
                              % (incortables, MIN_LADO_MM, int(cfg.escala)))
    if fuera_por_tope:
        avisos_previos.append('el modelo da %d placas cortables; se cortan las %d mas '
                              'grandes y quedan %d fuera. Sube la escala o exporta solo '
                              'muros, losas y techos'
                              % (len(placas) + len(fuera_por_tope), MAX_PLACAS,
                                 len(fuera_por_tope)))
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
        'incortables': incortables,
        'niveles': niveles,
        'fuera_por_tope': [p.get('id', '?') for p in fuera_por_tope],
        'n_uniones': n_uniones,
        'n_recortes': n_recortes,
        'avisos': avisos_previos + avisos_recorte,
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
