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
import shapely.affinity as aff

# una placa no puede quedar con menos de esta fraccion de su area por culpa
# de las uniones: abajo de eso ya no es pieza, es encaje de bolillo
MINIMO_AREA = 0.55

TOL_PERP = 0.90        # |nA . nB| menor a esto = se cruzan en angulo util
                       # (0.90 = desde 26 grados; el techo llega al muro a 28)
MIN_CONTACTO = 0.15    # m de modelo: contacto mas corto no se dedea
HOLGURA = 0.06         # mm de maqueta: juego de la ranura para que entre
LABIO = 1.5            # espesores: lo que asoma del otro lado hasta aqui es ruido
                       # del modelo; mas que eso, la placa sigue de largo (cruce)


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
    # q0 es el punto de la recta mas cercano al ORIGEN del mundo, no a la placa:
    # en un edificio lejos del origen el segmento no alcanzaba a la placa y el
    # contacto no existia. Se centra en el pie del centroide sobre la recta.
    # Los t se siguen midiendo desde q0: la otra placa usa el mismo punto.
    cen = np.array(placa['poly'].centroid.coords[0])
    qc = q0 + u * float(np.dot(cen - q0, u))
    recta = LineString([qc - u * largo, qc + u * largo])
    reg = recta.buffer(max(alcance, 1e-6), cap_style=2).intersection(placa['poly'])
    if reg.is_empty or reg.area <= 1e-9:
        return None, False
    xs = np.array(reg.bounds).reshape(2, 2)
    esquinas = [np.array([xs[0][0], xs[0][1]]), np.array([xs[1][0], xs[0][1]]),
                np.array([xs[0][0], xs[1][1]]), np.array([xs[1][0], xs[1][1]])]
    ts = [float(np.dot(c - q0, u)) for c in esquinas]
    cruza = recta.intersection(placa['poly']).length > 1e-6
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
    # Prefiltro (24 sep 2026): cada par iba a shapely (_tramo) aunque las placas
    # estuvieran en puntas opuestas del edificio: 109 mil llamadas y 14 de los 36 s
    # de Merida. Un contacto pide un punto de la recta comun a menos de 2*alc de
    # cada placa (alc = max(espesor de la otra, carton)/2 + 1e-4, el ancho de la
    # franja de _tramo, mas lo que el tramo se estira por medirse con la caja), o
    # sea que sus cajas en el mundo no pueden quedar mas lejos que r_i + r_j. El
    # 1.5 es holgura contra el redondeo: salen los mismos contactos, en el mismo orden.
    n = len(placas)
    lo, hi = np.full((n, 3), np.inf), np.full((n, 3), -np.inf)
    for k, p in enumerate(placas):
        if p['poly'].is_empty:
            continue                          # sin material no hay tramo: nunca pasa
        x0, y0, x1, y1 = p['poly'].bounds     # la caja 2D llevada al mundo: la contiene
        W = (p['a_mundo'] @ np.array([[x0, y0, 0, 1], [x1, y0, 0, 1],
                                      [x0, y1, 0, 1], [x1, y1, 0, 1]], float).T).T[:, :3]
        lo[k], hi[k] = W.min(axis=0), W.max(axis=0)
    r = np.array([max(p['espesor_real'], t_placa_modelo) + 2e-4 for p in placas])
    hueco = np.maximum(lo[None, :, :] - hi[:, None, :], lo[:, None, :] - hi[None, :, :]).max(axis=2)
    cerca = hueco <= 1.5 * (r[:, None] + r[None, :])
    cont = []
    for i in range(n):
        for j in range(n):
            if i == j or not cerca[i, j]:
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


def _perp_hacia(placa, q, u, t0, t1, alcance):
    """Perpendicular en el plano de la placa, apuntando de la placa hacia la recta.

    Se decide EN EL TRAMO del contacto: el lado con mas material pegado a la
    recta es el cuerpo. Con el centroide de la placa entera, una losa en L o un
    muro de cinco pisos quedaba al reves y la union le cortaba el lado bueno.
    """
    perp = np.array([-u[1], u[0]])
    g = placa['poly']
    mas = g.intersection(_rect(q, u, perp, t0, t1, 0.0, alcance)).area
    menos = g.intersection(_rect(q, u, perp, t0, t1, -alcance, 0.0)).area
    if abs(mas - menos) > 1e-12:
        return perp if menos > mas else -perp
    cen = np.array(g.centroid.coords[0])
    return perp if np.dot(q - cen, perp) >= 0 else -perp


