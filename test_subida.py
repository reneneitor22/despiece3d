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
        cls.srv = ThreadingHTTPServer(('127.0.0.1', 0), app.H)
        cls.puerto = cls.srv.server_address[1]
        cls.hilo = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.environ.pop('DESPIECE_DEBUG', None)

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


if __name__ == '__main__':
    unittest.main()
