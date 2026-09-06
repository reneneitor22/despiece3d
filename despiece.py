# -*- coding: utf-8 -*-
"""
Despiece 3D - motor de corte para maquetas.
Entra un modelo 3D (STL/OBJ/PLY/GLB/DAE), salen piezas planas numeradas
listas para corte laser (DXF) o impresion + corte a mano (SVG).

Modo actual: CURVAS DE NIVEL (apilado de rebanadas horizontales).
"""
import math
import numpy as np
import trimesh
from shapely.geometry import Polygon, MultiPolygon, box
from shapely.ops import unary_union
from rectpack import newPacker, PackingMode, PackingBin


# ---------------------------------------------------------------- parametros
class Config:
    def __init__(self,
                 escala=100,            # 1:100
                 espesor_mm=3.0,        # espesor real de la lamina (carton pluma, MDF)
                 kerf_mm=0.15,          # ancho de quemado del laser
                 hoja=(500.0, 700.0),   # carton ilustracion estandar MX
                 margen_mm=10.0,
                 sep_mm=4.0,
                 unidades_modelo='m',   # m | cm | mm
                 vaciar=True):          # recortar interior tapado
        self.escala = float(escala)
        self.espesor_mm = float(espesor_mm)
        self.kerf_mm = float(kerf_mm)
        self.hoja = (float(hoja[0]), float(hoja[1]))
        self.margen_mm = float(margen_mm)
        self.sep_mm = float(sep_mm)
        self.unidades_modelo = unidades_modelo
        self.vaciar = bool(vaciar)

    @property
    def a_mm(self):
        """Factor: unidad del modelo -> mm de maqueta."""
        base = {'m': 1000.0, 'cm': 10.0, 'mm': 1.0}[self.unidades_modelo]
        return base / self.escala


def cargar_modelo(ruta):
    """Abre el modelo diciendo QUE paso cuando no se puede.

    Un alumno baja lo que sea: la pagina de error del sitio guardada con
    extension .glb, un .zip sin descomprimir, un STL a medio bajar. Lo que no
    puede es toparse con un traceback de trimesh.
    """
    import os
    import trimesh

    if not os.path.exists(ruta):
        raise SystemExit('no existe el archivo: %s' % ruta)
    if os.path.getsize(ruta) < 64:
        raise SystemExit('el archivo esta vacio o se bajo a medias: %s' % ruta)

    cabeza = open(ruta, 'rb').read(400).lstrip()
    if cabeza[:1] == b'<' or b'<!DOCTYPE html' in cabeza or b'<html' in cabeza:
        raise SystemExit('esto no es un modelo 3D, es una pagina web guardada con '
                         'nombre de modelo. Vuelve a bajarlo desde el boton de '
                         'descarga del sitio: %s' % ruta)
    if cabeza[:2] == b'PK':
        raise SystemExit('esto es un ZIP. Descomprimelo y pasa el modelo de adentro: %s'
                         % ruta)

    try:
        m = trimesh.load(ruta, force='mesh')
    except Exception as e:
        raise SystemExit('no se pudo leer %s (%s). Formatos que si lee: '
                         'STL, OBJ, PLY, GLB, DAE.' % (ruta, e))
    if m is None or m.is_empty or len(m.faces) == 0:
        raise SystemExit('el archivo se leyo pero no trae geometria: %s' % ruta)
    return m


