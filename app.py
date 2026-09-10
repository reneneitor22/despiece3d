# -*- coding: utf-8 -*-
"""Despiece 3D - servidor local. Sube modelo -> corte listo."""
import io, json, os, re, shutil, sys, tempfile, time, traceback, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
JOBS = os.path.join(tempfile.gettempdir(), 'despiece3d_jobs')

# Etapa -> (que decir, cuanto llevamos). Los porcentajes no son un cronometro:
# son EN QUE PASO va, que es lo unico que se puede prometer sin mentir. El
# nombre de la etapa lo pone dbg.etapa() en el propio codigo que corre.
PASOS = {
    None: ('Preparando…', 2),
    'recibir': ('Recibiendo el modelo', 6),
    'parse': ('Leyendo la subida', 10),
    'guardar': ('Guardando el modelo', 14),
    'ejemplo.copiar': ('Copiando el ejemplo', 14),
    'cargar_modelo': ('Abriendo el modelo', 20),
    'solidificar': ('Cerrando la malla', 28),
    'rebanar': ('Rebanando', 40),
    'armar_piezas': ('Armando las piezas', 52),
    'despiece_estructural': ('Sacando muros, losas y techos', 52),
    'partir_grandes': ('Partiendo las que no caben en la hoja', 64),
    'acomodar': ('Acomodando en las hojas', 76),
    'export.hoja': ('Escribiendo las hojas', 86),
    'export.extras': ('PDF, DWG y guía de armado', 94),
    'listo': ('Listo', 100),
}
os.makedirs(JOBS, exist_ok=True)

import dbg
dbg.JOBS_DIR = JOBS
MAX = 200 * 1024 * 1024
MAX_MB = MAX // (1024 * 1024)   # el tope se escribe UNA vez: pantalla y error salen de aqui
EXT_OK = {'.stl', '.obj', '.ply', '.glb', '.gltf', '.dae', '.off', '.3mf', '.skp',
          '.fbx', '.ifc'}
# Formatos que se reconocen para NO leerlos: en vez de "formato no soportado"
# se contesta con la version del archivo y como sacarle el IFC (ver rvt.py).
EXT_CERRADA = {'.rvt', '.rfa', '.rte', '.rft'}

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
    meta = {'n_partes': 0, 'vacias': []}
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
        meta['n_partes'] += 1
        nombre = mname.group(1)
        mfile = re.search(r'filename="([^"]*)"', cab_s)
        if mfile and mfile.group(1):
            archivo = (mfile.group(1), dato)
        else:
            campos[nombre] = dato.decode('utf-8', 'replace').strip()
            if not campos[nombre]:
                meta['vacias'].append(nombre)
    return campos, archivo, meta


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

    def _query(self):
        return self.path.split('?', 1)[1] if '?' in self.path else ''

    def _job_pedido(self):
        """El id que mando el cliente en la query, o uno nuevo.

        Va en la QUERY y no en el formulario a proposito: la carpeta del trabajo
        se crea ANTES de leer el cuerpo (para que la subida entera quede en su
        debug.log), asi que cuando el id viaja en el multipart ya es tarde. Con
        el id de antemano la pantalla puede preguntar por el avance mientras el
        POST sigue abierto.
        """
        m = re.search(r'(?:^|&)job=([a-f0-9]{12})(?:&|$)', self._query())
        return m.group(1) if m else uuid.uuid4().hex[:12]

    def do_GET(self):
        dbg.set_request('debug=1' in self._query())
        try:
            return self._do_GET()
        finally:
            dbg.clear()

    def _do_GET(self):
        ruta = self.path.split('?')[0]
        if ruta in ('/', '/index.html'):
            return self._send(200, 'text/html; charset=utf-8', PAGINA)
        if ruta == '/progreso':
            m = re.search(r'(?:^|&)job=([a-f0-9]{12})(?:&|$)', self._query())
            e = dbg.progreso(m.group(1)) if m else None
            paso, seg = (e[0], time.time() - e[1]) if e else (None, 0.0)
            etiqueta, pct = PASOS.get(paso, ('Trabajando…', 50))
            return self._send(200, 'application/json; charset=utf-8',
                              json.dumps({'etapa': paso, 'texto': etiqueta,
                                          'pct': pct, 'seg': round(seg, 1)},
                                         ensure_ascii=False))
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
                        'text/plain; charset=utf-8' if nombre.endswith('.log') else
                        'application/octet-stream')
                extra = ({'Content-Disposition': 'attachment; filename="%s"' % nombre}
                         if nombre.endswith(('.zip', '.dxf', '.dwg', '.pdf')) else None)
                return self._send(200, tipo, open(f, 'rb').read(), extra)
        self._send(404, 'text/plain; charset=utf-8', 'no existe')

    def _ejemplo(self):
        """Corre un modelo que ya esta en disco, con sus parametros preparados."""
        job = None
        try:
            n = int(self.headers.get('Content-Length', 0))
            pedido = json.loads(self.rfile.read(n) or b'{}')
            dbg.log('ejemplo.pedido', id=pedido.get('id'))
            elegido = next((e for e in ejemplos_disponibles()
                            if e['id'] == pedido.get('id')), None)
            if elegido is None:
                dbg.log('ejemplo.rechazo', nivel='error', id=pedido.get('id'))
                return self._send(404, 'application/json; charset=utf-8',
                                  json.dumps({'error': 'ese ejemplo no esta en disco'}))
            job = self._job_pedido()
            carpeta = os.path.join(JOBS, job)
            os.makedirs(carpeta, exist_ok=True)
            dbg.set_job(job)
            dbg.marcar('recibir', job)
            ext = os.path.splitext(elegido['ruta'])[1].lower()
            destino = os.path.join(carpeta, 'modelo' + ext)
            with dbg.etapa('ejemplo.copiar', origen=elegido['ruta'], destino=destino):
                shutil.copyfile(elegido['ruta'], destino)
            dbg.log('ejemplo.verif', bytes=os.path.getsize(destino))
            r = procesar(destino, dict(elegido['campos']), carpeta, job,
                         re.sub(r'[^A-Za-z0-9_-]+', '_', elegido['titulo']))
            dbg.marcar('listo', job)
            if isinstance(r, dict) and job:
                r.setdefault('job', job)
            return self._send(200, 'application/json; charset=utf-8',
                              json.dumps(r, ensure_ascii=False))
        except Exception as e:
            traceback.print_exc()
            dbg.log('handler.error', nivel='error', excepcion=repr(e),
                    traceback=traceback.format_exc())
            err = {'error': '%s: %s' % (type(e).__name__, e)}
            if job:
                err['job'] = job
            return self._send(500, 'application/json; charset=utf-8',
                              json.dumps(err, ensure_ascii=False))

    def do_POST(self):
        dbg.set_request('debug=1' in self._query())
        try:
            return self._do_POST()
        finally:
            dbg.clear()

    def _do_POST(self):
        ruta = self.path.split('?')[0]
        if ruta == '/ejemplo':
            return self._ejemplo()
        if ruta != '/cortar':
            return self._send(404, 'text/plain', 'no existe')
        job = None
        try:
            n = int(self.headers.get('Content-Length', 0))
            ctype = self.headers.get('Content-Type', '')
            mb = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', ctype)
            dbg.log('recibir.cabeceras', content_length=n, content_type=ctype,
                    boundary_ok=bool(mb))
            if n <= 0 or n > MAX:
                dbg.log('recibir.rechazo', nivel='error', motivo='tamano', n=n, max=MAX)
                return self._send(413, 'application/json',
                                  json.dumps({'error': 'archivo vacio o mayor a %d MB' % MAX_MB}))
            if not mb:
                dbg.log('recibir.rechazo', nivel='error', motivo='sin_boundary')
                return self._send(400, 'application/json', json.dumps({'error': 'peticion mal formada'}))
            boundary = (mb.group(1) or mb.group(2)).strip().encode()

            # Carpeta del trabajo ya: asi la subida entera queda en su debug.log,
            # que es justo lo que hay que poder ver cuando una subida falla.
            job = self._job_pedido()
            carpeta = os.path.join(JOBS, job)
            os.makedirs(carpeta, exist_ok=True)
            dbg.set_job(job)
            dbg.marcar('recibir', job)

            leido, trozos, hito = 0, [], 4 << 20
            while leido < n:
                c = self.rfile.read(min(1 << 20, n - leido))
                if not c:
                    break
                trozos.append(c); leido += len(c)
                if leido >= hito:
                    dbg.log('recibir.progreso', leido=leido, total=n)
                    hito += 4 << 20
            dbg.log('recibir.fin', leido=leido, total=n, completo=(leido >= n))
            with dbg.etapa('parse'):
                campos, archivo, mp = parse_multipart(b''.join(trozos), boundary)
            dbg.log('parse.detalle', n_partes=mp['n_partes'], campos=sorted(campos),
                    vacias=mp['vacias'],
                    archivo=(archivo[0] if archivo else None),
                    bytes_archivo=(len(archivo[1]) if archivo else 0))
            if not archivo:
                dbg.log('parse.rechazo', nivel='error', motivo='sin_archivo')
                return self._send(400, 'application/json', json.dumps({'error': 'no llego el archivo'}))

            nombre_orig, datos = archivo
            ext = os.path.splitext(nombre_orig)[1].lower()
            dbg.log('validar', ext=ext, ok=(ext in EXT_OK))
            if ext in EXT_CERRADA:
                # Hay que guardarlo para poder leerle la version adentro.
                import rvt as _rvt
                tmp = os.path.join(carpeta, 'cerrado' + ext)
                open(tmp, 'wb').write(datos)
                err = {'error': _rvt.rechazo(tmp)}
                dbg.log('validar.cerrado', nivel='error', ext=ext,
                        version=_rvt.version_rvt(tmp)[0])
                return self._send(400, 'application/json', json.dumps({**err, 'job': job}))
            if ext not in EXT_OK:
                err = {'error': 'formato %s no soportado. Lee IFC, STL, OBJ, DAE, PLY, '
                                'GLB, SKP y FBX.' % (ext or '?')}
                return self._send(400, 'application/json', json.dumps({**err, 'job': job}))

            ruta_modelo = os.path.join(carpeta, 'modelo' + ext)
            with dbg.etapa('guardar', ruta=ruta_modelo):
                open(ruta_modelo, 'wb').write(datos)
            dbg.log('guardar.verif', bytes_pedidos=len(datos),
                    bytes_en_disco=os.path.getsize(ruta_modelo))
            resultado = procesar(ruta_modelo, campos, carpeta, job,
                                 os.path.splitext(os.path.basename(nombre_orig))[0])
            dbg.marcar('listo', job)
            if isinstance(resultado, dict) and job:
                resultado.setdefault('job', job)
            return self._send(200, 'application/json; charset=utf-8',
                              json.dumps(resultado, ensure_ascii=False))
        except Exception as e:
            traceback.print_exc()
            dbg.log('handler.error', nivel='error', excepcion=repr(e),
                    traceback=traceback.format_exc())
            err = {'error': '%s: %s' % (type(e).__name__, e)}
            if job:
                err['job'] = job
            return self._send(500, 'application/json; charset=utf-8',
                              json.dumps(err, ensure_ascii=False))


