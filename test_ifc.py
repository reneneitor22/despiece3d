# -*- coding: utf-8 -*-
"""El .ifc entra con su semantica, y el .rvt se reconoce para rechazarlo bien.

Correr: python3 -m unittest test_ifc

El IFC de la prueba se escribe aqui, en texto, y no se baja de ningun lado: asi
la prueba corre igual en cualquier Mac y ademas se ve exactamente que trae. Son
tres elementos escogidos para tocar las tres cosas que el IFC aporta y que
adivinar por geometria no da:

* el archivo esta en **milimetros** -- si el lector no aplicara la unidad, el
  muro de 4 m entraria como uno de 4 mm;
* el segundo muro esta **inclinado 20 grados**, o sea que su normal no es
  horizontal y `clasificar` lo llamaria techo. El IFC dice `IFCWALL` y gana el
  IFC;
* los tres elementos cuelgan de una `IfcBuildingStorey`, que es de donde sale
  la planta sin tener que agrupar losas por su Z;
* y hay un `IfcFurnishingElement` que NO debe salir en las hojas.
"""
import os
import struct
import tempfile
import unittest

import numpy as np

import ifc
import rvt


def _ifc_de_prueba():
    """Un IFC4 minimo: dos muros (uno inclinado), una losa y un mueble."""
    # cos(20) = 0.93969, sen(20) = 0.34202
    return """ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('prueba.ifc','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCCARTESIANPOINT((0.,0.,0.));
#2=IFCDIRECTION((0.,0.,1.));
#3=IFCDIRECTION((1.,0.,0.));
#4=IFCAXIS2PLACEMENT3D(#1,#2,#3);
#5=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#4,$);
#6=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);
#7=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);
#8=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);
#9=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);
#10=IFCUNITASSIGNMENT((#6,#7,#8,#9));
#11=IFCPROJECT('0PruebaProject00000001',$,'Prueba',$,$,$,$,(#5),#10);
#12=IFCLOCALPLACEMENT($,#4);
#13=IFCSITE('0PruebaSite0000000001',$,'Sitio',$,$,#12,$,$,.ELEMENT.,$,$,$,$,$);
#14=IFCBUILDING('0PruebaBuilding000001',$,'Casa',$,$,#12,$,$,.ELEMENT.,$,$,$);
#15=IFCBUILDINGSTOREY('0PruebaStorey00000001',$,'Planta baja',$,$,#12,$,$,.ELEMENT.,0.);
#16=IFCRELAGGREGATES('0PruebaAgg1000000001',$,$,$,#11,(#13));
#17=IFCRELAGGREGATES('0PruebaAgg2000000001',$,$,$,#13,(#14));
#18=IFCRELAGGREGATES('0PruebaAgg3000000001',$,$,$,#14,(#15));
#20=IFCCARTESIANPOINT((0.,0.));
#21=IFCAXIS2PLACEMENT2D(#20,$);
#22=IFCRECTANGLEPROFILEDEF(.AREA.,$,#21,4000.,200.);
#23=IFCEXTRUDEDAREASOLID(#22,#4,#2,3000.);
#24=IFCSHAPEREPRESENTATION(#5,'Body','SweptSolid',(#23));
#25=IFCPRODUCTDEFINITIONSHAPE($,$,(#24));
#26=IFCWALL('0PruebaMuroRecto00001',$,'Muro recto',$,$,#12,#25,$,$);
#30=IFCDIRECTION((0.,0.34202,0.93969));
#31=IFCAXIS2PLACEMENT3D(#1,#30,#3);
#32=IFCLOCALPLACEMENT($,#31);
#33=IFCWALL('0PruebaMuroIncli00001',$,'Muro inclinado',$,$,#32,#25,$,$);
#40=IFCRECTANGLEPROFILEDEF(.AREA.,$,#21,4000.,4000.);
#41=IFCEXTRUDEDAREASOLID(#40,#4,#2,200.);
#42=IFCSHAPEREPRESENTATION(#5,'Body','SweptSolid',(#41));
#43=IFCPRODUCTDEFINITIONSHAPE($,$,(#42));
#44=IFCSLAB('0PruebaLosa0000000001',$,'Losa',$,$,#12,#43,$,$);
#50=IFCFURNISHINGELEMENT('0PruebaMueble00000001',$,'Sofa',$,$,#12,#43,$);
#60=IFCRELCONTAINEDINSPATIALSTRUCTURE('0PruebaCont000000001',$,$,$,(#26,#33,#44,#50),#15);
ENDSEC;
END-OF-FILE;
"""


