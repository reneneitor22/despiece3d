# -*- coding: utf-8 -*-
"""Salidas: DXF por hoja (laser) + SVG/HTML imprimible (corte a mano) + guia de armado."""
import html
import os
import re
import ezdxf
from shapely.geometry import LineString

# Convencion de capas por operacion. Cada taller tiene la suya --el nombre y el
# color son lo unico que la cabina de corte mira para saber que hacer con cada
# linea-- asi que esto es solo el arranque: se puede cambiar desde la pantalla.
OPS_DEFAULT = {
    'corte':   {'capa': 'CORTE',   'rgb': (255, 0, 0)},
    'grabado': {'capa': 'GRABADO', 'rgb': (0, 0, 255)},
}
# Marco, tabla, rotulos y notas: se ven al abrir el plano y NO se cortan.
# Defpoints y no un nombre propio porque es la capa que AutoCAD nunca imprime y
# que todo taller ya conoce: asi lo entrega un arquitecto a mano. Se puede
# cambiar desde la pantalla.
CAPA_HOJA = 'Defpoints'

# Compatibilidad con lo que ya usaba el resto del proyecto.
CAPA_CORTE = OPS_DEFAULT['corte']['capa']
CAPA_GRABADO = OPS_DEFAULT['grabado']['capa']


def _aci_cercano(rgb):
    """Indice de color de AutoCAD (1-255) mas parecido a ese RGB.

    Se pone el ACI *y* el color verdadero: las cabinas viejas (RDWorks) mapean
    la operacion por indice y las nuevas (LightBurn) por RGB. Con los dos
    puestos el archivo cae bien en las dos.
    """
    from ezdxf.colors import DXF_DEFAULT_COLORS
    r, g, b = rgb
    mejor, dist = 7, None
    for i in range(1, 256):
        c = DXF_DEFAULT_COLORS[i]
        d = ((c >> 16 & 255) - r) ** 2 + ((c >> 8 & 255) - g) ** 2 + ((c & 255) - b) ** 2
        if dist is None or d < dist:
            mejor, dist = i, d
    return mejor


_NOMBRES_COLOR = {
    'rojo': (255, 0, 0), 'verde': (0, 160, 0), 'azul': (0, 0, 255),
    'amarillo': (255, 255, 0), 'cian': (0, 255, 255), 'magenta': (255, 0, 255),
    'naranja': (255, 140, 0), 'morado': (128, 0, 200), 'cafe': (140, 80, 20),
    'gris': (128, 128, 128), 'negro': (0, 0, 0), 'blanco': (255, 255, 255),
}


def nombre_color(rgb):
    """El color dicho como lo diria el operador: "azul graba, rojo corta"."""
    r, g, b = rgb
    return min(_NOMBRES_COLOR,
               key=lambda k: ((_NOMBRES_COLOR[k][0] - r) ** 2 +
                              (_NOMBRES_COLOR[k][1] - g) ** 2 +
                              (_NOMBRES_COLOR[k][2] - b) ** 2))


def _ops(ops):
    """Completa lo que falte con la convencion de fabrica."""
    salida = {k: dict(v) for k, v in OPS_DEFAULT.items()}
    for k, v in (ops or {}).items():
        if k in salida and v:
            if v.get('capa'):
                salida[k]['capa'] = str(v['capa']).strip()[:60] or salida[k]['capa']
            if v.get('rgb'):
                salida[k]['rgb'] = tuple(int(max(0, min(255, c))) for c in v['rgb'])
    return salida


def _anillos(geom):
    """Devuelve [(coords_exterior, [coords_interiores])] de Polygon o MultiPolygon."""
    partes = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
    out = []
    for p in partes:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        out.append((list(p.exterior.coords), [list(r.coords) for r in p.interiors]))
    return out


# Grabado encimado sobre corte: el laser pasa dos veces y el canto sale mas
# quemado (David, Alonso, 4 oct 2026: hasta 50% del grabado de una hoja caia
# sobre el borde, el muro del perimetro marcado justo en el canto de la losa).
# Ahi la marca no dice nada --el canto ya ensena donde va el muro--, se quita.
TOL_GRABADO_SOBRE_CORTE_MM = 0.3
GRABADO_MIN_MM = 0.5


def _trazos_grabado(col):
    """Lineas de grabado de una colocada, sin lo que cae sobre su corte.
    Devuelve [(coords, cerrado)]."""
    guia = col.get('guia')
    if guia is None or guia.is_empty:
        return []
    filo = col['geo'].boundary.buffer(TOL_GRABADO_SOBRE_CORTE_MM)
    out = []
    for ext, ints in _anillos(guia):
        for anillo in [ext] + ints:
            linea = LineString(anillo)
            if not linea.intersects(filo):
                out.append((anillo, True))
                continue
            resto = linea.difference(filo)
            for tramo in getattr(resto, 'geoms', [resto]):
                if tramo.geom_type == 'LineString' and tramo.length >= GRABADO_MIN_MM:
                    out.append((list(tramo.coords), False))
    return out


def _anillos_de_corte(colocadas):
    """Anillos de CORTE, pieza por pieza, en el orden en que se deben cortar.

    Cortar el contorno suelta la pieza: lo que se corte despues dentro de ella
    (ventanas, ranuras) sale movido. Por eso cada pieza trae sus huecos primero
    y su contorno al final. Y como el acomodo usa los huecos como espacio libre
    (en Revit ARC caen 3 piezas dentro de la ventana de otra), la pieza que vive
    dentro de un hueco va antes que la que la rodea: se ordena por cuantas
    piezas la rodean, de mas a menos. `sorted` es estable, asi que lo demas
    sigue en el orden del acomodo y la cabeza no brinca de mas.
    """
    from shapely.geometry import Polygon
    from shapely.strtree import STRtree
    piezas = [_anillos(c['geo']) for c in colocadas]
    llenos, duenos = [], []
    for i, partes in enumerate(piezas):
        for ext, _ in partes:
            llenos.append(Polygon(ext))
            duenos.append(i)
    if not llenos:
        return []
    arbol = STRtree(llenos)

    def rodean(i):
        pt = colocadas[i]['geo'].representative_point()
        return len({duenos[j] for j in arbol.query(pt)
                    if duenos[j] != i and llenos[j].contains(pt)})

    orden = sorted(range(len(piezas)), key=lambda i: -rodean(i))
    return [[r for _, ints in piezas[i] for r in ints] + [ext for ext, _ in piezas[i]]
            for i in orden]


# Ancho de una letra en alturas: la M de Helvetica mide 0.83, un digito 0.56. Se
# toma de mas para que el numero de pieza nunca se salga de su pieza.
ANCHO_LETRA = 0.75


# Del numero de pieza en la esquina al canto: menos y el laser lo quema junto con
# el corte en carton.
MARGEN_ESQUINA_MM = 1.5


