# -*- coding: utf-8 -*-
"""Arreglos del 12 sep 2026 (revision con 10 agentes, ver CAMBIOS-LOCALES.txt).

Cada prueba reproduce una falla que se encontro y comprueba que ya no pasa. El
codigo entre corchetes es el del hallazgo en la revision.

    python -m unittest test_arreglos
"""
import io
import math
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock

import numpy as np
import trimesh

AQUI = os.path.dirname(os.path.abspath(__file__))
FIJOS = os.path.join(AQUI, 'pruebas_formato')


def fijo(nombre):
    ruta = os.path.join(FIJOS, nombre)
    if not os.path.exists(ruta):
        raise unittest.SkipTest('falta %s' % ruta)
    return ruta


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='test_arreglos_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ruta(self, nombre):
        return os.path.join(self.tmp, nombre)

    def medidas(self, m):
        return np.round(m.extents, 3).tolist()


# ---------------------------------------------------------------- comprimidos
class Comprimidos(Base):
    def test_3mf_no_se_desempaca(self):                                   # [S7]
        from despiece import cargar_modelo, desempacar
        p = self.ruta('caja.3mf')
        trimesh.creation.box((4, 2, 10)).export(p)
        self.assertEqual(desempacar(p, self.tmp), (p, None))
        self.assertEqual(self.medidas(cargar_modelo(p)), [4.0, 2.0, 10.0])

    def test_zip_con_contrasena_se_explica(self):                          # [Z4]
        from despiece import desempacar
        z = self.ruta('cerrado.zip')
        with zipfile.ZipFile(z, 'w') as w:
            w.writestr('casa.obj', 'v 0 0 0\n' * 20)
        datos = bytearray(open(z, 'rb').read())
        datos[datos.find(b'PK\x03\x04') + 6] |= 1          # bandera de cifrado
        datos[datos.find(b'PK\x01\x02') + 8] |= 1
        open(z, 'wb').write(bytes(datos))
        with self.assertRaises(SystemExit) as e:
            desempacar(z, self.tmp)
        self.assertIn('contraseña', str(e.exception))

    def test_zip_prefiere_el_dxf(self):                                     # [Z5]
        import ezdxf
        from despiece import desempacar
        doc = ezdxf.new('R2018')
        doc.modelspace().add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0)])
        dxf = self.ruta('casa.dxf')
        doc.saveas(dxf)
        z = self.ruta('casa.zip')
        with zipfile.ZipFile(z, 'w') as w:
            w.write(dxf, 'casa.dxf')
            w.writestr('casa.dwg', b'AC1032' + b'\0' * 5000)
        elegido, _ = desempacar(z, self.ruta('sale'))
        self.assertTrue(elegido.endswith('casa.dxf'))

    def _7z(self, carpeta, nombre):
        if not shutil.which('bsdtar'):
            raise unittest.SkipTest('sin bsdtar')
        salida = self.ruta(nombre)
        r = subprocess.run(['bsdtar', '--format', '7zip', '-cf', salida, '-C', carpeta, '.'],
                           capture_output=True)
        if r.returncode != 0:
            raise unittest.SkipTest('bsdtar no escribe 7z aqui')
        return salida

    def test_7z_no_sigue_enlaces(self):                                     # [Z1]
        from despiece import desempacar
        ajeno = self.ruta('otro_trabajo.stl')
        trimesh.creation.box((7, 5, 3)).export(ajeno)
        src = self.ruta('src')
        os.makedirs(src)
        try:
            os.symlink(ajeno, os.path.join(src, 'casa.stl'))
        except (OSError, NotImplementedError):
            raise unittest.SkipTest('este sistema no deja crear enlaces (Windows sin privilegio)')
        siete = self._7z(src, 'enlace.7z')
        with self.assertRaises(SystemExit) as e:
            desempacar(siete, self.ruta('sale'))
        self.assertIn('no trae ningun modelo', str(e.exception))

    def test_7z_con_tope(self):                                             # [Z2]
        import despiece
        src = self.ruta('src')
        os.makedirs(src)
        with open(os.path.join(src, 'casa.stl'), 'wb') as f:
            f.write(b'\0' * (6 << 20))
        siete = self._7z(src, 'bomba.7z')
        with mock.patch.object(despiece, 'TOPE_DESEMPACAR', 1 << 20):
            with self.assertRaises(SystemExit) as e:
                despiece.desempacar(siete, self.ruta('sale'))
        self.assertIn('pasa de', str(e.exception))
        self.assertFalse(os.path.exists(self.ruta('sale/desempacado/casa.stl')))


