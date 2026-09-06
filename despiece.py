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
from shapely.geometry import Polygon, MultiPolygon
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

    capas = []
    for i in range(n_capas):
        # cortamos a la mitad de la lamina: la pieza representa esa franja
        z = z0 + (i + 0.5) * paso_modelo
        try:
            sec = mesh.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1])
        except Exception:
            sec = None
        if sec is None:
            continue
        try:
            plano, _ = sec.to_planar(to_2D=np.eye(4))
        except Exception:
            continue

        polys = []
        for p in plano.polygons_full:
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
