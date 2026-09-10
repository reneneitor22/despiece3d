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
        a = ifc.cargar_ifc(self.ruta, usar_cache=True, avisar=False)
        b = ifc.cargar_ifc(self.ruta, usar_cache=True, avisar=False)   # ya cacheado
        self.assertEqual(len(a.faces), len(b.faces))
        sa, sb = a.metadata['semantica'], b.metadata['semantica']
        self.assertEqual(sa['tipos'], sb['tipos'])
        self.assertEqual(sa['plantas'], sb['plantas'])
        self.assertEqual([len(c.faces) for c in sa['cuerpos']],
                         [len(c.faces) for c in sb['cuerpos']])

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