def _rect(q, u, perp, ta, tb, wa, wb):
    return Polygon([q + u * ta + perp * wa, q + u * tb + perp * wa,
                    q + u * tb + perp * wb, q + u * ta + perp * wb])


def _tejer(placas, c, i_ranura, i_espiga, t, diente_obj, holgura_modelo):
    """Calcula la union de UN contacto con unos papeles dados, sin tocar nada.

    Devuelve (modo, dientes, aportes, marcas) donde aportes es una lista de
    ('add'|'sub', indice_de_placa, poligono). Se calcula aparte justamente para
    poder pedirla otra vez con los papeles al reves: la placa que recibe las
    ranuras es la que pierde area, y a veces conviene que las reciba la otra.
    """
    pe, pr = placas[i_espiga], placas[i_ranura]
    re_, rr_ = _recta_local(pe, c['p0'], c['d']), _recta_local(pr, c['p0'], c['d'])
    if re_ is None or rr_ is None:
        return None
    qe, ue = re_
    qr, ur = rr_

    LEJOS = t * 40 + 1.0
    # a un angulo distinto de 90 grados hay que avanzar mas para salir del canto:
    # la distancia en el plano es (t/2) / sen(angulo entre placas)
    cosang = abs(float(np.dot(pe['normal'], pr['normal'])))
    sen = max(float(np.sqrt(max(1.0 - cosang * cosang, 0.0))), 0.30)
    # OJO: la placa no es una superficie, es una losa de espesor t. El material
    # que esta fuera del plano medio alcanza mas lejos, por eso el (1+cos).
    tt = t * (1.0 + cosang) / sen

    alcance = tt / 2 + (LABIO + 1.5) * t
    perp_e = _perp_hacia(pe, qe, ue, c['t0'], c['t1'], alcance)
    perp_r = _perp_hacia(pr, qr, ur, c['t0'], c['t1'], alcance)

    w_e = _w_borde(pe['poly'], qe, ue, perp_e, c['t0'], c['t1'])
    w_r = _w_borde(pr['poly'], qr, ur, perp_r, c['t0'], c['t1'])
    if w_e is None or w_r is None:
        return None

    def sigue(placa, q, u, perp):
        """¿La placa continua del otro lado, mas alla de un labio? Entonces no
        termina aqui: dientes o dedos le borrarian hasta LEJOS todo ese lado."""
        g = placa['poly'].intersection(
            _rect(q, u, perp, c['t0'], c['t1'], tt / 2 + LABIO * t, LEJOS))
        return g.area > 0.25 * (c['t1'] - c['t0']) * t

    # la espiga que atraviesa no es espiga: es un cruce y lo resuelve
    # recortar_choques con una ranura pasante
    if sigue(pe, qe, ue, perp_e):
        return None

    n = max(3, min(11, int(round(c['largo'] / max(diente_obj, 1e-6)))))
    if n % 2 == 0:
        n += 1
    paso = (c['t1'] - c['t0']) / n

    marcas = [(i_ranura, _rect(qr, ur, perp_r, c['t0'], c['t1'], -tt / 2, tt / 2)),
              (i_espiga, _rect(qe, ue, perp_e, c['t0'], c['t1'], -tt / 2, tt / 2))]

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

    aportes = []
    if cabe:
        modo = 'ranura'
        for k in range(n):
            ta, tb = c['t0'] + k * paso, c['t0'] + (k + 1) * paso
            if k % 2 == 0:
                g = crecer(pe, qe, ue, perp_e, ta, tb, max(tt / 2, w_e))
                if g is not None:
                    aportes.append(('add', i_espiga, g))
            else:
                aportes.append(('sub', i_espiga,
                                _rect(qe, ue, perp_e, ta, tb, -tt / 2, LEJOS)))
        for r in ranuras:
            aportes.append(('sub', i_ranura, r))
    else:
        if sigue(pr, qr, ur, perp_r):
            return None
        modo = 'dedos'
        for k in range(n):
            ta, tb = c['t0'] + k * paso, c['t0'] + (k + 1) * paso
            # par: sale la que topa y se mete la receptora; impar: al reves
            obj_e = (tt / 2) if k % 2 == 0 else (-tt / 2)
            obj_r = (-tt / 2) if k % 2 == 0 else (tt / 2)
            for placa, placa_i, q, u, perp, obj in (
                    (pe, i_espiga, qe, ue, perp_e, obj_e),
                    (pr, i_ranura, qr, ur, perp_r, obj_r)):
                g = crecer(placa, q, u, perp, ta, tb, obj)
                if g is not None:
                    aportes.append(('add', placa_i, g))
                else:
                    aportes.append(('sub', placa_i,
                                    _rect(q, u, perp, ta, tb, obj, LEJOS)))
    return modo, (n + 1) // 2, aportes, marcas


