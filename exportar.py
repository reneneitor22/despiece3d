# -*- coding: utf-8 -*-
"""Salidas: DXF por hoja (laser) + SVG/HTML imprimible (corte a mano) + guia de armado."""
import os
import ezdxf

CAPA_CORTE = 'CORTE'
CAPA_GRABADO = 'GRABADO'
CAPA_HOJA = 'HOJA'


def _anillos(geom):
    """Devuelve [(coords_exterior, [coords_interiores])] de Polygon o MultiPolygon."""
    partes = geom.geoms if geom.geom_type.startswith('Multi') else [geom]
    out = []
    for p in partes:
        if p.geom_type != 'Polygon' or p.is_empty:
            continue
        out.append((list(p.exterior.coords), [list(r.coords) for r in p.interiors]))
    return out


# ------------------------------------------------------------------- DXF
def hoja_a_dxf(colocadas, cfg, ruta, titulo):
    doc = ezdxf.new('R2010', setup=True)
    doc.header['$INSUNITS'] = 4          # milimetros
    msp = doc.modelspace()
    doc.layers.add(CAPA_CORTE, color=1)
    doc.layers.add(CAPA_GRABADO, color=5)
    doc.layers.add(CAPA_HOJA, color=8)

    W, H = cfg.hoja
    msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True,
                       dxfattribs={'layer': CAPA_HOJA})

    for col in colocadas:
        pz = col['pieza']
        for ext, ints in _anillos(col['geo']):
            msp.add_lwpolyline(ext, close=True, dxfattribs={'layer': CAPA_CORTE})
            for r in ints:
                msp.add_lwpolyline(r, close=True, dxfattribs={'layer': CAPA_CORTE})

        if col['guia'] is not None:
            for ext, ints in _anillos(col['guia']):
                msp.add_lwpolyline(ext, close=True, dxfattribs={'layer': CAPA_GRABADO})
                for r in ints:
                    msp.add_lwpolyline(r, close=True, dxfattribs={'layer': CAPA_GRABADO})

        g = col['geo']
        cx, cy = g.representative_point().x, g.representative_point().y
        alto = max(2.5, min(6.0, (g.bounds[2] - g.bounds[0]) / 8.0))
        msp.add_text(pz['id'],
                     dxfattribs={'layer': CAPA_GRABADO, 'height': alto}
                     ).set_placement((cx, cy), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    msp.add_text(titulo, dxfattribs={'layer': CAPA_HOJA, 'height': 6}
                 ).set_placement((cfg.margen_mm, H - cfg.margen_mm + 1))

    # Sin esto el archivo abre en un zoom cualquiera y el operador puede ver una
    # pantalla vacia hasta que se le ocurre hacer Zoom Extents. Lo que enmarca
    # la vista al abrir es el viewport *Active, no el encabezado: escribirle
    # $EXTMIN/$EXTMAX a `doc.header` no sirve de nada porque ezdxf los reescribe
    # al guardar --los deja en el centinela 1e20/-1e20 de "dibujo vacio"-- ya
    # que quien lleva la cuenta de la extension real es el programa que abre el
    # archivo, y AutoCAD la recalcula sola en el primer regen. Los limites de
    # hoja si se pegan, pero puestos en el layout y no en el encabezado.
    lay = doc.layouts.get('Model')
    lay.dxf.limmin = (0.0, 0.0)
    lay.dxf.limmax = (W, H)
    doc.set_modelspace_vport(height=H * 1.06, center=(W / 2.0, H / 2.0))
    doc.saveas(ruta)


# ------------------------------------------------------------------- SVG
def hoja_a_svg(colocadas, cfg, titulo):
    W, H = cfg.hoja
    p = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.1fmm" height="%.1fmm" '
         'viewBox="0 0 %.3f %.3f">' % (W, H, W, H),
         '<rect x="0" y="0" width="%.3f" height="%.3f" fill="#fff" stroke="#bbb" '
         'stroke-width="0.3"/>' % (W, H),
         '<g transform="translate(0,%.3f) scale(1,-1)">' % H]

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
        etiquetas.append((rp.x, rp.y, pz['id'],
                          max(2.5, min(6.0, (g.bounds[2] - g.bounds[0]) / 8.0))))

    p.append('</g>')
    for x, y, txt, h in etiquetas:
        p.append('<text x="%.3f" y="%.3f" font-family="Helvetica,Arial" font-size="%.2f" '
                 'fill="#111" text-anchor="middle" dominant-baseline="central">%s</text>'
                 % (x, H - y, h, txt))
    p.append('<text x="%.2f" y="%.2f" font-family="Helvetica,Arial" font-size="4" '
             'fill="#666">%s</text>' % (cfg.margen_mm, cfg.margen_mm - 3, titulo))
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
 <li>Empieza por la base (L…) y clava en ella los muros (M…) por los dientes.</li>
 <li>Las marcas grabadas indican dónde cae cada muro: si no coincide, la pieza va al revés.</li>
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
    """Convierte el DXF a DWG, si hay con que.

    ezdxf no escribe DWG -- es formato cerrado de Autodesk -- asi que hay que
    salir a una herramienta de afuera. Se intentan dos, en este orden:

    1. **ODA File Converter**, que escribe DWG de verdad. Es gratis pero se baja
       a mano, asi que no siempre esta.
    2. **dwgwrite (LibreDWG)**, que se instala con brew... y que en la version
       0.14 entrega un archivo que AutoCAD abre EN NEGRO. Medido: recorta todos
       los nombres a la primera letra --las capas CORTE/GRABADO/HOJA quedan como
       C/G/H, y el bloque `*Model_Space` queda como `*`-- asi que las 1396
       entidades quedan colgando de un bloque que ningun layout referencia. El
       archivo pesa, `dwgread` lo relee y hasta cuenta las 1106 polilineas, pero
       el espacio modelo esta vacio y $EXTMIN viene en el valor de "dibujo
       vacio". Pasa igual con un archivo de tres polilineas, o sea que no es el
       tamaño. Por eso el resultado SE VERIFICA abriendo el DWG y contando lo
       que hay en el espacio modelo, no buscando palabras en un volcado de
       texto: esa cuenta de texto es la que dejo pasar un DWG muerto.

    Devuelve (ruta_dwg, None) o (None, aviso). El DXF es la salida buena.
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
        return None, err or AVISO_SIN_DWG

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