def _rvt_de_prueba(formato='2024', build='20230413_1330(x64)'):
    """Los bytes minimos que hacen que un archivo se vea como un .rvt.

    No es un OLE valido --no hace falta-- pero trae lo unico que se le mira:
    los ocho bytes de firma, la palabra Revit en UTF-16 y el texto de
    `BasicFileInfo` con la version.
    """
    texto = ('Autodesk Revit\r\nWorksharing: Not enabled\r\n'
             'Format: %s\r\nBuild: %s\r\n' % (formato, build))
    relleno = b'\x00' * 512
    return (rvt.FIRMA_OLE + relleno + 'Revit'.encode('utf-16-le') + relleno
            + texto.encode('utf-16-le'))


class TestIFC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import ifcopenshell        # noqa: F401
        except ImportError:
            raise unittest.SkipTest('sin ifcopenshell')
        cls.dir = tempfile.mkdtemp()
        cls.ruta = os.path.join(cls.dir, 'prueba.ifc')
        with open(cls.ruta, 'w') as f:
            f.write(_ifc_de_prueba())
        # sin cache: la prueba tiene que medir el lector, no un .npz de antes
        cls.malla = ifc.cargar_ifc(cls.ruta, usar_cache=False, avisar=False)
        cls.sem = cls.malla.metadata['semantica']

    def test_reconoce_por_cabecera_aunque_le_cambien_el_nombre(self):
        otro = os.path.join(self.dir, 'modelo.dat')
        with open(otro, 'w') as f:
            f.write(_ifc_de_prueba())
        self.assertTrue(ifc.es_ifc(otro))

    def test_los_milimetros_del_archivo_salen_en_metros(self):
        # el muro mide 4 m de largo y la losa 4 x 4 m: el conjunto no pasa de 5 m
        self.assertLess(max(self.malla.extents), 6.0)
        self.assertGreater(max(self.malla.extents), 3.5)

    def test_un_elemento_un_cuerpo_y_el_mueble_no_entra(self):
        self.assertEqual(len(self.sem['cuerpos']), 3)
        self.assertIn('IfcFurnishingElement', self.sem['resumen']['fuera'])

    def test_el_muro_inclinado_sigue_siendo_muro(self):
        import numpy as np
        from placas import clasificar, extraer_placas, tabla_obb
        prep = (self.malla, self.sem['cuerpos'], [])
        placas, _ = extraer_placas(self.malla, preparado=prep,
                                   obbs=tabla_obb(prep[1]),
                                   tipos=self.sem['tipos'], superficies=False)
        por_tipo = {}
        for p in placas:
            por_tipo.setdefault(p['tipo'], []).append(p)
        self.assertEqual(len(por_tipo.get('muro', [])), 2,
                         'los dos muros tienen que salir muro: %s'
                         % {t: len(v) for t, v in por_tipo.items()})
        # y el de 20 grados, sin el IFC, se habria ido a techo
        inclinado = [p for p in placas
                     if 0.20 < abs(float(p['normal'][2])) < 0.97]
        self.assertEqual(len(inclinado), 1)
        self.assertEqual(clasificar(inclinado[0]['normal']), 'techo')
        self.assertEqual(inclinado[0]['tipo'], 'muro')

    def test_la_planta_sale_del_archivo(self):
        self.assertEqual(self.sem['resumen']['plantas'], ['Planta baja'])
        self.assertEqual(set(self.sem['plantas'].values()), {1})

    def test_las_placas_se_quedan_con_la_planta_que_dice_el_ifc(self):
        from placas import asignar_planta
        placas = [{'cuerpo': 0, 'z_min': 9.9}, {'cuerpo': 1, 'z_min': -3.0}]
        # z_min manda al camino viejo a inventar niveles; con plantas no se usa
        n = asignar_planta(placas, plantas={0: 2, 1: 1})
        self.assertEqual([p['planta'] for p in placas], [2, 1])
        self.assertEqual(n, 2)

    def test_el_cache_entrega_lo_mismo_que_leer_de_nuevo(self):
        """Comparar cuantas caras trae cada cuerpo no alcanza: el cache guardaba
        los vertices en float32 y en `cira` (255 m de lado) eso los movia 0.0076
        mm, cambiaba el area de 268 de 311 cuerpos y tiraba placas al filo. Aqui
        se comparan las coordenadas."""
        a = ifc.cargar_ifc(self.ruta, usar_cache=False, avisar=False)
        ifc.cargar_ifc(self.ruta, usar_cache=True, avisar=False)       # deja el cache
        b = ifc.cargar_ifc(self.ruta, usar_cache=True, avisar=False)   # lo lee
        np.testing.assert_array_equal(a.vertices, b.vertices)
        np.testing.assert_array_equal(a.faces, b.faces)
        sa, sb = a.metadata['semantica'], b.metadata['semantica']
        self.assertEqual(sa['tipos'], sb['tipos'])
        self.assertEqual(sa['plantas'], sb['plantas'])
        for x, y in zip(sa['cuerpos'], sb['cuerpos']):
            np.testing.assert_array_equal(x.vertices, y.vertices)
            np.testing.assert_array_equal(x.faces, y.faces)

    def test_el_cache_guarda_las_coordenadas_enteras(self):
        """Prueba directa al cache, con un cuerpo LEJOS del origen: es donde se
        ve. El fixture de este archivo mide 4 m y float32 lo representa exacto,
        asi que ahi el bug no sale; en `cira`, de 255 m de lado, movia los
        vertices 0.0076 mm y cambiaba el area de 268 de 311 cuerpos."""
        import trimesh
        v = np.array([[255.123456789, 198.987654321, 31.5],
                      [255.223456789, 198.987654321, 31.5],
                      [255.123456789, 199.087654321, 31.5],
                      [255.123456789, 198.987654321, 31.6]], dtype=np.float64)
        c = trimesh.Trimesh(vertices=v, faces=np.array([[0, 1, 2], [0, 1, 3],
                                                        [0, 2, 3], [1, 2, 3]]),
                            process=False)
        destino = os.path.join(self.dir, 'c.npz')
        ifc._guardar_cache(destino, [c], {0: 'muro'}, {0: 1},
                           {'esquema': 'IFC4', 'n_elementos': 1, 'n_cuerpos': 1,
                            'sin_geometria': 0, 'fuera': {}, 'plantas': ['p'],
                            'por_clase': {}})
        cuerpos, tipos, plantas, _ = ifc._cargar_cache(destino)
        np.testing.assert_array_equal(np.sort(cuerpos[0].vertices, axis=0),
                                      np.sort(v, axis=0))
        self.assertEqual(cuerpos[0].area, c.area)
        self.assertEqual(tipos, {0: 'muro'})
        self.assertEqual(plantas, {0: 1})

    def test_un_cache_de_version_vieja_se_ignora(self):
        """Los .npz de la v1 traen float32: no se reparan, se vuelven a leer."""
        import json as _json
        import trimesh
        c = trimesh.creation.box(extents=(1, 1, 1))
        destino = os.path.join(self.dir, 'viejo.npz')
        ifc._guardar_cache(destino, [c], {}, {0: 1},
                           {'esquema': 'IFC4', 'n_elementos': 1, 'n_cuerpos': 1,
                            'sin_geometria': 0, 'fuera': {}, 'plantas': ['p'],
                            'por_clase': {}})
        d = dict(np.load(destino, allow_pickle=False))
        meta = _json.loads(bytes(d['meta']).decode('utf-8'))
        meta['v'] = ifc.CACHE_V - 1
        d['meta'] = np.frombuffer(_json.dumps(meta).encode('utf-8'), dtype=np.uint8)
        np.savez_compressed(destino, **d)
        with self.assertRaises(ValueError):
            ifc._cargar_cache(destino)

    def test_el_orden_de_los_cuerpos_no_depende_de_los_hilos(self):
        """El iterador de ifcopenshell entrega por orden de TERMINO. Con 4 hilos,
        dos lecturas de `cira` ponian 102 de sus 311 cuerpos en otra posicion, y
        como el indice es la llave de `tipos` y `plantas`, el mismo archivo daba
        191 placas una vez y 188 la otra. El fixture es chico para que falle
        solo, pero el contrato es este: el orden sale del archivo, no del reloj."""
        uno = ifc._leer(self.ruta, avisar=False, hilos=1)
        cuatro = ifc._leer(self.ruta, avisar=False, hilos=4)
        self.assertEqual(uno[1], cuatro[1])          # tipos
        self.assertEqual(uno[2], cuatro[2])          # plantas
        self.assertEqual(len(uno[0]), len(cuatro[0]))
        for x, y in zip(uno[0], cuatro[0]):
            np.testing.assert_array_equal(x.vertices, y.vertices)
            np.testing.assert_array_equal(x.faces, y.faces)

    def test_la_viga_del_techo_no_sale_placa(self):
        """FZK-Haus (KIT, ArchiCAD): 42 `IfcMember` de 8 x 16 cm y 6.4 m. Su caja
        pasa el filtro de lamina y salian muros con ranura en el techo que los
        cubre: el techo en tiras y 1.96% de interferencia. El IFC dice que es
        viga: no es placa, es macizo (sale solo si se pide laminar)."""
        from placas import extraer_placas, tabla_obb
        from estructura import cuerpos_macizos
        from despiece import Config
        texto = _ifc_de_prueba().replace(
            '#60=IFCRELCONTAINEDINSPATIALSTRUCTURE(\'0PruebaCont000000001\',$,$,$,(#26,#33,#44,#50),#15);',
            '#70=IFCRECTANGLEPROFILEDEF(.AREA.,$,#21,80.,160.);\n'
            '#71=IFCEXTRUDEDAREASOLID(#70,#4,#2,4000.);\n'
            '#72=IFCSHAPEREPRESENTATION(#5,\'Body\',\'SweptSolid\',(#71));\n'
            '#73=IFCPRODUCTDEFINITIONSHAPE($,$,(#72));\n'
            '#74=IFCMEMBER(\'0PruebaSparren0000001\',$,\'Sparren\',$,$,#12,#73,$,$);\n'
            '#60=IFCRELCONTAINEDINSPATIALSTRUCTURE(\'0PruebaCont000000001\',$,$,$,(#26,#33,#44,#50,#74),#15);')
        ruta = os.path.join(self.dir, 'con_viga.ifc')
        with open(ruta, 'w') as f:
            f.write(texto)
        m = ifc.cargar_ifc(ruta, usar_cache=False, avisar=False)
        sem = m.metadata['semantica']
        self.assertEqual(len(sem['cuerpos']), 4)
        viga = [i for i, c in enumerate(sem['cuerpos']) if c.extents.max() > 3.9
                and sorted(c.extents)[1] < 0.2]
        self.assertEqual(len(viga), 1)
        prep = (m, sem['cuerpos'], [])
        placas, _ = extraer_placas(m, preparado=prep, obbs=tabla_obb(prep[1]),
                                   tipos=sem['tipos'], superficies=False)
        self.assertNotIn(viga[0], [p['cuerpo'] for p in placas])
        self.assertEqual(len(placas), 3)            # los dos muros y la losa
        cfg = Config(50, 2.0, 0.0, (600, 900), unidades_modelo='m')
        mac = cuerpos_macizos(m, cfg, preparado=prep, obbs=tabla_obb(prep[1]),
                              tipos=sem['tipos'], max_lado=2.0)
        self.assertEqual(len(mac), 1, 'la viga tiene que poder laminarse')

    def test_un_ifc_sin_muros_ni_losas_lo_dice(self):
        vacio = os.path.join(self.dir, 'vacio.ifc')
        texto = _ifc_de_prueba()
        for linea in ('#26=', '#33=', '#44=', '#60='):
            texto = '\n'.join(l for l in texto.split('\n') if not l.startswith(linea))
        with open(vacio, 'w') as f:
            f.write(texto)
        with self.assertRaises(SystemExit) as e:
            ifc.cargar_ifc(vacio, usar_cache=False, avisar=False)
        self.assertIn('muros', str(e.exception))