# ------------------------------------------------------------- solidificar
def _loops_de_frontera(mesh):
    """Devuelve listas de indices de vertice que forman los bordes abiertos."""
    from trimesh import grouping
    idx = grouping.group_rows(mesh.edges_sorted, require_count=1)
    if len(idx) == 0:
        return []
    aristas = mesh.edges_sorted[idx]

    vecinos = {}
    for a, b in aristas:
        vecinos.setdefault(a, []).append(b)
        vecinos.setdefault(b, []).append(a)

    vistas = set()
    loops = []
    for inicio in vecinos:
        if inicio in vistas:
            continue
        loop = [inicio]
        vistas.add(inicio)
        actual, previo = inicio, None
        while True:
            sig = [v for v in vecinos.get(actual, []) if v != previo and v not in vistas]
            if not sig:
                break
            previo, actual = actual, sig[0]
            vistas.add(actual)
            loop.append(actual)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def solidificar(mesh, holgura=1e-6):
    """
    Una superficie de terreno es una malla ABIERTA: rebanarla da curvas abiertas,
    no poligonos. Le cosemos faldon vertical + fondo plano para volverla solida.
    """
    if mesh.is_watertight:
        return mesh

    loops = _loops_de_frontera(mesh)
    if not loops:
        return mesh

    z_base = float(mesh.bounds[0][2]) - max(holgura, mesh.extents[2] * 0.01)
    V = list(mesh.vertices)
    F = list(mesh.faces)

    for loop in loops:
        # vertices espejo en la base
        mapa = {}
        for vi in loop:
            x, y, _ = mesh.vertices[vi]
            mapa[vi] = len(V)
            V.append([x, y, z_base])

        # faldon
        n = len(loop)
        for i in range(n):
            a, b = loop[i], loop[(i + 1) % n]
            a2, b2 = mapa[a], mapa[b]
            F.append([a, b, b2])
            F.append([a, b2, a2])

        # fondo
        pts = np.array([[mesh.vertices[v][0], mesh.vertices[v][1]] for v in loop])
        try:
            poly = Polygon(pts).buffer(0)
            if poly.is_empty:
                continue
            vv, ff = trimesh.creation.triangulate_polygon(poly, engine='earcut')
            base_off = len(V)
            for p in vv:
                V.append([p[0], p[1], z_base])
            for t in ff:
                F.append([base_off + t[2], base_off + t[1], base_off + t[0]])
        except Exception:
            pass

    solido = trimesh.Trimesh(vertices=np.array(V), faces=np.array(F), process=True)
    trimesh.repair.fix_normals(solido)
    return solido


# ---------------------------------------------------------------- rebanado
def _limpiar(geom, min_area):
    """Tira astillas y devuelve lista de Polygon validos."""
    if geom is None or geom.is_empty:
        return []
    geom = geom.buffer(0)
    partes = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    return [p for p in partes if p.area >= min_area and p.is_valid]


def rebanar(mesh, cfg, min_area_mm2=4.0):
    """
    Corta el solido en planos horizontales cada `espesor_mm` de maqueta.
    Devuelve lista de capas: {n, z_modelo, z_real_m, polys[mm de maqueta]}
    """
    paso_modelo = cfg.espesor_mm / cfg.a_mm       # cuanto sube cada lamina, en unidades del modelo
    z0, z1 = float(mesh.bounds[0][2]), float(mesh.bounds[1][2])
    n_capas = max(1, int(math.floor((z1 - z0) / paso_modelo)))

    alturas = [(i + 0.5) * paso_modelo for i in range(n_capas)]  # relativas a z0
    # section_multiplane recorre el BVH una sola vez: ~10x mas rapido que
    # llamar section() en un for con mallas de cientos de miles de caras.
    secciones = None
    try:
        secciones = mesh.section_multiplane(plane_origin=[0, 0, z0],
                                            plane_normal=[0, 0, 1],
                                            heights=alturas)
    except Exception:
        secciones = None

    capas, capas_malas = [], []
    for i in range(n_capas):
        z = z0 + alturas[i]
        plano = None
        if secciones is not None:
            plano = secciones[i]
        else:
            try:
                sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
                plano = sec.to_planar(to_2D=np.eye(4))[0] if sec is not None else None
            except Exception:
                plano = None
        if plano is None:
            continue

        # Con una malla sucia, la rebanada sale como contorno que se cruza a si
        # mismo y trimesh se rinde ("unable to recover polygon"). Antes eso
        # tumbaba TODO el despiece por una sola capa mala. Se intenta el camino
        # de repuesto y, si tampoco, se pierde esa capa y se avisa.
        try:
            anillos = plano.polygons_full
        except Exception:
            try:
                anillos = [g.buffer(0) for g in plano.polygons_closed if g is not None]
            except Exception:
                anillos = []
            capas_malas.append(i + 1)

        polys = []
        for p in anillos:
            if p is None or p.is_empty:
                continue
            polys.extend(_limpiar(p, min_area_mm2 / (cfg.a_mm ** 2)))
        if not polys:
            continue

        # a milimetros de maqueta, origen en la esquina del modelo
        ox, oy = float(mesh.bounds[0][0]), float(mesh.bounds[0][1])
        polys_mm = []
        for p in polys:
            pmm = _escalar_poly(p, cfg.a_mm, ox, oy)
            if cfg.kerf_mm:
                pmm = pmm.buffer(cfg.kerf_mm / 2.0, join_style=2)
                pmm = pmm if pmm.geom_type == 'Polygon' else max(pmm.geoms, key=lambda g: g.area)
            polys_mm.append(pmm)

        capas.append({
            'n': i + 1,
            'z_modelo': z,
            'z_real': (z - z0),
            'polys': polys_mm,
        })
    if capas_malas:
        print('  OJO: la malla esta sucia en %d capa(s) (%s...): el contorno se cruza '
              'a si mismo y se reconstruyo como se pudo'
              % (len(capas_malas), ', '.join(str(x) for x in capas_malas[:5])))
    return capas


