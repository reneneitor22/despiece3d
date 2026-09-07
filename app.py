# -*- coding: utf-8 -*-
"""Despiece 3D - servidor local. Sube modelo -> corte listo."""
import io, json, os, re, shutil, sys, tempfile, traceback, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
JOBS = os.path.join(tempfile.gettempdir(), 'despiece3d_jobs')
os.makedirs(JOBS, exist_ok=True)
MAX = 120 * 1024 * 1024
EXT_OK = {'.stl', '.obj', '.ply', '.glb', '.gltf', '.dae', '.off', '.3mf', '.skp', '.fbx'}

MOD = os.path.join(BASE, 'modelos_prueba')
# Ejemplos listos para probar sin buscar archivos. Los sinteticos siempre estan;
# los de internet solo si se clonaron los repos (ver PRUEBAS_REALES.md).
EJEMPLOS = [
    {'id': 'casa', 'titulo': 'Casa de prueba',
     'pie': 'Sintetica, con vanos, losa y techo · 9 placas',
     'ruta': os.path.join(BASE, 'out', 'casa_prueba.stl'),
     'campos': {'modo': 'estructura', 'escala': '100', 'espesor': '2',
                'hoja': '600x900', 'unidades': 'm'}},
    {'id': 'terreno', 'titulo': 'Terreno de prueba',
     'pie': 'Sintetico, curvas de nivel · 26 piezas',
     'ruta': os.path.join(BASE, 'out', 'terreno_prueba.stl'),
     'campos': {'modo': 'curvas', 'escala': '500', 'espesor': '3',
                'hoja': '500x700', 'unidades': 'm'}},
    {'id': 'bauhaus_env', 'titulo': 'Casa Engel · envolvente',
     'pie': 'Bauhaus de Tel Aviv, muros sin espesor · 56 placas en 1 hoja',
     'ruta': os.path.join(MOD, 'ladybug/obj/engel-house/AngelHouse_Bauhaus-in-Israel.obj'),
     'campos': {'modo': 'estructura', 'escala': '100', 'espesor': '2',
                'hoja': '600x900', 'unidades': 'm', 'envolvente': '1'}},
    {'id': 'bauhaus_piso', 'titulo': 'Casa Engel · piso 10',
     'pie': 'Un solo nivel, muros cortados a su altura · 38 placas',
     'ruta': os.path.join(MOD, 'ladybug/obj/engel-house/AngelHouse_Bauhaus-in-Israel.obj'),
     'campos': {'modo': 'estructura', 'escala': '100', 'espesor': '2',
                'hoja': '600x900', 'unidades': 'm', 'piso': '10'}},
    {'id': 'bauhaus', 'titulo': 'Casa Engel · completa',
     'pie': 'Con entrepisos y muros interiores · 143 placas',
     'ruta': os.path.join(MOD, 'ladybug/obj/engel-house/AngelHouse_Bauhaus-in-Israel.obj'),
     'campos': {'modo': 'estructura', 'escala': '100', 'espesor': '2',
                'hoja': '600x900', 'unidades': 'm'}},
    {'id': 'mainstreet', 'titulo': 'Main Street Place',
     'pie': 'STL de 703 mil caras, 10 676 cuerpos · 303 placas',
     'ruta': os.path.join(MOD, 'ladybug/stl-samples/MainStreetPlace.stl'),
     'campos': {'modo': 'estructura', 'escala': '500', 'espesor': '2',
                'hoja': '600x900', 'unidades': 'm'}},
    {'id': 'gale', 'titulo': 'Crater Gale (Marte)',
     'pie': 'Topografia de la NASA · 247 piezas, 11 partidas por no caber',
     'ruta': os.path.join(MOD, 'nasa/stl/gale_crater.STL'),
     'campos': {'modo': 'curvas', 'escala': '200', 'espesor': '3',
                'hoja': '500x700', 'unidades': 'm'}},
    {'id': 'valles', 'titulo': 'Valles Marineris (Marte)',
     'pie': 'Topografia de la NASA · 293 piezas en 33 hojas',
     'ruta': os.path.join(MOD, 'nasa/stl/mars_valles_mar.STL'.replace('.STL', '.stl')),
     'campos': {'modo': 'curvas', 'escala': '100', 'espesor': '3',
                'hoja': '500x700', 'unidades': 'm'}},
]


def ejemplos_disponibles():
    """Solo los que de verdad estan en disco: los de internet son opcionales."""
    return [e for e in EJEMPLOS if os.path.exists(e['ruta'])]


def parse_multipart(body, boundary):
    campos, archivo = {}, None
    sep = b'--' + boundary
    for parte in body.split(sep):
        if not parte or parte in (b'--\r\n', b'--'):
            continue
        if b'\r\n\r\n' not in parte:
            continue
        cab, dato = parte.split(b'\r\n\r\n', 1)
        dato = dato[:-2] if dato.endswith(b'\r\n') else dato
        cab_s = cab.decode('utf-8', 'replace')
        mname = re.search(r'name="([^"]*)"', cab_s)
        if not mname:
            continue
        nombre = mname.group(1)
        mfile = re.search(r'filename="([^"]*)"', cab_s)
        if mfile and mfile.group(1):
            archivo = (mfile.group(1), dato)
        else:
            campos[nombre] = dato.decode('utf-8', 'replace').strip()
    return campos, archivo


