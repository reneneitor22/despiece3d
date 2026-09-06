# -*- coding: utf-8 -*-
"""Uniones espiga-ranura entre placas perpendiculares.

En un modelo real los muros casi nunca se cruzan: uno TOPA contra la cara del otro.
Por eso la union correcta es espiga pasada: la placa que termina saca dientes, y la
placa contra la que topa recibe ranuras pasadas del ancho del carton. Se ensambla
sola, queda a escuadra y no depende del pegamento.
"""
import numpy as np
from shapely.geometry import LineString, Polygon, Point, MultiPolygon
from shapely.ops import unary_union

# una placa no puede quedar con menos de esta fraccion de su area por culpa
# de las uniones: abajo de eso ya no es pieza, es encaje de bolillo
MINIMO_AREA = 0.55

TOL_PERP = 0.90        # |nA . nB| menor a esto = se cruzan en angulo util
                       # (0.90 = desde 26 grados; el techo llega al muro a 28)
MIN_CONTACTO = 0.15    # m de modelo: contacto mas corto no se dedea
HOLGURA = 0.06         # mm de maqueta: juego de la ranura para que entre


def _inv(F):
    return np.linalg.inv(F)


def _a_local(F, pts3):
    p = np.hstack([np.asarray(pts3, float).reshape(-1, 3), np.ones((len(pts3), 1))])
    return (_inv(F) @ p.T).T[:, :3]


def _linea_planos(pa, na, pb, nb):
    d = np.cross(na, nb)
    L = np.linalg.norm(d)
    if L < 1e-9:
        return None
    d = d / L
    try:
        p0 = np.linalg.solve(np.array([na, nb, d]),
                             np.array([np.dot(na, pa), np.dot(nb, pb), 0.0]))
    except np.linalg.LinAlgError:
        return None
    return p0, d


def _recta_local(placa, p0, d):
    """La recta 3D vista en el marco 2D de la placa: (origen2, dir2 unitario)."""
    loc = _a_local(placa['a_mundo'], [p0, p0 + d])
    q0, q1 = loc[0][:2], loc[1][:2]
    u = q1 - q0
    n = np.linalg.norm(u)
    if n < 1e-9:
        return None
    return q0, u / n


def _tramo(placa, q0, u, alcance):
    """Rango [t0,t1] del poligono dentro de una franja de ancho 2*alcance sobre la recta."""
    largo = max(placa['poly'].bounds[2] - placa['poly'].bounds[0],
                placa['poly'].bounds[3] - placa['poly'].bounds[1]) * 4 + 10
    recta = LineString([q0 - u * largo, q0 + u * largo])
    reg = recta.buffer(max(alcance, 1e-6), cap_style=2).intersection(placa['poly'])
    if reg.is_empty or reg.area <= 1e-9:
        return None, False
    xs = np.array(reg.bounds).reshape(2, 2)
    esquinas = [np.array([xs[0][0], xs[0][1]]), np.array([xs[1][0], xs[0][1]]),
                np.array([xs[0][0], xs[1][1]]), np.array([xs[1][0], xs[1][1]])]
    ts = [float(np.dot(c - q0, u)) for c in esquinas]
    cruza = recta.intersection(placa['poly']).length > 1e-6
    # acotar al tramo realmente cubierto
    tramo = reg.intersection(LineString([q0 - u * largo, q0 + u * largo]).buffer(alcance * 2, cap_style=2))
    return (min(ts), max(ts)), cruza


def _w_borde(poly, q0, u, perp, t0, t1):
    """Hasta donde llega la placa en direccion `perp`, dentro del tramo [t0,t1].
    Devuelve None si la placa no asoma por ahi."""
    ws = []
    anillos = [poly.exterior] + list(poly.interiors)
    for anillo in anillos[:1]:
        for x, y in anillo.coords:
            p = np.array([x, y])
            t = float(np.dot(p - q0, u))
            if t0 - 1e-6 <= t <= t1 + 1e-6:
                ws.append(float(np.dot(p - q0, perp)))
    return max(ws) if ws else None


