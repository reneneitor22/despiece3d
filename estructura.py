# -*- coding: utf-8 -*-
"""Despiece estructural completo: modelo 3D -> placas con uniones -> piezas en mm."""
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon
import shapely.affinity as aff

from placas import (extraer_placas, nombrar, marcar_envolvente,
                    niveles_de_piso, cortar_por_piso, huellas_en_losas,
                    asignar_planta, preparar_cuerpos, tabla_obb)
from uniones import detectar_contactos, aplicar_uniones, recortar_choques, _solo_poligonos
import dbg     # dbg.marcar: la pantalla dice en cual de estos pasos va (o se atoro)

DIENTE_OBJ_MM = 12.0      # ancho buscado del diente, en mm de maqueta
HOLGURA_MM = 0.06         # juego de la ranura
MIN_LADO_MM = 2.0         # mm de maqueta: mas angosto que esto no se corta ni se pega
MIN_AREA_MM2 = 20.0       # mm2 de maqueta: menos que esto es confeti
MIN_ANCHO_MM = 1.0        # mm de maqueta: en algun punto de la pieza debe caber un disco asi
MAX_PLACAS = 400          # arriba de esto ya no es maqueta escolar


def _tiene_ancho(poly, mm_por_unidad):
    """False si en NINGUN punto la pieza llega a MIN_ANCHO_MM de ancho. La caja
    envolvente no lo ve: un marco de 19x146 mm con riel de 0.5 mm (un muro cuyo hueco
    se comio la cara, MainStreet 1:500) pasa MIN_LADO_MM y sale en la hoja como una
    tira que ni se levanta ni se pega. 0.99 deja pasar una tira de justo el minimo."""
    return not poly.buffer(-0.5 * 0.99 * MIN_ANCHO_MM / mm_por_unidad).is_empty


def _cortable_mm(poly):
    """Igual que _cortable pero para geometria que YA viene en mm de maqueta."""
    if poly.is_empty:
        return False
    minx, miny, maxx, maxy = poly.bounds
    return (min(maxx - minx, maxy - miny) >= MIN_LADO_MM
            and poly.area >= MIN_AREA_MM2
            and _tiene_ancho(poly, 1.0))


def _cortable(placa, cfg):
    """True si la placa, llevada a la escala pedida, se puede cortar de verdad."""
    g = placa['poly']
    if g.is_empty:
        return False
    minx, miny, maxx, maxy = g.bounds
    lado = min(maxx - minx, maxy - miny) * cfg.a_mm
    return (lado >= MIN_LADO_MM and g.area * cfg.a_mm * cfg.a_mm >= MIN_AREA_MM2
            and _tiene_ancho(g, cfg.a_mm))


# ------------------------------------------------- escaleras, muebles y demas
RAZON_PLACA_SOL = 0.34        # el mismo corte que usa placas.py
MIN_VOL_MM3 = 400.0           # mm3 de maqueta: menos que esto es una astilla
MAX_LADO_MODELO = 0.35        # un cuerpo mas grande que esto NO es un mueble
MAX_REBANADAS = 120           # por cuerpo: mas que esto no lo pega nadie


def cuerpos_macizos(mesh, cfg, razon=RAZON_PLACA_SOL, min_vol_mm3=MIN_VOL_MM3,
                    max_lado=MAX_LADO_MODELO, preparado=None, obbs=None, tipos=None):
    """Los cuerpos que NO son lamina: escaleras, barandales, muebles, columnas.

    placas.py los descarta con razon, porque no hay forma de sacarles una placa:
    una escalera no es una superficie. Pero tirarlos deja la maqueta coja.

    Se deja fuera lo que es demasiado grande para ser un mueble: muchos modelos
    traen ademas el volumen macizo del edificio entero como un cuerpo mas (en la
    casa Bauhaus, dos bloques del 53% y 56% del modelo). Laminar eso son 444
    rebanadas de nada.
    """
    from placas import _obb, preparar_cuerpos
    # `preparado` y `obbs` vienen de despiece_estructural: soldar el modelo,
    # partirlo en cuerpos y sacarle la caja orientada a cada uno son los pasos
    # mas caros del pipeline, y sin compartirlos se hacen DOS veces --una aqui y
    # otra en extraer_placas-- sobre exactamente los mismos cuerpos.
    _, cuerpos, _ = (preparado if preparado is not None
                     else preparar_cuerpos(mesh))
    v_min = min_vol_mm3 / (cfg.a_mm ** 3)          # a unidades del modelo
    lado_tope = float(np.max(mesh.extents)) * max_lado
    macizos = []
    for idx, c in enumerate(cuerpos):
        if len(c.faces) < 4 or c.area < 1e-9:
            continue
        caja = obbs[idx] if obbs is not None else None
        if caja is None:
            try:
                caja = _obb(c)
            except Exception as e:
                caja = e
        if isinstance(caja, Exception):
            continue
        _, ext, _ = caja
        esp, _, largo = ext
        # 'macizo' es el IFC diciendo viga o columna: placas.py no la tomo
        # aunque parezca lamina, asi que aqui si cuenta
        if largo <= 1e-9 or ((tipos or {}).get(idx) != 'macizo'
                             and esp / max(largo, 1e-9) <= razon):
            continue                                # es lamina: ya la vio placas.py
        if float(np.prod(ext)) < v_min:
            continue
        if largo > lado_tope:                       # es la masa del edificio
            continue
        macizos.append(c)
    return macizos