class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        sys.stderr.write('%s - %s\n' % (self.address_string(), a[0] % a[1:]))

    def _send(self, code, tipo, cuerpo, extra=None):
        if isinstance(cuerpo, str):
            cuerpo = cuerpo.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(cuerpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = self.path.split('?')[0]
        if ruta in ('/', '/index.html'):
            return self._send(200, 'text/html; charset=utf-8', PAGINA)
        if ruta == '/ejemplos':
            lista = [{'id': e['id'], 'titulo': e['titulo'], 'pie': e['pie']}
                     for e in ejemplos_disponibles()]
            return self._send(200, 'application/json; charset=utf-8',
                              json.dumps(lista, ensure_ascii=False))
        m = re.match(r'^/r/([a-f0-9]{12})/(.+)$', ruta)
        if m:
            job, nombre = m.group(1), os.path.basename(m.group(2))
            f = os.path.join(JOBS, job, nombre)
            if os.path.isfile(f):
                tipo = ('text/html; charset=utf-8' if nombre.endswith('.html') else
                        'image/svg+xml' if nombre.endswith('.svg') else
                        'application/zip' if nombre.endswith('.zip') else
                        'application/octet-stream')
                extra = ({'Content-Disposition': 'attachment; filename="%s"' % nombre}
                         if nombre.endswith('.zip') else None)
                return self._send(200, tipo, open(f, 'rb').read(), extra)
        self._send(404, 'text/plain; charset=utf-8', 'no existe')

    def _ejemplo(self):
        """Corre un modelo que ya esta en disco, con sus parametros preparados."""
        try:
            n = int(self.headers.get('Content-Length', 0))
            pedido = json.loads(self.rfile.read(n) or b'{}')
            elegido = next((e for e in ejemplos_disponibles()
                            if e['id'] == pedido.get('id')), None)
            if elegido is None:
                return self._send(404, 'application/json; charset=utf-8',
                                  json.dumps({'error': 'ese ejemplo no esta en disco'}))
            job = uuid.uuid4().hex[:12]
            carpeta = os.path.join(JOBS, job)
            os.makedirs(carpeta, exist_ok=True)
            ext = os.path.splitext(elegido['ruta'])[1].lower()
            destino = os.path.join(carpeta, 'modelo' + ext)
            shutil.copyfile(elegido['ruta'], destino)
            r = procesar(destino, dict(elegido['campos']), carpeta, job,
                         re.sub(r'[^A-Za-z0-9_-]+', '_', elegido['titulo']))
            return self._send(200, 'application/json; charset=utf-8',
                              json.dumps(r, ensure_ascii=False))
        except Exception as e:
            traceback.print_exc()
            return self._send(500, 'application/json; charset=utf-8',
                              json.dumps({'error': '%s: %s' % (type(e).__name__, e)},
                                         ensure_ascii=False))

    def do_POST(self):
        if self.path == '/ejemplo':
            return self._ejemplo()
        if self.path != '/cortar':
            return self._send(404, 'text/plain', 'no existe')
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n <= 0 or n > MAX:
                return self._send(413, 'application/json',
                                  json.dumps({'error': 'archivo vacio o mayor a 120 MB'}))
            ctype = self.headers.get('Content-Type', '')
            mb = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
            if not mb:
                return self._send(400, 'application/json', json.dumps({'error': 'peticion mal formada'}))
            boundary = (mb.group(1) or mb.group(2)).strip().encode()

            leido, trozos = 0, []
            while leido < n:
                c = self.rfile.read(min(1 << 20, n - leido))
                if not c:
                    break
                trozos.append(c); leido += len(c)
            campos, archivo = parse_multipart(b''.join(trozos), boundary)
            if not archivo:
                return self._send(400, 'application/json', json.dumps({'error': 'no llego el archivo'}))

            nombre_orig, datos = archivo
            ext = os.path.splitext(nombre_orig)[1].lower()
            if ext not in EXT_OK:
                return self._send(400, 'application/json', json.dumps(
                    {'error': 'formato %s no soportado. Lee STL, OBJ, DAE, PLY, GLB, SKP y FBX.' % (ext or '?')}))

            job = uuid.uuid4().hex[:12]
            carpeta = os.path.join(JOBS, job)
            os.makedirs(carpeta, exist_ok=True)
            ruta_modelo = os.path.join(carpeta, 'modelo' + ext)
            open(ruta_modelo, 'wb').write(datos)
            resultado = procesar(ruta_modelo, campos, carpeta, job,
                                 os.path.splitext(os.path.basename(nombre_orig))[0])
            return self._send(200, 'application/json; charset=utf-8',
                              json.dumps(resultado, ensure_ascii=False))
        except Exception as e:
            traceback.print_exc()
            return self._send(500, 'application/json; charset=utf-8',
                              json.dumps({'error': '%s: %s' % (type(e).__name__, e)}, ensure_ascii=False))


def procesar(ruta_modelo, campos, carpeta, job, nombre):
    import trimesh
    from despiece import (Config, solidificar, rebanar, armar_piezas, acomodar,
                          partir_grandes, ROTACIONES_ORTO)
    from estructura import despiece_estructural
    from isometrica import vista
    import exportar

    unidades = campos.get('unidades', 'm')
    espesor = float(campos.get('espesor', 3) or 3)
    kerf = float(campos.get('kerf', 0.15) or 0)
    hw, hh = [float(v) for v in campos.get('hoja', '500x700').lower().split('x')]

    from despiece import cargar_modelo
    import skp as _skp
    try:
        m = cargar_modelo(ruta_modelo)
    except SystemExit as e:
        # cargar_modelo explica en castellano que paso; aqui eso va a la
        # pantalla en vez de matar el hilo del trabajo.
        return {'error': str(e)}
    if m.is_empty or len(m.faces) == 0:
        return {'error': 'el archivo no trae geometria legible'}
    import fbx as _fbx
    if _skp.es_skp(ruta_modelo) or _fbx.es_fbx(ruta_modelo):
        # SketchUp guarda en pulgadas y el FBX trae su unidad anotada; los dos
        # lectores ya entregan metros, asi que lo que haya escogido el usuario
        # en el selector no aplica.
        unidades = 'm'

    modo_escala = campos.get('modo_escala', 'escala')
    if modo_escala == 'largo':
        largo_cm = float(campos.get('largo_cm', 40) or 40)
        a_mm = {'m': 1000.0, 'cm': 10.0, 'mm': 1.0}[unidades]
        largo_modelo_mm = max(m.extents[0], m.extents[1]) * a_mm
        escala = max(1.0, largo_modelo_mm / (largo_cm * 10.0))
        escala = round(escala / 5.0) * 5 or 5          # a multiplo de 5, mas legible
    else:
        escala = float(campos.get('escala', 200) or 200)

    vaciar = campos.get('vaciar', '1') not in ('0', 'false', '')
    cfg = Config(escala, espesor, kerf, (hw, hh), unidades_modelo=unidades, vaciar=vaciar)

    if campos.get('modo', 'curvas') == 'estructura':
        return _estructural(m, cfg, carpeta, job, nombre, campos)

    solidificado = False
    if not m.is_watertight:
        m = solidificar(m); solidificado = True

    capas = rebanar(m, cfg)
    if not capas:
        return {'error': 'no salieron capas. Revisa unidades del modelo o baja el espesor.'}
    if len(capas) > 400:
        return {'error': 'saldrian %d laminas. Sube la escala o el espesor.' % len(capas)}

    piezas = armar_piezas(capas, vaciar=vaciar)
    piezas, partidas = partir_grandes(piezas, cfg)
    hojas, grandes = acomodar(piezas, cfg)
    if not hojas:
        return {'error': 'ninguna pieza cabe en la hoja. Sube la escala o usa hoja mas grande.'}

    svgs, area_usada, urls, dxfs = [], 0.0, [], []
    for i, colocadas in enumerate(hojas):
        titulo = '%s  hoja %d/%d  1:%d  lamina %.1fmm' % (nombre, i + 1, len(hojas), int(escala), espesor)
        dxf = os.path.join(carpeta, '%s_hoja%02d.dxf' % (nombre, i + 1))
        exportar.hoja_a_dxf(colocadas, cfg, dxf, titulo)
        dxfs.append(dxf)
        svg = exportar.hoja_a_svg(colocadas, cfg, titulo)
        svgs.append(svg)
        open(os.path.join(carpeta, '%s_hoja%02d.svg' % (nombre, i + 1)), 'w').write(svg)
        for col in colocadas:
            area_usada += col['geo'].area

    _extras(hojas, cfg, carpeta, nombre, dxfs,
            '%s  1:%d  lamina %.1fmm' % (nombre, int(escala), espesor))

    factor_m = {'m': 1.0, 'cm': 0.01, 'mm': 0.001}[unidades]
    for pz in piezas:
        pz['z_real_m'] = pz['z_real'] * factor_m
        pz.setdefault('hoja', 0)

    stats = {'n_piezas': len(piezas), 'n_hojas': len(hojas),
             'alto_mm': len(capas) * espesor,
             'aprov': 100.0 * area_usada / (hw * hh * len(hojas)),
             'material_cm2': area_usada / 100.0}
    html = exportar.guia_html(hojas, sorted(piezas, key=lambda p: p['id']), cfg, svgs,
                              nombre, grandes, stats)
    open(os.path.join(carpeta, 'guia.html'), 'w').write(html)

    zip_path = os.path.join(carpeta, '%s_despiece.zip' % re.sub(r'[^\w\-]', '_', nombre))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(carpeta)):
            if f.endswith(('.dxf', '.dwg', '.pdf', '.svg')) or f == 'guia.html':
                z.write(os.path.join(carpeta, f), f)

    return {'ok': True, 'job': job,
            'guia': '/r/%s/guia.html' % job,
            'zip': '/r/%s/%s' % (job, os.path.basename(zip_path)),
            'svgs': svgs,
            'escala': int(escala), 'espesor': espesor,
            'solidificado': solidificado,
            'vaciado': vaciar,
            'grandes': grandes,
            'medidas_maqueta': [round(m.extents[0] * cfg.a_mm), round(m.extents[1] * cfg.a_mm),
                                round(stats['alto_mm'])],
            'stats': {k: round(v, 1) for k, v in stats.items()}}