def detectar_contactos(placas, t_placa_modelo):
    """t_placa_modelo: espesor del carton llevado a unidades del modelo."""
    cont = []
    for i in range(len(placas)):
        for j in range(len(placas)):
            if i == j:
                continue
            a, b = placas[i], placas[j]      # b es la que podria topar contra a
            if abs(float(np.dot(a['normal'], b['normal']))) > TOL_PERP:
                continue
            lin = _linea_planos(a['centro'], a['normal'], b['centro'], b['normal'])
            if lin is None:
                continue
            p0, d = lin
            ra_ = _recta_local(a, p0, d)
            rb_ = _recta_local(b, p0, d)
            if ra_ is None or rb_ is None:
                continue
            alc_a = max(b['espesor_real'], t_placa_modelo) / 2.0 + 1e-4
            alc_b = max(a['espesor_real'], t_placa_modelo) / 2.0 + 1e-4
            ta, cruza_a = _tramo(a, ra_[0], ra_[1], alc_a)
            tb, cruza_b = _tramo(b, rb_[0], rb_[1], alc_b)
            if not ta or not tb:
                continue
            t0, t1 = max(ta[0], tb[0]), min(ta[1], tb[1])
            if t1 - t0 < MIN_CONTACTO:
                continue
            if cruza_b and not cruza_a:
                continue                      # al reves: se resuelve en el par espejo
            cont.append({'ranura': i, 'espiga': j, 'p0': p0, 'd': d,
                         't0': float(t0), 't1': float(t1), 'largo': float(t1 - t0)})
    # quita duplicados simetricos quedandose con un solo sentido por par
    vistos, limpio = set(), []
    for c in sorted(cont, key=lambda c: -c['largo']):
        k = tuple(sorted((c['ranura'], c['espiga'])))
        if k in vistos:
            continue
        vistos.add(k); limpio.append(c)
    return limpio


def _w_borde_geom(geom, q0, u, perp):
    partes = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
    ws = []
    for p in partes:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        for x, y in p.exterior.coords:
            ws.append(float(np.dot(np.array([x, y]) - q0, perp)))
    return max(ws) if ws else None


def _perp_hacia(placa, q, perp_base, otro_q):
    """Perpendicular en el plano de la placa, apuntando hacia la otra placa."""
    cen = np.array(placa['poly'].centroid.coords[0])
    return perp_base if np.dot(q - cen, perp_base) >= 0 else -perp_base


def _rect(q, u, perp, ta, tb, wa, wb):
    return Polygon([q + u * ta + perp * wa, q + u * tb + perp * wa,
                    q + u * tb + perp * wb, q + u * ta + perp * wb])