def _resumir_encimes(avisos, tope=5):
    """Los primeros `tope` "se enciman" y un renglon con el resto. Hearst Tower
    (SketchUp) daba 291 avisos, uno por renglon en la pantalla: nadie los lee y
    el que importa --que ahi no va a cerrar-- se pierde."""
    enc = [a for a in avisos if 'se enciman y no se pudo recortar' in a]
    if len(enc) <= tope:
        return avisos
    otros = [a for a in avisos if 'se enciman y no se pudo recortar' not in a]
    return otros + enc[:tope] + [
        '... y %d pares mas se enciman sin poder recortarse (%d en total): en esos '
        'encuentros la maqueta no va a cerrar al armar. Casi siempre es el modelo '
        '(piezas metidas una en otra, o caras sin espesor): corrigelo, o usa "Terreno / '
        'topografia", que lo saca en rebanadas apiladas que siempre cierran.'
        % (len(enc) - tope, len(enc))]


def rebanar_solidos(mesh, cfg, prefijo='S', preparado=None, obbs=None, tipos=None):
    """Una escalera no se corta: se LAMINA. Se rebana en horizontal cada espesor
    de carton y se apilan las rebanadas, igual que el modo terreno.

    Devuelve piezas con la misma forma que las del modo casa, para que entren al
    mismo acomodo y a la misma guia.
    """
    from despiece import rebanar, armar_piezas, solidificar
    macizos = cuerpos_macizos(mesh, cfg, preparado=preparado, obbs=obbs, tipos=tipos)
    piezas, resumen = [], []
    for k, c in enumerate(macizos, 1):
        nombre = '%s%d' % (prefijo, k)
        # Estos cuerpos casi nunca vienen cerrados (una escalera exportada son
        # unas caras sueltas) y rebanar una malla abierta da curvas, no
        # poligonos: cero capas. Se le cose faldon y fondo primero.
        try:
            cerrado = c if c.is_watertight else solidificar(c)
            capas = rebanar(cerrado, cfg)
        except Exception:
            resumen.append((nombre, 0, 0.0, 'no se pudo rebanar'))
            continue
        if not capas:
            resumen.append((nombre, 0, 0.0, 'no salieron capas'))
            continue
        if len(capas) > MAX_REBANADAS:
            resumen.append((nombre, 0, 0.0,
                            'pediria %d rebanadas, mas del tope de %d'
                            % (len(capas), MAX_REBANADAS)))
            continue
        # sin vaciar: son piezas chicas, el hueco no ahorra y las debilita
        crudas = armar_piezas(capas, vaciar=False)
        # las rebanadas ya vienen en mm de maqueta: el mismo filtro de las placas.
        # Arriba y abajo de un cuerpo inclinado salen astillas de un milimetro.
        crudas = [pz for pz in crudas if _cortable_mm(pz['poly'])]
        if not crudas:
            resumen.append((nombre, 0, 0.0, 'todas las rebanadas quedan mas chicas '
                                            'que %.0f mm' % MIN_LADO_MM))
            continue
        alto_mm = len(capas) * cfg.espesor_mm
        for pz in crudas:
            piezas.append({
                'id': '%s-%s' % (nombre, pz['id']),
                'tipo': 'macizo',
                'poly': pz['poly'],
                'guia': pz['guia'],
                'z_real': pz['z_real'],
                # z_real es desde el pie del PROPIO cuerpo; la planta se decide con
                # donde se para en el edificio, y es la misma para todas sus rebanadas
                'z_base': float(c.bounds[0][2]),
                'espesor_real_cm': cfg.espesor_mm / 10.0,
                'vanos': len(pz['poly'].interiors),
                'dientes': 0,
                'ranuras': 0,
                'apilada': True,
            })
        resumen.append((nombre, len(crudas), alto_mm, ''))
    return piezas, resumen


