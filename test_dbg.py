# -*- coding: utf-8 -*-
"""Pruebas de la correa de debug. Correr: python3 -m unittest test_dbg"""
import json
import os
import tempfile
import threading
import time
import unittest

import dbg


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._jobs_dir = dbg.JOBS_DIR
        dbg.JOBS_DIR = self.tmp
        os.environ.pop('DESPIECE_DEBUG', None)
        dbg.clear()

    def tearDown(self):
        dbg.JOBS_DIR = self._jobs_dir
        dbg.clear()

    def _log_de(self, job):
        p = os.path.join(self.tmp, job, 'debug.log')
        if not os.path.exists(p):
            return []
        with open(p, encoding='utf-8') as f:
            return [json.loads(l) for l in f if l.strip()]

    def _job(self, nombre='j'):
        os.makedirs(os.path.join(self.tmp, nombre), exist_ok=True)
        dbg.set_job(nombre)
        return nombre


class TestActivo(Base):
    def test_env_prende(self):
        self.assertFalse(dbg.activo())
        for v in ('1', 'true', 'YES', 'On'):
            os.environ['DESPIECE_DEBUG'] = v
            self.assertTrue(dbg.activo(), v)
        os.environ['DESPIECE_DEBUG'] = '0'
        self.assertFalse(dbg.activo())

    def test_request_prende(self):
        dbg.set_request(True)
        self.assertTrue(dbg.activo())
        dbg.set_request(False)
        self.assertFalse(dbg.activo())


class TestLog(Base):
    def test_info_no_escribe_si_apagado(self):
        job = self._job()
        dbg.log('x', dato=1)
        self.assertEqual(self._log_de(job), [])

    def test_error_escribe_aunque_apagado(self):
        job = self._job()
        dbg.log('boom', nivel='error', motivo='prueba')
        regs = self._log_de(job)
        self.assertEqual(len(regs), 1)
        self.assertEqual(regs[0]['evento'], 'boom')
        self.assertEqual(regs[0]['nivel'], 'error')
        self.assertEqual(regs[0]['job'], job)

    def test_info_escribe_si_prendido(self):
        dbg.set_request(True)
        job = self._job()
        dbg.log('x', dato=1)
        self.assertEqual(len(self._log_de(job)), 1)


class TestEtapa(Base):
    def test_inicio_fin_con_ms(self):
        dbg.set_request(True)
        job = self._job()
        with dbg.etapa('paso'):
            time.sleep(0.02)
        ev = [r['evento'] for r in self._log_de(job)]
        self.assertEqual(ev, ['paso.inicio', 'paso.fin'])
        fin = self._log_de(job)[1]
        self.assertGreaterEqual(fin['ms'], 15)

    def test_excepcion_registra_y_relanza(self):
        job = self._job()  # apagado: el .error debe pasar igual
        with self.assertRaises(ValueError):
            with dbg.etapa('paso'):
                raise ValueError('feo')
        regs = self._log_de(job)
        self.assertEqual(regs[-1]['evento'], 'paso.error')
        self.assertIn('feo', regs[-1]['traceback'])


class TestHilos(Base):
    def test_aislamiento_por_hilo(self):
        dbg.set_request(True)

        def trabajo(nombre):
            os.makedirs(os.path.join(self.tmp, nombre), exist_ok=True)
            dbg.set_request(True)
            dbg.set_job(nombre)
            time.sleep(0.01)
            dbg.log('hola', quien=nombre)
            dbg.clear()

        hs = [threading.Thread(target=trabajo, args=('a',)),
              threading.Thread(target=trabajo, args=('b',))]
        [h.start() for h in hs]
        [h.join() for h in hs]
        a, b = self._log_de('a'), self._log_de('b')
        self.assertEqual([r['quien'] for r in a], ['a'])
        self.assertEqual([r['quien'] for r in b], ['b'])


if __name__ == '__main__':
    unittest.main()