def _mayor_poligono(g):
    """La placa mas grande que haya adentro, SIEMPRE como Polygon.

    `difference()` no devuelve siempre un poligono: cuando el corte roza un borde
    entrega una GeometryCollection con poligonos Y lineas sueltas. Eso se colaba
    tal cual hasta `huellas_en_losas`, que le pide `.exterior` --que solo tiene un
    Polygon-- y tronaba con "'GeometryCollection' object has no attribute
    'exterior'". Como el grabado de planta va envuelto en try/except, no se caia
    la corrida: se apagaba EL GRABADO ENTERO y solo quedaba un aviso gris.

    Devolver siempre un Polygon (vacio si no quedo nada) le quita el problema a
    todos los que consumen 'poly', no nada mas al que se quejo.
    """
    if g is None or g.is_empty:
        return Polygon()
    partes = list(g.geoms) if hasattr(g, 'geoms') else [g]
    poligonos = [p for p in partes if p.geom_type == 'Polygon' and not p.is_empty]
    if not poligonos:
        return Polygon()
    return max(poligonos, key=lambda x: x.area)


def _figura(base, mas, menos):
    g = base
    if mas:
        g = unary_union([g] + mas).buffer(0)
    if menos:
        g = g.difference(unary_union(menos).buffer(0))
    return _mayor_poligono(g)


def _area_con(placas, i, tejidos, saltar=None):
    """Area que le queda a la placa i con las uniones vivas."""
    mas, menos = [], []
    for ic, tej in tejidos.items():
        if tej is None or ic == saltar:
            continue
        for cual, pi, g in tej['aportes']:
            if pi != i:
                continue
            (mas if cual == 'add' else menos).append(g)
    return _figura(placas[i]['poly'], mas, menos).area


def _repartir_uniones(placas, contactos, tejidos, t, diente_obj, holgura_modelo,
                      minimo=MINIMO_AREA):
    """Que ninguna placa se destruya a si misma, sin perder la union si se puede.

    En un edificio real un muro medianero recibe las ranuras de TODOS los
    entrepisos y particiones que llegan: 154 ranuras y el muro queda hecho encaje
    de bolillo (208 m2 terminan en 12).

    Antes se cancelaba la union y esa junta se pegaba a tope, lo que deja el
    traslape sin resolver. Ahora primero se intenta VOLTEAR LOS PAPELES: la
    ranura la recibe la otra placa del par. El muro se salva y la union sigue de
    pie. Solo si voltear tampoco alcanza (o hunde a la otra) se cancela.

    Voltear se prueba empezando por el contacto que mas area le cuesta a la placa,
    que es el que mas rinde. Cancelar se hace al reves: primero los contactos mas
    cortos, que son los que menos amarran.
    """
    volteadas, canceladas = set(), set()

    por_placa = {}
    for ic, tej in tejidos.items():
        if tej is None:
            continue
        for k in (contactos[ic]['espiga'], contactos[ic]['ranura']):
            por_placa.setdefault(k, []).append(ic)

    for i, p in enumerate(placas):
        mios = [ic for ic in por_placa.get(i, [])
                if ic not in canceladas and tejidos.get(ic)]
        if not mios or p['poly'].is_empty or p['poly'].area <= 0:
            continue
        meta = p['poly'].area * minimo
        if _area_con(placas, i, tejidos) >= meta:
            continue

        # cuanto le quita cada contacto a ESTA placa
        def costo(ic):
            q = [g for cual, pi, g in tejidos[ic]['aportes']
                 if pi == i and cual == 'sub']
            return sum(g.area for g in q)

        for ic in sorted(mios, key=costo, reverse=True):
            if _area_con(placas, i, tejidos) >= meta:
                break
            c = contactos[ic]
            if ic in volteadas:
                continue
            otro = c['espiga'] if c['ranura'] == i else c['ranura']
            # los papeles al reves: la ranura la recibe la otra placa
            nuevo = _tejer(placas, c, i_ranura=c['espiga'], i_espiga=c['ranura'],
                           t=t, diente_obj=diente_obj, holgura_modelo=holgura_modelo)
            if nuevo is None:
                continue
            antes_yo = _area_con(placas, i, tejidos)
            antes_otro = _area_con(placas, otro, tejidos)
            guardado = tejidos[ic]
            tejidos[ic] = {'modo': nuevo[0], 'dientes': nuevo[1],
                           'aportes': nuevo[2], 'marcas': nuevo[3],
                           'ranura': c['espiga'], 'espiga': c['ranura']}
            ahora_yo = _area_con(placas, i, tejidos)
            ahora_otro = _area_con(placas, otro, tejidos)
            meta_otro = placas[otro]['poly'].area * minimo
            # se acepta si a mi me ayuda y al otro no lo hunde
            if ahora_yo > antes_yo and (ahora_otro >= meta_otro
                                        or ahora_otro >= antes_otro):
                volteadas.add(ic)
            else:
                tejidos[ic] = guardado

        # lo que voltear no alcanzo a salvar, se cancela: primero los contactos
        # mas cortos, que son los que menos amarran
        if _area_con(placas, i, tejidos) < meta:
            for ic in sorted(mios, key=lambda k: contactos[k]['largo']):
                if ic in canceladas:
                    continue
                canceladas.add(ic)
                tejidos[ic] = None
                if _area_con(placas, i, tejidos) >= meta:
                    break

    return volteadas, canceladas