def _extras(hojas, cfg, carpeta, nombre, dxfs, titulo):
    """PDF de todas las hojas y DWG de cada una, junto a los DXF que ya salieron.

    El DWG puede no salir --depende de que este instalado LibreDWG-- y eso no
    tumba el trabajo: el DXF es la salida buena y el aviso queda en la consola
    del servidor.
    """
    import exportar
    exportar.hojas_a_pdf(hojas, cfg, os.path.join(carpeta, '%s.pdf' % nombre), titulo)
    for dxf in dxfs:
        _dwg, err = exportar.dxf_a_dwg(dxf)
        if err:
            print('   ' + err)
            break


def _empacar(carpeta, nombre):
    zip_path = os.path.join(carpeta, '%s_despiece.zip' % re.sub(r'[^\w\-]', '_', nombre))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(carpeta)):
            if f.endswith(('.dxf', '.dwg', '.pdf', '.svg')) or f == 'guia.html':
                z.write(os.path.join(carpeta, f), f)
    return zip_path


def _estructural(m, cfg, carpeta, job, nombre, campos):
    """Modo casa: muros, losas y techos como placas con uniones."""
    from despiece import acomodar, partir_grandes, ROTACIONES_ORTO
    from estructura import despiece_estructural
    from isometrica import vista
    import exportar

    con_uniones = campos.get('uniones', '1') not in ('0', 'false', '')
    envolvente = campos.get('envolvente', '0') in ('1', 'true', 'on')
    macizos = campos.get('macizos', '0') in ('1', 'true', 'on')
    piso = campos.get('piso', '').strip()
    piso = int(piso) if piso.isdigit() and int(piso) > 0 else None
    piezas, info = despiece_estructural(m, cfg, con_uniones=con_uniones,
                                        solo_envolvente=envolvente, piso=piso,
                                        laminar_macizos=macizos)
    if 'error' in info:
        return {'error': info['error']}

    piezas, partidas = partir_grandes(piezas, cfg, rotaciones=ROTACIONES_ORTO)
    hojas, grandes = acomodar(piezas, cfg, rotaciones=ROTACIONES_ORTO)
    if not hojas:
        return {'error': 'ninguna pieza cabe en la hoja. Sube la escala o usa hoja mas grande.'}

    svgs, area, dxfs = [], 0.0, []
    for i, col in enumerate(hojas):
        tit = '%s  hoja %d/%d  1:%d  lamina %.1fmm' % (nombre, i + 1, len(hojas),
                                                       int(cfg.escala), cfg.espesor_mm)
        dxf = os.path.join(carpeta, '%s_hoja%02d.dxf' % (nombre, i + 1))
        exportar.hoja_a_dxf(col, cfg, dxf, tit)
        dxfs.append(dxf)
        svg = exportar.hoja_a_svg(col, cfg, tit)
        svgs.append(svg)
        open(os.path.join(carpeta, '%s_hoja%02d.svg' % (nombre, i + 1)), 'w').write(svg)
        area += sum(c['geo'].area for c in col)

    _extras(hojas, cfg, carpeta, nombre, dxfs,
            '%s  1:%d  lamina %.1fmm' % (nombre, int(cfg.escala), cfg.espesor_mm))

    for p in piezas:
        p.setdefault('hoja', 0)
    stats = {'n_piezas': len(piezas), 'n_hojas': len(hojas), 'material_cm2': area / 100.0}
    iso_a = vista(info['placas'])
    iso_e = vista(info['placas'], explotar=(cfg.espesor_mm / cfg.a_mm) * 10)
    html = exportar.guia_estructural(hojas, piezas, cfg, svgs, nombre, grandes, stats, info,
                                     iso_a, iso_e)
    open(os.path.join(carpeta, 'guia.html'), 'w').write(html)
    zip_path = _empacar(carpeta, nombre)

    usada = [0.0, 0.0]
    for col in hojas:
        for c in col:
            b = c['geo'].bounds
            usada[0] = max(usada[0], b[2]); usada[1] = max(usada[1], b[3])

    return {'ok': True, 'job': job, 'modo': 'estructura',
            'guia': '/r/%s/guia.html' % job,
            'zip': '/r/%s/%s' % (job, os.path.basename(zip_path)),
            'svgs': svgs, 'iso': iso_a, 'iso_explotada': iso_e,
            'escala': int(cfg.escala), 'espesor': cfg.espesor_mm,
            'grandes': grandes,
            'por_tipo': info['por_tipo'], 'n_uniones': info['n_uniones'],
            'n_recortes': info['n_recortes'], 'avisos': info.get('avisos', []),
            'descartados': len(info.get('descartados', [])),
            'cabe_en': [round(usada[0] + cfg.margen_mm), round(usada[1] + cfg.margen_mm)],
            'stats': {k: round(v, 1) for k, v in stats.items()}}