def aplicar_uniones(placas, contactos, t_placa_modelo, diente_obj, holgura_modelo):
    """Dos tipos de union, elegidos por geometria:

    RANURA  la placa que topa cae en medio de la receptora -> ranura pasada + espiga.
    DEDOS   se encuentran canto con canto (esquinas, muro parado en el filo de la losa)
            -> dientes alternados: donde una sale, la otra se mete.
    """
    # cada trozo se guarda con el contacto del que salio: si luego hay que
    # cancelar una union, se quita de LAS DOS placas del par
    add = {i: [] for i in range(len(placas))}
    sub = {i: [] for i in range(len(placas))}
    marcas = {i: [] for i in range(len(placas))}
    del_contacto = {}                       # id(trozo) -> indice del contacto
    t = t_placa_modelo
    LEJOS = t * 40 + 1.0
    hechas = 0

    for ic, c in enumerate(contactos):
        pe, pr = placas[c['espiga']], placas[c['ranura']]
        re_, rr_ = _recta_local(pe, c['p0'], c['d']), _recta_local(pr, c['p0'], c['d'])
        if re_ is None or rr_ is None:
            continue
        qe, ue = re_
        qr, ur = rr_
        perp_e = _perp_hacia(pe, qe, np.array([-ue[1], ue[0]]), qr)
        perp_r = _perp_hacia(pr, qr, np.array([-ur[1], ur[0]]), qe)

        w_e = _w_borde(pe['poly'], qe, ue, perp_e, c['t0'], c['t1'])
        w_r = _w_borde(pr['poly'], qr, ur, perp_r, c['t0'], c['t1'])
        if w_e is None or w_r is None:
            continue

        # a un angulo distinto de 90 grados hay que avanzar mas para salir del canto:
        # la distancia en el plano es (t/2) / sen(angulo entre placas)
        cosang = abs(float(np.dot(pe['normal'], pr['normal'])))
        sen = max(float(np.sqrt(max(1.0 - cosang * cosang, 0.0))), 0.30)
        # OJO: la placa no es una superficie, es una losa de espesor t. El material
        # que esta fuera del plano medio alcanza mas lejos, por eso el (1+cos).
        # A 90 grados da t, igual que antes; en una pendiente da bastante mas.
        tt = t * (1.0 + cosang) / sen

        n = max(3, min(11, int(round(c['largo'] / max(diente_obj, 1e-6)))))
        if n % 2 == 0:
            n += 1
        paso = (c['t1'] - c['t0']) / n

        def anota(cubeta, placa_i, geo):
            cubeta[placa_i].append(geo)
            del_contacto[id(geo)] = ic
            return geo

        marcas[c['ranura']].append(_rect(qr, ur, perp_r, c['t0'], c['t1'], -tt / 2, tt / 2))
        marcas[c['espiga']].append(_rect(qe, ue, perp_e, c['t0'], c['t1'], -tt / 2, tt / 2))
        del_contacto[id(marcas[c['ranura']][-1])] = ic
        del_contacto[id(marcas[c['espiga']][-1])] = ic

        # ¿la ranura cabe entera dentro de la receptora?
        h = tt / 2.0 + holgura_modelo
        ranuras = [_rect(qr, ur, perp_r, c['t0'] + k * paso - holgura_modelo,
                         c['t0'] + (k + 1) * paso + holgura_modelo, -h, h)
                   for k in range(0, n, 2)]
        cabe = all(pr['poly'].buffer(1e-9).contains(r) for r in ranuras)

        def crecer(placa, q, u, perp, ta, tb, obj):
            """Alarga la placa hasta `obj` SOLO en esta franja, arrancando del
            borde que tiene ahi. Sin esto el diente se desborda en las esquinas."""
            franja = _rect(q, u, perp, ta, tb, -LEJOS, LEJOS)
            dentro = placa['poly'].intersection(franja)
            if dentro.is_empty or dentro.area <= 0:
                return None
            w_max = _w_borde_geom(dentro, q, u, perp)
            if w_max is None or obj <= w_max + 1e-9:
                return None
            return _rect(q, u, perp, ta, tb, w_max - tt * 0.25, obj)

        if cabe:
            c['modo'] = 'ranura'
            for k in range(n):
                ta, tb = c['t0'] + k * paso, c['t0'] + (k + 1) * paso
                if k % 2 == 0:
                    g = crecer(pe, qe, ue, perp_e, ta, tb, max(tt / 2, w_e))
                    if g is not None:
                        anota(add, c['espiga'], g)
                else:
                    anota(sub, c['espiga'], _rect(qe, ue, perp_e, ta, tb, -tt / 2, LEJOS))
            for r in ranuras:
                anota(sub, c['ranura'], r)
        else:
            c['modo'] = 'dedos'
            for k in range(n):
                ta, tb = c['t0'] + k * paso, c['t0'] + (k + 1) * paso
                # par: sale la que topa y se mete la receptora; impar: al reves
                obj_e = (tt / 2) if k % 2 == 0 else (-tt / 2)
                obj_r = (-tt / 2) if k % 2 == 0 else (tt / 2)
                for placa, placa_i, q, u, perp, obj in (
                        (pe, c['espiga'], qe, ue, perp_e, obj_e),
                        (pr, c['ranura'], qr, ur, perp_r, obj_r)):
                    g = crecer(placa, q, u, perp, ta, tb, obj)
                    if g is not None:
                        anota(add, placa_i, g)
                    else:
                        anota(sub, placa_i, _rect(q, u, perp, ta, tb, obj, LEJOS))
        c['dientes'] = (n + 1) // 2
        hechas += 1

    canceladas = _cuidar_placas(placas, contactos, add, sub, del_contacto)

    for i, p in enumerate(placas):
        g = p['poly']
        p['poly_original'] = g
        if add[i]:
            g = unary_union([g] + add[i]).buffer(0)
        if sub[i]:
            g = g.difference(unary_union(sub[i]).buffer(0))
        if g.geom_type == 'MultiPolygon':
            g = max(g.geoms, key=lambda x: x.area)
        p['poly'] = g
        p['n_dientes'] = len(add[i])
        p['n_ranuras'] = len(sub[i])
        vivas = [m for m in marcas[i] if del_contacto.get(id(m)) not in canceladas]
        m = unary_union(vivas).intersection(g) if vivas else None
        p['marcas'] = _solo_poligonos(m)
    return hechas - len(canceladas)