# -------------------------------------------------------- STEP, GLB, DAE, IFC
class Formatos(Base):
    def test_step_tolerancia_en_mm(self):                                   # [F1]
        from despiece import _tolerancia_step
        self.assertEqual(_tolerancia_step('cualquiera.step')['tol_linear'], 1.0)

    def test_glb_en_milimetros_se_corrige(self):                            # [F3]
        from despiece import cargar_modelo
        p = self.ruta('casa_mm.glb')
        trimesh.creation.box((10000, 6000, 8000)).export(p)
        m = cargar_modelo(p)
        self.assertEqual(self.medidas(m), [10.0, 8.0, 6.0])
        self.assertTrue(m.metadata.get('despiece_avisos'))

    def test_dae_comillas_simples_y_x_arriba(self):                         # [F4]
        from despiece import cargar_modelo
        txt = open(fijo('caja_blender.dae'), encoding='utf-8').read()
        p = self.ruta('simples.dae')
        open(p, 'w').write(txt.replace('meter="1"', "meter='0.0254'"))
        self.assertEqual(self.medidas(cargar_modelo(p)),
                         [round(4 * 0.0254, 3), round(2 * 0.0254, 3), round(10 * 0.0254, 3)])
        p = self.ruta('xup.dae')
        open(p, 'w').write(txt.replace('Z_UP', 'X_UP'))
        self.assertEqual(self.medidas(cargar_modelo(p)), [2.0, 10.0, 4.0])

    def test_ifc_de_cabecera_larga_no_es_step(self):                        # [F5]
        from despiece import es_step
        cab = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('%s'),'2;1');\n"
               "FILE_NAME('a','',(''),(''),'','','');\nFILE_SCHEMA(('%s'));\nENDSEC;\n")
        p = self.ruta('largo.ifc')
        open(p, 'w').write(cab % ('x' * 4040, 'IFC4'))
        self.assertFalse(es_step(p))
        p = self.ruta('pieza.dat')
        open(p, 'w').write(cab % ('x', 'AUTOMOTIVE_DESIGN'))
        self.assertTrue(es_step(p))


