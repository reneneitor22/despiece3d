# -*- coding: utf-8 -*-
"""Pruebas de la correa en la subida. Correr: python3 -m unittest test_subida

La prueba de punta a punta levanta el servidor real y manda un STL chico.
"""
import http.client
import json
import os
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer

import trimesh

import app
import dbg

# Cubo solido de 2 m de lado, exportado a STL binario.
CUBO_STL = trimesh.creation.box(extents=(2.0, 2.0, 2.0)).export(file_type='stl')


def multipart(campos, archivo):
    b = 'X' + uuid.uuid4().hex
    out = []
    for k, v in campos.items():
        out.append(('--' + b + '\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (k, v)).encode())
    if archivo:
        nombre, datos = archivo
        out.append(('--' + b + '\r\nContent-Disposition: form-data; name="modelo"; filename="%s"\r\n'
                    'Content-Type: application/octet-stream\r\n\r\n' % nombre).encode())
        out.append(datos + b'\r\n')
    out.append(('--' + b + '--\r\n').encode())
    return 'multipart/form-data; boundary=' + b, b''.join(out)


def _truena(*args):
    1 / 0


def _colgado(*args):
    """Se queda pensando para siempre, como un modelo que traba al motor."""
    import time
    with open(os.environ['DESPIECE_PRUEBA_PID'], 'w') as f:
        f.write(str(os.getpid()))
    dbg.marcar('uniones')
    time.sleep(300)


class TestParse(unittest.TestCase):
    def test_meta(self):
        ctype, cuerpo = multipart({'a': '1', 'b': '', 'c': 'z'}, ('m.stl', b'DATA'))
        boundary = ctype.split('boundary=')[1].encode()
        campos, arch, meta = app.parse_multipart(cuerpo, boundary)
        self.assertEqual(campos['a'], '1')
        self.assertEqual(arch[0], 'm.stl')
        self.assertEqual(meta['n_partes'], 4)
        self.assertEqual(meta['vacias'], ['b'])


class TestPOST(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['DESPIECE_DEBUG'] = '1'
        import tempfile
        cls.casos_reales, app.CASOS = app.CASOS, tempfile.mkdtemp()
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), app.H)
        cls.puerto = cls.srv.server_address[1]
        cls.hilo = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop('DESPIECE_DEBUG', None)
        app.CASOS = cls.casos_reales

    def _post(self, ctype, cuerpo, ruta='/cortar'):
        c = http.client.HTTPConnection('127.0.0.1', self.puerto, timeout=60)
        c.request('POST', ruta, body=cuerpo, headers={'Content-Type': ctype,
                                                      'Content-Length': str(len(cuerpo))})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    def test_cubo_ok_y_log(self):
        campos = {'modo': 'curvas', 'unidades': 'm', 'modo_escala': 'escala',
                  'escala': '10', 'espesor': '3', 'hoja': '500x700'}
        ctype, cuerpo = multipart(campos, ('cubo.stl', CUBO_STL))
        status, d = self._post(ctype, cuerpo)
        self.assertEqual(status, 200, d)
        self.assertIn('job', d)
        log = os.path.join(dbg.JOBS_DIR, d['job'], 'debug.log')
        self.assertTrue(os.path.exists(log))
        with open(log, encoding='utf-8') as f:
            eventos = {json.loads(l)['evento'] for l in f if l.strip()}
        for esperado in ('recibir.fin', 'parse.detalle', 'guardar.verif',
                         'modelo.cargado', 'resumen'):
            self.assertIn(esperado, eventos)

    def test_extension_rechazada(self):
        ctype, cuerpo = multipart({'modo': 'curvas'}, ('malo.txt', b'nope'))
        status, d = self._post(ctype, cuerpo)
        self.assertEqual(status, 400)
        self.assertIn('no soportado', d['error'])

    def test_ifc_entra_por_la_pantalla_y_sale_con_semantica(self):
        try:
            import ifcopenshell            # noqa: F401
        except ImportError:
            self.skipTest('sin ifcopenshell')
        import test_ifc
        campos = {'modo': 'estructura', 'unidades': 'mm', 'modo_escala': 'escala',
                  'escala': '50', 'espesor': '2', 'hoja': '500x700'}
        # unidades='mm' a proposito: el .ifc trae la suya y el selector no manda
        ctype, cuerpo = multipart(campos, ('casa.ifc',
                                           test_ifc._ifc_de_prueba().encode()))
        status, d = self._post(ctype, cuerpo)
        self.assertEqual(status, 200, d)
        self.assertEqual(d.get('por_tipo'), {'muro': 2, 'losa': 1, 'techo': 0}, d)
        self.assertTrue(any('IFC trae los elementos nombrados' in a
                            for a in d.get('avisos', [])), d.get('avisos'))
        log = os.path.join(dbg.JOBS_DIR, d['job'], 'debug.log')
        with open(log, encoding='utf-8') as f:
            cargado = [json.loads(l) for l in f
                       if l.strip() and json.loads(l)['evento'] == 'modelo.cargado'][0]
        # si el 'mm' del selector hubiera ganado, el muro de 4 m entraria como
        # uno de 4 mm y no quedaria ni una pieza cortable
        self.assertGreater(max(cargado['extents']), 3.5)

    def test_rvt_se_rechaza_diciendo_como_exportar_ifc(self):
        import test_ifc
        ctype, cuerpo = multipart({'modo': 'estructura'},
                                  ('casa.rvt', test_ifc._rvt_de_prueba('2024')))
        status, d = self._post(ctype, cuerpo)
        self.assertEqual(status, 400)
        self.assertIn('Revit 2024', d['error'])
        self.assertIn('Exportar', d['error'])

    def test_falla_deja_caso_y_se_repite_con_el_folio(self):
        import reproducir
        campos = {'modo': 'curvas', 'unidades': 'm', 'modo_escala': 'escala',
                  'escala': '10', 'espesor': '3', 'hoja': '10x10'}
        ctype, cuerpo = multipart(campos, ('cubo.stl', CUBO_STL))
        status, d = self._post(ctype, cuerpo)
        self.assertIn('entre 50 y 5000', d['error'])
        self.assertEqual(d['caso'], d['job'])          # el folio que ve el alumno
        caso, r, _, _, ruta = reproducir.reproducir(d['caso'])
        self.assertEqual(caso['campos']['hoja'], '10x10')
        self.assertEqual(os.path.basename(ruta), 'cubo.stl')
        self.assertEqual(r['error'], d['error'])

    def test_si_truena_tambien_deja_caso_con_traceback(self):
        app._CORTE = ('test_subida', '_truena')
        try:
            status, d = self._post(*multipart({'modo': 'curvas'}, ('cubo.stl', CUBO_STL)))
        finally:
            app._CORTE = None
        self.assertIn('ZeroDivisionError', d['error'])
        base = os.path.join(app.CASOS, d['caso'])
        with open(os.path.join(base, 'caso.json'), encoding='utf-8') as f:
            self.assertIn('ZeroDivisionError', json.load(f)['traceback'])
        self.assertEqual(os.listdir(os.path.join(base, 'entrada')), ['cubo.stl'])

    def test_corte_colgado_se_detiene_guarda_caso_y_la_fila_sigue(self):
        import tempfile
        import time
        pid_txt = os.path.join(tempfile.mkdtemp(), 'pid')
        os.environ['DESPIECE_PRUEBA_PID'] = pid_txt
        tope = app.MAX_MIN
        app._CORTE, app.MAX_MIN = ('test_subida', '_colgado'), 4 / 60.0
        t0 = time.time()
        try:
            status, d = self._post(*multipart({'modo': 'curvas'}, ('cubo.stl', CUBO_STL)))
        finally:
            app._CORTE, app.MAX_MIN = None, tope
        self.assertLess(time.time() - t0, 30)
        # la etapa en la que se atoro llego desde el otro proceso
        self.assertIn('Armando las uniones', d['error'])
        self.assertTrue(os.path.exists(os.path.join(app.CASOS, d['caso'], 'caso.json')))
        if os.name != 'nt':
            with open(pid_txt) as f:
                pid = int(f.read())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)                   # el colgado ya no existe
        campos = {'modo': 'curvas', 'unidades': 'm', 'modo_escala': 'escala',
                  'escala': '10', 'espesor': '3', 'hoja': '500x700'}
        status, d2 = self._post(*multipart(campos, ('cubo.stl', CUBO_STL)))
        self.assertTrue(d2.get('ok'), d2)         # la fila quedo libre


if __name__ == '__main__':
    unittest.main()