PAGINA = r"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Despiece 3D</title>
<style>
:root{
 --bg:#f5f5f7; --card:#fff; --linea:#e4e4e7; --txt:#18181b; --tenue:#71717a;
 --acento:#e11d48; --acento2:#2563eb; --ok:#16a34a; --aviso:#b45309; --avisobg:#fff7ed;
 color-scheme:light;
}
@media(prefers-color-scheme:dark){:root:not([data-t="light"]){
 --bg:#0b0b0d; --card:#161619; --linea:#26262b; --txt:#f4f4f5; --tenue:#a1a1aa;
 --avisobg:#2a1c0b; color-scheme:dark;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:40px 20px 90px}
header{margin-bottom:28px}
h1{font-size:30px;letter-spacing:-.02em;margin:0 0 6px}
h1 span{color:var(--acento)}
.lead{color:var(--tenue);margin:0;max-width:60ch}
.card{background:var(--card);border:1px solid var(--linea);border-radius:14px;padding:20px;margin-bottom:16px}
.drop{border:2px dashed var(--linea);border-radius:12px;padding:34px 20px;text-align:center;
 cursor:pointer;transition:border-color .15s,background .15s}
.drop:hover,.drop.on{border-color:var(--acento);background:color-mix(in srgb,var(--acento) 5%,transparent)}
.drop b{display:block;font-size:16px;margin-bottom:4px}
.drop small{color:var(--tenue)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-top:18px}
label{display:block;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
 color:var(--tenue);margin-bottom:5px}
input:not([type=radio]),select{width:100%;min-width:0;padding:9px 11px;border:1px solid var(--linea);
 border-radius:9px;background:var(--card);color:var(--txt);font:inherit;font-size:14px}
input:focus,select:focus{outline:2px solid var(--acento);outline-offset:1px}
.seg{display:flex;gap:0;border:1px solid var(--linea);border-radius:9px;overflow:hidden}
.seg label{flex:1;margin:0;padding:9px 6px;text-align:center;cursor:pointer;font-size:12px;
 background:var(--card);color:var(--tenue);text-transform:none;letter-spacing:0;font-weight:500}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg input:checked+span{color:#fff}
.seg label:has(input:checked){background:var(--acento);color:#fff}
.seg label+label{border-left:1px solid var(--linea)}
button.go{margin-top:20px;width:100%;padding:13px;border:0;border-radius:11px;background:var(--acento);
 color:#fff;font:inherit;font-weight:600;font-size:15px;cursor:pointer}
button.go:disabled{opacity:.5;cursor:default}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(115px,1fr));gap:10px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--linea);border-radius:11px;padding:12px 14px}
.kpi b{display:block;font-size:21px;letter-spacing:-.01em}
.kpi span{font-size:12px;color:var(--tenue)}
.aviso{background:var(--avisobg);border:1px solid color-mix(in srgb,var(--aviso) 35%,transparent);
 color:var(--aviso);border-radius:11px;padding:11px 14px;margin-bottom:14px;font-size:13px}
.acciones{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.ejs{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:10px;margin-top:14px}
.ej{text-align:left;padding:11px 13px;border:1px solid var(--linea);border-radius:11px;
 background:var(--card);color:var(--txt);font:inherit;cursor:pointer;transition:border-color .15s}
.ej:hover:not(:disabled){border-color:var(--acento)}
.ej:disabled{opacity:.55;cursor:default}
.ej b{display:block;font-size:14px;margin-bottom:2px}
.ej small{color:var(--tenue);font-size:12px;line-height:1.35;display:block}
.ejs-tit{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;
 color:var(--tenue);margin:22px 0 0}
.chk{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--txt);
 text-transform:none;letter-spacing:0;font-weight:500;margin:0}
.chk input{width:auto;min-width:0}
.btn{display:inline-block;padding:10px 16px;border-radius:10px;text-decoration:none;font-size:14px;
 font-weight:600;border:1px solid var(--linea);color:var(--txt);background:var(--card)}
.btn.pri{background:var(--txt);color:var(--bg);border-color:var(--txt)}
.hoja h3{font-size:13px;margin:0 0 10px;color:var(--tenue);font-weight:600;
 text-transform:uppercase;letter-spacing:.05em}
.hoja svg{width:100%;height:auto;background:#fff;border:1px solid var(--linea);border-radius:8px}
.leyenda{display:flex;gap:16px;font-size:12px;color:var(--tenue);margin:6px 0 16px;flex-wrap:wrap}
.leyenda i{display:inline-block;width:20px;height:0;border-top:2px solid;margin-right:6px;vertical-align:middle}
.spin{display:inline-block;width:15px;height:15px;border:2px solid #fff6;border-top-color:#fff;
 border-radius:50%;animation:g .7s linear infinite;vertical-align:-3px;margin-right:8px}
@keyframes g{to{transform:rotate(360deg)}}
.err{color:var(--acento);font-size:14px;margin-top:12px}
[hidden]{display:none!important}
.pista{color:var(--tenue);font-size:13px;margin:8px 0 14px}
.par{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 16px}
@media(max-width:640px){.par{grid-template-columns:1fr}}
.vista{background:var(--card);border:1px solid var(--linea);border-radius:12px;padding:12px}
.vista h4{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--tenue);font-weight:600}
.vista svg{width:100%;height:auto;background:#fff;border-radius:8px}
</style>
<div class="wrap">
<header>
 <h1>Despiece <span>3D</span></h1>
 <p class="lead">Sube tu modelo y te devuelve las piezas cortadas, numeradas y acomodadas en hojas.
 DXF para láser, SVG para imprimir y cortar a mano.</p>
</header>

<div class="card">
 <label>Qué vas a despiezar</label>
 <div class="seg modo">
  <label><input type="radio" name="modo" value="estructura" checked><span>Casa / edificio</span></label>
  <label><input type="radio" name="modo" value="curvas"><span>Terreno / topografía</span></label>
 </div>
 <p class="pista" id="pista">Muros, losas y techos como placas, con dientes para que ensamble sola.
  El modelo debe traer los muros con <b>espesor</b>, no como caras sueltas.</p>
 <div class="drop" id="drop">
  <b id="dropTxt">Arrastra tu modelo aquí</b>
  <small>STL · OBJ · DAE · PLY · GLB · SKP · FBX — hasta 120 MB</small>
  <input type="file" id="file" accept=".stl,.obj,.dae,.ply,.glb,.gltf,.off,.3mf,.skp,.fbx" hidden>
 </div>

 <div class="grid">
  <div>
   <label>Unidades del modelo</label>
   <select id="unidades"><option value="m">metros</option><option value="cm">centímetros</option>
   <option value="mm">milímetros</option></select>
  </div>
  <div>
   <label>Tamaño de la maqueta</label>
   <div class="seg">
    <label><input type="radio" name="ms" value="escala" checked><span>Por escala</span></label>
    <label><input type="radio" name="ms" value="largo"><span>Por medida</span></label>
   </div>
  </div>
  <div id="cEscala">
   <label>Escala 1:</label>
   <input type="number" id="escala" value="200" min="1" step="1">
  </div>
  <div id="cLargo" hidden>
   <label>Que mida (cm de largo)</label>
   <input type="number" id="largo_cm" value="40" min="2" step="1">
  </div>
  <div>
   <label>Lámina</label>
   <select id="espesorSel">
    <option value="1">Cartulina 1 mm</option>
    <option value="2">Cartón 2 mm</option>
    <option value="3" selected>Cartón pluma / MDF 3 mm</option>
    <option value="5">Cartón pluma 5 mm</option>
    <option value="6">MDF 6 mm</option>
    <option value="otro">Otro…</option>
   </select>
  </div>
  <div id="cEspesor" hidden>
   <label>Espesor (mm)</label><input type="number" id="espesor" value="3" min="0.3" step="0.1">
  </div>
  <div>
   <label>Hoja</label>
   <select id="hoja">
    <option value="500x700" selected>Ilustración 50 × 70 cm</option>
    <option value="600x900">Cartón pluma 60 × 90 cm</option>
    <option value="900x600">Láser 90 × 60 cm</option>
    <option value="297x420">A3 29.7 × 42 cm</option>
    <option value="210x297">A4 21 × 29.7 cm</option>
   </select>
  </div>
  <div>
   <label>Kerf del láser (mm)</label>
   <input type="number" id="kerf" value="0.15" min="0" max="1" step="0.05">
  </div>
  <div id="cVaciado" hidden>
   <label>Interior de las láminas</label>
   <div class="seg">
    <label><input type="radio" name="vc" value="1" checked><span>Vaciado</span></label>
    <label><input type="radio" name="vc" value="0"><span>Sólido</span></label>
   </div>
  </div>
  <div id="cUniones">
   <label>Uniones</label>
   <div class="seg">
    <label><input type="radio" name="un" value="1" checked><span>Con dientes</span></label>
    <label><input type="radio" name="un" value="0"><span>A tope</span></label>
   </div>
  </div>
  <div id="cPiso">
   <label>Qué cortar</label>
   <label class="chk"><input type="checkbox" id="envolvente"> Solo la envolvente</label>
   <label class="chk" style="margin-top:6px"><input type="checkbox" id="macizos">
    Laminar escaleras y muebles</label>
  </div>
  <div id="cNivel">
   <label>Piso (vacío = todos)</label>
   <input type="number" id="piso" min="1" step="1" placeholder="todos">
  </div>
 </div>

 <button class="go" id="go" disabled>Elige un modelo</button>
 <div class="err" id="err" hidden></div>

 <p class="ejs-tit">O prueba con un ejemplo</p>
 <div class="ejs" id="ejs"></div>
 <p class="lead" id="ejsNota" style="margin-top:10px;font-size:13px" hidden></p>
</div>

<div id="res" hidden></div>
</div>

<script>
const $=id=>document.getElementById(id);
let archivo=null;
const modo=()=>document.querySelector('input[name=modo]:checked').value;

function pintarModo(){
  const est=modo()==='estructura';
  $('cVaciado').hidden=est; $('cUniones').hidden=!est;
  $('cPiso').hidden=!est; $('cNivel').hidden=!est;
  $('espesorSel').value=est?'2':'3';
  $('espesorSel').dispatchEvent(new Event('change'));
  $('escala').value=est?100:200;
  $('pista').innerHTML=est
    ? 'Muros, losas y techos como placas, con dientes para que ensamble sola. '+
      'El modelo debe traer los muros con <b>espesor</b>, no como caras sueltas.'
    : 'Rebanadas horizontales apiladas, como curvas de nivel. '+
      'Sirve con una superficie de terreno aunque esté abierta.';
}
document.querySelectorAll('input[name=modo]').forEach(r=>r.onchange=pintarModo);

$('drop').onclick=()=>$('file').click();
$('file').onchange=e=>setArchivo(e.target.files[0]);
['dragenter','dragover'].forEach(t=>$('drop').addEventListener(t,e=>{
  e.preventDefault();$('drop').classList.add('on');}));
['dragleave','drop'].forEach(t=>$('drop').addEventListener(t,e=>{
  e.preventDefault();$('drop').classList.remove('on');}));
$('drop').addEventListener('drop',e=>{if(e.dataTransfer.files[0])setArchivo(e.dataTransfer.files[0]);});

function setArchivo(f){
  if(!f)return;
  archivo=f;
  const kb=f.size/1024;
  $('dropTxt').textContent=f.name+'  ·  '+(kb<1024?kb.toFixed(0)+' KB':(kb/1024).toFixed(1)+' MB');
  $('go').disabled=false;$('go').textContent='Generar despiece';
}
document.querySelectorAll('input[name=ms]').forEach(r=>r.onchange=()=>{
  const porEscala=document.querySelector('input[name=ms]:checked').value==='escala';
  $('cEscala').hidden=!porEscala;$('cLargo').hidden=porEscala;});
$('espesorSel').onchange=e=>{
  const otro=e.target.value==='otro';
  $('cEspesor').hidden=!otro;
  if(!otro)$('espesor').value=e.target.value;};

pintarModo();

// Ejemplos: modelos que ya estan en disco del lado del servidor. Los sinteticos
// siempre estan; los de internet solo si se clonaron los repos.
let corriendo=false;
fetch('/ejemplos').then(r=>r.json()).then(lista=>{
  const cont=$('ejs');
  cont.innerHTML=lista.map(e=>
    '<button class="ej" data-id="'+e.id+'"><b>'+e.titulo+'</b><small>'+e.pie+'</small></button>'
  ).join('');
  if(lista.length<4){
    $('ejsNota').hidden=false;
    $('ejsNota').textContent='Solo aparecen los ejemplos que estan en disco. '+
      'Para los modelos reales, clona los repos que dice PRUEBAS_REALES.md.';
  }
  cont.querySelectorAll('.ej').forEach(b=>b.onclick=()=>correrEjemplo(b));
}).catch(()=>{});

async function correrEjemplo(boton){
  if(corriendo)return;
  corriendo=true;
  const antes=boton.innerHTML;
  document.querySelectorAll('.ej').forEach(b=>b.disabled=true);
  boton.innerHTML='<b><span class="spin"></span>Rebanando y acomodando…</b>'+
                  '<small>los modelos grandes tardan un minuto</small>';
  $('err').hidden=true;$('res').hidden=true;
  try{
    const r=await fetch('/ejemplo',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id:boton.dataset.id})});
    const d=await r.json();
    if(d.error){fallo(d.error);}else{pintar(d);
      $('res').scrollIntoView({behavior:'smooth',block:'start'});}
  }catch(e){fallo('No se pudo procesar: '+e.message);}
  finally{
    corriendo=false;
    boton.innerHTML=antes;
    document.querySelectorAll('.ej').forEach(b=>b.disabled=false);
  }
}