def _convencion(campos):
    """Capas y colores por operacion, como los pide el taller.

    Lo unico que la cabina de corte mira para saber si una linea se corta, se
    graba o se marca es el nombre de la capa y su color. Cada taller tiene su
    convencion, asi que viene de la pantalla y no cableada.
    """
    import exportar

    def rgb(clave, x):
        v = (campos.get('color_' + clave) or '').strip().lstrip('#')
        if len(v) == 6:
            try:
                return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
            except ValueError:
                pass
        return x

    ops = {}
    for clave, base in exportar.OPS_DEFAULT.items():
        ops[clave] = {'capa': (campos.get('capa_' + clave) or base['capa']).strip().upper()[:60]
                      or base['capa'],
                      'rgb': rgb(clave, base['rgb'])}
    return ops


def _ficha(nombre, cfg, material, hoja_i, hojas_n, n_piezas):
    """Renglones de la tabla de corte que va dentro del DXF/DWG."""
    import datetime
    filas = [
        ('Proyecto', nombre),
        ('Hoja', '%d de %d' % (hoja_i, hojas_n)),
        ('Escala', '1:%d' % int(cfg.escala)),
        ('Material', material or 'sin especificar'),
        ('Espesor de lamina', '%.1f mm' % cfg.espesor_mm),
        ('Tamano de hoja', '%g x %g mm' % cfg.hoja),
        ('Piezas en la hoja', str(n_piezas)),
        ('Fecha', datetime.date.today().isoformat()),
    ]
    if cfg.kerf_mm:
        filas.insert(6, ('Kerf compensado', '%.2f mm' % cfg.kerf_mm))
    return filas


def _material(campos):
    txt = (campos.get('material') or '').strip()
    return txt[:60] if txt else ''


def _capa_hoja(campos):
    return (campos.get('capa_hoja') or '').strip()[:60] or None


def _notas(ops, material, cfg):
    """Las dos lineas que lee el que opera la maquina, arriba del marco.

    Copiado de como lo entrega un arquitecto a mano: "azul graba, rojo corta"
    en texto pelado. La tabla de corte, abajo, es para cotizar; esto es para no
    equivocarse de operacion.
    """
    import exportar
    orden = [('corte', 'CORTA'), ('grabado', 'GRABA'), ('marcado', 'MARCA')]
    legenda = '  ·  '.join(
        '%s %s' % (exportar.nombre_color(ops[k]['rgb']).upper(), verbo)
        for k, verbo in orden)
    return [legenda,
            '%s  ·  hoja %g x %g mm  ·  escala 1:%d'
            % (material or 'material sin especificar', cfg.hoja[0], cfg.hoja[1],
               int(cfg.escala))]


def _cajetin(nombre, cfg, campos):
    """Lo que se GRABA en una pieza: se queda en la maqueta armada."""
    alumno = (campos.get('alumno') or '').strip()[:40]
    lineas = [nombre.upper()[:40], 'ESCALA 1:%d' % int(cfg.escala)]
    if alumno:
        lineas.append(alumno.upper())
    return lineas