def _rotulo(geo, texto, guia=None):
    """(x, y, alto, giro) del numero de pieza: en la ESQUINA de la pieza mas
    cercana donde la letra quepa entera sin pisar el corte ni la huella grabada
    (David, el del corte, 6 oct 2026: en el centro se ve en la maqueta armada).
    Si no cabe en ninguna esquina, el punto MAS ADENTRO de la pieza (polylabel).

    Antes el alto salia del ancho de la caja y nadie revisaba que cupiera: un M12
    de 6 mm sobre una tira de 3.1 mm (FZK) y 65 de 291 fuera de su pieza (Merida).
    DXF, SVG y PDF lo toman de aqui para decir lo mismo."""
    from shapely.geometry import box as _box
    from shapely.ops import polylabel
    p = geo if geo.geom_type == 'Polygon' else max(geo.geoms, key=lambda g: g.area)
    c = polylabel(p, tolerance=0.1)
    x, y = c.x, c.y
    dentro = p.buffer(-0.3)                      # que no pise el corte
    tope = max(2.5, min(6.0, (p.bounds[2] - p.bounds[0]) / 8.0))
    n = max(1, len(str(texto)))
    tamanos = [a for a in (6.0, 5.0, 4.0, 3.5, 3.0, 2.5, 2.0, 1.5) if a <= tope]

    # Esquina: de cada esquina de la caja de la pieza se camina hacia el centro
    # hasta que la letra cabe. Gana la que menos camina.
    orilla = p.buffer(-MARGEN_ESQUINA_MM)
    huella = guia.buffer(0.5) if guia is not None and not guia.is_empty else None
    x0, y0, x1, y1 = p.bounds
    for alto in tamanos:
        w = ANCHO_LETRA * alto * n
        mejor = None
        for giro, (bw, bh) in ((0, (w, alto)), (90, (alto, w))):
            for ex, ey in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                # el centro de la caja arranca a media caja de la esquina
                sx = ex + (bw / 2 if ex == x0 else -bw / 2)
                sy = ey + (bh / 2 if ey == y0 else -bh / 2)
                for i in range(41):
                    t = i / 40.0
                    if mejor is not None and t >= mejor[0]:
                        break
                    px, py = sx + (x - sx) * t, sy + (y - sy) * t
                    caja = _box(px - bw / 2, py - bh / 2, px + bw / 2, py + bh / 2)
                    if orilla.contains(caja) and (huella is None or not huella.intersects(caja)):
                        mejor = (t, px, py, giro)
                        break
        if mejor is not None:
            return mejor[1], mejor[2], alto, mejor[3]

    for alto in tamanos:
        w = ANCHO_LETRA * alto * n
        for giro, (bw, bh) in ((0, (w, alto)), (90, (alto, w))):
            if dentro.contains(_box(x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2)):
                return x, y, alto, giro
    return x, y, 1.5, 0 if p.bounds[2] - p.bounds[0] >= p.bounds[3] - p.bounds[1] else 90