$('go').onclick=async()=>{
  if(!archivo)return;
  $('err').hidden=true;$('res').hidden=true;
  $('go').disabled=true;
  $('go').innerHTML='<span class="spin"></span>Rebanando y acomodando…';
  const fd=new FormData();
  fd.append('modelo',archivo);
  fd.append('unidades',$('unidades').value);
  fd.append('modo_escala',document.querySelector('input[name=ms]:checked').value);
  fd.append('escala',$('escala').value);
  fd.append('largo_cm',$('largo_cm').value);
  fd.append('espesor',$('espesor').value);
  fd.append('hoja',$('hoja').value);
  fd.append('kerf',$('kerf').value);
  fd.append('vaciar',document.querySelector('input[name=vc]:checked').value);
  fd.append('modo',modo());
  fd.append('uniones',document.querySelector('input[name=un]:checked').value);
  fd.append('envolvente',$('envolvente').checked?'1':'0');
  fd.append('macizos',$('macizos').checked?'1':'0');
  fd.append('piso',$('piso').value||'');
  try{
    const r=await fetch('/cortar',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error){fallo(d.error);return;}
    pintar(d);
  }catch(e){fallo('No se pudo procesar: '+e.message);}
  finally{$('go').disabled=false;$('go').textContent='Generar despiece';}
};