def _figura(base, mas, menos):
    g = base
    if mas:
        g = unary_union([g] + mas).buffer(0)
    if menos:
        g = g.difference(unary_union(menos).buffer(0))
    if g.geom_type == 'MultiPolygon':
        g = max(g.geoms, key=lambda x: x.area)
    return g


def _cuidar_placas(placas, contactos, add, sub, del_contacto, minimo=MINIMO_AREA):
    """Una union por cada placa que llega esta bien en una casita de nueve piezas.
    En un edificio real un muro medianero recibe CIENTO CINCUENTA Y CUATRO ranuras
    y queda hecho encaje de bolillo: 208 m2 de muro terminan en 12.

    Aqui se cancelan uniones hasta que cada placa conserve al menos `minimo` de su
    area. Se tumban primero los contactos mas cortos, que son los que menos amarran
    y los que mas abundan. Cancelar es simetrico: la union es un par, y dejar el
    diente de un lado sin la ranura del otro es peor que no ponerla.

    El contacto cancelado se queda SIN 'modo', asi que `recortar_choques` lo vuelve
    a mirar y recorta el traslape. Esa junta se pega, no se ensambla.
    """
    canceladas = set()
    por_placa = {}
    for ic, c in enumerate(contactos):
        if not c.get('modo'):
            continue
        for k in (c['espiga'], c['ranura']):
            por_placa.setdefault(k, []).append(ic)

    for i, p in enumerate(placas):
        pendientes = [ic for ic in por_placa.get(i, []) if ic not in canceladas]
        if not pendientes:
            continue
        base = p['poly']
        if base.is_empty or base.area <= 0:
            continue
        area = _figura(base, add[i], sub[i]).area
        if area >= base.area * minimo:
            continue
        # de la que menos amarra a la que mas
        pendientes.sort(key=lambda ic: contactos[ic]['largo'])
        for ic in pendientes:
            canceladas.add(ic)
            for k in (contactos[ic]['espiga'], contactos[ic]['ranura']):
                add[k] = [g for g in add[k] if del_contacto.get(id(g)) != ic]
                sub[k] = [g for g in sub[k] if del_contacto.get(id(g)) != ic]
            if _figura(base, add[i], sub[i]).area >= base.area * minimo:
                break

    for ic in canceladas:
        contactos[ic]['modo'] = None
        contactos[ic]['cancelada'] = True
        contactos[ic]['dientes'] = 0
    return canceladas


def _solo_poligonos(g, min_area=1e-9):
    """La interseccion suele devolver GeometryCollection (poligonos + lineas sueltas).
    El dibujante solo entiende poligonos: lo demas hay que tirarlo aqui, no despues,
    o la marca desaparece sin avisar."""
    if g is None or g.is_empty:
        return None
    partes = list(g.geoms) if hasattr(g, 'geoms') else [g]
    buenos = [p for p in partes if p.geom_type == 'Polygon' and p.area > min_area]
    if not buenos:
        return None
    return buenos[0] if len(buenos) == 1 else MultiPolygon(buenos)