def _escalar_poly(p, f, ox, oy):
    def tx(coords):
        return [((x - ox) * f, (y - oy) * f) for x, y in coords]
    return Polygon(tx(p.exterior.coords), [tx(r.coords) for r in p.interiors])


# ---------------------------------------------------------------- piezas
def _sufijo_alfa(j):
    """0->a, 25->z, 26->aa, 27->ab...  Nunca se sale del alfabeto (bug: chr(97+j)
    pasaba a '{','|','}' con capas de mas de 26 islas, tipico en terreno real)."""
    s = ''
    j += 1
    while j:
        j, r = divmod(j - 1, 26)
        s = chr(97 + r) + s
    return s


def _id_pieza(n_capa, j, n_polys):
    return '%02d%s' % (n_capa, _sufijo_alfa(j) if n_polys > 1 else '')


def armar_piezas(capas, vaciar=True, ceja_mm=7.0, min_hueco_mm2=900.0):
    """Cada poligono de cada capa es una pieza. Guarda la silueta de la capa
    de ARRIBA para grabarla encima -> asi el alumno sabe donde apilar."""
    piezas = []
    for i, capa in enumerate(capas):
        arriba = unary_union(capas[i + 1]['polys']) if i + 1 < len(capas) else None
        for j, poly in enumerate(capa['polys']):
            guia = None
            if arriba is not None:
                try:
                    g = arriba.intersection(poly.buffer(0.5))
                    if not g.is_empty and g.area > 1.0:
                        guia = g
                except Exception:
                    pass
            # Vaciado: lo que queda tapado por la capa de arriba no se ve.
            # Se recorta dejando una ceja para pegar -> menos material y hueco
            # donde el acomodo mete piezas chicas.
            recorte = None
            if vaciar and guia is not None:
                try:
                    h = guia.buffer(-ceja_mm, join_style=2)
                    if not h.is_empty:
                        h = h if h.geom_type == 'Polygon' else max(h.geoms, key=lambda g: g.area)
                        if h.area >= min_hueco_mm2:
                            resto = poly.difference(h)
                            if resto.geom_type == 'Polygon' and not resto.is_empty:
                                recorte, poly = h, resto
                except Exception:
                    pass

            # al vaciar, la huella de la capa de arriba cruza el hueco recien
            # abierto: ese tramo se grabaria sobre el aire (y sobre la cama del
            # laser). La huella solo vale donde queda material.
            if guia is not None:
                try:
                    guia = guia.intersection(poly)
                    if guia.is_empty:
                        guia = None
                except Exception:
                    pass

            piezas.append({
                'id': _id_pieza(capa['n'], j, len(capa['polys'])),
                'capa': capa['n'],
                'z_real': capa['z_real'],
                'poly': poly,
                'guia': guia,
                'vaciada': recorte is not None,
            })
    return piezas


# ---------------------------------------------------------------- acomodo
# Nesting por geometria real (mascara raster + correlacion FFT), no por caja
# envolvente: las curvas de nivel son blobs y la caja desperdicia ~40%.
import shapely.affinity as aff
from PIL import Image, ImageDraw
from scipy.signal import fftconvolve
from scipy.ndimage import binary_dilation