function fallo(msg){$('err').textContent=msg;$('err').hidden=false;}

function pintar(d){
  if(d.modo==='estructura')return pintarEstructura(d);
  const m=d.medidas_maqueta;
  let avisos='';
  if(d.solidificado)avisos+='<div class="aviso">Tu modelo era una superficie abierta '+
    '(típico de un terreno). Le generamos faldón y base para poder rebanarlo.</div>';
  if(d.grandes&&d.grandes.length)avisos+='<div class="aviso"><b>'+d.grandes.length+
    ' pieza(s) no caben en la hoja</b> ('+d.grandes.join(', ')+'). Sube la escala o usa hoja más grande.</div>';
  if(d.vaciado)avisos+='<div class="aviso" style="background:transparent;border-color:var(--linea);color:var(--tenue)">'+
    'Interiores vaciados: se recorta lo que tapa la lámina de arriba y queda una ceja de 7 mm para pegar. '+
    'Si prefieres bloques macizos, elige <b>Sólido</b>.</div>';

  $('res').innerHTML=
   '<div class="kpis">'+
   kpi(d.stats.n_piezas,'piezas')+
   kpi(d.stats.n_hojas,'hojas')+
   kpi('1:'+d.escala,'escala')+
   kpi(m[0]+'×'+m[1]+'×'+m[2],'maqueta (mm)')+
   kpi(Math.round(d.stats.material_cm2)+' cm²','material cortado')+
   '</div>'+avisos+
   '<div class="acciones">'+
   '<a class="btn pri" href="'+d.zip+'">Descargar DXF + SVG (.zip)</a>'+
   '<a class="btn" href="'+d.guia+'" target="_blank">Abrir guía de armado</a></div>'+
   '<div class="leyenda"><span><i style="color:#e11d48"></i>corte</span>'+
   '<span><i style="color:#2563eb;border-top-style:dashed"></i>grabado — silueta de la pieza de arriba</span></div>'+
   d.svgs.map((s,i)=>'<div class="card hoja"><h3>Hoja '+(i+1)+' de '+d.svgs.length+'</h3>'+s+'</div>').join('');
  $('res').hidden=false;
  $('res').scrollIntoView({behavior:'smooth',block:'start'});
}
function kpi(v,t){return '<div class="kpi"><b>'+v+'</b><span>'+t+'</span></div>';}