PLANTA_BASTIDOR = 10 ** 6   # va al final de todo: se arma cuando ya hay maqueta


def piezas_bastidor(mesh, cfg, alto_mm=15.0, margen_mm=8.0):
    """La base con faldon donde se para la maqueta.

    Cinco piezas: la tabla del tamano de la huella del modelo mas un margen, y
    cuatro faldones a tope que la levantan. Los faldones cortos van MENOS dos
    espesores porque entran entre los largos; si no, el bastidor sale un espesor
    mas grande de cada lado y la tabla ya no le tapa el canto.
    """
    from shapely.geometry import box
    b = mesh.bounds
    t = float(cfg.espesor_mm)
    W = (float(b[1][0]) - float(b[0][0])) * cfg.a_mm + 2 * margen_mm
    D = (float(b[1][1]) - float(b[0][1])) * cfg.a_mm + 2 * margen_mm
    if W < 20 or D < 20 or alto_mm <= 0:
        return []

    def pieza(idp, poly, guia=None):
        return {'id': idp, 'tipo': 'bastidor', 'poly': poly, 'guia': guia,
                'z_real': 0.0, 'espesor_real_cm': t / 10.0,
                'vanos': 0, 'dientes': 0, 'ranuras': 0,
                'planta': PLANTA_BASTIDOR, 'rotulo': 'BASTIDOR'}

    # el rectangulo grabado en la tabla: donde cae la cara de adentro del faldon
    tapa = box(0, 0, W, D)
    guia = (tapa.difference(box(t, t, W - t, D - t))
            if W > 4 * t and D > 4 * t else None)
    salida = [pieza('BA1', tapa, guia)]
    for k in (1, 2):
        salida.append(pieza('BF%d' % k, box(0, 0, W, alto_mm)))
    corto = max(10.0, D - 2 * t)
    for k in (3, 4):
        salida.append(pieza('BF%d' % k, box(0, 0, corto, alto_mm)))
    return salida


def partir_en_caras(placas, t_mod):
    """Muro hueco: cada muro sale como DOS caras de carton, una en cada paño del
    muro real, con la holgura en medio para que el alumno pase instalaciones.
    Holgura = espesor del muro a escala - 2 caras (20 cm a 1:25 en 2 mm: 8-4 = 4 mm).
    El muro que no da para dos caras y algo de hueco se queda macizo."""
    out, n = [], 0
    for p in placas:
        e = float(p['espesor_real'])
        if p['tipo'] != 'muro' or e < 2.5 * t_mod:
            out.append(p)
            continue
        d = (e - t_mod) / 2.0          # del eje del muro al eje de cada cara
        for lado, s in (('a', 1.0), ('b', -1.0)):
            q = dict(p)
            q['a_mundo'] = p['a_mundo'].copy()
            q['a_mundo'][:3, 3] = p['a_mundo'][:3, 3] + s * d * p['normal']
            q['centro'] = p['centro'] + s * d * p['normal']
            q['espesor_real'] = t_mod
            q['holgura_real'] = e - 2 * t_mod
            q['id'] = p['id'] + lado
            out.append(q)
        n += 1
    for i, p in enumerate(out):
        p['i'] = i
    return out, n