def aplicar_uniones(placas, contactos, t_placa_modelo, diente_obj, holgura_modelo):
    """Dos tipos de union, elegidos por geometria:

    RANURA  la placa que topa cae en medio de la receptora -> ranura pasada + espiga.
    DEDOS   se encuentran canto con canto (esquinas, muro parado en el filo de la losa)
            -> dientes alternados: donde una sale, la otra se mete.

    Se calcula cada contacto por separado, se reparte la carga entre las placas y
    hasta el final se aplica: una placa que recibe todas las ranuras se destruye.
    """
    t = t_placa_modelo
    tejidos = {}
    for ic, c in enumerate(contactos):
        r = _tejer(placas, c, c['ranura'], c['espiga'], t, diente_obj, holgura_modelo)
        if r is None:
            tejidos[ic] = None
            continue
        tejidos[ic] = {'modo': r[0], 'dientes': r[1], 'aportes': r[2], 'marcas': r[3],
                       'ranura': c['ranura'], 'espiga': c['espiga']}

    volteadas, canceladas = _repartir_uniones(placas, contactos, tejidos, t,
                                              diente_obj, holgura_modelo)

    add = {i: [] for i in range(len(placas))}
    sub = {i: [] for i in range(len(placas))}
    marcas = {i: [] for i in range(len(placas))}
    hechas = 0
    for ic, c in enumerate(contactos):
        tej = tejidos.get(ic)
        if tej is None:
            c['modo'] = None
            c['dientes'] = 0
            c['cancelada'] = ic in canceladas
            continue
        c['modo'] = tej['modo']
        c['dientes'] = tej['dientes']
        c['volteada'] = ic in volteadas
        c['ranura'], c['espiga'] = tej['ranura'], tej['espiga']
        for cual, pi, g in tej['aportes']:
            (add if cual == 'add' else sub)[pi].append(g)
        for pi, g in tej['marcas']:
            marcas[pi].append(g)
        hechas += 1

    for i, p in enumerate(placas):
        g = p['poly']
        p['poly_original'] = g
        g = _figura(g, add[i], sub[i])
        p['poly'] = g
        p['n_dientes'] = len(add[i])
        p['n_ranuras'] = len(sub[i])
        m = unary_union(marcas[i]).intersection(g) if marcas[i] else None
        p['marcas'] = _solo_poligonos(m)
    return hechas


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


