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
            return {'ok': True}, None
        # _correr_aparte y no _procesar: el corte va en otro proceso y el parche
        # no llegaria alla; la fila se decide aqui, en el servidor
        with mock.patch.object(app, '_correr_aparte', falso), mock.patch.object(app, 'TRABAJOS', 1):
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


class SkpPesado(Base):
    """15 sep 2026: un .skp con 422 MB de geometria se quedaba 10 min paginando."""

    def _skp(self, mb):
        ruta = os.path.join(self.tmp, 'casa.skp')
        with zipfile.ZipFile(ruta, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('model.dat', b'\0' * int(mb * 1e6))
        return ruta

    def test_geometria_de_mas_se_rechaza_sin_abrir(self):
        import skp
        ruta = self._skp(2)
        with mock.patch.object(skp, 'MAX_GEOMETRIA_MB', 1), \
                mock.patch('openskp.SkpFile.open') as abrir:
            with self.assertRaises(SystemExit) as e:
                skp.cargar_skp(ruta, usar_cache=False)
        self.assertIn('2 MB de geometria', str(e.exception))
        abrir.assert_not_called()

    def test_debajo_del_tope_si_se_abre(self):
        import skp
        ruta = self._skp(2)
        with mock.patch.object(skp, 'MAX_GEOMETRIA_MB', 3), \
                mock.patch('openskp.SkpFile.open', side_effect=ValueError('x')) as abrir:
            with self.assertRaises(SystemExit) as e:
                skp.cargar_skp(ruta, usar_cache=False)
        abrir.assert_called_once()
        self.assertNotIn('geometria', str(e.exception))

    def test_por_componente_da_lo_mismo_que_openskp(self):
        """El iterador que abre F901/7017/7117 entrega las mismas hojas, en el
        mismo orden, que el de openskp (que arma F901 entero de golpe)."""
        import struct
        import skp
        from openskp import _core
        openskp_iter = skp._ITER_OPENSKP or _core.iter_top_level_lazy
        self.assertIsNot(openskp_iter, skp._por_componente)

        def rec(tag, cuerpo):
            return bytes.fromhex(tag) + struct.pack('<I', len(cuerpo)) + cuerpo

        def comp(n):
            return rec('7C15', rec('7D15', bytes([n]) * 16) + rec('7E15', b'comp%d' % n)
                       + rec('D007', rec('D107', bytes([n]))))
        data = rec('F401', rec('F601', rec('D007', b'raiz'))
                   + rec('F901', rec('7017', rec('6300', b'x') + rec('7117',
                         comp(1) + rec('6300', b'y') + comp(2) + comp(3))))
                   + rec('F801', b'fin'))

        def hojas(iterador):
            out, tops = [], []

            def walk(n):
                if n['children']:
                    for h in n['children']:
                        walk(h)
                elif n['tag'] not in skp._ENVOLTURAS:
                    out.append((n['tag'], bytes(n['payload'])))
            for _, _, nodo in iterador(data, 0, len(data), _core.CONTAINER_TAGS):
                tops.append(nodo['tag'])
                walk(nodo)
            return out, tops

        viejo, tops_viejo = hojas(openskp_iter)
        nuevo, tops_nuevo = hojas(skp._por_componente)
        self.assertEqual(len(viejo), 13)
        self.assertEqual(nuevo, viejo)
        self.assertEqual(tops_viejo.count('7C15'), 0)     # openskp: F901 de un bloque
        self.assertEqual(tops_nuevo.count('7C15'), 3)     # aqui: cada componente aparte
    def test_seccion_con_contorno_irreparable_no_tumba(self):
        """15 sep 2026: trimesh deja None un contorno que no repara y polygons_full
        le pide .exterior; con 5.3 M caras eso tumbaba extraer_placas."""
        from types import SimpleNamespace
        from shapely.geometry import box
        import placas

        class PlanoRoto(SimpleNamespace):
            @property
            def polygons_full(self):
                raise AttributeError("'NoneType' object has no attribute 'exterior'")

        plano = PlanoRoto(polygons_closed=[box(0, 0, 10, 10), None, box(2, 2, 4, 4), None],
                          root=[0, 1], enclosure_directed={0: {2: {}, 3: {}}, 1: {}})
        polis, rota = placas.poligonos_llenos(plano)
        self.assertTrue(rota)
        self.assertEqual(len(polis), 1)
        self.assertAlmostEqual(polis[0].area, 100 - 4)      # el hueco se conserva
        self.assertEqual(len(polis[0].interiors), 1)

        sano = SimpleNamespace(polygons_full=[box(0, 0, 1, 1)])
        self.assertEqual(placas.poligonos_llenos(sano), ([sano.polygons_full[0]], False))

    def test_cuerpos_sin_el_ply_crudo(self):
        """15 sep 2026: submesh copia la metadata por cuerpo y la de un .ply trae el
        archivo crudo (_ply_raw): 17 924 cuerpos x 91 MB tumbaron la Mac."""
        import placas
        cubos = [trimesh.creation.box(extents=(1, 1, 0.1)).apply_translation((3 * k, 0, 0))
                 for k in range(3)]
        m = trimesh.util.concatenate(cubos)
        m.metadata['_ply_raw'] = {'vertex': np.zeros(10)}
        soldada, cuerpos, _ = placas.preparar_cuerpos(m)
        self.assertEqual(len(cuerpos), 3)
        self.assertTrue(all('_ply_raw' not in c.metadata for c in cuerpos))
        self.assertNotIn('_ply_raw', soldada.metadata)
        self.assertIn('_ply_raw', m.metadata)              # el modelo original no se toca

    def test_obb_no_deja_el_casco_en_el_cuerpo(self):
        """15 sep 2026: bounding_box_oriented guarda el casco convexo en la cache de
        cada cuerpo; con 17 924 cuerpos el proceso subio de 2.4 a 7.9 GB."""
        import placas
        c = trimesh.creation.box(extents=(2, 1, 0.1))
        c.apply_transform(trimesh.transformations.rotation_matrix(0.4, (1, 1, 0)))
        ejes, ext, centro = placas._obb(c)
        self.assertNotIn('convex_hull', c._cache.cache)
        self.assertNotIn('bounding_box_oriented', c._cache.cache)
        T = c.copy().bounding_box_oriented.primitive.transform
        ext_trimesh = np.array(c.copy().bounding_box_oriented.primitive.extents, dtype=float)
        orden = np.argsort(ext_trimesh)
        np.testing.assert_array_equal(ext, ext_trimesh[orden])
        np.testing.assert_array_equal(ejes, np.array([T[:3, 0], T[:3, 1], T[:3, 2]])[orden])
        np.testing.assert_array_equal(centro, T[:3, 3])


class PlanDeSuperficies(Base):
    """18 sep 2026: "Ya ahora si el final.skp" (5.3 M caras, 3.9 M componentes
    sueltos) salia con 195 piezas de 28 mm y sin el edificio. El repliegue de
    caras sin espesor SI reconstruia las losas, pero se elegia por CUANTAS
    placas daba cada camino y las astillas ganaban por numero."""

    def _molde(self):
        """Una losa de 10 x 10 m dibujada como dos triangulos sin espesor, mas
        cinco astillas solidas. El plan de solidos solo ve las astillas."""
        partes = []
        v = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
        partes.append(trimesh.Trimesh(vertices=v, faces=np.array([[0, 1, 2], [0, 2, 3]]),
                                      process=False))
        for k in range(5):
            c = trimesh.creation.box(extents=(0.5, 0.5, 0.02))
            c.apply_translation((k * 1.5 + 1, 1, 2.0))
            partes.append(c)
        return trimesh.util.concatenate(partes)

    def test_gana_el_que_reconstruye_mas_area(self):
        import placas
        m = self._molde()
        solidas, _ = placas.extraer_placas(m, t_modelo=0.02, superficies=False)
        sup, _ = placas._placas_de_superficies(m, placas.MIN_AREA_SUP, t_modelo=0.02)
        # el molde es el caso dificil: las astillas son MAS pero valen MENOS
        self.assertGreater(len(solidas), len(sup))
        self.assertGreater(sum(p['area'] for p in sup), sum(p['area'] for p in solidas))

        placas_ok, _ = placas.extraer_placas(m, t_modelo=0.02)
        self.assertAlmostEqual(max(p['area'] for p in placas_ok), 100.0, places=1)

    def test_si_pierde_lo_dice(self):
        """Antes el plan B corria, perdia y no dejaba rastro: si la salida sale
        pobre tiene que verse por que."""
        import placas
        m = self._molde()
        with mock.patch.object(placas, '_placas_de_superficies',
                               return_value=([], 'sin parches')):
            _, descartados = placas.extraer_placas(m, t_modelo=0.02)
        notas = [d for i, d in descartados if i == -1]
        self.assertTrue(any('reconstruye menos modelo' in n for n in notas), notas)

    def test_elegir_por_cuenta_se_queda_con_las_astillas(self):
        """Control negativo: con la regla vieja el molde pierde la losa."""
        import placas
        m = self._molde()
        solidas, _ = placas.extraer_placas(m, t_modelo=0.02, superficies=False)
        sup, _ = placas._placas_de_superficies(m, placas.MIN_AREA_SUP, t_modelo=0.02)
        elegidas = sup if len(sup) > len(solidas) else solidas
        self.assertLess(max(p['area'] for p in elegidas), 1.0)


# ------------------------------------------------------- revision del 24 sep
def _caja(x0, y0, z0, x1, y1, z1):
    b = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    b.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return b


def _dos_pisos(extra=()):
    """Losa, cuatro muros y otra losa a 3 m: dos plantas."""
    partes = [_caja(0, 0, 0, 10, 8, 0.2), _caja(0, 0, 0.2, 10, 0.2, 3.0),
              _caja(0, 7.8, 0.2, 10, 8, 3.0), _caja(0, 0.2, 0.2, 0.2, 7.8, 3.0),
              _caja(9.8, 0.2, 0.2, 10, 7.8, 3.0), _caja(0, 0, 3.0, 10, 8, 3.2)]
    return trimesh.util.concatenate(partes + list(extra))


class Revision24Sep(Base):
    def test_grabado_no_se_sale_de_la_pieza(self):
        """recortar_choques achica la placa DESPUES de que las uniones dejaron sus
        marcas: el grabado quedaba colgando fuera de la pieza (Engel: 32 de 143)."""
        import estructura
        from despiece import Config
        from shapely.geometry import box
        cfg = Config(100, 2.0, 0.0, (600, 900))
        real = estructura.recortar_choques

        def recorta_de_mas(placas, contactos, t):
            r = real(placas, contactos, t)
            for p in placas:                 # como un recorte grande: media placa fuera
                if p.get('marcas') is not None:
                    b = p['poly'].bounds
                    p['poly'] = p['poly'].intersection(box(b[0], b[1], (b[0] + b[2]) / 2, b[3]))
            return r
        with mock.patch.object(estructura, 'recortar_choques', recorta_de_mas):
            piezas, _ = estructura.despiece_estructural(_dos_pisos(), cfg, grabar_planta=False)
        con_grabado = [pz for pz in piezas if pz.get('guia') is not None]
        self.assertTrue(con_grabado)
        for pz in con_grabado:
            self.assertLess(pz['guia'].difference(pz['poly'].buffer(1e-6)).area, 1e-6, pz['id'])

    def test_macizo_va_en_la_planta_donde_se_para(self):
        """La escalera del segundo piso caia en la planta 1: se media con la z desde
        el pie del propio cuerpo, no del edificio."""
        import estructura
        from despiece import Config
        cfg = Config(100, 2.0, 0.0, (600, 900))
        piezas, info = estructura.despiece_estructural(
            _dos_pisos([_caja(4, 3, 3.2, 5, 4, 4.2)]), cfg, laminar_macizos=True)
        macizas = [pz for pz in piezas if pz['tipo'] == 'macizo']
        self.assertTrue(macizas)
        self.assertEqual({pz['planta'] for pz in macizas}, {2})

    def test_cache_skp_roto_se_vuelve_a_leer(self):
        """Un .ply a medias en la cache tronaba en cada subida siguiente del modelo."""
        import types
        import skp
        ruta = self.ruta('casa.skp')
        with open(ruta, 'wb') as f:
            f.write(b'no importa, se lee con _abrir falso')
        caja = trimesh.creation.box(extents=(4, 3, 2))
        # openskp entrega Y arriba: se le da asi para que salga igual que la caja
        pos = np.column_stack((caja.vertices[:, 0], caja.vertices[:, 2], -caja.vertices[:, 1]))
        escena = types.SimpleNamespace(glb_primitives=[types.SimpleNamespace(
            positions=pos.ravel().tolist(), indices=caja.faces.ravel().tolist())])
        falso = types.SimpleNamespace(build_scene=lambda: escena)
        with mock.patch.object(skp, 'CACHE', self.tmp), \
                mock.patch.object(skp, '_abrir', return_value=falso):
            guardada = os.path.join(self.tmp, 'casa-%s.ply' % skp._firma(ruta))
            with open(guardada, 'wb') as f:
                f.write(b'ply\nformat binary_little_endian 1.0\nelement vertex 99\n')
            m = skp.cargar_skp(ruta, avisar=False)
            self.assertEqual(self.medidas(m), [4.0, 3.0, 2.0])
            self.assertFalse(os.path.exists(guardada + '.tmp'))
            self.assertEqual(self.medidas(skp.cargar_skp(ruta, avisar=False)), [4.0, 3.0, 2.0])

    def test_plantas_en_orden_de_hoja(self):
        """Una planta chica ya no regresa a llenar el hueco de una hoja anterior:
        con 1 chica, 2 grande y 3 chica, la 3 caia en la hoja 1 junto a la 1."""
        from despiece import Config, acomodar, ROTACIONES_ORTO
        from shapely.geometry import box
        piezas = [{'id': 'P%d' % n, 'poly': box(0, 0, w, h), 'guia': None,
                   'planta': n, 'rotulo': 'PLANTA %d' % n}
                  for n, w, h in ((1, 100, 100), (2, 400, 600), (3, 100, 100))]
        hojas, grandes = acomodar(piezas, Config(100, 2.0, 0.0, (500, 700)),
                                  rotaciones=ROTACIONES_ORTO, agrupar='planta')
        self.assertFalse(grandes)
        self.assertEqual([sorted({c['pieza']['planta'] for c in h}) for h in hojas],
                         [[1], [2], [3]])

    def test_unidades_invalidas_se_contestan(self):
        import app
        stl = self.ruta('cubo.stl')
        trimesh.creation.box().export(stl)
        r = app._procesar(stl, {'unidades': 'in', 'modo': 'curvas'}, self.tmp, 'x' * 12, 'cubo', [])
        self.assertIn('unidades', r.get('error', ''))


class Modelos27Sep(Base):
    """Cinco modelos bajados de internet (KIT, SketchUp, IAAC, Kenney, Revit)."""

    @staticmethod
    def casa():
        return trimesh.creation.box(extents=(10, 8, 6))

    def test_objeto_perdido_a_km_se_quita(self):
        """Project LoopS (IAAC, Rhino): 4 mallas de 7 cm a 1.0-1.4 km bajo el
        pabellon. El modelo media 1437 m de alto y el modo terreno moria sin
        memoria al rebanarlo."""
        from despiece import quitar_perdidos
        perdido = trimesh.creation.box(extents=(0.07, 0.07, 0.07))
        perdido.apply_translation((-12, -3, -1437))
        m = trimesh.util.concatenate([self.casa(), perdido])
        m.metadata = {}
        q = quitar_perdidos(m)
        np.testing.assert_allclose(q.extents, (10, 8, 6))
        self.assertIn('lejos', ' '.join(q.metadata.get('despiece_avisos', [])))

    def test_lo_que_no_esta_perdido_no_se_toca(self):
        from despiece import quitar_perdidos
        antena = trimesh.creation.box(extents=(0.1, 0.1, 30))
        antena.apply_translation((0, 0, 3 + 15))           # pegada al techo: crece el modelo
        otra = self.casa()
        otra.apply_translation((1000, 0, 0))                # dos edificios a 1 km: los dos valen
        for m in (trimesh.util.concatenate([self.casa(), antena]),
                  trimesh.util.concatenate([self.casa(), otra])):
            antes = m.extents.copy()
            q = quitar_perdidos(m)
            np.testing.assert_allclose(q.extents, antes)
            self.assertFalse(q.metadata.get('despiece_avisos'))

    def test_demasiadas_laminas_se_contesta_sin_rebanar(self):
        import app
        import despiece
        stl = self.ruta('torre.stl')
        trimesh.creation.box(extents=(1, 1, 1000)).export(stl)
        with mock.patch.object(despiece, 'rebanar', side_effect=AssertionError('rebano')):
            r = app._procesar(stl, {'modo': 'curvas', 'escala': '100', 'espesor': '2'},
                              self.tmp, 'y' * 12, 'torre', [])
        self.assertIn('5000 laminas', r.get('error', ''))

    def test_forma_libre_manda_al_modo_terreno(self):
        """Project LoopS en modo casa: 4 min y 'no se encontraron muros ni losas.
        ¿El modelo trae cuerpos con espesor?' -- que no le dice al alumno que
        hacer con un pabellon de forma libre."""
        from despiece import Config
        from estructura import despiece_estructural
        # un bloque macizo sin una sola cara que llegue a placa
        bloque = trimesh.creation.box(extents=(0.9, 0.9, 0.9))
        _, info = despiece_estructural(bloque, Config(100, 2.0, 0.0, (600, 900)))
        self.assertIn('Terreno', info.get('error', ''))

    def test_placas_todas_chicas_piden_otra_escala(self):
        from despiece import Config
        from estructura import despiece_estructural
        # una casita de 30 cm (losa y 3 muros) a 1:1000: todo sale de 0.3 mm
        partes = [trimesh.creation.box(extents=e) for e in
                  ((0.3, 0.3, 0.01), (0.3, 0.01, 0.2), (0.01, 0.3, 0.2), (0.01, 0.3, 0.2))]
        for b, t in zip(partes, ((0, 0, 0), (0, 0.15, 0.1), (0.15, 0, 0.1), (-0.15, 0, 0.1))):
            b.apply_translation(t)
        casita = trimesh.util.concatenate(partes)
        _, info = despiece_estructural(casita, Config(1000, 2.0, 0.0, (600, 900)))
        self.assertIn('escala', info.get('error', ''))
        self.assertNotIn('Terreno', info.get('error', ''))

    def test_los_encimes_se_resumen(self):
        """Hearst Tower (SketchUp): 291 avisos, casi todos 'se enciman', uno
        por renglon en la pantalla."""
        from estructura import _resumir_encimes
        enc = ['M%d y T1 se enciman y no se pudo recortar ninguna (x)' % i for i in range(40)]
        r = _resumir_encimes(['otro aviso'] + enc)
        self.assertEqual(r[0], 'otro aviso')
        self.assertLessEqual(len(r), 8)
        self.assertIn('35', r[-1])
        self.assertEqual(_resumir_encimes(enc[:3]), enc[:3])
        self.assertIn('Terreno', r[-1])   # la envolvente lo empeoraba: 0.65% -> 1.63%

    def test_maqueta_de_milimetros_se_avisa(self):
        """Kenney (casa de juego, 1.41 m) con lo de fabrica, 1:200: una pieza de
        7 mm, 'ok' y sin un solo aviso de que la escala no cuadra."""
        import app
        stl = self.ruta('casita.stl')
        trimesh.creation.box(extents=(1.4, 1.3, 1.2)).export(stl)
        avisos = []
        r = app._procesar(stl, {'modo': 'curvas', 'escala': '200', 'espesor': '2'},
                          self.tmp, 'z' * 12, 'casita', avisos)
        self.assertTrue(any('mm de largo' in a for a in r.get('avisos') or []), r.get('avisos'))
        r = app._procesar(stl, {'modo': 'curvas', 'escala': '20', 'espesor': '2'},
                          self.tmp, 'w' * 12, 'casita', [])
        self.assertFalse(any('de largo' in a for a in r.get('avisos') or []))


if __name__ == '__main__':
    unittest.main()