ROTACIONES = (0, 45, 90, 135, 180, 225, 270, 315)
ROTACIONES_ORTO = (0, 90, 180, 270)


def _mascara(geom, res):
    """Rasteriza a bool. Devuelve (arr, minx, miny) del bbox de la geometria."""
    minx, miny, maxx, maxy = geom.bounds
    w = int(np.ceil((maxx - minx) / res)) + 2
    h = int(np.ceil((maxy - miny) / res)) + 2
    img = Image.new('1', (w, h), 0)
    dr = ImageDraw.Draw(img)
    partes = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
    for p in partes:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        dr.polygon([((x - minx) / res, (y - miny) / res) for x, y in p.exterior.coords], fill=1)
        for r in p.interiors:
            dr.polygon([((x - minx) / res, (y - miny) / res) for x, y in r.coords], fill=0)
    arr = binary_dilation(np.array(img, dtype=bool), iterations=1)
    return arr, minx, miny


def _cabe(poly, cfg, res, rotaciones):
    """True si la pieza entra en la hoja util con alguna de las rotaciones."""
    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    holgura = cfg.sep_mm + 2 * res
    for ang in (rotaciones or ROTACIONES):
        g = aff.rotate(poly, ang, origin='centroid') if ang else poly
        minx, miny, maxx, maxy = g.bounds
        if (maxx - minx) + holgura <= W and (maxy - miny) + holgura <= H:
            return True
    return False


def partir_grandes(piezas, cfg, res=2.0, rotaciones=None, max_trozos=64):
    """Una pieza mas grande que la hoja no se puede cortar: hasta ahora se tiraba
    en silencio y la maqueta salia sin base (el terreno de Marte perdia 23 de 235
    piezas, entre ellas TODAS las capas de abajo).

    Se parte con una reja en trozos que si caben. Los trozos van a tope: en un
    terreno cada capa se pega plana sobre la de abajo, que es la que amarra la
    junta, asi que no necesitan diente. Se numeran <id>.1, <id>.2 ... y quedan
    marcados con 'partida_de' para que la guia diga de donde salio cada uno.
    """
    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    holgura = cfg.sep_mm + 2 * res
    util_w, util_h = W - holgura, H - holgura
    if util_w <= 0 or util_h <= 0:
        return piezas, []

    salida, partidas = [], []
    for pz in piezas:
        poly = pz['poly']
        if poly.is_empty or _cabe(poly, cfg, res, rotaciones):
            salida.append(pz)
            continue

        # la reja se traza sobre la orientacion que menos cortes necesita
        mejor = None
        for ang in (rotaciones or ROTACIONES):
            g = aff.rotate(poly, ang, origin='centroid') if ang else poly
            minx, miny, maxx, maxy = g.bounds
            nx = int(math.ceil((maxx - minx) / util_w))
            ny = int(math.ceil((maxy - miny) / util_h))
            if nx * ny < 1:
                continue
            if mejor is None or nx * ny < mejor[0]:
                mejor = (nx * ny, ang, g, nx, ny)
        if mejor is None or mejor[0] > max_trozos:
            salida.append(pz)                     # ni partiendola cabe: que avise acomodar
            continue

        _, ang, g, nx, ny = mejor
        minx, miny, maxx, maxy = g.bounds
        ancho = (maxx - minx) / nx
        alto = (maxy - miny) / ny
        guia = pz.get('guia')
        if guia is not None and ang:
            guia = aff.rotate(guia, ang, origin=poly.centroid)

        trozos = []
        for iy in range(ny):
            for ix in range(nx):
                celda = box(minx + ix * ancho - 1e-6, miny + iy * alto - 1e-6,
                            minx + (ix + 1) * ancho + 1e-6, miny + (iy + 1) * alto + 1e-6)
                try:
                    corte = g.intersection(celda)
                except Exception:
                    continue
                if corte.is_empty:
                    continue
                for parte in (corte.geoms if corte.geom_type.startswith('Multi') else [corte]):
                    if parte.geom_type != 'Polygon' or parte.area < 1.0:
                        continue
                    trozos.append((parte, celda))

        if len(trozos) < 2:
            salida.append(pz)
            continue

        for k, (parte, celda) in enumerate(trozos, 1):
            hijo = dict(pz)
            hijo['id'] = '%s.%d' % (pz['id'], k)
            hijo['poly'] = parte
            hijo['partida_de'] = pz['id']
            hijo['trozo'] = (k, len(trozos))
            if guia is not None:
                # OJO: se recorta contra el TROZO, no contra la celda de la reja.
                # Contra la celda, la huella grabada se sale de la pieza (y de la
                # hoja) y ademas los bordes de la reja quedan grabados como lineas
                # que no significan nada.
                try:
                    gg = guia.intersection(parte)
                    hijo['guia'] = None if gg.is_empty else gg
                except Exception:
                    hijo['guia'] = None
            salida.append(hijo)
        partidas.append((pz['id'], len(trozos)))

    return salida, partidas