def _intervalos(poly, q, u, perp, wa, wb, min_area=1e-9):
    """Tramos [t0, t1] de la recta donde la placa tiene material entre wa y wb
    (distancia perpendicular a la recta, en su plano). Unidos y ordenados."""
    b = poly.bounds
    largo = (b[2] - b[0]) + (b[3] - b[1]) + 1.0
    tc = float(np.dot(np.array(poly.centroid.coords[0]) - q, u))
    reg = poly.intersection(_rect(q, u, perp, tc - largo, tc + largo, wa, wb))
    ts = []
    for g in (reg.geoms if hasattr(reg, 'geoms') else [reg]):
        if g.geom_type != 'Polygon' or g.area <= min_area:
            continue
        xs = np.asarray(g.exterior.coords) - q
        v = xs @ u
        ts.append([float(v.min()), float(v.max())])
    ts.sort()
    unidos = []
    for t0, t1 in ts:
        if unidos and t0 <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], t1)
        else:
            unidos.append([t0, t1])
    return unidos


def _cruce_intervalos(xa, xb, minimo):
    out, i, j = [], 0, 0
    while i < len(xa) and j < len(xb):
        t0, t1 = max(xa[i][0], xb[j][0]), min(xa[i][1], xb[j][1])
        if t1 - t0 > minimo:
            out.append((t0, t1))
        if xa[i][1] < xb[j][1]:
            i += 1
        else:
            j += 1
    return out


def _caja_mundo(p, holgura):
    xy = np.asarray(p['poly'].exterior.coords) if not p['poly'].is_empty else np.zeros((0, 2))
    if len(xy) == 0:
        return None
    L = np.hstack([xy, np.zeros((len(xy), 1)), np.ones((len(xy), 1))])
    W = (p['a_mundo'] @ L.T).T[:, :3]
    return W.min(axis=0) - holgura, W.max(axis=0) + holgura


def _probar_corte(P, q, u, perp, tramos, d, t, max_perdida):
    """Quitarle a P la franja [-d, d] en los tramos. Devuelve (nuevo, perdida,
    termina) o (None, motivo, None) si la pieza no lo aguanta.

    Lo que queda del otro lado de la franja solo se tira si es un LABIO (mas
    delgado que LABIO espesores): el muro que asoma 4 mm arriba de la losa. Un
    pedazo mas grande es media pieza y entonces el corte la parte en dos.
    """
    e = t * 1e-3
    corte = unary_union([_rect(q, u, perp, t0 - e, t1 + e, -d, d) for t0, t1 in tramos])
    resto = P['poly'].difference(corte)
    partes = sorted([g for g in (resto.geoms if hasattr(resto, 'geoms') else [resto])
                     if g.geom_type == 'Polygon' and g.area > 1e-12],
                    key=lambda g: -g.area)
    if not partes:
        return None, 'desaparece', None
    for g in partes[1:]:
        s = (np.asarray(g.exterior.coords) - q) @ perp
        if s.max() - s.min() > LABIO * t:
            return None, 'se partiria en %d' % len(partes), None
    nuevo = partes[0]
    area = P['poly'].area
    perdida = (area - nuevo.area) / area if area > 0 else 1.0
    if perdida > max_perdida:
        return None, 'perderia %.0f%%' % (100 * perdida), None
    # tope acumulado: uniones y recortes juntos no dejan la pieza en menos de la
    # mitad de lo que era (verificar_casa la da por destruida)
    if nuevo.area < 0.5 * P.get('poly_original', P['poly']).area:
        return None, 'quedaria en menos de la mitad', None
    # ¿sigue de los dos lados de la franja? Entonces no termina ahi: la otra
    # placa la atraviesa y lo que se le hizo es una ranura, no un recorte.
    largo = sum(t1 - t0 for t0, t1 in tramos)
    lados = [nuevo.intersection(unary_union(
        [_rect(q, u, perp, t0, t1, a, b) for t0, t1 in tramos])).area
        for a, b in ((d, d + 2 * t), (-d - 2 * t, -d))]
    termina = min(lados) <= 0.25 * largo * 2 * t
    return nuevo, perdida, termina


def _en_plano_de(P, X):
    """La huella de X vista en el plano local de P, y su altura sobre ese plano
    como funcion afin de las coordenadas de P: z = g . (u, v) + h.
    None si X no es casi paralela a P."""
    M = np.linalg.inv(P['a_mundo']) @ X['a_mundo']
    A2 = M[:2, :2]
    if abs(float(np.linalg.det(A2))) < 1e-6:
        return None
    huella = aff.affine_transform(X['poly'], [M[0, 0], M[0, 1], M[1, 0], M[1, 1],
                                               M[0, 3], M[1, 3]])
    g = M[2, :2] @ np.linalg.inv(A2)
    h = float(M[2, 3] - g @ M[:2, 3])
    return huella, g, h