def procesar(ruta_modelo, campos, carpeta, job, nombre):
    _t0 = time.perf_counter()
    dbg.log('procesar.inicio', ruta=ruta_modelo, nombre=nombre,
            modo=campos.get('modo', 'curvas'), campos=sorted(campos))
    import trimesh
    from despiece import (Config, solidificar, rebanar, armar_piezas, acomodar,
                          partir_grandes, ROTACIONES_ORTO)
    from estructura import despiece_estructural
    from isometrica import vista
    import exportar

    unidades = campos.get('unidades', 'm')
    espesor = float(campos.get('espesor', 3) or 3)
    # Sin compensacion de kerf. Irving: el taller ya la mete en su maquina, y
    # dos compensaciones encimadas dejan la pieza floja. Se manda la medida
    # real y el que corta decide.
    kerf = float(campos.get('kerf', 0) or 0)
    hoja_txt = (campos.get('hoja') or '500x700').lower()
    if hoja_txt == 'otra':
        hoja_txt = '%sx%s' % (campos.get('hoja_w') or 500, campos.get('hoja_h') or 700)
    try:
        hw, hh = [float(v) for v in hoja_txt.split('x')[:2]]
    except ValueError:
        dbg.log('procesar.error', nivel='error', motivo='hoja_invalida', hoja=hoja_txt)
        return {'error': 'medida de hoja invalida: %s' % hoja_txt}
    if not (50 <= hw <= 5000 and 50 <= hh <= 5000):
        dbg.log('procesar.error', nivel='error', motivo='hoja_fuera_rango', hw=hw, hh=hh)
        return {'error': 'la hoja debe medir entre 50 y 5000 mm por lado'}

    from despiece import cargar_modelo
    import skp as _skp
    try:
        with dbg.etapa('cargar_modelo', ruta=ruta_modelo):
            m = cargar_modelo(ruta_modelo)
    except SystemExit as e:
        # cargar_modelo explica en castellano que paso; aqui eso va a la
        # pantalla en vez de matar el hilo del trabajo.
        dbg.log('cargar_modelo.rechazo', nivel='error', motivo=str(e))
        return {'error': str(e)}
    dbg.log('modelo.cargado', vertices=len(m.vertices), caras=len(m.faces),
            extents=[round(x, 4) for x in m.extents],
            watertight=bool(m.is_watertight), vacio=bool(m.is_empty))
    if m.is_empty or len(m.faces) == 0:
        dbg.log('procesar.error', nivel='error', motivo='sin_geometria')
        return {'error': 'el archivo no trae geometria legible'}
    import fbx as _fbx
    import ifc as _ifc
    if _skp.es_skp(ruta_modelo) or _fbx.es_fbx(ruta_modelo) or _ifc.es_ifc(ruta_modelo):
        # SketchUp guarda en pulgadas, el FBX trae su unidad anotada y el IFC la
        # declara en IfcUnitAssignment; los tres lectores ya entregan metros,
        # asi que lo que haya escogido el usuario en el selector no aplica.
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
        r = _estructural(m, cfg, carpeta, job, nombre, campos)
        if isinstance(r, dict) and r.get('ok'):
            r['segundos'] = round(time.perf_counter() - _t0, 1)
        return r

    solidificado = False
    if not m.is_watertight:
        with dbg.etapa('solidificar'):
            m = solidificar(m)
        solidificado = True

    with dbg.etapa('rebanar', escala=int(escala), espesor=espesor):
        capas = rebanar(m, cfg)
    dbg.log('rebanar.detalle', n_capas=len(capas))
    if not capas:
        dbg.log('procesar.error', nivel='error', motivo='sin_capas')
        return {'error': 'no salieron capas. Revisa unidades del modelo o baja el espesor.'}
    if len(capas) > 400:
        dbg.log('procesar.error', nivel='error', motivo='demasiadas_laminas', n=len(capas))
        return {'error': 'saldrian %d laminas. Sube la escala o el espesor.' % len(capas)}

    with dbg.etapa('armar_piezas'):
        piezas = armar_piezas(capas, vaciar=vaciar)
    with dbg.etapa('partir_grandes'):
        piezas, partidas = partir_grandes(piezas, cfg)
    with dbg.etapa('acomodar'):
        hojas, grandes = acomodar(piezas, cfg)
    dbg.log('acomodar.detalle', n_piezas=len(piezas), partidas=partidas,
            n_hojas=len(hojas), grandes=grandes)
    if not hojas:
        dbg.log('procesar.error', nivel='error', motivo='no_cabe')
        return {'error': 'ninguna pieza cabe en la hoja. Sube la escala o usa hoja mas grande.'}

    ops = _convencion(campos)
    material = _material(campos)
    con_tabla = campos.get('tabla', '1') not in ('0', 'false', '')
    con_marco = campos.get('marco', '1') not in ('0', 'false', '')
    capa_hoja = _capa_hoja(campos)
    notas = _notas(ops, material, cfg) if campos.get('notas', '1') not in ('0', 'false', '') else None
    cajetin = _cajetin(nombre, cfg, campos) if campos.get('cajetin', '1') not in ('0', 'false', '') else None

    svgs, area_usada, urls, dxfs = [], 0.0, [], []
    for i, colocadas in enumerate(hojas):
        titulo = '%s  hoja %d/%d  1:%d  lamina %.1fmm' % (nombre, i + 1, len(hojas), int(escala), espesor)
        dxf = os.path.join(carpeta, '%s_hoja%02d.dxf' % (nombre, i + 1))
        with dbg.etapa('export.hoja', i=i + 1, piezas=len(colocadas)):
            exportar.hoja_a_dxf(colocadas, cfg, dxf, titulo, ops=ops, marco=con_marco,
                                ficha=_ficha(nombre, cfg, material, i + 1, len(hojas),
                                             len(colocadas)) if con_tabla else None,
                                capa_hoja=capa_hoja, notas=notas, cajetin=cajetin)
            svg = exportar.hoja_a_svg(colocadas, cfg, titulo, notas=notas,
                                      cajetin=cajetin)
        dxfs.append(dxf)
        svgs.append(svg)
        open(os.path.join(carpeta, '%s_hoja%02d.svg' % (nombre, i + 1)), 'w').write(svg)
        for col in colocadas:
            area_usada += col['geo'].area

    with dbg.etapa('export.extras'):
        dwg = _extras(hojas, cfg, carpeta, nombre, dxfs,
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
            if f.endswith(('.dxf', '.dwg', '.pdf', '.svg')) or f in ('guia.html', 'debug.log'):
                z.write(os.path.join(carpeta, f), f)
    dbg.log('zip.fin', bytes=os.path.getsize(zip_path))
    dbg.log('resumen', ms_total=round((time.perf_counter() - _t0) * 1000, 1),
            n_piezas=len(piezas), n_hojas=len(hojas),
            archivos=[{'nombre': a['nombre']} for a in _descargables(carpeta, job)])

    return {'ok': True, 'job': job,
            'segundos': round(time.perf_counter() - _t0, 1),
            'dwg': dwg, 'capas': {k: v['capa'] for k, v in ops.items()},
            'archivos': _descargables(carpeta, job),
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

    El DWG puede no salir --hace falta el ODA File Converter-- y eso no tumba el
    trabajo: el DXF lleva lo mismo, capas y tabla incluidas, y toda cabina de
    corte lo lee. Devuelve {'n':cuantos DWG salieron, 'aviso':por que no} para
    que la pantalla lo diga en vez de esconderlo en la consola del servidor.
    """
    import exportar
    exportar.hojas_a_pdf(hojas, cfg, os.path.join(carpeta, '%s.pdf' % nombre), titulo)
    hechos, aviso = 0, None
    for dxf in dxfs:
        ruta, err = exportar.dxf_a_dwg(dxf)
        if err:
            aviso = err
            print('   ' + err)
            dbg.log('export.dwg.aviso', nivel='error', err=err)
            break
        hechos += 1
    return {'n': hechos, 'aviso': aviso}


def _descargables(carpeta, job):
    """Los archivos que el alumno manda al taller, uno por uno."""
    return [{'nombre': f, 'url': '/r/%s/%s' % (job, f)}
            for f in sorted(os.listdir(carpeta))
            if f.endswith(('.dxf', '.dwg', '.pdf'))]


def _empacar(carpeta, nombre):
    zip_path = os.path.join(carpeta, '%s_despiece.zip' % re.sub(r'[^\w\-]', '_', nombre))
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(os.listdir(carpeta)):
            if f.endswith(('.dxf', '.dwg', '.pdf', '.svg')) or f in ('guia.html', 'debug.log'):
                z.write(os.path.join(carpeta, f), f)
    return zip_path


def _estructural(m, cfg, carpeta, job, nombre, campos):
    """Modo casa: muros, losas y techos como placas con uniones."""
    _t0 = time.perf_counter()
    from despiece import acomodar, partir_grandes, ROTACIONES_ORTO
    from estructura import despiece_estructural
    from isometrica import vista
    import exportar

    con_uniones = campos.get('uniones', '1') not in ('0', 'false', '')
    envolvente = campos.get('envolvente', '0') in ('1', 'true', 'on')
    macizos = campos.get('macizos', '0') in ('1', 'true', 'on')
    grabar_planta = campos.get('planta_grabada', '1') not in ('0', 'false', '')
    try:
        bastidor = float(campos.get('bastidor') or 0)
    except ValueError:
        bastidor = 0.0
    bastidor = min(max(bastidor, 0.0), 120.0)
    juntar = campos.get('juntar_plantas', '0') in ('1', 'true', 'on')
    piso = campos.get('piso', '').strip()
    piso = int(piso) if piso.isdigit() and int(piso) > 0 else None
    with dbg.etapa('despiece_estructural', uniones=con_uniones, envolvente=envolvente,
                   macizos=macizos, piso=piso):
        piezas, info = despiece_estructural(m, cfg, con_uniones=con_uniones,
                                            solo_envolvente=envolvente, piso=piso,
                                            laminar_macizos=macizos,
                                            grabar_planta=grabar_planta,
                                            bastidor_mm=bastidor)
    if 'error' in info:
        dbg.log('estructural.error', nivel='error', motivo=info['error'])
        return {'error': info['error']}
    dbg.log('estructural.detalle', n_piezas=len(piezas),
            uniones=info.get('n_uniones'), recortes=info.get('n_recortes'),
            descartados=len(info.get('descartados', [])))

    with dbg.etapa('partir_grandes'):
        piezas, partidas = partir_grandes(piezas, cfg, rotaciones=ROTACIONES_ORTO)
    with dbg.etapa('acomodar'):
        hojas, grandes = acomodar(piezas, cfg, rotaciones=ROTACIONES_ORTO,
                                  agrupar=None if juntar else 'planta')
    dbg.log('acomodar.detalle', n_piezas=len(piezas), n_hojas=len(hojas), grandes=grandes)
    if not hojas:
        dbg.log('estructural.error', nivel='error', motivo='no_cabe')
        return {'error': 'ninguna pieza cabe en la hoja. Sube la escala o usa hoja mas grande.'}

    ops = _convencion(campos)
    material = _material(campos)
    con_tabla = campos.get('tabla', '1') not in ('0', 'false', '')
    con_marco = campos.get('marco', '1') not in ('0', 'false', '')
    capa_hoja = _capa_hoja(campos)
    notas = _notas(ops, material, cfg) if campos.get('notas', '1') not in ('0', 'false', '') else None
    cajetin = _cajetin(nombre, cfg, campos) if campos.get('cajetin', '1') not in ('0', 'false', '') else None

    n_plantas = info.get('n_plantas', 1)
    svgs, area, dxfs = [], 0.0, []
    for i, col in enumerate(hojas):
        zonas = sorted({c['pieza'].get('rotulo', 'PLANTA 1') for c in col})
        plantas = sorted({c['pieza'].get('planta', 1) for c in col})
        # La hoja dice de que zona es: es lo primero que busca quien arma, y con
        # las plantas separadas cada hoja suele ser de una sola.
        sello = '  %s' % zonas[0] if len(zonas) == 1 and not juntar else ''
        tit = '%s  hoja %d/%d%s  1:%d  lamina %.1fmm' % (nombre, i + 1, len(hojas),
                                                         sello, int(cfg.escala),
                                                         cfg.espesor_mm)
        dxf = os.path.join(carpeta, '%s_hoja%02d.dxf' % (nombre, i + 1))
        ficha = _ficha(nombre, cfg, material, i + 1, len(hojas), len(col))
        if len(zonas) > 1 or n_plantas > 1:
            ficha.insert(2, ('Zona', ', '.join(zonas)))
        exportar.hoja_a_dxf(col, cfg, dxf, tit, ops=ops, marco=con_marco,
                            ficha=ficha if con_tabla else None,
                            capa_hoja=capa_hoja, notas=notas, cajetin=cajetin)
        dxfs.append(dxf)
        svg = exportar.hoja_a_svg(col, cfg, tit, notas=notas, cajetin=cajetin)
        svgs.append(svg)
        open(os.path.join(carpeta, '%s_hoja%02d.svg' % (nombre, i + 1)), 'w').write(svg)
        area += sum(c['geo'].area for c in col)

    with dbg.etapa('export.extras'):
        dwg = _extras(hojas, cfg, carpeta, nombre, dxfs,
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
    dbg.log('resumen', ms_total=round((time.perf_counter() - _t0) * 1000, 1),
            n_piezas=len(piezas), n_hojas=len(hojas),
            archivos=[a['nombre'] for a in _descargables(carpeta, job)])

    usada = [0.0, 0.0]
    for col in hojas:
        for c in col:
            b = c['geo'].bounds
            usada[0] = max(usada[0], b[2]); usada[1] = max(usada[1], b[3])

    return {'ok': True, 'job': job, 'modo': 'estructura',
            'dwg': dwg, 'capas': {k: v['capa'] for k, v in ops.items()},
            'archivos': _descargables(carpeta, job),
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
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%2315151a'/%3E%3Crect x='10' y='10' width='12' height='12' rx='2.6' fill='none' stroke='%23fff' stroke-width='2.4'/%3E%3C/svg%3E">
<link rel="apple-touch-icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%2315151a'/%3E%3Crect x='10' y='10' width='12' height='12' rx='2.6' fill='none' stroke='%23fff' stroke-width='2.4'/%3E%3C/svg%3E">
<style>
/* Claro siempre, a proposito: el archivo que sale de aqui se manda a un taller
   y se paga. Una pantalla negra se lee como herramienta de juguete; el papel
   blanco se lee como plano. `color-scheme:light` ademas evita que el sistema
   en modo oscuro pinte los <select> y los <input> de negro por su cuenta. */
:root{
 --bg:#f6f6f3; --card:#fff; --linea:#e6e6e0; --linea2:#d6d6ce;
 --txt:#15151a; --tenue:#6c6c76; --tenue2:#8c8c96;
 --acento:#1d4ed8; --acentobg:#eef2ff;
 --corte:#dc2626; --grabado:#1d4ed8; --marcado:#15803d;
 --aviso:#92400e; --avisobg:#fffbeb; --avisoln:#fde68a;
 --ok:#15803d; --okbg:#f0fdf4; --okln:#bbf7d0;
 --sombra:0 1px 2px rgba(16,16,24,.05),0 1px 3px rgba(16,16,24,.04);
 color-scheme:light;
}
*{box-sizing:border-box}
html,body{background:var(--bg)}
body{margin:0;color:var(--txt);-webkit-font-smoothing:antialiased;
 font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Helvetica,Arial,sans-serif}
.wrap{max-width:940px;margin:0 auto;padding:56px 20px 110px}
header{margin-bottom:34px}
.marca{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.marca i{width:26px;height:26px;border-radius:7px;background:var(--txt);position:relative;flex:none}
.marca i::after{content:"";position:absolute;inset:7px;border:1.5px solid #fff;border-radius:2px}
.marca span{font-size:12px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--tenue)}
.marca .by{font-size:11px;font-weight:500;letter-spacing:.03em;text-transform:none;color:var(--tenue2);
 border-left:1px solid var(--linea2);padding-left:10px}
h1{font-size:36px;line-height:1.12;letter-spacing:-.03em;margin:0 0 10px;font-weight:640}
.lead{color:var(--tenue);margin:0;max-width:62ch;font-size:16px}
.card{background:var(--card);border:1px solid var(--linea);border-radius:16px;padding:24px;
 margin-bottom:16px;box-shadow:var(--sombra)}
.card h2{font-size:15px;margin:0 0 4px;font-weight:640;letter-spacing:-.01em}
.card h2+.sub{margin:0 0 18px;color:var(--tenue);font-size:13.5px}
.drop{border:1.5px dashed var(--linea2);border-radius:13px;padding:38px 20px;text-align:center;
 cursor:pointer;transition:border-color .15s,background .15s;background:#fcfcfa}
.drop:hover,.drop.on{border-color:var(--acento);background:var(--acentobg)}
.drop b{display:block;font-size:16px;margin-bottom:4px;font-weight:600}
/* Ese `b` de arriba es el titulo "Arrastra tu modelo aqui" y es de BLOQUE. Un
   <b> dentro de los renglones chicos --el "IFC" que se quiere resaltar-- lo
   heredaba: se iba a su propio renglon y a 16px, y la linea quedaba partida en
   tres pedazos sin sentido. Se devuelve a inline solo ahi dentro. */
.drop small b{display:inline;font-size:inherit;margin:0;font-weight:600}
.drop small{color:var(--tenue2);font-size:12.5px}
.drop small.nota-ifc{display:block;max-width:34em;margin:10px auto 0;font-size:12px;
     line-height:1.5;opacity:.85}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:16px;margin-top:20px}
label{display:block;font-size:11.5px;font-weight:650;text-transform:uppercase;letter-spacing:.06em;
 color:var(--tenue2);margin-bottom:6px}
input:not([type=radio]):not([type=checkbox]):not([type=color]),select{
 width:100%;min-width:0;padding:10px 12px;border:1px solid var(--linea2);border-radius:10px;
 background:#fff;color:var(--txt);font:inherit;font-size:14px;appearance:none;
 background-image:none}
select{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%236c6c76' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
 background-repeat:no-repeat;background-position:right 12px center;padding-right:32px}
input:focus,select:focus{outline:2px solid var(--acento);outline-offset:1px;border-color:transparent}
.seg{display:flex;border:1px solid var(--linea2);border-radius:10px;overflow:hidden;background:#fff}
.seg label{flex:1;margin:0;padding:10px 6px;text-align:center;cursor:pointer;font-size:13px;
 color:var(--tenue);text-transform:none;letter-spacing:0;font-weight:550;transition:background .12s}
.seg label:hover{background:#f4f4f0}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg label:has(input:checked){background:var(--txt);color:#fff}
.seg label+label{border-left:1px solid var(--linea2)}
button.go{margin-top:24px;width:100%;padding:14px;border:0;border-radius:12px;background:var(--acento);
 color:#fff;font:inherit;font-weight:640;font-size:15px;cursor:pointer;transition:opacity .15s}
button.go:hover:not(:disabled){opacity:.9}
button.go:disabled{background:#e9e9e4;color:var(--tenue2);cursor:default}
details.taller{background:var(--card);border:1px solid var(--linea);border-radius:16px;
 margin-bottom:16px;box-shadow:var(--sombra)}
details.taller>summary{list-style:none;cursor:pointer;padding:18px 24px;display:flex;
 align-items:center;justify-content:space-between;gap:12px}
details.taller>summary::-webkit-details-marker{display:none}
details.taller>summary b{font-size:15px;font-weight:640;letter-spacing:-.01em}
details.taller>summary small{display:block;color:var(--tenue);font-weight:400;font-size:13.5px;margin-top:2px}
details.taller>summary .chev{color:var(--tenue2);font-size:12px;flex:none}
details.taller[open]>summary .chev{transform:rotate(180deg)}
.tallerbody{padding:0 24px 24px}
.capas{display:grid;gap:10px;margin-top:6px}
.capa{display:grid;grid-template-columns:104px 44px 1fr;gap:10px;align-items:center}
.capa .op{font-size:13px;font-weight:600}
.capa .op i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:7px;vertical-align:1px}
input[type=color]{width:44px;height:40px;padding:2px;border:1px solid var(--linea2);
 border-radius:9px;background:#fff;cursor:pointer}
.chk{display:flex;align-items:center;gap:9px;font-size:13.5px;color:var(--txt);
 text-transform:none;letter-spacing:0;font-weight:500;margin:0}
.chk input{width:16px;height:16px;accent-color:var(--acento)}
.nota{color:var(--tenue);font-size:12.5px;margin:14px 0 0;line-height:1.5}
.pista{color:var(--tenue);font-size:13.5px;margin:12px 0 18px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--linea);border-radius:13px;padding:14px 16px;
 box-shadow:var(--sombra)}
.kpi b{display:block;font-size:22px;letter-spacing:-.02em;font-weight:620}
.kpi span{font-size:12px;color:var(--tenue2)}
.aviso{background:var(--avisobg);border:1px solid var(--avisoln);color:var(--aviso);
 border-radius:12px;padding:13px 16px;margin-bottom:12px;font-size:13.5px;line-height:1.5}
.aviso.ok{background:var(--okbg);border-color:var(--okln);color:var(--ok)}
.aviso.gris{background:transparent;border-color:var(--linea);color:var(--tenue)}
.acciones{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}
.btn{display:inline-block;padding:11px 18px;border-radius:11px;text-decoration:none;font-size:14px;
 font-weight:600;border:1px solid var(--linea2);color:var(--txt);background:#fff}
.btn:hover{border-color:var(--tenue2)}
.btn.pri{background:var(--txt);color:#fff;border-color:var(--txt)}
.archivos{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px}
.archivos a{font-size:12.5px;padding:6px 11px;border:1px solid var(--linea);border-radius:8px;
 background:#fff;color:var(--tenue);text-decoration:none;font-variant-numeric:tabular-nums}
.archivos a:hover{color:var(--txt);border-color:var(--tenue2)}
.ejs{display:grid;grid-template-columns:repeat(auto-fit,minmax(218px,1fr));gap:10px;margin-top:12px}
.ej{text-align:left;padding:13px 15px;border:1px solid var(--linea);border-radius:12px;
 background:#fff;color:var(--txt);font:inherit;cursor:pointer;transition:border-color .15s}
.ej:hover:not(:disabled){border-color:var(--acento)}
.ej:disabled{opacity:.55;cursor:default}
.ej b{display:block;font-size:14px;margin-bottom:2px;font-weight:600}
.ej small{color:var(--tenue2);font-size:12px;line-height:1.4;display:block}
.ejs-tit{font-size:11.5px;font-weight:650;text-transform:uppercase;letter-spacing:.06em;
 color:var(--tenue2);margin:26px 0 0}
.hoja h3{font-size:12px;margin:0 0 12px;color:var(--tenue2);font-weight:650;
 text-transform:uppercase;letter-spacing:.06em}
.hoja svg{width:100%;height:auto;background:#fff;border:1px solid var(--linea);border-radius:10px}
.leyenda{display:flex;gap:18px;font-size:12.5px;color:var(--tenue);margin:4px 0 18px;flex-wrap:wrap}
.leyenda i{display:inline-block;width:20px;height:0;border-top:2px solid;margin-right:7px;vertical-align:middle}
.spin{display:inline-block;width:15px;height:15px;border:2px solid #ffffff59;border-top-color:#fff;
 border-radius:50%;animation:g .7s linear infinite;vertical-align:-3px;margin-right:9px}
@keyframes g{to{transform:rotate(360deg)}}
.prog{margin-top:14px}
.progbar{height:7px;border-radius:99px;background:var(--linea);overflow:hidden}
.progbar i{display:block;height:100%;width:2%;border-radius:99px;background:var(--acento);
 transition:width .45s cubic-bezier(.4,0,.2,1)}
.progpie{display:flex;justify-content:space-between;gap:14px;margin-top:7px;
 font-size:12.5px;color:var(--tenue2)}
.progpie span:first-child{color:var(--tenue);font-weight:550}
.err{color:var(--corte);font-size:14px;margin-top:14px;
     /* El rechazo de un .rvt son siete renglones con los pasos del menu de
        Revit; sin esto textContent los pega todos en un parrafo. */
     white-space:pre-line;text-align:left;line-height:1.5}
[hidden]{display:none!important}
.par{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:0 0 16px}
@media(max-width:640px){.par{grid-template-columns:1fr}.wrap{padding-top:34px}h1{font-size:29px}
 .capa{grid-template-columns:1fr 44px 1fr}}
.vista{background:var(--card);border:1px solid var(--linea);border-radius:13px;padding:14px;
 box-shadow:var(--sombra)}
.vista h4{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
 color:var(--tenue2);font-weight:650}
.vista svg{width:100%;height:auto;background:#fff;border-radius:8px}
</style>
<div class="wrap">
<header>
 <div class="marca"><i></i><span>Despiece 3D</span><span class="by">by Irving y René</span></div>
 <h1>Del modelo 3D al archivo de corte.</h1>
 <p class="lead">Sube tu maqueta y baja el archivo listo para el taller: piezas numeradas,
 acomodadas en la hoja, con sus capas de corte, grabado y marcado y la tabla de corte adentro.
 Sin abrir AutoCAD.</p>
</header>

<div class="card">
 <h2>1 · Qué vas a despiezar</h2>
 <p class="sub">De esto depende cómo se parte el modelo.</p>
 <div class="seg modo">
  <label><input type="radio" name="modo" value="estructura" checked><span>Casa / edificio</span></label>
  <label><input type="radio" name="modo" value="curvas"><span>Terreno / topografía</span></label>
 </div>
 <p class="pista" id="pista">Muros, losas y techos como placas, con dientes para que ensamble sola.
  El modelo debe traer los muros con <b>espesor</b>, no como caras sueltas.</p>
 <div class="drop" id="drop">
  <b id="dropTxt">Arrastra tu modelo aquí</b>
  <small><b>IFC</b> · STL · OBJ · DAE · PLY · GLB · SKP · FBX — hasta {{MAX_MB}} MB</small>
  <small class="nota-ifc">Si tu proyecto está en Revit o ArchiCAD, exporta <b>IFC</b>: trae
   escrito cuál elemento es muro y a qué planta va, y el despiece sale mejor que con
   cualquier otro formato.</small>
  <input type="file" id="file" accept=".ifc,.stl,.obj,.dae,.ply,.glb,.gltf,.off,.3mf,.skp,.fbx,.rvt" hidden>
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
    <option value="1">Cartón batería 1 mm</option>
    <option value="2" selected>Cartón batería 2 mm</option>
    <option value="1">MDF 1 mm</option>
    <option value="2">MDF 2 mm</option>
    <option value="3">MDF 3 mm</option>
    <option value="3">Acrílico 3 mm</option>
    <option value="5">Cartón pluma 5 mm</option>
    <option value="otro">Otro…</option>
   </select>
   <p class="nota" id="notaAcrilico" hidden>El acrílico es sobre todo para grabar: en corte
    derrite el borde y las piezas chicas se pegan entre sí.</p>
  </div>
  <div id="cEspesor" hidden>
   <label>Espesor (mm)</label><input type="number" id="espesor" value="2" min="0.3" step="0.1">
  </div>
  <div>
   <label>Hoja</label>
   <select id="hoja">
    <option value="1000x780" selected>Cartón batería 100 × 78 cm</option>
    <option value="500x780">Cartón batería 50 × 78 cm</option>
    <option value="500x700">Ilustración 50 × 70 cm</option>
    <option value="600x900">Cartón pluma 60 × 90 cm</option>
    <option value="900x600">Cama de láser 90 × 60 cm</option>
    <option value="1200x900">Cama de láser 120 × 90 cm</option>
    <option value="600x400">Acrílico 60 × 40 cm</option>
    <option value="1220x2440">MDF 1.22 × 2.44 m</option>
    <option value="297x420">A3 29.7 × 42 cm</option>
    <option value="otra">Otra medida…</option>
   </select>
  </div>
  <div id="cHojaOtra" hidden>
   <label>Hoja a la medida (mm)</label>
   <div style="display:flex;gap:8px;align-items:center">
    <input type="number" id="hoja_w" value="600" min="50" max="5000" step="10">
    <span style="color:var(--tenue2)">×</span>
    <input type="number" id="hoja_h" value="900" min="50" max="5000" step="10">
   </div>
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
   <label class="chk" style="margin-top:8px"><input type="checkbox" id="macizos">
    Laminar escaleras y muebles</label>
   <label class="chk" style="margin-top:8px"><input type="checkbox" id="planta_grabada" checked>
    Grabar la planta sobre la losa</label>
   <label class="chk" style="margin-top:8px"><input type="checkbox" id="no_juntar" checked>
    No juntar las plantas en una hoja</label>
   <label style="margin-top:12px;display:block">Bastidor (mm de faldón, 0 = sin bastidor)</label>
   <input type="number" id="bastidor" value="0" min="0" max="120" step="5">
   <p class="nota">La losa sale con los muros de su planta dibujados encima, puertas incluidas:
    es donde el alumno ve a qué pared corresponde cada pieza. Y cada hoja lleva una sola planta,
    aunque sobre material. El <b>bastidor</b> son 5 piezas más —tabla y cuatro faldones— para
    montar la maqueta encima; salen en su propia zona, al final.</p>
  </div>
  <div id="cNivel">
   <label>Piso (vacío = todos)</label>
   <input type="number" id="piso" min="1" step="1" placeholder="todos">
  </div>
 </div>
</div>

<details class="taller" id="taller">
 <summary>
  <span><b>2 · Cómo lo quiere tu taller</b>
   <small id="resumenTaller">Corte rojo · Grabado azul · Marcado verde · con tabla de corte</small></span>
  <span class="chev">▾</span>
 </summary>
 <div class="tallerbody">
  <div class="capas">
   <div class="capa">
    <span class="op"><i style="background:var(--corte)"></i>Corte</span>
    <input type="color" id="color_corte" value="#ff0000">
    <input type="text" id="capa_corte" value="CORTE" maxlength="60" spellcheck="false">
   </div>
   <div class="capa">
    <span class="op"><i style="background:var(--grabado)"></i>Grabado</span>
    <input type="color" id="color_grabado" value="#0000ff">
    <input type="text" id="capa_grabado" value="GRABADO" maxlength="60" spellcheck="false">
   </div>
   <div class="capa">
    <span class="op"><i style="background:var(--marcado)"></i>Marcado</span>
    <input type="color" id="color_marcado" value="#008000">
    <input type="text" id="capa_marcado" value="MARCADO" maxlength="60" spellcheck="false">
   </div>
   <div class="capa">
    <span class="op"><i style="background:#9ca3af"></i>No se corta</span>
    <span></span>
    <input type="text" id="capa_hoja" value="Defpoints" maxlength="60" spellcheck="false">
   </div>
  </div>
  <p class="nota">Cada línea del archivo sale en la capa de su operación, con el nombre y el color
   que pongas aquí — que es lo único que mira la cabina para saber qué hacer con ella.
   El número de la pieza va en <b>marcado</b>, aparte del grabado, para que el taller lo pueda
   bajar de potencia o apagar sin tocar las huellas de ensamble.</p>
  <div style="display:flex;gap:22px;flex-wrap:wrap;margin-top:16px">
   <label class="chk"><input type="checkbox" id="tabla" checked> Tabla de corte dentro del archivo</label>
   <label class="chk"><input type="checkbox" id="marco" checked> Marco del tamaño de hoja</label>
   <label class="chk"><input type="checkbox" id="notas" checked> Notas del operador arriba del marco</label>
   <label class="chk"><input type="checkbox" id="cajetin" checked> Grabar el cajetín en una pieza</label>
  </div>
  <div style="margin-top:14px">
   <label>Alumno / autor (va grabado en el cajetín)</label>
   <input type="text" id="alumno" maxlength="40" placeholder="opcional" spellcheck="false">
  </div>
  <p class="nota">La tabla lleva material, espesor, escala, medida de hoja, piezas y la
   equivalencia de capas; va <b>debajo</b> del marco. Las notas del operador
   («ROJO CORTA · AZUL GRABA…», material y escala) van <b>arriba</b>. Las dos, junto con el marco
   y los rótulos de planta, caen en la capa que no se corta — de fábrica <b>Defpoints</b>, que es
   la que AutoCAD nunca imprime y todo taller reconoce.
   El <b>cajetín</b> sí se graba, y dentro de una pieza: así se queda en la maqueta armada
   en vez de irse a la basura con el recorte.</p>
 </div>
</details>

<div class="card">
 <button class="go" id="go" disabled>Elige un modelo</button>
 <div class="prog" id="prog" hidden>
  <div class="progbar"><i id="progBar"></i></div>
  <div class="progpie">
   <span id="progTxt">Preparando…</span>
   <span id="progFrase"></span>
  </div>
 </div>
 <div class="err" id="err" hidden></div>
 <p class="ejs-tit">O pruébalo con un ejemplo</p>
 <div class="ejs" id="ejs"></div>
 <p class="nota" id="ejsNota" hidden></p>
 <p class="nota"><a href="#" id="dbgToggle"></a></p>
</div>

<details class="card" id="dbg" hidden>
 <summary><b>Debug</b> — subida y proceso</summary>
 <div id="dbgProg" style="margin:10px 0">
   <div style="height:6px;background:var(--linea);border-radius:3px;overflow:hidden">
     <div id="dbgBar" style="height:100%;width:0;background:var(--acento);transition:width .1s"></div>
   </div>
   <p class="nota" id="dbgProgTxt">sin subida todavía</p>
 </div>
 <dl id="dbgKV" style="display:grid;grid-template-columns:auto 1fr;gap:2px 14px;font-size:13px"></dl>
 <p class="nota"><a href="#" id="dbgLog" hidden target="_blank">ver debug.log del servidor</a></p>
 <pre id="dbgRaw" style="max-height:280px;overflow:auto;background:var(--bg);padding:12px;
   border-radius:8px;font-size:12px;white-space:pre-wrap"></pre>
</details>

<div id="res" hidden></div>
</div>

<script>
const $=id=>document.getElementById(id);
let archivo=null;
const modo=()=>document.querySelector('input[name=modo]:checked').value;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// --- Correa de debug -------------------------------------------------------
const DEBUG=/(?:^|[?&])debug=1(?:&|$)/.test(location.search);
function dbgToggleTxt(){$('dbgToggle').textContent=DEBUG?'ocultar debug':'mostrar debug';}
$('dbgToggle').onclick=e=>{
  e.preventDefault();
  const p=new URLSearchParams(location.search);
  if(DEBUG)p.delete('debug');else p.set('debug','1');
  location.search=p.toString();
};
dbgToggleTxt();
if(DEBUG){$('dbg').hidden=false;$('dbg').open=true;}
function dbgKV(obj){
  $('dbgKV').innerHTML=Object.entries(obj).map(([k,v])=>
    '<dt style="color:var(--tenue)">'+esc(k)+'</dt><dd style="margin:0">'+esc(v)+'</dd>').join('');
}
function dbgProgreso(loaded,total){
  const pct=total?Math.round(loaded/total*100):0;
  $('dbgBar').style.width=pct+'%';
  const mb=n=>(n/1048576).toFixed(1);
  $('dbgProgTxt').textContent='subiendo '+mb(loaded)+' / '+mb(total)+' MB ('+pct+'%)';
}
function dbgResultado(info){
  dbgKV(info.kv);
  $('dbgRaw').textContent=info.raw||'';
  if(info.job){$('dbgLog').hidden=false;$('dbgLog').href='/r/'+info.job+'/debug.log';}
  else{$('dbgLog').hidden=true;}
}
// El id del trabajo lo pone el CLIENTE y viaja en la query: el POST se queda
// abierto minutos y sin id de antemano no hay a quien preguntarle como va.
function nuevoJob(){
  const b=new Uint8Array(6);crypto.getRandomValues(b);
  return [...b].map(x=>x.toString(16).padStart(2,'0')).join('');
}
// Frases para la espera. La barra dice el paso de verdad (viene del servidor);
// esto solo acompaña, y por eso no inventa ningun porcentaje.
const FRASES=['Tranqui arqui, aún hay tiempo',
              'Tranqui arqui, aún hay tiempo',
              'Los modelos grandes tardan un minuto',
              'Va bien. No cierres la pestaña',
              'Tranqui arqui, aún hay tiempo'];
let sondeo=null;
function arrancarProgreso(job){
  const p=$('prog');p.hidden=false;
  $('progBar').style.width='2%';
  $('progTxt').textContent='Preparando…';
  let i=0;
  $('progFrase').textContent=FRASES[0];
  const tick=async()=>{
    try{
      const r=await fetch('/progreso?job='+job);
      const d=await r.json();
      $('progBar').style.width=Math.max(2,d.pct)+'%';
      $('progTxt').textContent=d.texto+(d.seg>4?' · '+Math.round(d.seg)+' s':'');
    }catch(_){}
    if(++i%4===0)$('progFrase').textContent=FRASES[(i/4)%FRASES.length];
  };
  tick();
  sondeo=setInterval(tick,900);
}
function pararProgreso(){
  if(sondeo){clearInterval(sondeo);sondeo=null;}
  $('progBar').style.width='100%';
  setTimeout(()=>{$('prog').hidden=true;},350);
}

// Sube por XHR para tener progreso de subida (fetch no lo da). Resuelve {status,text,ms}.
function subir(url,body,headers){
  return new Promise((resolve,reject)=>{
    const x=new XMLHttpRequest();
    const t0=performance.now();
    x.open('POST',DEBUG?(url+(url.includes('?')?'&':'?')+'debug=1'):url);
    for(const k in (headers||{}))x.setRequestHeader(k,headers[k]);
    x.upload.onprogress=e=>{if(e.lengthComputable)dbgProgreso(e.loaded,e.total);};
    x.onload=()=>resolve({status:x.status,text:x.responseText,ms:Math.round(performance.now()-t0)});
    x.onerror=()=>reject(new Error('red'));
    x.send(body);
  });
}

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

const MAX_MB={{MAX_MB}};

function setArchivo(f){
  if(!f)return;
  // El servidor corta la conexion cuando el cuerpo pasa del tope, y el navegador
  // solo ensena "error de red". Mejor decirlo aqui, antes de subir nada.
  if(f.size>MAX_MB*1048576){
    archivo=null;$('go').disabled=true;
    $('dropTxt').textContent='Arrastra tu modelo aquí';
    fallo(f.name+' pesa '+(f.size/1048576).toFixed(1)+' MB y el tope son '+MAX_MB+' MB.');
    return;}
  $('err').hidden=true;
  archivo=f;
  const kb=f.size/1024;
  $('dropTxt').textContent=f.name+'  ·  '+(kb<1024?kb.toFixed(0)+' KB':(kb/1024).toFixed(1)+' MB');
  $('go').disabled=false;$('go').textContent='Generar archivo de corte';
}
document.querySelectorAll('input[name=ms]').forEach(r=>r.onchange=()=>{
  const porEscala=document.querySelector('input[name=ms]:checked').value==='escala';
  $('cEscala').hidden=!porEscala;$('cLargo').hidden=porEscala;});
$('espesorSel').onchange=e=>{
  const otro=e.target.value==='otro';
  $('cEspesor').hidden=!otro;
  if(!otro)$('espesor').value=e.target.value;
  const t=otro?'':e.target.options[e.target.selectedIndex].text;
  $('notaAcrilico').hidden=!/acr/i.test(t);};
$('hoja').onchange=e=>{$('cHojaOtra').hidden=e.target.value!=='otra';};

// El resumen del cajon cerrado dice como va a salir el archivo, para no tener
// que abrirlo cada vez solo para comprobar.
function resumenTaller(){
  const n=id=>$(id).value.trim().toUpperCase()||id.replace('capa_','').toUpperCase();
  $('resumenTaller').textContent=
    n('capa_corte')+' · '+n('capa_grabado')+' · '+n('capa_marcado')+
    ' · '+n('capa_hoja')+' no se corta'+
    ($('tabla').checked?' · con tabla de corte':' · sin tabla')+
    ($('marco').checked?'':' · sin marco');
}
['capa_corte','capa_grabado','capa_marcado','capa_hoja','tabla','marco'].forEach(id=>{
  $(id).addEventListener('input',resumenTaller);
  $(id).addEventListener('change',resumenTaller);});
['corte','grabado','marcado'].forEach(op=>{
  $('color_'+op).addEventListener('input',e=>{
    e.target.closest('.capa').querySelector('.op i').style.background=e.target.value;});});

// El material que dice la etiqueta es el que va en la tabla de corte: "MDF 3 mm"
// le sirve mas al taller que un 3 pelado.
function material(){
  const s=$('espesorSel');
  if(s.value==='otro')return 'Lámina '+$('espesor').value+' mm';
  return s.options[s.selectedIndex].text.replace('…','').trim();
}

pintarModo();resumenTaller();

let corriendo=false;
fetch('/ejemplos').then(r=>r.json()).then(lista=>{
  const cont=$('ejs');
  cont.innerHTML=lista.map(e=>
    '<button class="ej" data-id="'+e.id+'"><b>'+esc(e.titulo)+'</b><small>'+esc(e.pie)+'</small></button>'
  ).join('');
  if(lista.length<4){
    $('ejsNota').hidden=false;
    $('ejsNota').textContent='Solo aparecen los ejemplos que están en disco. '+
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
                  '<small>Tranqui arqui, aún hay tiempo</small>';
  $('err').hidden=true;$('res').hidden=true;
  const job=nuevoJob();
  arrancarProgreso(job);
  try{
    const r=await subir('/ejemplo?job='+job,JSON.stringify({id:boton.dataset.id}),
                        {'Content-Type':'application/json'});
    let d;try{d=JSON.parse(r.text);}catch(_){d={error:'respuesta no-JSON del servidor'};}
    dbgResultado({job:d.job,raw:JSON.stringify(d,null,2),
      kv:{ejemplo:boton.dataset.id,'status HTTP':r.status,'tiempo total':r.ms+' ms',
          'export (servidor)':(d.segundos!=null?d.segundos+' s':'—')}});
    if(d.error){fallo(d.error);}else{pintar(d);}
  }catch(e){fallo('No se pudo procesar: '+e.message);}
  finally{
    pararProgreso();
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
  const job=nuevoJob();
  arrancarProgreso(job);
  const fd=new FormData();
  fd.append('modelo',archivo);
  fd.append('unidades',$('unidades').value);
  fd.append('modo_escala',document.querySelector('input[name=ms]:checked').value);
  fd.append('escala',$('escala').value);
  fd.append('largo_cm',$('largo_cm').value);
  fd.append('espesor',$('espesor').value);
  fd.append('material',material());
  fd.append('hoja',$('hoja').value);
  fd.append('hoja_w',$('hoja_w').value);
  fd.append('hoja_h',$('hoja_h').value);
  fd.append('vaciar',document.querySelector('input[name=vc]:checked').value);
  fd.append('modo',modo());
  fd.append('uniones',document.querySelector('input[name=un]:checked').value);
  fd.append('envolvente',$('envolvente').checked?'1':'0');
  fd.append('macizos',$('macizos').checked?'1':'0');
  fd.append('planta_grabada',$('planta_grabada').checked?'1':'0');
  fd.append('juntar_plantas',$('no_juntar').checked?'0':'1');
  fd.append('bastidor',$('bastidor').value||'0');
  fd.append('piso',$('piso').value||'');
  fd.append('tabla',$('tabla').checked?'1':'0');
  fd.append('marco',$('marco').checked?'1':'0');
  fd.append('capa_hoja',$('capa_hoja').value);
  fd.append('notas',$('notas').checked?'1':'0');
  fd.append('cajetin',$('cajetin').checked?'1':'0');
  fd.append('alumno',$('alumno').value);
  ['corte','grabado','marcado'].forEach(op=>{
    fd.append('capa_'+op,$('capa_'+op).value);
    fd.append('color_'+op,$('color_'+op).value);});
  try{
    $('dbgProgTxt').textContent='subiendo…';$('dbgBar').style.width='0';
    const r=await subir('/cortar?job='+job,fd);
    let d;try{d=JSON.parse(r.text);}catch(_){d={error:'respuesta no-JSON del servidor'};}
    dbgResultado({job:d.job,raw:JSON.stringify(d,null,2),
      kv:{archivo:archivo.name,
          'tamaño':(archivo.size/1048576).toFixed(2)+' MB',
          'status HTTP':r.status,'tiempo total':r.ms+' ms',
          'export (servidor)':(d.segundos!=null?d.segundos+' s':'—')}});
    if(d.error){fallo(d.error);return;}
    pintar(d);
  }catch(e){fallo('No se pudo procesar: '+e.message);}
  finally{pararProgreso();$('go').disabled=false;
          $('go').textContent='Generar archivo de corte';}
};

function fallo(msg){$('err').textContent=msg;$('err').hidden=false;}
function kpi(v,t){return '<div class="kpi"><b>'+v+'</b><span>'+t+'</span></div>';}
// Cuanto tardo el servidor en generar el archivo (sin contar la subida).
function dur(s){if(s==null)return '—';return s<60?s+' s'
  :Math.floor(s/60)+' m '+String(Math.round(s%60)).padStart(2,'0')+' s';}

// El DWG no siempre sale --hace falta el ODA File Converter-- y eso el alumno
// tiene que verlo aqui, no enterarse cuando el taller le diga que no abre.
function bloqueEntrega(d){
  const dwg=d.dwg||{};
  let h='<div class="acciones">'+
    '<a class="btn pri" href="'+d.zip+'">Descargar todo (.zip)</a>'+
    '<a class="btn" href="'+d.guia+'" target="_blank">Abrir guía de armado</a></div>';
  if(d.archivos&&d.archivos.length)
    h+='<div class="archivos">'+d.archivos.map(a=>
      '<a href="'+a.url+'">'+esc(a.nombre)+'</a>').join('')+'</div>';
  const capas=d.capas||{};
  h+=(dwg.n
    ? '<div class="aviso ok"><b>Listo para mandar al taller.</b> '+dwg.n+(dwg.n===1?' hoja':' hojas')+' en DWG y DXF, '+
      'con las capas '+esc(capas.corte||'CORTE')+' / '+esc(capas.grabado||'GRABADO')+' / '+
      esc(capas.marcado||'MARCADO')+' y la tabla de corte adentro.</div>'
    : '<div class="aviso"><b>Salió en DXF, no en DWG.</b> El DXF lleva exactamente lo mismo '+
      '—capas, colores y tabla de corte— y lo abre AutoCAD y lo lee toda cabina de corte. '+
      'Para que además salga el .dwg hace falta el ODA File Converter (gratis, '+
      'opendesign.com/guestfiles/oda_file_converter): en cuanto esté instalado se detecta solo.</div>');
  return h;
}

function pintar(d){
  if(d.modo==='estructura')return pintarEstructura(d);
  const m=d.medidas_maqueta;
  let avisos='';
  if(d.solidificado)avisos+='<div class="aviso">Tu modelo era una superficie abierta '+
    '(típico de un terreno). Le generamos faldón y base para poder rebanarlo.</div>';
  if(d.grandes&&d.grandes.length)avisos+='<div class="aviso"><b>'+d.grandes.length+
    ' pieza(s) no caben en la hoja</b> ('+esc(d.grandes.join(', '))+'). Sube la escala o usa hoja más grande.</div>';
  if(d.vaciado)avisos+='<div class="aviso gris">'+
    'Interiores vaciados: se recorta lo que tapa la lámina de arriba y queda una ceja de 7 mm para pegar. '+
    'Si prefieres bloques macizos, elige <b>Sólido</b>.</div>';

  $('res').innerHTML=
   '<div class="kpis">'+
   kpi(d.stats.n_piezas,'piezas')+
   kpi(d.stats.n_hojas,'hojas')+
   kpi('1:'+d.escala,'escala')+
   kpi(m[0]+'×'+m[1]+'×'+m[2],'maqueta (mm)')+
   kpi(Math.round(d.stats.material_cm2)+' cm²','material cortado')+
   kpi(dur(d.segundos),'tardó en exportar')+
   '</div>'+avisos+bloqueEntrega(d)+
   leyenda(d,'grabado — silueta de la pieza de arriba')+
   d.svgs.map((s,i)=>'<div class="card hoja"><h3>Hoja '+(i+1)+' de '+d.svgs.length+'</h3>'+s+'</div>').join('');
  $('res').hidden=false;
  $('res').scrollIntoView({behavior:'smooth',block:'start'});
}

function leyenda(d,txtGrabado){
  const c=d.capas||{};
  return '<div class="leyenda">'+
   '<span><i style="color:var(--corte)"></i>'+esc(c.corte||'CORTE')+' — contorno</span>'+
   '<span><i style="color:var(--grabado);border-top-style:dashed"></i>'+
     esc(c.grabado||'GRABADO')+' — '+txtGrabado+'</span>'+
   '<span><i style="color:var(--marcado)"></i>'+esc(c.marcado||'MARCADO')+
     ' — número de pieza</span></div>';
}

function pintarEstructura(d){
  const T=d.por_tipo;
  let avisos='';
  if(d.grandes&&d.grandes.length)avisos+='<div class="aviso"><b>'+d.grandes.length+
    ' pieza(s) no caben en la hoja</b> ('+esc(d.grandes.join(', '))+'). Sube la escala o usa hoja más grande.</div>';
  (d.avisos||[]).forEach(a=>avisos+='<div class="aviso">'+esc(a)+'</div>');
  if(!d.n_uniones)avisos+='<div class="aviso">No se generó ninguna unión: las piezas van '+
    'a tope y hay que pegarlas. Suele pasar si los muros son caras sin espesor.</div>';
  if(d.descartados)avisos+='<div class="aviso gris">'+
    'Se ignoraron '+d.descartados+' cuerpos que no son láminas (astillas o sólidos macizos).</div>';
  if(d.cabe_en&&d.stats.n_hojas===1)avisos+='<div class="aviso gris">'+
    'Todo cabe en '+d.cabe_en[0]+'×'+d.cabe_en[1]+' mm. Si te sobra hoja, puedes bajar la escala '+
    'para que la maqueta salga más grande.</div>';

  $('res').innerHTML=
   '<div class="kpis">'+
   kpi(d.stats.n_piezas,'piezas')+
   kpi((T.muro||0)+' / '+(T.losa||0)+' / '+(T.techo||0),'muros / losas / techos')+
   kpi(d.n_uniones,'uniones')+
   kpi(d.stats.n_hojas,'hojas')+
   kpi('1:'+d.escala,'escala')+
   kpi(dur(d.segundos),'tardó en exportar')+
   '</div>'+avisos+bloqueEntrega(d)+
   '<div class="par"><div class="vista"><h4>Armada</h4>'+d.iso+'</div>'+
   '<div class="vista"><h4>Explotada</h4>'+d.iso_explotada+'</div></div>'+
   leyenda(d,'dónde apoya la otra pieza')+
   d.svgs.map((s,i)=>'<div class="card hoja"><h3>Hoja '+(i+1)+' de '+d.svgs.length+'</h3>'+s+'</div>').join('');
  $('res').hidden=false;
  $('res').scrollIntoView({behavior:'smooth',block:'start'});
}
</script>
</html>"""
PAGINA = PAGINA.replace('{{MAX_MB}}', str(MAX_MB))


def ip_lan():
    """La IP con la que nos ven los demas equipos de la red."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))          # no manda nada, solo elige la interfaz
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--red']
    red = '--red' in sys.argv[1:]           # abrir a la red local: hay que pedirlo
    puerto = int(args[0]) if args else 3561
    host = '0.0.0.0' if red else '127.0.0.1'
    if red:
        ip = ip_lan()
        print('Despiece 3D ABIERTO A LA RED en http://%s:%d' % (ip or '<tu-ip>', puerto))
        print('Cualquiera en esta misma red puede entrar y subir archivos. '
              'Sin contrasena. Ciérralo cuando termines (Ctrl+C).')
    else:
        print('Despiece 3D en http://localhost:%d  (solo esta Mac; usa --red para compartir)'
              % puerto)
    ThreadingHTTPServer((host, puerto), H).serve_forever()