def acomodar(piezas, cfg, res=2.0, rotaciones=None):
    """Bottom-left-fill sobre malla. Devuelve (hojas, ids_que_no_caben).
    Cada colocada trae ya la geometria final: {'pieza','geo','guia'}."""
    W = cfg.hoja[0] - 2 * cfg.margen_mm
    H = cfg.hoja[1] - 2 * cfg.margen_mm
    nw, nh = int(W / res), int(H / res)

    orden = sorted(range(len(piezas)), key=lambda k: -piezas[k]['poly'].area)
    hojas, ocupacion = [], []
    grandes = []

    for k in orden:
        pz = piezas[k]
        # variantes rotadas, con su holgura de separacion ya incorporada
        variantes = []
        for ang in (rotaciones or ROTACIONES):
            g = aff.rotate(pz['poly'], ang, origin='centroid') if ang else pz['poly']
            buf = g.buffer(cfg.sep_mm / 2.0 + res * 0.5, join_style=2)
            m, bx, by = _mascara(buf, res)
            if m.shape[0] <= nh and m.shape[1] <= nw:
                variantes.append((ang, g, buf, m, bx, by))
        if not variantes:
            grandes.append(pz['id'])
            continue

        colocada = False
        for idx_hoja in range(len(hojas) + 1):
            if idx_hoja == len(hojas):
                hojas.append([])
                ocupacion.append(np.zeros((nh, nw), dtype=bool))
            occ = ocupacion[idx_hoja]
            mejor = None
            for ang, g, buf, m, bx, by in variantes:
                libre = fftconvolve(occ.astype(np.float32),
                                    m[::-1, ::-1].astype(np.float32), mode='valid') < 0.5
                if not libre.any():
                    continue
                filas, cols = np.nonzero(libre)
                j = int(np.lexsort((cols, filas))[0])          # el mas abajo, luego el mas a la izq
                fila, col = int(filas[j]), int(cols[j])
                puntaje = (fila, col)
                if mejor is None or puntaje < mejor[0]:
                    mejor = (puntaje, ang, g, buf, bx, by, fila, col, m)
            if mejor is None:
                if not hojas[idx_hoja]:      # hoja recien creada y aun asi no cabe
                    hojas.pop(); ocupacion.pop()
                    grandes.append(pz['id'])
                    colocada = True
                continue

            _, ang, g, buf, bx, by, fila, col, m = mejor
            dx = cfg.margen_mm + col * res - bx
            dy = cfg.margen_mm + fila * res - by
            geo = aff.translate(g, dx, dy)
            guia = None
            if pz['guia'] is not None:
                gg = aff.rotate(pz['guia'], ang, origin=pz['poly'].centroid) if ang else pz['guia']
                guia = aff.translate(gg, dx, dy)
            hojas[idx_hoja].append({'pieza': pz, 'geo': geo, 'guia': guia, 'ang': ang})
            occ[fila:fila + m.shape[0], col:col + m.shape[1]] |= m
            colocada = True
            break
        if not colocada:
            grandes.append(pz['id'])

    hojas = [h for h in hojas if h]
    for i, h in enumerate(hojas):
        for col in h:
            col['pieza']['hoja'] = i + 1
    return hojas, grandes