def _traslape_paralelo(P, X, t):
    """Donde las placas casi paralelas P y X ocupan el mismo volumen, en el plano de P."""
    r = _en_plano_de(P, X)
    if r is None:
        return None
    huella, g, h = r
    try:
        zona = P['poly'].intersection(huella)
    except Exception:
        return None
    if zona.is_empty or zona.area <= 1e-9:
        return None
    lim = t * 0.98                       # dos caras que se besan no son choque
    ng = float(np.hypot(g[0], g[1]))
    if ng < 1e-9:
        return zona if abs(h) < lim else None
    # la altura crece a lo largo de n: la franja |z| < lim es una banda recta
    n, e = g / ng, np.array([-g[1], g[0]]) / ng
    b = zona.bounds
    L = (b[2] - b[0]) + (b[3] - b[1]) + 1.0
    ec = float(e @ np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]))
    franja = _rect(np.zeros(2), e, n, ec - L, ec + L, (-lim - h) / ng, (lim - h) / ng)
    zona = zona.intersection(franja)
    return zona if not zona.is_empty and zona.area > 1e-9 else None


def _resolver_paralelas(A, B, t, max_perdida):
    """Dos placas casi paralelas encimadas: la losa inclinada 5 grados sobre la
    plana, o el diente de un tramo de muro que crece dentro del tramo vecino del
    mismo plano (SketchUp parte el muro por cuarto). Se le quita la zona comun a
    la que menos pierde. Devuelve True si recorto, un aviso, o None si no chocan."""
    opciones, motivos = [], []
    for P, X in ((A, B), (B, A)):
        zona = _traslape_paralelo(P, X, t)
        if zona is None:
            return None                 # si no chocan vistas desde una, no chocan
        area = P['poly'].area
        resto = P['poly'].difference(zona)
        partes = sorted([g for g in (resto.geoms if hasattr(resto, 'geoms') else [resto])
                         if g.geom_type == 'Polygon' and g.area > 1e-12],
                        key=lambda g: -g.area)
        if not partes:
            motivos.append('%s desaparece' % P.get('id', '?'))
            continue
        if sum(g.area for g in partes[1:]) > 0.02 * area:
            motivos.append('%s se partiria en %d' % (P.get('id', '?'), len(partes)))
            continue
        nuevo = partes[0]
        perdida = (area - nuevo.area) / area if area > 0 else 1.0
        if perdida > max_perdida:
            motivos.append('%s perderia %.0f%%' % (P.get('id', '?'), 100 * perdida))
            continue
        if nuevo.area < 0.5 * P.get('poly_original', P['poly']).area:
            motivos.append('%s quedaria en menos de la mitad' % P.get('id', '?'))
            continue
        opciones.append((perdida, P, nuevo))
    if not opciones:
        return ('%s y %s se enciman y no se pudo recortar ninguna (%s)'
                % (A.get('id', '?'), B.get('id', '?'), '; '.join(motivos)))
    _, P, nuevo = min(opciones, key=lambda x: x[0])
    P['poly'] = nuevo
    return True


def _media_y_media(datos, tramos, d, t, max_perdida):
    """Ranura hasta la mitad del tramo en una placa y desde la mitad en la otra.
    Solo vale si ninguna queda con un hueco cerrado: una media ranura que no
    llega a la orilla no se puede encajar."""
    for primero in (0, 1):
        X, Y = datos[primero], datos[1 - primero]
        mitad_x = [(t0, (t0 + t1) / 2) for t0, t1 in tramos]
        mitad_y = [((t0 + t1) / 2, t1) for t0, t1 in tramos]
        hechos = []
        for (P, q, u, perp, _), mitad in ((X, mitad_x), (Y, mitad_y)):
            nuevo, _, _ = _probar_corte(P, q, u, perp, mitad, d, t, max_perdida)
            if nuevo is None or len(nuevo.interiors) > len(P['poly'].interiors):
                break
            hechos.append((P, nuevo))
        if len(hechos) == 2:
            for P, nuevo in hechos:
                P['poly'] = nuevo
                P['n_ranuras'] = P.get('n_ranuras', 0) + 1
            return True
    return False