def recortar_choques(placas, contactos, t_placa_modelo, max_perdida=0.25):
    """Red de seguridad. Dos placas que se traslapan y NO alcanzaron a formar union
    (tipico: el alero del techo contra el remate del muro) ocupan el mismo volumen y
    la maqueta no cierra. Se recorta la que TERMINA ahi hasta la cara de la otra.

    Devuelve (recortes_hechos, avisos)."""
    con_union = {tuple(sorted((c['ranura'], c['espiga']))) for c in contactos if c.get('modo')}
    hechos, avisos = 0, []

    for i in range(len(placas)):
        for j in range(i + 1, len(placas)):
            if (i, j) in con_union:
                continue
            A, B = placas[i], placas[j]
            cos = abs(float(np.dot(A['normal'], B['normal'])))
            if cos > 0.995:                       # paralelas: no se cruzan
                continue
            sen = max(float(np.sqrt(max(1.0 - cos * cos, 0.0))), 0.20)
            d = (t_placa_modelo / 2.0) * (1.0 + cos) / sen

            lin = _linea_planos(A['centro'], A['normal'], B['centro'], B['normal'])
            if lin is None:
                continue
            p0, dd = lin
            ra, rb = _recta_local(A, p0, dd), _recta_local(B, p0, dd)
            if ra is None or rb is None:
                continue
            ta, _ = _tramo(A, ra[0], ra[1], d)
            tb, _ = _tramo(B, rb[0], rb[1], d)
            if not ta or not tb:
                continue
            t0, t1 = max(ta[0], tb[0]), min(ta[1], tb[1])
            if t1 - t0 <= 1e-6:
                continue

            # Regla por placa, no por par:
            #   w <= -d      ya se queda antes de la otra -> nada
            #   -d < w <= d  TERMINA dentro del carton de la otra -> se recorta
            #   w > d        la atraviesa de lado a lado -> es la que manda, no se toca
            datos = []
            for P, (q, u) in ((A, ra), (B, rb)):
                perp = _perp_hacia(P, q, np.array([-u[1], u[0]]), None)
                w = _w_borde(P['poly'], q, u, perp, t0, t1)
                datos.append((P, q, u, perp, w if w is not None else -1e9))
            atraviesan = [x for x in datos if x[4] > d]
            if len(atraviesan) == 2:
                continue                           # se cruzan de verdad: no es un tope
            # cede UNA sola: la que termina antes. Si ceden las dos queda un hueco
            # y, peor, se puede partir una pieza a la mitad.
            ceden = sorted([x for x in datos if -d < x[4] <= d], key=lambda x: x[4])[:1]

            for P, q, u, perp, w in ceden:
                if w <= -d + 1e-9:
                    continue
                corte = _rect(q, u, perp, t0 - 1e-4, t1 + 1e-4, -d, t_placa_modelo * 40 + 1.0)
                nuevo = P['poly'].difference(corte)
                if nuevo.is_empty:
                    avisos.append('%s desaparece al recortar contra %s' %
                                  (P.get('id', '?'), (B if P is A else A).get('id', '?')))
                    continue
                if nuevo.geom_type == 'MultiPolygon':
                    avisos.append('%s se partiria en %d contra %s: no se recorta' %
                                  (P.get('id', '?'), len(nuevo.geoms),
                                   (B if P is A else A).get('id', '?')))
                    continue
                if nuevo.area < P['poly'].area * (1 - max_perdida):
                    avisos.append('%s perderia %.0f%% contra %s: no se recorta' %
                                  (P.get('id', '?'), 100 * (1 - nuevo.area / P['poly'].area),
                                   (B if P is A else A).get('id', '?')))
                    continue
                P['poly'] = nuevo
                hechos += 1
    return hechos, avisos