function pintarEstructura(d){
  const T=d.por_tipo;
  let avisos='';
  if(d.grandes&&d.grandes.length)avisos+='<div class="aviso"><b>'+d.grandes.length+
    ' pieza(s) no caben en la hoja</b> ('+d.grandes.join(', ')+'). Sube la escala o usa hoja más grande.</div>';
  (d.avisos||[]).forEach(a=>avisos+='<div class="aviso">'+a+'</div>');
  if(!d.n_uniones)avisos+='<div class="aviso">No se generó ninguna unión: las piezas van '+
    'a tope y hay que pegarlas. Suele pasar si los muros son caras sin espesor.</div>';
  if(d.descartados)avisos+='<div class="aviso" style="background:transparent;border-color:var(--linea);color:var(--tenue)">'+
    'Se ignoraron '+d.descartados+' cuerpos que no son láminas (astillas o sólidos macizos).</div>';
  if(d.cabe_en&&d.stats.n_hojas===1)avisos+='<div class="aviso" style="background:transparent;border-color:var(--linea);color:var(--tenue)">'+
    'Todo cabe en '+d.cabe_en[0]+'×'+d.cabe_en[1]+' mm. Si te sobra hoja, puedes bajar la escala '+
    'para que la maqueta salga más grande.</div>';

  $('res').innerHTML=
   '<div class="kpis">'+
   kpi(d.stats.n_piezas,'piezas')+
   kpi((T.muro||0)+' / '+(T.losa||0)+' / '+(T.techo||0),'muros / losas / techos')+
   kpi(d.n_uniones,'uniones')+
   kpi(d.stats.n_hojas,'hojas')+
   kpi('1:'+d.escala,'escala')+
   '</div>'+avisos+
   '<div class="acciones">'+
   '<a class="btn pri" href="'+d.zip+'">Descargar DXF + SVG (.zip)</a>'+
   '<a class="btn" href="'+d.guia+'" target="_blank">Abrir guía de armado</a></div>'+
   '<div class="par"><div class="vista"><h4>Armada</h4>'+d.iso+'</div>'+
   '<div class="vista"><h4>Explotada</h4>'+d.iso_explotada+'</div></div>'+
   '<div class="leyenda"><span><i style="color:#e11d48"></i>corte</span>'+
   '<span><i style="color:#2563eb;border-top-style:dashed"></i>grabado — dónde apoya la otra pieza</span></div>'+
   d.svgs.map((s,i)=>'<div class="card hoja"><h3>Hoja '+(i+1)+' de '+d.svgs.length+'</h3>'+s+'</div>').join('');
  $('res').hidden=false;
  $('res').scrollIntoView({behavior:'smooth',block:'start'});
}
</script>
</html>"""


if __name__ == '__main__':
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 else 3561
    print('Despiece 3D en http://localhost:%d' % puerto)
    ThreadingHTTPServer(('127.0.0.1', puerto), H).serve_forever()