def recortar_choques(placas, contactos, t_placa_modelo, max_perdida=0.25):
    """Red de seguridad. Dos placas que se traslapan y NO alcanzaron a formar union
    ocupan el mismo volumen y la maqueta no cierra.

    Se mide el traslape de verdad: los tramos de la recta donde LAS DOS tienen
    material dentro del espesor de la otra. Ahi se le quita la franja a una sola:
      - si una TERMINA ahi (tope, esquina, remate sobre el muro), se recorta esa
        hasta la cara de la otra; lo que asome del otro lado es un labio y se va;
      - si las dos siguen de largo (el muro que ATRAVIESA la losa), la que no se
        parte recibe una ranura pasante y la otra entra por ahi.
    Antes, si las dos asomaban del otro lado aunque fuera un milimetro, se
    tomaba por cruce y no se tocaba ninguna: era el 1.23% de la casa Engel.

    Devuelve (recortes_hechos, avisos)."""
    con_union = {tuple(sorted((c['ranura'], c['espiga']))) for c in contactos if c.get('modo')}
    hechos, avisos = 0, []
    t = t_placa_modelo
    cajas = [_caja_mundo(p, t) for p in placas]

    for i in range(len(placas)):
        for j in range(i + 1, len(placas)):
            if (i, j) in con_union or cajas[i] is None or cajas[j] is None:
                continue
            if np.any(cajas[i][1] < cajas[j][0]) or np.any(cajas[j][1] < cajas[i][0]):
                continue                          # ni se acercan
            A, B = placas[i], placas[j]
            if A['poly'].is_empty or B['poly'].is_empty:
                continue
            cos = abs(float(np.dot(A['normal'], B['normal'])))
            if cos > 0.995:
                # paralelas: no se cruzan, pero SI se pueden encimar
                res = _resolver_paralelas(A, B, t, max_perdida)
                if res is True:
                    hechos += 1
                elif res:
                    avisos.append(res)
                continue
            sen = max(float(np.sqrt(max(1.0 - cos * cos, 0.0))), 0.20)
            d = (t / 2.0) * (1.0 + cos) / sen

            lin = _linea_planos(A['centro'], A['normal'], B['centro'], B['normal'])
            if lin is None:
                continue
            p0, dd = lin
            ra, rb = _recta_local(A, p0, dd), _recta_local(B, p0, dd)
            if ra is None or rb is None:
                continue
            datos = []
            for P, (q, u) in ((A, ra), (B, rb)):
                perp = np.array([-u[1], u[0]])
                # un pelo adentro: dos caras que se besan no son choque
                datos.append((P, q, u, perp,
                              _intervalos(P['poly'], q, u, perp, -d + t * 0.02, d - t * 0.02)))
            tramos = _cruce_intervalos(datos[0][4], datos[1][4], t * 0.02)
            if not tramos:
                continue

            opciones, motivos = [], []
            for P, q, u, perp, _ in datos:
                nuevo, perdida, termina = _probar_corte(P, q, u, perp, tramos, d, t,
                                                        max_perdida)
                if nuevo is None:
                    motivos.append('%s %s' % (P.get('id', '?'), perdida))
                    continue
                # primero la que termina ahi; luego la que menos pierde. La perdida
                # va redondeada al 1%: con 6.0000% contra 5.9999% decidia el ruido
                # de 1e-8 y el orden de recortes salia distinto con cada espesor
                # (cubo de 3 mm, 2 oct 2026: muro con dos ranuras y patitas de
                # 3 mm). En empate gana el orden fijo de las placas.
                opciones.append(((not termina, round(perdida, 2)), P, nuevo))
            if not opciones:
                # Ninguna aguanta la ranura entera: se cruzan de orilla a orilla.
                # Media ranura en cada una, cada media abierta hacia la orilla de
                # su pieza (caja de huevos): se encajan una en la otra.
                if _media_y_media(datos, tramos, d, t, max_perdida):
                    hechos += 2
                    continue
                avisos.append('%s y %s se enciman y no se pudo recortar ninguna (%s)'
                              % (A.get('id', '?'), B.get('id', '?'), '; '.join(motivos)))
                continue
            (sigue, _), P, nuevo = min(opciones, key=lambda x: x[0])
            P['poly'] = nuevo
            if sigue:                             # no termina ahi: es ranura, no recorte
                P['n_ranuras'] = P.get('n_ranuras', 0) + 1
            hechos += 1
    return hechos, avisos