def despiece_estructural(mesh, cfg, con_uniones=True, solo_envolvente=False,
                         piso=None, laminar_macizos=False, grabar_planta=True,
                         bastidor_mm=0.0, muros_huecos=False):
    """Devuelve (piezas_mm, info). Las piezas traen 'poly' en mm de maqueta."""
    # Un modelo con semantica (.ifc) ya trae partidos los cuerpos --cada
    # elemento es uno-- y ademas dice cual es muro y a que planta pertenece.
    # Ahi no hay nada que deducir: se usa lo que trae escrito. Ver ifc.py.
    sem = (mesh.metadata or {}).get('semantica') or {}
    if sem.get('cuerpos'):
        preparado = (mesh, sem['cuerpos'], [])
    else:
        # Soldar el modelo y partirlo en cuerpos es, de lejos, el paso mas caro
        # del pipeline. Lo piden extraer_placas y cuerpos_macizos por igual,
        # sobre los MISMOS cuerpos: se hace una vez aqui y se les pasa hecho.
        dbg.marcar('preparar_cuerpos')
        preparado = preparar_cuerpos(mesh)
    dbg.marcar('extraer_placas')
    obbs = tabla_obb(preparado[1])

    # el espesor del carton llevado a unidades del modelo: lo necesita el camino
    # de superficies para saber que dos caras ya no caben separadas
    placas, descartados = extraer_placas(mesh, t_modelo=cfg.espesor_mm / cfg.a_mm,
                                         preparado=preparado, obbs=obbs,
                                         tipos=sem.get('tipos'),
                                         superficies=not sem.get('cuerpos'))

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
        # Que haga algo con esto: "¿trae cuerpos con espesor?" no le dice nada a
        # quien subio un pabellon de forma libre (Project LoopS, 27 sep 2026).
        if incortables >= 3:
            error = ('las %d placas que salieron quedan mas chicas que %.0f mm a 1:%d. '
                     'Usa una escala mas grande (1:%d en vez de 1:%d).'
                     % (incortables, MIN_LADO_MM, int(cfg.escala),
                        max(1, int(cfg.escala) // 4), int(cfg.escala)))
        else:
            error = ('no se encontraron muros ni losas planas que se puedan cortar. '
                     'Si es una forma libre '
                     '(pabellon, escultura, cubierta curva), cambia a "Terreno / '
                     'topografia": la corta en rebanadas. Si es una casa, revisa que '
                     'los muros tengan espesor y no sean caras sueltas.')
        return [], {'error': error, 'descartados': descartados}
    nombrar(placas)

    # espesor del carton llevado a unidades del modelo
    t_mod = cfg.espesor_mm / cfg.a_mm
    n_huecos = 0
    if muros_huecos:
        placas, n_huecos = partir_en_caras(placas, t_mod)
    contactos, n_uniones = [], 0
    n_recortes, avisos_recorte = 0, []
    avisos_previos = []
    if sem.get('resumen'):
        r = sem['resumen']
        avisos_previos.append(
            'el %s trae los elementos nombrados: %d de ellos son muros, losas y '
            'techos y %s no van en la maqueta (%s). Las plantas salen del '
            'archivo: %s'
            % (sem.get('origen', 'archivo').upper(),
               r.get('n_cuerpos', 0) - sum(1 for v in (sem.get('tipos') or {}).values()
                                           if v == 'macizo'),
               sum(r.get('fuera', {}).values()),
               ', '.join('%d %s' % (n, c.replace('Ifc', '').lower())
                         for c, n in sorted(r.get('fuera', {}).items(),
                                            key=lambda x: -x[1])[:4]) or 'nada',
               ', '.join(r.get('plantas', [])) or 'una'))
    if n_huecos:
        hs = sorted({round(p['holgura_real'] * cfg.a_mm, 1) for p in placas if 'holgura_real' in p})
        avisos_previos.append('%d muros van huecos: dos caras de %.1f mm con %s mm de holgura '
                              'entre ellas para instalaciones'
                              % (n_huecos, cfg.espesor_mm, ' / '.join('%g' % h for h in hs)))
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
        dbg.marcar('uniones')
        contactos = detectar_contactos(placas, t_mod)
        n_uniones = aplicar_uniones(placas, contactos, t_mod,
                                    diente_obj=DIENTE_OBJ_MM / cfg.a_mm,
                                    holgura_modelo=HOLGURA_MM / cfg.a_mm)
    else:
        for p in placas:                  # el tope de "menos de la mitad" mide contra esto
            p['poly_original'] = p['poly']
    # Con dientes o a tope: dos placas que ocupan el mismo volumen no arman. A tope
    # esto vivia dentro del `if` y no corria (FZK 1:200: 3.64%, Engel: 2.71%, 28 sep).
    dbg.marcar('recortar_choques')
    n_recortes, avisos_recorte = recortar_choques(placas, contactos, t_mod)

    # Escaleras, barandales y muebles no son laminas y placas.py los descarta.
    # Para maqueta la salida es laminarlos: rebanadas horizontales que se apilan.
    piezas_macizas, resumen_macizos = [], []
    dbg.marcar('macizos')
    if laminar_macizos:
        piezas_macizas, resumen_macizos = rebanar_solidos(mesh, cfg,
                                                          preparado=preparado, obbs=obbs,
                                                          tipos=sem.get('tipos'))
    else:
        try:
            n_mac = len(cuerpos_macizos(mesh, cfg, preparado=preparado, obbs=obbs,
                                        tipos=sem.get('tipos')))
        except Exception:
            n_mac = 0
        if n_mac:
            # Lo lee el alumno en la pantalla: la bandera de la terminal no le
            # sirve, la casilla si (Kenney, 27 sep 2026: la casa entera era
            # macizo y la hoja salio con UNA pieza).
            avisos_previos.append('el modelo trae %d cuerpo(s) macizo(s) (escaleras, '
                                  'vigas, muebles, columnas) que no son lamina y no van '
                                  'en las hojas. Para sacarlos en rebanadas marca '
                                  '"Laminar escaleras y muebles"' % n_mac)
    for nombre_m, n_reb, alto, motivo in resumen_macizos:
        if n_reb:
            avisos_previos.append('%s va laminado: %d rebanadas, %.0f mm de alto'
                                  % (nombre_m, n_reb, alto))
        else:
            avisos_previos.append('%s se ignora: %s' % (nombre_m, motivo))

    # La planta de los muros grabada sobre su losa: sin esto la hoja es un monton
    # de rectangulos anonimos y el alumno no sabe donde pega cada muro. Va
    # despues de las uniones para que se sume a las marcas de ensamble, y
    # despues de recortar_choques porque ese paso todavia mueve la geometria.
    n_huellas = 0
    if grabar_planta:
        dbg.marcar('planta_grabada')
        try:
            n_huellas = huellas_en_losas(placas, t_min=t_mod)
        except Exception as e:
            avisos_previos.append('no se pudo grabar la planta en las losas: %s' % e)
    asignar_planta(placas, plantas=sem.get('plantas'))

    piezas = []
    for p in placas:
        g = aff.scale(p['poly'], cfg.a_mm, cfg.a_mm, origin=(0, 0))
        minx, miny, _, _ = g.bounds
        g = aff.translate(g, -minx, -miny)
        marca = p.get('marcas')
        # Las marcas de las uniones se dibujan ANTES de recortar_choques, que luego
        # le quita franjas y labios a la placa: sin recortarlas aqui, el grabado se
        # salia de la pieza (Casa Engel: 32 de 143 piezas, hasta 21 mm afuera) y el
        # laser lo pintaba en el desperdicio o en la pieza de al lado. 24 sep 2026.
        if marca is not None and not marca.is_empty:
            marca = _solo_poligonos(marca.intersection(p['poly']))
        if marca is not None:
            marca = aff.translate(aff.scale(marca, cfg.a_mm, cfg.a_mm, origin=(0, 0)),
                                  -minx, -miny)
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
            'planta': p.get('planta', 1),
            'rotulo': 'PLANTA %d' % p.get('planta', 1),
        })

    # Las escaleras y muebles laminados tambien llevan planta, si no todos caen
    # en la primera hoja y se revuelven con la planta baja.
    # Con z_real (medida desde el pie de la escalera, no del edificio) una escalera
    # del tercer piso caia en la planta 1 y sus rebanadas altas en otra zona.
    for pz in piezas_macizas:
        z = float(pz['z_base'])
        k = 0
        for i, nz in enumerate(niveles or []):
            if z >= nz - 0.30:
                k = i
        pz['planta'] = k + 1
        pz['rotulo'] = 'PLANTA %d' % pz['planta']
    piezas.extend(piezas_macizas)

    # El bastidor va al final y en su propia zona: no es de ninguna planta y el
    # alumno lo arma cuando la maqueta ya esta de pie.
    n_bastidor = 0
    if bastidor_mm and bastidor_mm > 0:
        bs = piezas_bastidor(mesh, cfg, alto_mm=float(bastidor_mm))
        piezas.extend(bs)
        n_bastidor = len(bs)

    info = {
        'n_placas': len(placas),
        'n_macizas': len(piezas_macizas),
        'incortables': incortables,
        'niveles': niveles,
        'fuera_por_tope': [p.get('id', '?') for p in fuera_por_tope],
        'n_uniones': n_uniones,
        'n_recortes': n_recortes,
        'n_huellas': n_huellas,
        'n_bastidor': n_bastidor,
        'n_huecos': n_huecos,
        'n_plantas': max([p.get('planta', 1) for p in placas] or [1]),
        'avisos': avisos_previos + _resumir_encimes(avisos_recorte),
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