def _xml(txt):
    """Un nombre de proyecto con & o < rompe el SVG en silencio."""
    return (str(txt).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def zonas_de(colocadas):
    """Las cajas de cada zona de la hoja: {clave: (x0, y0, x1, y1)}.

    Una zona es un grupo que no se mezcla con los demas --hoy, una planta--.
    Enmarcarla y rotularla es lo que hace legible la hoja: el que arma ve
    "PLANTA 2" y sabe que todo lo de adentro es de ese nivel.
    """
    cajas = {}
    for col in colocadas:
        z = col.get('zona')
        if z is None:
            continue
        b = col['geo'].bounds
        if col.get('guia') is not None and not col['guia'].is_empty:
            gb = col['guia'].bounds
            b = (min(b[0], gb[0]), min(b[1], gb[1]),
                 max(b[2], gb[2]), max(b[3], gb[3]))
        a = cajas.get(z)
        cajas[z] = b if a is None else (min(a[0], b[0]), min(a[1], b[1]),
                                        max(a[2], b[2]), max(a[3], b[3]))
    return cajas


# ------------------------------------------------------------- tabla de corte
def _tabla_corte(msp, cfg, ficha, ops, ancho_hoja, capa=CAPA_HOJA):
    """Cajetin con los datos del corte, DEBAJO de la hoja y fuera del marco.

    Va afuera a proposito: adentro se comeria area de corte, y como cae en la
    capa HOJA --que la cabina no tiene asignada a ninguna operacion-- se ve en
    AutoCAD pero no se corta ni se graba. Devuelve el alto que ocupo.
    """
    from ezdxf.colors import rgb2int

    ancho = max(170.0, min(ancho_hoja, 260.0))
    col = 52.0                      # ancho de la columna de etiquetas
    fila = 8.0
    y = -14.0                       # arranca debajo de la hoja
    alto_tit = 10.0

    filas = list(ficha) + [
        (op['capa'], '%s  ·  RGB %d,%d,%d  ·  color %d de AutoCAD'
         % (nombre.upper(), op['rgb'][0], op['rgb'][1], op['rgb'][2],
            _aci_cercano(op['rgb'])))
        for nombre, op in (('corte', ops['corte']), ('grabado', ops['grabado']))
    ]
    alto = alto_tit + fila * len(filas)
    y0 = y - alto

    def linea(p1, p2):
        msp.add_line(p1, p2, dxfattribs={'layer': capa})

    def texto(txt, x, yc, h=3.0, negrita=False):
        t = msp.add_text(str(txt), dxfattribs={'layer': capa, 'height': h})
        t.set_placement((x, yc), align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)

    msp.add_lwpolyline([(0, y0), (ancho, y0), (ancho, y), (0, y)], close=True,
                       dxfattribs={'layer': capa})
    linea((0, y - alto_tit), (ancho, y - alto_tit))
    texto('TABLA DE CORTE', 4, y - alto_tit / 2.0, 4.2)

    yy = y - alto_tit
    for i, (etiqueta, valor) in enumerate(filas):
        yb = yy - fila
        if i:
            linea((0, yy), (ancho, yy))
        texto(etiqueta, 4, yy - fila / 2.0)
        texto(valor, col + 4, yy - fila / 2.0)
        # Muestra de color de la operacion: el color va en la entidad, no en la
        # capa, para que se vea rojo/azul/verde sin salirse de la capa HOJA.
        clave = etiqueta.strip().upper()
        for op in ops.values():
            if op['capa'].upper() == clave:
                msp.add_lwpolyline(
                    [(col - 12, yb + 2.0), (col - 2, yb + 2.0),
                     (col - 2, yy - 2.0), (col - 12, yy - 2.0)], close=True,
                    dxfattribs={'layer': capa,
                                'true_color': rgb2int(op['rgb'])})
                break
        yy = yb
    linea((col, y - alto_tit), (col, y0))

    msp.add_text('El marco y esta tabla estan en la capa %s: no se cortan ni se graban.' % capa,
                 dxfattribs={'layer': capa, 'height': 2.6}
                 ).set_placement((0, y0 - 5),
                                 align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)
    return alto + 20.0


def _hueco_para_texto(col, ancho, alto, paso=3.0):
    """Un punto DENTRO de la pieza donde cabe un bloque de ancho x alto.

    Devuelve (x, y) de la esquina inferior izquierda, o None. Se pide que el
    bloque quepa entero en la pieza y que no pise lo ya grabado: el cajetin no
    sirve de nada encima de la planta.
    """
    from shapely.geometry import box as _box
    g = col['geo']
    dentro = g.buffer(-1.5)
    if dentro.is_empty:
        return None
    if dentro.geom_type.startswith('Multi'):
        dentro = max(dentro.geoms, key=lambda x: x.area)
    x0, y0, x1, y1 = dentro.bounds
    if x1 - x0 < ancho or y1 - y0 < alto:
        return None
    guia = col.get('guia')
    # ni encima del numero de pieza: grabado sobre grabado no se lee ninguno
    nx, ny, nh, giro = _rotulo(g, col['pieza']['id'], guia)
    nw = ANCHO_LETRA * nh * len(str(col['pieza']['id']))
    nw, nh = (nw, nh) if giro == 0 else (nh, nw)
    numero = _box(nx - nw / 2 - 1, ny - nh / 2 - 1, nx + nw / 2 + 1, ny + nh / 2 + 1)
    y = y0
    while y + alto <= y1:
        x = x0
        while x + ancho <= x1:
            caja = _box(x, y, x + ancho, y + alto)
            if (dentro.contains(caja) and not numero.intersects(caja)
                    and (guia is None or guia.is_empty or not guia.intersects(caja))):
                return (x, y)
            x += paso
        y += paso
    return None


def _cajetin(msp, colocadas, lineas, capa, alto=3.2):
    """Graba el nombre del proyecto en una PIEZA, no en el margen de la hoja.

    Asi lo hace el arquitecto: el cajetin queda en la maqueta armada y no se va
    a la basura con el recorte. Se busca la pieza mas grande donde quepa sin
    pisar lo ya grabado; si no cabe en ninguna, no se pone.
    """
    lineas = [str(x) for x in lineas if str(x).strip()]
    if not lineas:
        return None
    ancho = ANCHO_LETRA * alto * max(len(x) for x in lineas)
    total = alto * 1.7 * len(lineas)
    for col in sorted(colocadas, key=lambda c: (c['pieza'].get('tipo') != 'losa',
                                                -c['geo'].area)):
        p = _hueco_para_texto(col, ancho, total)
        if p is None:
            continue
        x, y = p
        for i, txt in enumerate(lineas):
            yy = y + total - alto * 1.7 * (i + 0.5)
            msp.add_text(txt, dxfattribs={'layer': capa, 'height': alto}
                         ).set_placement((x, yy),
                                         align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)
        return (col['pieza']['id'], x, y, ancho, total)
    return None


# ------------------------------------------------------------------- DXF
def hoja_a_dxf(colocadas, cfg, ruta, titulo, ops=None, ficha=None, marco=True,
               rotulo_zona='%s', capa_hoja=None, notas=None, cajetin=None):
    """Una hoja lista para mandar al taller.

    `ops`   convencion de capas por operacion (corte / grabado).
    `ficha` renglones (etiqueta, valor) de la tabla de corte; None = sin tabla.
    `marco` dibuja el rectangulo del tamaño de lamina elegido.
    `rotulo_zona` formato del nombre de cada zona (las piezas traen 'zona').
    `capa_hoja` capa de lo que no se corta (marco, tabla, rotulos, notas).
    `notas`   renglones sueltos para el operador, arriba del marco.
    `cajetin` renglones grabados DENTRO de una pieza (proyecto, escala, alumno).
    """
    from ezdxf.colors import rgb2int

    ops = _ops(ops)
    # R2004 y no R2010, y no es cosmetico: de R2007 en adelante el DXF guarda
    # los nombres en UTF-8, y `dwgwrite` (LibreDWG 0.14) los relee como si
    # fueran de dos bytes, se topa con el NUL y corta cada nombre en su primera
    # letra. Asi es como CORTE/GRABADO/HOJA quedaban en C/G/H y el bloque
    # *Model_Space en *, que es lo que dejaba el DWG abriendo en negro. Medido:
    # el mismo dibujo entrado como R2000 o R2004 sale con los nombres enteros y
    # las 129 entidades en el espacio modelo. R2004 es la version mas vieja que
    # todavia guarda color verdadero (RGB) en la capa, que es lo que necesitan
    # las cabinas nuevas.
    doc = ezdxf.new('R2004', setup=True)
    doc.header['$INSUNITS'] = 4          # milimetros
    # Todo texto sale en Standard, y Standard trae txt.shx de AutoCAD, que
    # LightBurn y los programas de laser no tienen: el numero de pieza no salia
    # (David, el del corte, 6 oct 2026). Arial la tiene cualquier maquina.
    doc.styles.get('Standard').dxf.font = 'arial.ttf'
    msp = doc.modelspace()

    for op in ops.values():
        capa = doc.layers.add(op['capa'], color=_aci_cercano(op['rgb']))
        capa.rgb = op['rgb']
    # `setup=True` ya trae Defpoints en la plantilla: si se pide esa, se toma la
    # que hay en vez de reventar por nombre repetido. Y se le apaga el plot, que
    # es lo que hace que AutoCAD nunca la imprima.
    L_HOJA = (capa_hoja or CAPA_HOJA).strip()[:60] or CAPA_HOJA
    if L_HOJA in doc.layers:
        capa_h = doc.layers.get(L_HOJA)
    else:
        capa_h = doc.layers.add(L_HOJA, color=8)
    capa_h.dxf.plot = 0

    L_CORTE = ops['corte']['capa']
    L_GRAB = ops['grabado']['capa']

    W, H = cfg.hoja
    if marco:
        msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True,
                           dxfattribs={'layer': L_HOJA})

    # Orden de entidades = orden en que corta la maquina cuando el operador no
    # optimiza: primero todo lo que se graba o marca (la pieza sigue fija en la
    # lamina) y al final el corte, cada pieza con sus huecos antes que su
    # contorno. Al reves, el grabado y las ranuras salen movidos.
    for col in colocadas:
        pz = col['pieza']
        for trazo, cerrado in _trazos_grabado(col):
            msp.add_lwpolyline(trazo, close=cerrado, dxfattribs={'layer': L_GRAB})

        cx, cy, alto, giro = _rotulo(col['geo'], pz['id'], col.get('guia'))
        # El numero de pieza va en GRABADO, azul, con las huellas: asi lo pidio
        # David, el del corte (6 oct 2026). En una capa aparte (MARCADO, verde)
        # su programa no lo grababa en la misma pasada.
        msp.add_text(pz['id'],
                     dxfattribs={'layer': L_GRAB, 'height': alto, 'rotation': giro}
                     ).set_placement((cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    # Marco y rotulo de cada zona. Van en la capa HOJA: se ven al abrir el plano
    # pero la cabina no los corta.
    for clave, (x0, y0, x1, y1) in sorted(zonas_de(colocadas).items(),
                                          key=lambda kv: str(kv[0])):
        m = 3.0
        msp.add_lwpolyline([(x0 - m, y0 - m), (x1 + m, y0 - m),
                            (x1 + m, y1 + m), (x0 - m, y1 + m)], close=True,
                           dxfattribs={'layer': L_HOJA})
        msp.add_text(rotulo_zona % clave,
                     dxfattribs={'layer': L_HOJA, 'height': 5}
                     ).set_placement((x0 - m, y1 + m + 2))

    msp.add_text(titulo, dxfattribs={'layer': L_HOJA, 'height': 6}
                 ).set_placement((cfg.margen_mm, H - cfg.margen_mm + 1))

    # Las notas del operador van ARRIBA del marco, en texto pelado y en la capa
    # que no se corta. Es lo primero que lee quien opera la maquina; la tabla de
    # corte, abajo, es para el que cobra.
    for i, nota in enumerate(notas or []):
        msp.add_text(str(nota), dxfattribs={'layer': L_HOJA, 'height': 4.5}
                     ).set_placement((0, H + 6 + 7.0 * (len(notas) - 1 - i)))

    if cajetin:
        _cajetin(msp, colocadas, cajetin, L_GRAB)

    for anillos in _anillos_de_corte(colocadas):
        for r in anillos:
            msp.add_lwpolyline(r, close=True, dxfattribs={'layer': L_CORTE})

    bajo =_tabla_corte(msp, cfg, ficha, ops, W, capa=L_HOJA) if ficha else 0.0

    # Sin esto el archivo abre en un zoom cualquiera y el operador puede ver una
    # pantalla vacia hasta que se le ocurre hacer Zoom Extents. Lo que enmarca
    # la vista al abrir es el viewport *Active, no el encabezado: escribirle
    # $EXTMIN/$EXTMAX a `doc.header` no sirve de nada porque ezdxf los reescribe
    # al guardar --los deja en el centinela 1e20/-1e20 de "dibujo vacio"-- ya
    # que quien lleva la cuenta de la extension real es el programa que abre el
    # archivo, y AutoCAD la recalcula sola en el primer regen. Los limites de
    # hoja si se pegan, pero puestos en el layout y no en el encabezado.
    lay = doc.layouts.get('Model')
    lay.dxf.limmin = (0.0, -bajo)
    lay.dxf.limmax = (W, H)
    doc.set_modelspace_vport(height=(H + bajo) * 1.06,
                             center=(W / 2.0, (H - bajo) / 2.0))
    doc.saveas(ruta)


# ------------------------------------------------------------------- SVG
def _arial_pdf():
    """Arial incrustada en el PDF: Helvetica no va adentro del archivo y el
    programa del laser la cambia por la que tenga. Sin Arial, Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for ruta in ('/System/Library/Fonts/Supplemental/Arial.ttf',
                 '/Library/Fonts/Arial.ttf', 'C:/Windows/Fonts/arial.ttf'):
        if os.path.exists(ruta):
            if 'Arial' not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont('Arial', ruta))
            return 'Arial'
    return 'Helvetica'


def hoja_a_svg(colocadas, cfg, titulo, rotulo_zona='%s', notas=None,
               cajetin=None):
    W, H = cfg.hoja
    # La vista previa tiene que enseñar lo MISMO que el DXF, notas incluidas, si
    # no el alumno manda a cortar algo que no vio. Las notas van arriba del
    # marco, asi que el lienzo crece hacia arriba.
    notas = [str(x) for x in (notas or []) if str(x).strip()]
    arriba = (6.0 + 7.0 * len(notas)) if notas else 0.0
    Ht = H + arriba
    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
         'viewBox="0 0 %.3f %.3f">' % (W, Ht, W, Ht),
         '<rect x="0" y="%.3f" width="%.3f" height="%.3f" fill="#fff" stroke="#bbb" '
         'stroke-width="0.3"/>' % (arriba, W, H),
         '<g transform="translate(0,%.3f) scale(1,-1)">' % Ht]

    def path_de(anillos, color, grosor, punteado=False, abiertos=()):
        d = ['M ' + ' L '.join('%.3f %.3f' % (x, y) for x, y in a) + ' Z' for a in anillos]
        d += ['M ' + ' L '.join('%.3f %.3f' % (x, y) for x, y in a) for a in abiertos]
        if not d:
            return
        dash = ' stroke-dasharray="2 1.5"' if punteado else ''
        p.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f"%s/>'
                 % (' '.join(d), color, grosor, dash))

    # Mismo orden que el DXF (LightBurn tambien lee SVG): grabado primero, corte
    # al final y cada pieza con sus huecos antes que su contorno.
    etiquetas = []
    for col in colocadas:
        pz = col['pieza']
        g = col['geo']
        trazos = _trazos_grabado(col)
        path_de([t for t, cerrado in trazos if cerrado], '#2563eb', 0.25, punteado=True,
                abiertos=[t for t, cerrado in trazos if not cerrado])
        x, y, alto, giro = _rotulo(g, pz['id'], col.get('guia'))
        etiquetas.append((x, y, _xml(pz['id']), alto, 'middle', giro))
    for anillos in _anillos_de_corte(colocadas):
        path_de(anillos, '#e11d48', 0.35)

    if cajetin:
        lineas = [str(x) for x in cajetin if str(x).strip()]
        alto = 3.2
        ancho = ANCHO_LETRA * alto * max(len(x) for x in lineas) if lineas else 0
        total = alto * 1.7 * len(lineas)
        for col in sorted(colocadas, key=lambda c: (c['pieza'].get('tipo') != 'losa',
                                                    -c['geo'].area)):
            pos = _hueco_para_texto(col, ancho, total)
            if pos is None:
                continue
            for i, txt in enumerate(lineas):
                etiquetas.append((pos[0], pos[1] + total - alto * 1.7 * (i + 0.5),
                                  _xml(txt), alto, 'start'))
            break

    for clave, (x0, y0, x1, y1) in zonas_de(colocadas).items():
        m = 3.0
        p.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="none" '
                 'stroke="#94a3b8" stroke-width="0.4" stroke-dasharray="3 2"/>'
                 % (x0 - m, y0 - m, (x1 - x0) + 2 * m, (y1 - y0) + 2 * m))
        etiquetas.append((x0 - m, y1 + m + 4, _xml(rotulo_zona % clave), 5.0, 'start'))

    p.append('</g>')
    for et in etiquetas:
        x, y, txt, h = et[:4]
        anclaje = et[4] if len(et) > 4 else 'middle'
        giro = (' transform="rotate(%d %.3f %.3f)"' % (-et[5], x, Ht - y)
                if len(et) > 5 and et[5] else '')
        # el numero de pieza (el unico con giro) se graba: azul como las huellas
        relleno = '#2563eb' if len(et) > 5 else '#111'
        p.append('<text x="%.3f" y="%.3f" font-family="Arial,Helvetica" font-size="%.2f" '
                 'fill="%s" text-anchor="%s" dominant-baseline="central"%s>%s</text>'
                 % (x, Ht - y, h, relleno, anclaje, giro, txt))
    for i, nota in enumerate(notas):
        p.append('<text x="0" y="%.2f" font-family="Arial,Helvetica" font-size="4.5" '
                 'fill="#555">%s</text>' % (Ht - (H + 6 + 7.0 * (len(notas) - 1 - i)),
                                            _xml(nota)))
    p.append('<text x="%.2f" y="%.2f" font-family="Arial,Helvetica" font-size="4" '
             'fill="#666">%s</text>' % (cfg.margen_mm, arriba + cfg.margen_mm - 3,
                                        _xml(titulo)))
    p.append('</svg>')
    return '\n'.join(p)


# ------------------------------------------------------------------- guia
def guia_html(hojas, piezas, cfg, svgs, nombre, grandes, stats):
    filas = ''.join(
        '<tr><td class="id">%s</td><td>%.1f m</td><td>%.1f cm2</td><td>%d</td></tr>'
        % (pz['id'], pz['z_real_m'], pz['poly'].area / 100.0, pz['hoja'])
        for pz in piezas)

    aviso = ''
    if grandes:
        aviso = ('<div class="aviso"><b>%d pieza(s) no caben en la hoja</b> (%s). '
                 'Sube la escala o usa hoja mas grande.</div>'
                 % (len(grandes), ', '.join(grandes)))

    laminas = ''.join(
        '<section class="hoja"><h2>Hoja %d <small>%d piezas</small></h2>%s</section>'
        % (i + 1, len(hojas[i]), svg) for i, svg in enumerate(svgs))

    return """<!doctype html><meta charset="utf-8">
<title>Despiece 3D — %(nombre)s</title>
<style>
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,Helvetica,Arial;color:#111;background:#f6f6f7}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:24px;margin:0 0 4px}
.sub{color:#666;margin:0 0 24px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:0 0 28px}
.kpi{background:#fff;border:1px solid #e5e5e7;border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:20px}
.kpi span{color:#666;font-size:12px}
.aviso{background:#fff4e5;border:1px solid #f0c98a;border-radius:10px;padding:12px 14px;margin:0 0 20px}
table{width:100%%;border-collapse:collapse;background:#fff;border:1px solid #e5e5e7;border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid #f0f0f2;font-size:13px}
th{background:#fafafa;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#666}
td.id{font-family:ui-monospace,Menlo,monospace;font-weight:600}
.hoja{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:18px;margin:18px 0}
.hoja h2{font-size:15px;margin:0 0 12px;display:flex;gap:8px;align-items:baseline}
.hoja small{color:#888;font-weight:400}
.hoja svg{width:100%%;height:auto;border:1px solid #eee}
.leyenda{display:flex;gap:18px;font-size:12px;color:#555;margin:22px 0 6px}
.leyenda i{display:inline-block;width:22px;height:0;border-top:2px solid;margin-right:6px;vertical-align:middle}
@media print{body{background:#fff}.wrap{max-width:none;padding:0}.hoja{page-break-after:always;border:0}}
</style>
<div class="wrap">
<h1>%(nombre)s</h1>
<p class="sub">Escala 1:%(escala)d · lámina %(espesor).1f mm · hoja %(hw).0f×%(hh).0f mm · kerf %(kerf).2f mm</p>
<div class="kpis">
 <div class="kpi"><b>%(n_piezas)d</b><span>piezas</span></div>
 <div class="kpi"><b>%(n_hojas)d</b><span>hojas</span></div>
 <div class="kpi"><b>%(alto).0f mm</b><span>alto maqueta</span></div>
 <div class="kpi"><b>%(material).0f cm²</b><span>material cortado</span></div>
</div>
%(aviso)s
<div class="leyenda"><span><i style="color:#e11d48"></i>corte</span>
<span><i style="color:#2563eb;border-top-style:dashed"></i>grabado — silueta de la pieza que va encima</span></div>
%(laminas)s
<h2 style="font-size:15px;margin:34px 0 10px">Orden de armado (de abajo hacia arriba)</h2>
<table><thead><tr><th>Pieza</th><th>Altura real</th><th>Área</th><th>Hoja</th></tr></thead>
<tbody>%(filas)s</tbody></table>
</div>""" % dict(nombre=html.escape(nombre), escala=int(cfg.escala), espesor=cfg.espesor_mm,
                 hw=cfg.hoja[0], hh=cfg.hoja[1], kerf=cfg.kerf_mm,
                 n_piezas=stats['n_piezas'], n_hojas=stats['n_hojas'],
                 alto=stats['alto_mm'], material=stats.get('material_cm2', 0),
                 aviso=aviso, laminas=laminas, filas=filas)


# ------------------------------------------------- guia del modo estructural
def guia_estructural(hojas, piezas, cfg, svgs, nombre, grandes, stats, info,
                     iso_armada='', iso_explotada=''):
    orden = {'losa': 0, 'muro': 1, 'techo': 2}
    filas = ''.join(
        '<tr><td class="id">%s</td><td>%s</td><td>%.0f × %.0f mm</td>'
        '<td>%s</td><td>%s</td><td>%s</td><td>%d</td></tr>'
        % (p['id'], p['tipo'],
           p['poly'].bounds[2] - p['poly'].bounds[0],
           p['poly'].bounds[3] - p['poly'].bounds[1],
           p['vanos'] or '—', p['dientes'] or '—', p['ranuras'] or '—', p['hoja'])
        for p in sorted(piezas, key=lambda p: (orden.get(p['tipo'], 9), p['id'])))

    avisos = ''
    if grandes:
        avisos += ('<div class="aviso"><b>%d pieza(s) no caben en la hoja</b> (%s). '
                   'Sube la escala o usa una hoja más grande.</div>'
                   % (len(grandes), ', '.join(grandes)))
    # El nombre y los avisos traen texto del alumno (nombre del archivo, nombres
    # de elementos del IFC) y la guia se sirve como HTML: van escapados.
    for a in info.get('avisos', []):
        avisos += '<div class="aviso">%s</div>' % html.escape(str(a))
    if info.get('descartados'):
        avisos += ('<div class="nota">Se ignoraron %d cuerpos que no son láminas '
                   '(astillas o sólidos macizos).</div>' % len(info['descartados']))

    laminas = ''.join(
        '<section class="hoja"><h2>Hoja %d <small>%d piezas</small></h2>%s</section>'
        % (i + 1, len(hojas[i]), s) for i, s in enumerate(svgs))

    # A tope (lo de fabrica, o si no salio ninguna union) no hay dientes que
    # clavar: decirle "por los dientes" al alumno lo pone a buscar algo que no hay.
    if info.get('n_uniones'):
        base = 'Empieza por la base (L…) y clava en ella los muros (M…) por los dientes.'
        cierre = 'Los dientes entran a presión. Si aprieta de más, lija el diente; no fuerces el cartón.'
    else:
        base = 'Empieza por la base (L…) y pega sobre ella los muros (M…), de canto.'
        cierre = ('Todo va a tope: pega con poco pegamento blanco y sostén cada muro a escuadra '
                  'unos segundos.')
        if any(p['ranuras'] for p in piezas):
            cierre += ' Donde dos muros se cruzan traen una ranura: se encajan una en la otra.'

    return """<!doctype html><meta charset="utf-8">
<title>Despiece 3D — %(nombre)s</title>
<style>
:root{color-scheme:light}*{box-sizing:border-box}
body{margin:0;font:14px/1.55 -apple-system,BlinkMacSystemFont,Helvetica,Arial;color:#18181b;background:#f6f6f7}
.wrap{max-width:940px;margin:0 auto;padding:34px 20px 90px}
h1{font-size:26px;letter-spacing:-.02em;margin:0 0 4px}
.sub{color:#71717a;margin:0 0 24px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:10px;margin:0 0 22px}
.kpi{background:#fff;border:1px solid #e5e5e7;border-radius:11px;padding:12px 14px}
.kpi b{display:block;font-size:20px}.kpi span{color:#71717a;font-size:12px}
.aviso{background:#fff7ed;border:1px solid #f0c98a;color:#b45309;border-radius:11px;padding:11px 14px;margin:0 0 12px}
.nota{color:#71717a;font-size:13px;margin:0 0 12px}
.par{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:0 0 20px}
@media(max-width:720px){.par{grid-template-columns:1fr}}
.vista{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:12px}
.vista h3{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#71717a}
.vista svg{width:100%%;height:auto}
table{width:100%%;border-collapse:collapse;background:#fff;border:1px solid #e5e5e7;border-radius:11px;overflow:hidden}
th,td{text-align:left;padding:7px 11px;border-bottom:1px solid #f2f2f4;font-size:13px}
th{background:#fafafa;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#71717a}
td.id{font-family:ui-monospace,Menlo,monospace;font-weight:600}
.hoja{background:#fff;border:1px solid #e5e5e7;border-radius:12px;padding:16px;margin:14px 0}
.hoja h2{font-size:14px;margin:0 0 10px}.hoja small{color:#a1a1aa;font-weight:400}
.hoja svg{width:100%%;height:auto;border:1px solid #eee}
.leyenda{display:flex;gap:16px;font-size:12px;color:#71717a;margin:18px 0 4px;flex-wrap:wrap}
.leyenda i{display:inline-block;width:20px;height:0;border-top:2px solid;margin-right:6px;vertical-align:middle}
ol.pasos{background:#fff;border:1px solid #e5e5e7;border-radius:11px;padding:14px 14px 14px 34px;margin:10px 0 0}
ol.pasos li{margin:4px 0}
@media print{body{background:#fff}.wrap{max-width:none;padding:0}.hoja{page-break-after:always;border:0}}
</style>
<div class="wrap">
<h1>%(nombre)s</h1>
<p class="sub">Escala 1:%(escala)d · lámina %(espesor).1f mm · hoja %(hw).0f×%(hh).0f mm · kerf %(kerf).2f mm</p>
<div class="kpis">
 <div class="kpi"><b>%(n_piezas)d</b><span>piezas</span></div>
 <div class="kpi"><b>%(n_hojas)d</b><span>hojas</span></div>
 <div class="kpi"><b>%(n_uniones)d</b><span>uniones</span></div>
 <div class="kpi"><b>%(n_plantas)d</b><span>plantas</span></div>
 <div class="kpi"><b>%(material).0f cm²</b><span>material cortado</span></div>
</div>
%(avisos)s
<div class="par">
 <div class="vista"><h3>Armada</h3>%(iso_a)s</div>
 <div class="vista"><h3>Explotada</h3>%(iso_e)s</div>
</div>
<h2 style="font-size:15px;margin:26px 0 8px">Cómo se arma</h2>
<ol class="pasos">
 <li>Corta todas las hojas. Las líneas <b style="color:#e11d48">rojas</b> se cortan; las
     <b style="color:#2563eb">azules punteadas</b> sólo se graban.</li>
 <li>Cada hoja trae una o varias <b>zonas rotuladas</b> (PLANTA 1, PLANTA 2…). No mezcles
     piezas de zonas distintas: cada zona es un nivel de la maqueta y se arma completo.</li>
 <li>%(base)s</li>
 <li>La losa trae <b>grabada la planta de sus muros</b>, puertas incluidas: pon cada muro sobre
     su línea. Si no coincide ninguna, la pieza va al revés o es de otro nivel.</li>
 <li>Cierra con los faldones del techo (T…). Estos suelen ir pegados al final.</li>
 <li>%(cierre)s</li>
</ol>
<h2 style="font-size:15px;margin:26px 0 8px">Piezas</h2>
<table><thead><tr><th>Pieza</th><th>Tipo</th><th>Medida</th><th>Vanos</th>
<th>Dientes</th><th>Ranuras</th><th>Hoja</th></tr></thead><tbody>%(filas)s</tbody></table>
<div class="leyenda"><span><i style="color:#e11d48"></i>corte</span>
<span><i style="color:#2563eb;border-top-style:dashed"></i>grabado — dónde apoya la otra pieza</span></div>
%(laminas)s
</div>""" % dict(nombre=html.escape(nombre), escala=int(cfg.escala), espesor=cfg.espesor_mm,
                 hw=cfg.hoja[0], hh=cfg.hoja[1], kerf=cfg.kerf_mm,
                 n_piezas=stats['n_piezas'], n_hojas=stats['n_hojas'],
                 n_uniones=info.get('n_uniones', 0), material=stats.get('material_cm2', 0),
                 n_plantas=info.get('n_plantas', 1),
                 avisos=avisos, iso_a=iso_armada, iso_e=iso_explotada,
                 filas=filas, laminas=laminas, base=base, cierre=cierre)


# ------------------------------------------------ instructivo de armado
def orden_natural(texto):
    """'M2a' antes que 'M10a': el orden alfabetico revolvia la lista."""
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', str(texto))]


GRUPOS = (('L', 'Base'), ('M', 'Muros'), ('T', 'Techo'))


def _grupo(pid):
    for letra, _ in GRUPOS:
        if pid.startswith(letra) and not pid.startswith('BA'):
            return letra
    return 'X'                             # escaleras, muebles, bastidor


def pasos_de_armado(piezas):
    """[(rotulo, grupo, [piezas])] en el orden en que se pegan: planta por planta y
    dentro de cada una base, muros, techo y al final lo macizo."""
    orden = [l for l, _ in GRUPOS] + ['X']
    pasos = {}
    for p in piezas:
        pid = p.get('partida_de') or p['id']
        clave = (p.get('planta', 1), orden.index(_grupo(str(pid))))
        pasos.setdefault(clave, []).append(p)
    return [(pasos[k][0].get('rotulo', 'PLANTA %d' % k[0]), orden[k[1]],
             sorted(pasos[k], key=lambda p: orden_natural(p['id'])))
            for k in sorted(pasos)]


def instructivo_estructural(piezas, cfg, nombre, info, n_hojas, grandes=(), huecos=False):
    """Instructivo para imprimir (carta): un paso por planta y grupo, cada uno con
    su dibujo -- lo ya armado en gris, lo nuevo en color con su numero -- y la
    lista pieza -> hoja. Sale de info['placas'] (la geometria real), no de las
    medidas: con el cubo (2-oct) unas instrucciones sacadas de las medidas
    pusieron las piezas donde no iban."""
    from isometrica import vista

    placas = info.get('placas', [])
    nombre_grupo = dict(GRUPOS, X='Escaleras y muebles')
    pasos = pasos_de_armado(piezas)
    a_tope = not info.get('n_uniones')
    hay_techo = any(g == 'T' for _, g, _ in pasos)
    hay_ranuras = any(p.get('ranuras') for p in piezas)
    partidas = sorted({p['partida_de'] for p in piezas if p.get('partida_de')}, key=orden_natural)

    # Lo general, solo lo que trae ESTE modelo: hablarle de techos o dientes que
    # no tiene lo pone a buscar algo que no existe.
    reglas = ['Cada pieza trae su número <b>grabado</b>. Búscala por número, no por forma.']
    if a_tope:
        reglas.append('Todo va <b>a tope</b>: poco pegamento blanco en el canto y sostén '
                      'cada muro a escuadra unos segundos.')
    else:
        reglas.append('Los <b>dientes</b> entran a presión en las ranuras de la otra pieza. '
                      'Si aprieta de más, lija el diente; no fuerces la lámina.')
    if hay_ranuras:
        reglas.append('Donde dos muros se cruzan, cada uno trae una <b>ranura</b> a media '
                      'altura: se encajan uno en el otro.')
    reglas.append('La base trae <b>grabada la planta de sus muros</b>, puertas incluidas: '
                  'cada muro va sobre su línea. Si no coincide, la pieza va al revés o es de otra planta.')
    if huecos:
        reglas.append('Los muros son <b>huecos</b>: cada uno son dos caras (<b>a</b> y <b>b</b>) '
                      'con un espacio en medio para pasar instalaciones. Las tiras '
                      '<b>-B</b> van de canto entre las dos caras de la losa.')
    if partidas:
        reglas.append('No cupieron enteras en la hoja y vienen en partes (.1, .2…): '
                      '<b>%s</b>. Pega las partes antes de armar.'
                      % html.escape(', '.join(map(str, partidas))))

    hechas, bloques = set(), []
    for n, (rotulo, grupo, lista) in enumerate(pasos, 1):
        ids = {str(p.get('partida_de') or p['id']) for p in lista}
        dibujo = ''
        nuevas = [pl for pl in placas if pl['id'] in ids]
        if nuevas:
            dibujo = vista([pl for pl in placas if pl['id'] in hechas] + nuevas,
                           ancho=760, resaltar=ids)
        hechas |= ids
        filas = ''.join(
            '<tr><td class="id">%s</td><td>%.0f × %.0f mm</td><td>%s</td></tr>'
            % (html.escape(str(p['id'])),
               p['poly'].bounds[2] - p['poly'].bounds[0],
               p['poly'].bounds[3] - p['poly'].bounds[1],
               p.get('hoja') or '—')
            for p in lista)
        titulo = rotulo.title() if rotulo else ''
        bloques.append(
            '<section class="paso"><h2><span>%d</span>%s · %s <small>%d pieza%s</small></h2>'
            '<div class="cuerpo"><div class="dib">%s</div>'
            '<table><thead><tr><th>Pieza</th><th>Medida</th><th>Hoja</th></tr></thead>'
            '<tbody>%s</tbody></table></div></section>'
            % (n, html.escape(titulo), nombre_grupo[grupo], len(lista),
               '' if len(lista) == 1 else 's', dibujo, filas))

    # Para el taller, no para quien arma: van al final y chiquitos.
    notas = ''
    if grandes:
        notas += ('<p class="alerta">%d pieza(s) no cupieron en la hoja y no se cortaron: %s.</p>'
                  % (len(grandes), html.escape(', '.join(map(str, grandes)))))
    for a in info.get('avisos', []):
        notas += '<p>%s</p>' % html.escape(str(a))
    if notas:
        notas = '<section class="notas"><h3>Notas del archivo</h3>%s</section>' % notas

    return """<!doctype html><meta charset="utf-8">
<title>Instructivo de armado — %(nombre)s</title>
<style>
@page{size:letter;margin:12mm}
:root{color-scheme:light}*{box-sizing:border-box}
body{margin:0;font:12.5px/1.5 -apple-system,BlinkMacSystemFont,Helvetica,Arial;color:#18181b;background:#fff}
h1{font-size:24px;letter-spacing:-.02em;margin:0 0 2px}
.sub{color:#71717a;margin:0 0 14px}
.antes{border:2px solid #16a34a;border-radius:10px;padding:10px 14px;margin:0 0 14px}
.antes b.t{display:block;color:#15803d;font-size:13px;margin-bottom:2px}
.colores{display:flex;gap:18px;flex-wrap:wrap;margin-top:6px}
.colores i{display:inline-block;width:22px;height:0;border-top:2px solid;margin-right:6px;vertical-align:middle}
.portada svg{width:100%%;height:auto;max-height:118mm}
ul.reglas{margin:10px 0 0;padding-left:18px}ul.reglas li{margin:3px 0}
.paso{break-inside:avoid;border-top:1px solid #e4e4e7;padding:12px 0 6px}
.paso h2{font-size:15px;margin:0 0 8px;display:flex;align-items:center;gap:9px}
.paso h2 span{background:#18181b;color:#fff;border-radius:50%%;width:26px;height:26px;display:inline-grid;place-items:center;font-size:13px}
.paso h2 small{color:#a1a1aa;font-weight:400;font-size:12px}
.cuerpo{display:grid;grid-template-columns:1fr 210px;gap:14px;align-items:start}
.dib svg{width:100%%;height:auto;max-height:105mm}
table{width:100%%;border-collapse:collapse}
th,td{text-align:left;padding:2px 6px;border-bottom:1px solid #f0f0f2;font-size:11.5px}
th{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:#71717a}
td.id{font-family:ui-monospace,Menlo,monospace;font-weight:700}
.notas{break-before:page;color:#71717a;font-size:11px}.notas h3{font-size:12px;color:#18181b}
.alerta{color:#b45309;font-weight:600}
</style>
<div class="portada">
<h1>%(nombre)s</h1>
<p class="sub">Instructivo de armado · escala 1:%(escala)d · lámina %(espesor)g mm · %(n)d piezas en %(hojas)d hoja%(s)s</p>
<div class="antes"><b class="t">Antes de cortar: pide que graben la capa GRABADO</b>
Ahí va el número de cada pieza, en una esquina. Sin él no hay forma de saber cuál es cuál.
<div class="colores"><span><i style="color:#e11d48"></i>CORTE: se corta</span>
<span><i style="color:#2563eb;border-top-style:dashed"></i>GRABADO: dónde apoya otra pieza y número de pieza</span></div></div>
%(armada)s
<ul class="reglas">%(reglas)s</ul>
</div>
%(pasos)s
%(notas)s""" % dict(nombre=html.escape(nombre), escala=int(cfg.escala), espesor=cfg.espesor_mm,
                    n=len(piezas), hojas=n_hojas, s='' if n_hojas == 1 else 's',
                    armada=vista(placas, ancho=760) if placas else '',
                    reglas=''.join('<li>%s</li>' % r for r in reglas),
                    pasos=''.join(bloques), notas=notas)


# ------------------------------------------------------------------- PDF
def hojas_a_pdf(hojas, cfg, ruta, titulo_base):
    """Todas las hojas en un PDF vectorial, a tamaño real (1 mm de hoja = 1 mm).

    El SVG ya sirve para imprimir, pero el navegador reescala al papel y el
    corte sale a otra medida. El PDF trae la hoja como tamaño de pagina, asi
    que se manda a imprimir "a escala 100%" y las piezas miden lo que dicen.

    Colores como en el DXF: corte en rojo, grabado en azul punteado. Los
    programas de laser que leen PDF (LightBurn, RDWorks) separan por color.
    """
    from reportlab.pdfgen import canvas as _canvas
    from reportlab.lib.units import mm as MM

    W, H = cfg.hoja
    c = _canvas.Canvas(ruta, pagesize=(W * MM, H * MM))
    c.setTitle(titulo_base)
    letra = _arial_pdf()

    for i, colocadas in enumerate(hojas):
        c.setLineWidth(0.1 * MM)
        c.setStrokeColorRGB(0.72, 0.72, 0.75)
        c.rect(0, 0, W * MM, H * MM, stroke=1, fill=0)

        def trazo(anillo, cerrado=True):
            p = c.beginPath()
            p.moveTo(anillo[0][0] * MM, anillo[0][1] * MM)
            for x, y in anillo[1:]:
                p.lineTo(x * MM, y * MM)
            if cerrado:
                p.close()
            c.drawPath(p, stroke=1, fill=0)

        # Mismo orden que el DXF: grabado primero, corte al final, huecos antes
        # que el contorno de su pieza.
        for col in colocadas:
            trazos = _trazos_grabado(col)
            if trazos:
                c.setStrokeColorRGB(0.15, 0.39, 0.92)      # grabado
                c.setDash(2 * MM, 1.5 * MM)
                for t, cerrado in trazos:
                    trazo(t, cerrado)
                c.setDash()

            x, y, alto, giro = _rotulo(col['geo'], col['pieza']['id'], col.get('guia'))
            c.setFillColorRGB(0.15, 0.39, 0.92)            # se graba, como en el DXF
            c.setFont(letra, alto * MM)
            # drawCentredString pone la linea base; se baja media altura para
            # que el numero quede centrado en la pieza y no encima del borde.
            c.saveState()
            c.translate(x * MM, y * MM)
            c.rotate(giro)
            c.drawCentredString(0, -alto * MM * 0.36, str(col['pieza']['id']))
            c.restoreState()

        c.setStrokeColorRGB(0.88, 0.11, 0.28)              # corte
        for anillos in _anillos_de_corte(colocadas):
            for anillo in anillos:
                trazo(anillo)

        c.setFillColorRGB(0.42, 0.42, 0.45)
        c.setFont(letra, 4 * MM)
        c.drawString(cfg.margen_mm * MM, (cfg.margen_mm - 4) * MM,
                     '%s  hoja %d/%d' % (titulo_base, i + 1, len(hojas)))
        c.showPage()

    c.save()
    return ruta


# ------------------------------------------------------------------- DWG
def _oda_convertidor():
    """Ruta del ODA File Converter, si esta instalado.

    Es gratuito (opendesign.com) pero se baja a mano dando un correo, asi que
    no se puede dar por hecho. Es el unico que escribe DWG de verdad.
    """
    import glob
    import shutil

    exe = shutil.which('ODAFileConverter')
    if exe:
        return exe
    for patron in ('/Applications/ODAFileConverter*.app/Contents/MacOS/ODAFileConverter',
                   '/Applications/ODA/ODAFileConverter*/ODAFileConverter',
                   # Windows (cambio local, 12 sep 2026): el instalador del ODA no
                   # lo pone en el PATH. Sin probar: no hay Windows a la mano.
                   'C:/Program Files/ODA/ODAFileConverter*/ODAFileConverter.exe',
                   'C:/Program Files (x86)/ODA/ODAFileConverter*/ODAFileConverter.exe'):
        hallados = sorted(glob.glob(patron))
        if hallados:
            return hallados[-1]
    return None


def _con_oda(exe, ruta_dxf, ruta_dwg):
    """ODAFileConverter trabaja por carpetas, no por archivo suelto."""
    import shutil
    import subprocess
    import tempfile

    entrada = tempfile.mkdtemp(prefix='oda_in_')
    salida = tempfile.mkdtemp(prefix='oda_out_')
    try:
        copia = os.path.join(entrada, os.path.basename(ruta_dxf))
        shutil.copy2(ruta_dxf, copia)
        subprocess.run([exe, entrada, salida, 'ACAD2018', 'DWG', '0', '1', '*.DXF'],
                       capture_output=True, timeout=600)
        hecho = os.path.join(salida, os.path.splitext(os.path.basename(ruta_dxf))[0] + '.dwg')
        if not os.path.exists(hecho):
            return None, 'ODAFileConverter no dejo salida para %s' % os.path.basename(ruta_dxf)
        shutil.move(hecho, ruta_dwg)
        return ruta_dwg, None
    except Exception as e:
        return None, 'ODAFileConverter fallo: %s' % e
    finally:
        shutil.rmtree(entrada, ignore_errors=True)
        shutil.rmtree(salida, ignore_errors=True)


AVISO_SIN_DWG = (
    'no se pudo escribir DWG. Usa el DXF: AutoCAD lo abre igual y toda maquina\n'
    '   de corte lo lee. Si el taller exige DWG de verdad, instala el ODA File\n'
    '   Converter (gratis, opendesign.com/guestfiles/oda_file_converter) y vuelve\n'
    '   a correr esto: se detecta solo.')


def dxf_a_dwg(ruta_dxf, ruta_dwg=None, verificar=True):
    """Convierte el DXF a DWG, que es lo que piden las cabinas de corte.

    ezdxf no escribe DWG --es formato cerrado de Autodesk-- asi que hay que
    salir a una herramienta de afuera. Se intentan dos, en este orden:

    1. **ODA File Converter** (opendesign.com). Gratis pero se baja a mano dando
       un correo, asi que no siempre esta. Escribe hasta ACAD2018.
    2. **`dwgwrite` (LibreDWG)**, que se instala con brew y solo escribe r2000.

    Durante meses el camino 2 entrego un archivo que AutoCAD abria EN NEGRO, y
    la causa no era el tamaño ni el escritor: era la **version del DXF de
    entrada**. De R2007 en adelante el DXF guarda los nombres en UTF-8 y
    LibreDWG 0.14 los relee como si fueran de dos bytes, corta en el primer NUL
    y deja cada nombre en su letra inicial --las capas en C/G/H y el bloque
    `*Model_Space` en `*`, que es lo que dejaba las entidades colgando de un
    bloque que ningun layout referencia. Entrando el mismo dibujo como R2000 o
    R2004 los nombres salen enteros y el espacio modelo trae todo. Por eso
    `hoja_a_dxf` escribe R2004; si algun dia se cambia esa version, esto se
    rompe en silencio y el aviso de aqui no lo va a decir --lo cacha la
    verificacion de abajo.

    Lo que si se pierde por el camino 2: el color verdadero (RGB) de las capas,
    porque el DWG r2000 es anterior a el. Queda el indice de color de AutoCAD,
    que es exacto para la convencion normal (rojo 1, azul 5, verde 3) y lo unico
    que miran las cabinas viejas. El DXF que va en el mismo zip si lleva los dos.

    Devuelve (ruta_dwg, None) o (None, aviso).
    """
    import shutil
    import subprocess

    if ruta_dwg is None:
        ruta_dwg = os.path.splitext(ruta_dxf)[0] + '.dwg'

    oda = _oda_convertidor()
    if oda:
        hecho, err = _con_oda(oda, ruta_dxf, ruta_dwg)
        if hecho and not (verificar and _dwg_vacio(ruta_dwg)):
            return hecho, None
        if hecho:
            os.remove(ruta_dwg)
        # Si el ODA fallo todavia queda dwgwrite: no se regresa aqui.

    exe = shutil.which('dwgwrite')
    if not exe:
        return None, AVISO_SIN_DWG
    try:
        r = subprocess.run([exe, '--as', 'r2000', '-o', ruta_dwg, ruta_dxf],
                           capture_output=True, timeout=300)
    except Exception as e:
        return None, 'dwgwrite fallo: %s' % e
    if r.returncode != 0 or not os.path.exists(ruta_dwg):
        return None, AVISO_SIN_DWG

    if verificar:
        falla = _dwg_vacio(ruta_dwg)
        if falla:
            os.remove(ruta_dwg)
            return None, '%s\n   (%s)' % (AVISO_SIN_DWG, falla)
    return ruta_dwg, None


def _dwg_vacio(ruta_dwg):
    """¿El DWG abre sin nada dibujado? Devuelve el motivo, o None si trae obra.

    Se relee el DWG a DXF y se cuenta lo que hay EN EL ESPACIO MODELO, que es lo
    unico que el operador va a ver. Contar la palabra LWPOLYLINE en el texto no
    sirve: un DWG con el bloque roto trae las 1106 y aun asi abre en negro.
    """
    import shutil
    import subprocess
    import tempfile
    import warnings

    lector = shutil.which('dwgread')
    if not lector:
        return None                      # sin con que revisar, no se condena

    tmp = tempfile.NamedTemporaryFile(suffix='.dxf', delete=False)
    tmp.close()
    try:
        subprocess.run([lector, '-O', 'DXF', '-o', tmp.name, ruta_dwg],
                       capture_output=True, timeout=300)
        import logging
        import ezdxf
        # ezdxf grita por el bitacora ("non-unique entity handle") cuando relee
        # lo que escribio LibreDWG; aqui eso ya es el resultado esperado, no
        # algo que el usuario tenga que ver.
        ruido = logging.getLogger('ezdxf')
        antes = ruido.level
        ruido.setLevel(logging.CRITICAL)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                doc = ezdxf.readfile(tmp.name)
        finally:
            ruido.setLevel(antes)
        n = sum(1 for _ in doc.modelspace())
        if n == 0:
            return 'el espacio modelo quedo vacio'
        return None
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
