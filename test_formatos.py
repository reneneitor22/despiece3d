# -*- coding: utf-8 -*-
"""Formatos de entrada agregados en local el 11 sep 2026: DWG, DXF, 3DM, DAE con
unidad, FBX sin Homebrew, STEP y ZIP.

Cada prueba compara contra una medida que se sabe de antemano: la caja de
4 x 2 x 10 m que se exporto de Blender, el dibujo de AutoCAD con una caja, un
cilindro, una escalera unida y tres columnas en bloque (una girada 45 grados),
y archivos que se arman aqui mismo con ezdxf y rhino3dm.

    python -m unittest test_formatos
"""
import os
import shutil
import tempfile
import unittest
import zipfile

import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
FIJOS = os.path.join(AQUI, 'pruebas_formato')


def fijo(nombre):
    ruta = os.path.join(FIJOS, nombre)
    if not os.path.exists(ruta):
        raise unittest.SkipTest('falta %s' % ruta)
    return ruta


class Formatos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='test_formatos_')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def medidas(self, m):
        return np.round(m.extents, 3).tolist()

    # ------------------------------------------------------------ DAE
    def test_dae_ya_no_es_pagina_web(self):
        from despiece import cargar_modelo
        m = cargar_modelo(fijo('caja_blender.dae'))
        self.assertEqual(self.medidas(m), [4.0, 2.0, 10.0])
        self.assertTrue(m.metadata.get('despiece_metros'))

    def test_dae_aplica_pulgadas_y_eje_y(self):
        from despiece import cargar_modelo
        txt = open(fijo('caja_blender.dae'), encoding='utf-8').read()
        pulgadas = os.path.join(self.tmp, 'pulgadas.dae')
        open(pulgadas, 'w').write(txt.replace('meter="1"', 'meter="0.0254"'))
        self.assertEqual(self.medidas(cargar_modelo(pulgadas)),
                         [round(4 * 0.0254, 3), round(2 * 0.0254, 3), round(10 * 0.0254, 3)])
        y_arriba = os.path.join(self.tmp, 'yup.dae')
        open(y_arriba, 'w').write(txt.replace('Z_UP', 'Y_UP'))
        self.assertEqual(self.medidas(cargar_modelo(y_arriba)), [4.0, 10.0, 2.0])

    # ------------------------------------------------------------ FBX
    def test_fbx_sin_assimp_de_homebrew(self):
        import fbx
        from unittest import mock
        # Sin Homebrew a la fuerza: en una Mac con `brew install assimp` esta prueba
        # pasaba por el assimp de linea de comandos y nunca tocaba assimp_py.
        with mock.patch('shutil.which', return_value=None), \
                mock.patch.object(fbx, 'CACHE', self.tmp):
            m = fbx.cargar_fbx(fijo('caja_blender.fbx'), usar_cache=False, avisar=False)
        self.assertEqual(self.medidas(m), [4.0, 2.0, 10.0])

    # ------------------------------------------------------------ DXF
    def _dxf(self, insunits=6):
        import ezdxf
        from ezdxf.render import forms
        doc = ezdxf.new('R2018')
        doc.header['$INSUNITS'] = insunits
        blk = doc.blocks.new('CAJA')
        forms.cube().scale(2, 1, 3).translate(1, 0.5, 1.5).render_polyface(blk)
        msp = doc.modelspace()
        msp.add_blockref('CAJA', (0, 0, 0))
        # girada 90 grados y al doble: ocupa x de -2 a 0 y y de 10 a 14
        msp.add_blockref('CAJA', (0, 10, 0), dxfattribs={'rotation': 90, 'xscale': 2,
                                                          'yscale': 2, 'zscale': 1})
        msp.add_3dface([(20, 0, 0), (21, 0, 0), (21, 1, 0), (20, 1, 0)])
        return doc

    def test_dxf_bloques_con_matriz(self):
        import dxf
        ruta = os.path.join(self.tmp, 'bloques.dxf')
        self._dxf().saveas(ruta)
        m = dxf.cargar_dxf(ruta, usar_cache=False, avisar=False)
        np.testing.assert_allclose(m.bounds, [[-2, 0, 0], [21, 14, 3]], atol=1e-9)
        self.assertEqual(m.metadata['despiece_avisos'], [])

    def test_dxf_unidad_mal_declarada(self):
        import dxf
        ruta = os.path.join(self.tmp, 'mm_mentira.dxf')
        self._dxf(insunits=4).saveas(ruta)             # dice mm, se dibujo en metros
        m = dxf.cargar_dxf(ruta, usar_cache=False, avisar=False)
        self.assertAlmostEqual(float(m.extents[0]), 23.0, places=6)
        self.assertIn('milimetros', m.metadata['despiece_avisos'][0])

    def test_dxf_plano_2d_se_explica(self):
        import ezdxf
        import dxf
        doc = ezdxf.new('R2018')
        doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 8), (0, 8)], close=True)
        ruta = os.path.join(self.tmp, 'plano.dxf')
        doc.saveas(ruta)
        with self.assertRaises(SystemExit) as e:
            dxf.cargar_dxf(ruta, usar_cache=False, avisar=False)
        self.assertIn('2D', str(e.exception))

    def test_dwg_de_autocad_con_solidos(self):
        import dxf
        if not dxf.accore():
            raise unittest.SkipTest('sin AutoCAD en esta maquina')
        for nombre in ('prueba_autocad.dwg', 'prueba_autocad.dxf'):
            m = dxf.cargar_dxf(fijo(nombre), usar_cache=False, avisar=False)
            # caja 4x2x10 en el origen, cilindro r=1 en x=10, escalera hasta x=24,
            # columnas de 0.3 en y=20 (la de x=10 girada 45: llega a 20.424)
            np.testing.assert_allclose(m.bounds, [[0, -1, 0], [24, 20.424, 10]], atol=2e-3)

    # ------------------------------------------------------------ 3DM
    def test_3dm_caras_planas_sin_malla(self):
        import rhino3dm as r
        import rhino
        f = r.File3dm()
        f.Settings.ModelUnitSystem = r.UnitSystem.Meters
        caja = r.Brep.CreateFromBoundingBox(r.BoundingBox(r.Point3d(0, 0, 0),
                                                           r.Point3d(6, 4, 3)))
        f.Objects.AddBrep(caja)
        ruta = os.path.join(self.tmp, 'caja.3dm')
        f.Write(ruta, 7)
        m = rhino.cargar_3dm(ruta, avisar=False)
        self.assertEqual(self.medidas(m), [6.0, 4.0, 3.0])
        self.assertTrue(m.is_watertight)
        self.assertAlmostEqual(float(m.volume), 72.0, places=6)

    # ------------------------------------------------------------ ZIP
    def test_zip_toma_el_mejor_modelo(self):
        from despiece import cargar_modelo, desempacar
        z = os.path.join(self.tmp, 'bajada.zip')
        with zipfile.ZipFile(z, 'w') as w:
            w.write(fijo('caja_blender.obj'), 'modelo/caja.obj')
            w.write(fijo('caja_blender.dae'), 'modelo/caja.dae')
            w.writestr('LEEME.txt', 'hola')
            w.writestr('../fuera.obj', 'no debe salir de la carpeta')
        interior, aviso = desempacar(z, self.tmp)
        self.assertTrue(interior.endswith('caja.dae'))      # DAE trae unidad; OBJ no
        self.assertIn('2 modelos', aviso)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, 'fuera.obj')))
        m = cargar_modelo(z)
        self.assertEqual(self.medidas(m), [4.0, 2.0, 10.0])

    def test_obj_acostado_se_para(self):
        from despiece import cargar_modelo
        # caja_blender.obj trae la caja de 4 x 2 x 10 con Y arriba: 4 x 10 x 2
        m = cargar_modelo(fijo('caja_blender.obj'))
        self.assertEqual(self.medidas(m), [4.0, 10.0, 2.0])   # no es "claro": se queda
        acostada = os.path.join(self.tmp, 'losa.obj')
        with open(acostada, 'w') as f:                       # 20 x 3 x 12, Y arriba
            f.write('v 0 0 0\nv 20 0 0\nv 20 0 12\nv 0 0 12\n'
                    'v 0 3 0\nv 20 3 0\nv 20 3 12\nv 0 3 12\n'
                    'f 1 2 3 4\nf 5 8 7 6\nf 1 5 6 2\nf 2 6 7 3\nf 3 7 8 4\nf 4 8 5 1\n')
        m = cargar_modelo(acostada)
        self.assertEqual(self.medidas(m), [20.0, 12.0, 3.0])
        self.assertIn('acostado', m.metadata['despiece_avisos'][0])

    # ------------------------------------------------------------ STEP
    def test_step_en_metros_y_ligero(self):
        from despiece import cargar_modelo
        m = cargar_modelo(fijo('iglesia_de_la_luz.step'))
        self.assertTrue(m.metadata.get('despiece_metros'))
        self.assertAlmostEqual(float(m.extents[0]), 83.407, places=2)
        self.assertLess(len(m.faces), 100000)


if __name__ == '__main__':
    unittest.main()
