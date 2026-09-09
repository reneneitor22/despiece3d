# -*- coding: utf-8 -*-
"""Salidas: DXF por hoja (laser) + SVG/HTML imprimible (corte a mano) + guia de armado."""
import os
import ezdxf

# Convencion de capas por operacion. Cada taller tiene la suya --el nombre y el
# color son lo unico que la cabina de corte mira para saber que hacer con cada
# linea-- asi que esto es solo el arranque: se puede cambiar desde la pantalla.
OPS_DEFAULT = {
    'corte':   {'capa': 'CORTE',   'rgb': (255, 0, 0)},
    'grabado': {'capa': 'GRABADO', 'rgb': (0, 0, 255)},
    'marcado': {'capa': 'MARCADO', 'rgb': (0, 128, 0)},
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
        for nombre, op in (('corte', ops['corte']), ('grabado', ops['grabado']),
                           ('marcado', ops['marcado']))
    ]
    alto = alto_tit + fila * len(filas)
    y0 = y - alto

    def linea(p1, p2):
        msp.add_line(p1, p2, dxfattribs={'layer': capa})

    def texto(txt, x, yc, h=3.0, negrita=False):
        t = msp.add_text(str(txt), dxfattribs={'layer': capa, 'height': h,
                                               'style': 'OpenSans-Bold' if negrita else 'Standard'})
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
    y = y0
    while y + alto <= y1:
        x = x0
        while x + ancho <= x1:
            caja = _box(x, y, x + ancho, y + alto)
            if dentro.contains(caja) and (guia is None or guia.is_empty
                                          or not guia.intersects(caja)):
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
    ancho = 0.62 * alto * max(len(x) for x in lineas)
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

    `ops`   convencion de capas por operacion (corte / grabado / marcado).
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
    L_MARCA = ops['marcado']['capa']

    W, H = cfg.hoja
    if marco:
        msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True,
                           dxfattribs={'layer': L_HOJA})

    for col in colocadas:
        pz = col['pieza']
        for ext, ints in _anillos(col['geo']):
            msp.add_lwpolyline(ext, close=True, dxfattribs={'layer': L_CORTE})
            for r in ints:
                msp.add_lwpolyline(r, close=True, dxfattribs={'layer': L_CORTE})

        if col['guia'] is not None:
            for ext, ints in _anillos(col['guia']):
                msp.add_lwpolyline(ext, close=True, dxfattribs={'layer': L_GRAB})
                for r in ints:
                    msp.add_lwpolyline(r, close=True, dxfattribs={'layer': L_GRAB})

        g = col['geo']
        cx, cy = g.representative_point().x, g.representative_point().y
        alto = max(2.5, min(6.0, (g.bounds[2] - g.bounds[0]) / 8.0))
        # El numero de pieza es MARCADO, no grabado: es lo que el alumno lee
        # para armar, no relieve. Separarlo deja que el taller lo corra a menos
        # potencia o lo apague sin tocar las huellas de ensamble.
        msp.add_text(pz['id'],
                     dxfattribs={'layer': L_MARCA, 'height': alto}
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

    bajo = _tabla_corte(msp, cfg, ficha, ops, W, capa=L_HOJA) if ficha else 0.0

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

    def path_de(geom, color, grosor, punteado=False):
        d = []
        for ext, ints in _anillos(geom):
            for anillo in [ext] + ints:
                d.append('M ' + ' L '.join('%.3f %.3f' % (x, y) for x, y in anillo) + ' Z')
        if not d:
            return
        dash = ' stroke-dasharray="2 1.5"' if punteado else ''
        p.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f"%s/>'
                 % (' '.join(d), color, grosor, dash))

    etiquetas = []
    for col in colocadas:
        pz = col['pieza']
        g = col['geo']
        path_de(g, '#e11d48', 0.35)
        if col['guia'] is not None:
            path_de(col['guia'], '#2563eb', 0.25, punteado=True)
        rp = g.representative_point()
        etiquetas.append((rp.x, rp.y, _xml(pz['id']),
                          max(2.5, min(6.0, (g.bounds[2] - g.bounds[0]) / 8.0))))

    if cajetin:
        lineas = [str(x) for x in cajetin if str(x).strip()]
        alto = 3.2
        ancho = 0.62 * alto * max(len(x) for x in lineas) if lineas else 0
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
        p.append('<text x="%.3f" y="%.3f" font-family="Helvetica,Arial" font-size="%.2f" '
                 'fill="#111" text-anchor="%s" dominant-baseline="central">%s</text>'
                 % (x, Ht - y, h, anclaje, txt))
    for i, nota in enumerate(notas):
        p.append('<text x="0" y="%.2f" font-family="Helvetica,Arial" font-size="4.5" '
                 'fill="#555">%s</text>' % (Ht - (H + 6 + 7.0 * (len(notas) - 1 - i)),
                                            _xml(nota)))
    p.append('<text x="%.2f" y="%.2f" font-family="Helvetica,Arial" font-size="4" '
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
</div>""" % dict(nombre=nombre, escala=int(cfg.escala), espesor=cfg.espesor_mm,
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
    for a in info.get('avisos', []):
        avisos += '<div class="aviso">%s</div>' % a
    if info.get('descartados'):
        avisos += ('<div class="nota">Se ignoraron %d cuerpos que no son láminas '
                   '(astillas o sólidos macizos).</div>' % len(info['descartados']))

    laminas = ''.join(
        '<section class="hoja"><h2>Hoja %d <small>%d piezas</small></h2>%s</section>'
        % (i + 1, len(hojas[i]), s) for i, s in enumerate(svgs))

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
 <li>Empieza por la base (L…) y clava en ella los muros (M…) por los dientes.</li>
 <li>La losa trae <b>grabada la planta de sus muros</b>, puertas incluidas: pon cada muro sobre
     su línea. Si no coincide ninguna, la pieza va al revés o es de otro nivel.</li>
 <li>Cierra con los faldones del techo (T…). Estos suelen ir pegados al final.</li>
 <li>Los dientes entran a presión. Si aprieta de más, lija el diente; no fuerces el cartón.</li>
</ol>
<h2 style="font-size:15px;margin:26px 0 8px">Piezas</h2>
<table><thead><tr><th>Pieza</th><th>Tipo</th><th>Medida</th><th>Vanos</th>
<th>Dientes</th><th>Ranuras</th><th>Hoja</th></tr></thead><tbody>%(filas)s</tbody></table>
<div class="leyenda"><span><i style="color:#e11d48"></i>corte</span>
<span><i style="color:#2563eb;border-top-style:dashed"></i>grabado — dónde apoya la otra pieza</span></div>
%(laminas)s
</div>""" % dict(nombre=nombre, escala=int(cfg.escala), espesor=cfg.espesor_mm,
                 hw=cfg.hoja[0], hh=cfg.hoja[1], kerf=cfg.kerf_mm,
                 n_piezas=stats['n_piezas'], n_hojas=stats['n_hojas'],
                 n_uniones=info.get('n_uniones', 0), material=stats.get('material_cm2', 0),
                 n_plantas=info.get('n_plantas', 1),
                 avisos=avisos, iso_a=iso_armada, iso_e=iso_explotada,
                 filas=filas, laminas=laminas)


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

    for i, colocadas in enumerate(hojas):
        c.setLineWidth(0.1 * MM)
        c.setStrokeColorRGB(0.72, 0.72, 0.75)
        c.rect(0, 0, W * MM, H * MM, stroke=1, fill=0)

        for col in colocadas:
            c.setStrokeColorRGB(0.88, 0.11, 0.28)          # corte
            c.setDash()
            for ext, ints in _anillos(col['geo']):
                for anillo in [ext] + ints:
                    p = c.beginPath()
                    p.moveTo(anillo[0][0] * MM, anillo[0][1] * MM)
                    for x, y in anillo[1:]:
                        p.lineTo(x * MM, y * MM)
                    p.close()
                    c.drawPath(p, stroke=1, fill=0)

            if col['guia'] is not None:
                c.setStrokeColorRGB(0.15, 0.39, 0.92)      # grabado
                c.setDash(2 * MM, 1.5 * MM)
                for ext, ints in _anillos(col['guia']):
                    for anillo in [ext] + ints:
                        p = c.beginPath()
                        p.moveTo(anillo[0][0] * MM, anillo[0][1] * MM)
                        for x, y in anillo[1:]:
                            p.lineTo(x * MM, y * MM)
                        p.close()
                        c.drawPath(p, stroke=1, fill=0)
                c.setDash()

            g = col['geo']
            rp = g.representative_point()
            alto = max(2.5, min(6.0, (g.bounds[2] - g.bounds[0]) / 8.0))
            c.setFillColorRGB(0.07, 0.07, 0.09)
            c.setFont('Helvetica', alto * MM)
            # drawCentredString pone la linea base; se baja media altura para
            # que el numero quede centrado en la pieza y no encima del borde.
            c.drawCentredString(rp.x * MM, (rp.y * MM) - alto * MM * 0.36,
                                str(col['pieza']['id']))

        c.setFillColorRGB(0.42, 0.42, 0.45)
        c.setFont('Helvetica', 4 * MM)
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
                   '/Applications/ODA/ODAFileConverter*/ODAFileConverter'):
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