# ------------------------------------------------------------------------ DXF
class Dxf(Base):
    def setUp(self):
        super().setUp()
        import dxf
        self._cache = mock.patch.object(dxf, 'CACHE', self.ruta('cache'))
        self._cache.start()

    def tearDown(self):
        self._cache.stop()
        super().tearDown()

    def _doc(self, insunits=6):
        import ezdxf
        doc = ezdxf.new('R2018')
        doc.header['$INSUNITS'] = insunits
        return doc

    def _cargar(self, doc, nombre='x.dxf', **kw):
        import dxf
        p = self.ruta(nombre)
        doc.saveas(p)
        return dxf.cargar_dxf(p, avisar=False, **kw)

    def test_capas_apagadas_y_congeladas_fuera(self):                       # [X3]
        from ezdxf.render import forms
        doc = self._doc()
        doc.layers.add('APAGADA').off()
        doc.layers.add('CONGELADA').freeze()
        msp = doc.modelspace()
        forms.cube().scale(2, 2, 2).translate(1, 1, 1).render_polyface(msp)
        forms.cube().translate(500, 0, 0).render_polyface(msp, dxfattribs={'layer': 'APAGADA'})
        forms.cube().translate(0, 500, 0).render_polyface(msp, dxfattribs={'layer': 'CONGELADA'})
        msp.add_3dface([(0, 0, 900), (1, 0, 900), (1, 1, 900)], dxfattribs={'invisible': 1})
        m = self._cargar(doc, usar_cache=False)
        np.testing.assert_allclose(m.bounds, [[0, 0, 0], [2, 2, 2]], atol=1e-9)
        self.assertTrue(any('apagadas' in a for a in m.metadata['despiece_avisos']))

    def test_bloques_con_tope_de_caras(self):                               # [X4]
        import dxf
        from ezdxf.render import forms
        doc = self._doc()
        blk = doc.blocks.new('B')
        forms.cube().render_polyface(blk)
        doc.modelspace().add_blockref('B', (0, 0, 0)).grid(size=(20, 20), spacing=(2, 2))
        with mock.patch.object(dxf, 'MAX_CARAS', 1000):
            with self.assertRaises(SystemExit) as e:
                self._cargar(doc, usar_cache=False)
        self.assertIn('caras', str(e.exception))

    def test_mesh_con_indices_malos(self):                                  # [X7]
        doc = self._doc()
        msp = doc.modelspace()
        malla = msp.add_mesh()
        with malla.edit_data() as d:
            d.vertices = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
            d.faces = [(0, 1, 7)]
        msp.add_3dface([(10, 0, 0), (11, 0, 0), (11, 1, 0)])
        m = self._cargar(doc, usar_cache=False)
        self.assertTrue(any('no se pudieron leer' in a for a in m.metadata['despiece_avisos']))
        self.assertAlmostEqual(float(m.bounds[0][0]), 10.0)

    def test_muros_con_altura_se_explican(self):                            # [X9]
        doc = self._doc()
        doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 0.2), (0, 0.2)], close=True,
                                        dxfattribs={'thickness': 3})
        with self.assertRaises(SystemExit) as e:
            self._cargar(doc, usar_cache=False)
        self.assertIn('altura', str(e.exception))

    def test_unidad_declarada_rara_se_avisa(self):                          # [X1]
        from ezdxf.render import forms
        doc = self._doc(insunits=4)                       # dice mm; se dibujo en cm
        forms.cube().scale(1200, 800, 600).translate(600, 400, 300).render_polyface(
            doc.modelspace())
        m = self._cargar(doc, usar_cache=False)
        self.assertAlmostEqual(float(m.extents[0]), 1.2, places=6)
        self.assertIn('revisa las unidades', m.metadata['despiece_avisos'][0])

    def test_sin_unidad_no_se_toma_en_pulgadas(self):                        # [X2]
        from ezdxf.render import forms
        doc = self._doc(insunits=0)
        forms.cube().scale(200, 100, 50).translate(100, 50, 25).render_polyface(
            doc.modelspace())
        m = self._cargar(doc, usar_cache=False)
        self.assertAlmostEqual(float(m.extents[0]), 200.0, places=6)

    def test_cache_roto_se_vuelve_a_sacar(self):                           # [X6]
        import dxf
        from ezdxf.render import forms
        doc = self._doc()
        forms.cube().scale(4, 2, 3).translate(2, 1, 1.5).render_polyface(doc.modelspace())
        m1 = self._cargar(doc, nombre='casa.dxf', usar_cache=True)
        plys = [f for f in os.listdir(dxf.CACHE) if f.endswith('.ply')]
        self.assertEqual(len(plys), 1)
        p = os.path.join(dxf.CACHE, plys[0])
        with open(p, 'r+b') as f:
            f.truncate(os.path.getsize(p) // 2)
        m2 = dxf.cargar_dxf(self.ruta('casa.dxf'), avisar=False)
        self.assertEqual(self.medidas(m1), self.medidas(m2))


# --------------------------------------------------------------- Rhino y FBX
class RhinoFbx(Base):
    def test_capa_hija_de_una_apagada(self):                                # [R6]
        import rhino
        m = rhino.cargar_3dm(fijo('F_ocultos.3dm'), avisar=False)
        self.assertLess(float(m.bounds[1][0]), 30.0)

    def test_unidades_raras_de_rhino(self):                                 # [R8]
        import rhino
        mils = rhino.cargar_3dm(fijo('unidad_Mils.3dm'), avisar=False)
        metros = rhino.cargar_3dm(fijo('unidad_Meters.3dm'), avisar=False)
        self.assertEqual(self.medidas(mils), self.medidas(metros))

    def test_curvas_finas(self):                                            # [R3]
        import rhino3dm as r
        import rhino
        f = r.File3dm()
        f.Settings.ModelUnitSystem = r.UnitSystem.Meters
        circ = r.Circle(r.Point3d(0, 0, 0), 10.0).ToNurbsCurve()
        ext = r.Extrusion.Create(circ, 3.0, True)
        if ext is None:
            raise unittest.SkipTest('rhino3dm no armo la extrusion')
        f.Objects.AddExtrusion(ext)
        p = self.ruta('losa.3dm')
        f.Write(p, 7)
        m = rhino.cargar_3dm(p, avisar=False)
        tapas = 2 * math.pi * 100.0                  # las dos tapas; el costado es curvo
        self.assertLess(abs(float(m.area) - tapas) / tapas, 0.005)

    def test_3dm_a_medias_se_explica(self):                                 # [R10]
        import rhino
        with self.assertRaises(SystemExit) as e:
            rhino.cargar_3dm(fijo('trunc_ver8.3dm'), avisar=False)
        self.assertIn('a medias', str(e.exception))

    def _fbx(self, nombre):
        import fbx
        with mock.patch('shutil.which', return_value=None), \
                mock.patch.object(fbx, 'CACHE', self.tmp):
            return fbx.cargar_fbx(fijo(nombre), usar_cache=False, avisar=False)

    def test_fbx_unidad_mal_anotada(self):                                  # [R4]
        m = self._fbx('U_mal.fbx')
        self.assertGreater(float(max(m.extents)), 1.0)
        self.assertTrue(m.metadata.get('despiece_avisos'))

    def test_fbx_con_lineas_se_explica(self):                               # [R1]
        with self.assertRaises(SystemExit) as e:
            self._fbx('F2_linea.fbx')
        self.assertIn('lineas', str(e.exception))

    def test_fbx_sin_mallas_se_explica(self):                               # [R9]
        with self.assertRaises(SystemExit) as e:
            self._fbx('F6_vacio.fbx')
        self.assertIn('no trae ninguna malla', str(e.exception))


# --------------------------------------------------------------------- pisos
class Pisos(Base):
    def test_ultimo_piso_trae_remate_y_techo(self):                         # [P1]
        from placas import extraer_placas, niveles_de_piso, cortar_por_piso

        def caja(x0, y0, z0, x1, y1, z1):
            b = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
            b.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
            return b
        partes = [caja(0, 0, 0, 10, 8, 0.2), caja(0, 0, 0.2, 10, 0.2, 3.2),
                  caja(0, 7.8, 0.2, 10, 8, 3.2), caja(0, 0.2, 0.2, 0.2, 7.8, 3.2),
                  caja(9.8, 0.2, 0.2, 10, 7.8, 3.2)]
        sin_techo = trimesh.util.concatenate(partes)
        placas, _ = extraer_placas(sin_techo, t_modelo=0.02)
        sal, niv, _ = cortar_por_piso(placas, len(niveles_de_piso(placas)))

        def z(p):
            return [float((p['a_mundo'] @ np.array([c[0], c[1], 0.0, 1.0]))[2])
                    for c in p['poly'].exterior.coords]
        muros = [p for p in sal if p['tipo'] == 'muro']
        self.assertTrue(muros)
        self.assertTrue(all(abs(max(z(p)) - 3.2) < 1e-6 for p in muros))   # completos

        con_techo = trimesh.util.concatenate(partes + [caja(0, 0, 3.2, 10, 8, 3.4)])
        placas, _ = extraer_placas(con_techo, t_modelo=0.02)
        sal, _, _ = cortar_por_piso(placas, len(niveles_de_piso(placas)))
        self.assertTrue(any(p['tipo'] == 'losa' and abs(max(z(p)) - 3.3) < 1e-6 for p in sal))


# ----------------------------------------------------------------- servidor
class Servidor(Base):
    B = b'----WebKitFormBoundaryAbC123xyz'

    def _cuerpo(self, datos, nombre='Casa Díaz FINAL.STL'):
        return (b'--' + self.B + b'\r\nContent-Disposition: form-data; name="escala"\r\n\r\n100\r\n'
                + b'--' + self.B + b'\r\nContent-Disposition: form-data; name="modelo"; filename="'
                + nombre.encode('utf-8') + b'"\r\n\r\n' + datos + b'\r\n--' + self.B + b'--\r\n')

    def test_subida_por_partes_da_lo_mismo(self):                          # [S1]
        import app
        from subida import leer_multipart
        rnd = random.Random(3)
        for _ in range(40):
            datos = bytearray(rnd.randbytes(rnd.choice([0, 7, 900, 30000])))
            for _ in range(rnd.randint(0, 4)):
                pos = rnd.randint(0, len(datos))
                datos[pos:pos] = rnd.choice([b'\r\n--', b'\r\n--' + self.B[:-1], b'--'])
            body = self._cuerpo(bytes(datos))
            c0, a0, m0 = app.parse_multipart(body, self.B)
            for trozo in (5, 4096, 1 << 20):
                ruta = self.ruta('m.bin')
                c1, a1, m1, leido = leer_multipart(io.BytesIO(body).read, len(body), self.B,
                                                   lambda n: ruta, trozo=trozo)
                self.assertEqual((c0, m0, a0[0]), (c1, m1, a1[0]))
                with open(ruta, 'rb') as f:              # abierto, Windows no deja reescribirlo
                    self.assertEqual(f.read(), a0[1])
                self.assertEqual(leido, len(body))

    def test_subida_cortada_no_pasa(self):                                  # [S3]
        from subida import leer_multipart, SubidaMala
        body = self._cuerpo(b'x' * 10000)
        with self.assertRaises(SubidaMala):
            leer_multipart(io.BytesIO(body[:6000]).read, len(body), self.B,
                           lambda n: self.ruta('c.bin'))

    def test_nombres_con_acentos(self):                                     # [S4, S5]
        from urllib.parse import quote, unquote
        import app
        self.assertEqual(app._nombre_seguro('C:\\tareas\\Casa Díaz FINAL.STL'),
                         'Casa Diaz FINAL.stl')
        app._disposicion('Łódź #2 despiece.zip').encode('latin-1')   # no truena
        url = '/r/abcdef012345/' + quote('Casa Díaz #2.pdf')
        self.assertEqual(unquote(url).rsplit('/', 1)[1], 'Casa Díaz #2.pdf')

    def test_mensajes_sin_rutas_del_servidor(self):                         # [S10]
        import app
        c = '/tmp/x/despiece3d_jobs/abc123abc123'
        self.assertEqual(app._sin_rutas('no se pudo leer %s/entrada/Casa.dwg: roto' % c, c),
                         'no se pudo leer Casa.dwg: roto')

    def test_stl_gigante_se_rechaza_sin_cargarlo(self):
        import app
        p = self.ruta('grande.stl')
        with open(p, 'wb') as f:
            f.write(b'\0' * 80 + (3).to_bytes(4, 'little') + b'\0' * 150)
        self.assertEqual(app._caras_stl_binario(p), 3)

    def test_un_corte_a_la_vez(self):                                       # [S2]
        import app
        activos, maximo, lock = [0], [0], threading.Lock()

        def falso(*a, **k):
            with lock:
                activos[0] += 1
                maximo[0] = max(maximo[0], activos[0])
            time.sleep(0.2)
            with lock:
                activos[0] -= 1
            return {'ok': True}
        with mock.patch.object(app, '_procesar', falso), mock.patch.object(app, 'TRABAJOS', 1):
            hilos = [threading.Thread(target=app.procesar,
                                      args=('x', {}, self.ruta('j%d' % i), 'j%011d' % i, 'x'))
                     for i in range(3)]
            for h in hilos:
                h.start()
            for h in hilos:
                h.join()
        self.assertEqual(maximo[0], 1)
        self.assertEqual(app._ESPERA, [])


# --------------------------------------------- auditoria de Aldo, 14 sep 2026
class Auditoria(Base):
    MALO = 'x<img src=x onerror=alert(1)>'

    def test_guia_escapa_nombre_y_avisos(self):                            # [A1]
        import exportar
        from despiece import Config
        stats = {'n_piezas': 0, 'n_hojas': 0, 'alto_mm': 0}
        for guia in (exportar.guia_html([], [], Config(), [], self.MALO, [], stats),
                     exportar.guia_estructural([], [], Config(), [], self.MALO, [], stats,
                                               {'avisos': [self.MALO]})):
            self.assertNotIn('<img', guia)
            self.assertIn('&lt;img', guia)

    def test_job_con_carpeta_no_se_reusa(self):                            # [A2]
        import app
        h = app.H.__new__(app.H)
        h.path = '/cortar?job=abcdef012345'
        with mock.patch.object(app, 'JOBS', self.tmp):
            self.assertEqual(h._job_pedido(), 'abcdef012345')
            self.assertNotEqual(h._job_pedido(), 'abcdef012345')

    def test_parametros_fuera_de_rango(self):                              # [A4]
        import app
        for v in ('nan', 'inf', '0', '-3', 'abc', '1e9', None):
            with self.assertRaises(ValueError):
                app._numero(v, 'el espesor', 0.3, 50)
        self.assertEqual(app._numero('2', 'el espesor', 0.3, 50), 2.0)
        # Se contesta antes de abrir el modelo (aqui ni existe).
        r = app._procesar(self.ruta('no_existe.stl'), {'kerf': '1e9'}, self.tmp, 'j', 'x', [])
        self.assertIn('kerf', r['error'])

    def test_nombre_de_salida(self):                                       # [A5]
        import unicodedata
        import app
        self.assertNotIn('<', app._nombre_seguro(self.MALO + '.stl', acentos=True))
        nfd = unicodedata.normalize('NFD', 'Casa Díaz.STL')
        self.assertEqual(app._nombre_seguro(nfd, acentos=True), 'Casa Díaz.stl')
        self.assertEqual(len(app._nombre_seguro('a' * 250 + '.stl', acentos=True)), 84)


if __name__ == '__main__':
    unittest.main()