class TestRVT(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _escribir(self, nombre, datos):
        ruta = os.path.join(self.dir, nombre)
        with open(ruta, 'wb') as f:
            f.write(datos)
        return ruta

    def test_reconoce_por_extension_y_por_firma(self):
        self.assertTrue(rvt.es_rvt(self._escribir('casa.rvt', b'lo que sea')))
        self.assertTrue(rvt.es_rvt(self._escribir('casa.dat', _rvt_de_prueba())))
        self.assertFalse(rvt.es_rvt(self._escribir('casa.stl', b'solid x\nendsolid x\n')))

    def test_saca_la_version_aunque_este_hasta_el_final(self):
        # en los 21 .rvt medidos, BasicFileInfo cayo al FINAL del archivo
        datos = b'\x00' * (2 << 20) + _rvt_de_prueba('2024')
        datos = rvt.FIRMA_OLE + datos[len(rvt.FIRMA_OLE):]
        fmt, build = rvt.version_rvt(self._escribir('grande.rvt', datos))
        self.assertEqual(fmt, '2024')
        self.assertEqual(build, '20230413_1330(x64)')

    def test_el_rechazo_dice_la_version_y_como_exportar_ifc(self):
        texto = rvt.rechazo(self._escribir('casa.rvt', _rvt_de_prueba('2024')))
        self.assertIn('Revit 2024', texto)
        self.assertIn('IFC', texto)
        self.assertIn('Exportar', texto)

    def test_cargar_modelo_no_intenta_leerlo(self):
        from despiece import cargar_modelo
        ruta = self._escribir('casa.rvt', _rvt_de_prueba())
        with self.assertRaises(SystemExit) as e:
            cargar_modelo(ruta)
        self.assertIn('formato es cerrado', str(e.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
