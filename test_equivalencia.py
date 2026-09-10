# -*- coding: utf-8 -*-
"""El prefiltro de cuerpos da EXACTAMENTE lo mismo que el split viejo.

Correr: python3 -m unittest test_equivalencia

`preparar_cuerpos` cambio el paso mas caro del pipeline: donde antes se llamaba
`mesh.split(only_watertight=False)` --que construye un Trimesh por cada
componente conectado, decenas de miles en un modelo de SketchUp-- ahora se mira
el grafo de conectividad, se tiran las astillas sin construirlas y se arma solo
lo que sobrevive. La ganancia no sirve de nada si la salida cambia: estas
pruebas comparan contra el camino viejo, escrito aqui tal como era.

Las mallas se arman en la prueba, sin archivos: asi corre igual en cualquier Mac.
"""
import unittest

import numpy as np
import trimesh

import placas


# El contrato, escrito con numeros literales A PROPOSITO: si alguien mueve las
# constantes de placas.py, estas pruebas tienen que gritar. Leerlas de alla las
# volveria ciegas justo al cambio que vigilan.
MIN_CARAS_VIEJO = 4
MIN_AREA_VIEJA = 1e-9


def camino_viejo(mesh):
    """El codigo que estaba antes en extraer_placas y en cuerpos_macizos."""
    m = placas._soldar(mesh)
    cuerpos = m.split(only_watertight=False)
    if len(cuerpos) <= 1:
        cuerpos = [m]
    return m, list(cuerpos)


def firma_cuerpos(cuerpos):
    """Identifica un cuerpo por su geometria, no por el orden en que salio."""
    return sorted('%d|%.9f|%s' % (len(c.faces), c.area,
                                  ','.join('%.6f' % v for v in c.bounds.ravel()))
                  for c in cuerpos)


def firma_placas(pl):
    return sorted('%s|%.9f|%.9f|%.9f|%d' % (p['tipo'], p['area'], p['espesor_real'],
                                            p['z_min'], p['vanos']) for p in pl)


def caja(dx, dy, dz, desplazar):
    m = trimesh.creation.box(extents=(dx, dy, dz))
    m.apply_translation(desplazar)
    return m


def parche(n_caras, desplazar, radio=0.2):
    """Un abanico suelto de `n_caras` triangulos: el confeti de un export real.

    El area es holgada a proposito. Lo que decide si es astilla tiene que ser el
    CONTEO DE CARAS y nada mas; si el parche fuera microscopico, el filtro de
    area lo tiraria igual y la prueba dejaria de vigilar el umbral de caras.
    """
    ang = np.linspace(0.0, np.pi, n_caras + 2)
    v = [[0.0, 0.0, 0.0]] + [[radio * np.cos(a), radio * np.sin(a), 0.0] for a in ang]
    caras = [[0, i + 1, i + 2] for i in range(n_caras)]
    return trimesh.Trimesh(vertices=np.array(v) + np.asarray(desplazar, dtype=float),
                           faces=np.array(caras), process=False)


def astilla(desplazar):
    """El caso mas comun: un triangulo suelto."""
    return parche(1, desplazar)


def confeti(desde, cuantos=12):
    """Astillas de 1, 2 y 3 caras: las tres que el umbral de 4 tiene que tirar.

    Sin las de 2 y 3 caras la prueba no distingue un umbral de 4 de uno de 2, y
    pasa igual con el codigo roto (se comprobo mutando la constante).
    """
    piezas = []
    for i in range(cuantos):
        piezas.append(parche(1 + i % 3, (desde + 2.0 * i, 0.0, 0.0)))
    return piezas


class PrefiltroDaLoMismo(unittest.TestCase):
    def _comparar(self, mesh, msg):
        m_viejo, cuerpos_viejos = camino_viejo(mesh)
        # el loop viejo tiraba las astillas en su primera linea; el prefiltro las
        # quita antes de construirlas. El conjunto que SOBREVIVE debe ser igual.
        vivos_viejos = [c for c in cuerpos_viejos
                        if len(c.faces) >= MIN_CARAS_VIEJO and c.area >= MIN_AREA_VIEJA]
        _, cuerpos_nuevos, astillas = placas.preparar_cuerpos(mesh)
        self.assertEqual(firma_cuerpos(vivos_viejos), firma_cuerpos(cuerpos_nuevos), msg)
        # y el conteo de lo descartado tiene que cuadrar: se le enseña al alumno
        self.assertEqual(len(cuerpos_viejos) - len(vivos_viejos), len(astillas), msg)

    def test_placas_con_astillas(self):
        """Lo normal: unas placas de verdad ahogadas en confeti."""
        partes = [caja(4.0, 3.0, 0.15, (0, 0, 0)),
                  caja(4.0, 0.15, 2.5, (0, -1.5, 1.3)),
                  caja(0.15, 3.0, 2.5, (-2.0, 0, 1.3))]
        partes += confeti(desde=10.0, cuantos=39)
        self._comparar(trimesh.util.concatenate(partes), 'placas + astillas')

    def test_un_solo_cuerpo(self):
        """El repliegue a [malla] cuando el modelo no se parte en nada."""
        self._comparar(caja(4.0, 3.0, 0.15, (0, 0, 0)), 'un solo cuerpo')

    def test_cuerpo_unico_que_ES_astilla(self):
        """El repliegue de verdad: UN componente y ademas basura.

        Con una caja entera el repliegue no se nota, porque el componente pasa el
        filtro de todos modos. El caso que lo distingue es un modelo que es un
        solo parche de 2 caras: ahi el codigo viejo entregaba [malla] y quien
        llamaba la tiraba en su loop. Se fija el conteo de descartados, que es lo
        que ve el alumno.
        """
        m = parche(2, (0.0, 0.0, 0.0))
        m_viejo, cuerpos_viejos = camino_viejo(m)
        self.assertEqual(len(cuerpos_viejos), 1, 'la referencia entrega [malla]')

        placas_n, desc_n = placas.extraer_placas(m, t_modelo=0.003)
        placas_v, desc_v = placas.extraer_placas(
            m, t_modelo=0.003, preparado=(m_viejo, cuerpos_viejos, []))
        self.assertEqual(firma_placas(placas_v), firma_placas(placas_n))
        self.assertEqual(len(desc_v), len(desc_n), 'cambio el conteo de descartados')

    def test_cuerpo_unico_abierto_NO_se_repara(self):
        """El repliegue carga peso: con un solo componente se entrega la malla CRUDA.

        `split()` devolvia la malla tal cual cuando no habia nada que partir, sin
        pasarla por submesh. Si se quita el repliegue, la malla se reconstruye
        CON repair y a una malla abierta le aparecen caras que antes no tenia.
        """
        c = caja(2.0, 2.0, 0.15, (0, 0, 0))
        abierta = trimesh.Trimesh(vertices=c.vertices.copy(),
                                  faces=c.faces[1:].copy(), process=False)
        _, cuerpos_viejos = camino_viejo(abierta)
        _, cuerpos_nuevos, astillas = placas.preparar_cuerpos(abierta)
        self.assertEqual(len(cuerpos_nuevos), 1)
        self.assertEqual(astillas, [])
        self.assertEqual([len(x.faces) for x in cuerpos_viejos],
                         [len(x.faces) for x in cuerpos_nuevos],
                         'se repararon caras que el camino viejo dejaba abiertas')
        self.assertEqual(firma_cuerpos(cuerpos_viejos), firma_cuerpos(cuerpos_nuevos))

    def test_area_en_la_banda_sobrevive(self):
        """Un cuerpo con area entre 1e-9 y 1e-6 NO es astilla para el prefiltro.

        El prefiltro tiene que usar el umbral FLOJO (1e-9), porque cuerpos_macizos
        corta ahi; extraer_placas ya aprieta a 1e-6 en su propio loop. Si alguien
        sube AREA_ASTILLA a 1e-6, este cuerpo desaparece del camino de macizos.
        """
        chico = parche(6, (20.0, 0.0, 0.0), radio=3e-4)
        self.assertTrue(1e-9 < chico.area < 1e-6,
                        'la prueba perdio su punto: area %g fuera de la banda'
                        % chico.area)
        m = trimesh.util.concatenate([caja(4.0, 3.0, 0.15, (0, 0, 0)), chico])
        _, cuerpos, astillas = placas.preparar_cuerpos(m)
        self.assertEqual(len(cuerpos), 2, 'se tiro un cuerpo que macizos si quiere')
        self.assertEqual(astillas, [])

    def test_malla_abierta_se_repara_igual(self):
        """submesh(repair=True) es lo que hacia split: una malla con hoyo lo prueba.

        Con cajas cerradas `repair` no hace nada y la prueba no vigila nada (se
        comprobo mutandolo). A esta caja le falta una cara: si el repair se
        apagara, saldrian menos caras que por el camino viejo.
        """
        c = caja(2.0, 2.0, 0.15, (0, 0, 0))
        abierta = trimesh.Trimesh(vertices=c.vertices.copy(),
                                  faces=c.faces[1:].copy(), process=False)
        m = trimesh.util.concatenate([abierta, caja(2.0, 2.0, 0.15, (9.0, 0, 0))])
        self._comparar(m, 'malla abierta: el repair debe correr igual')

    def test_solo_astillas(self):
        """Un modelo que es puro confeti no debe reventar."""
        m = trimesh.util.concatenate(confeti(desde=0.0, cuantos=12))
        _, cuerpos, astillas = placas.preparar_cuerpos(m)
        self.assertEqual(cuerpos, [])
        self.assertEqual(len(astillas), 12)

    def test_no_se_cuela_un_parche_de_tres_caras(self):
        """El umbral son 4 caras: 1, 2 y 3 son astillas y 4 ya no.

        Explicito porque es el numero que separa una placa de la basura, y una
        prueba hecha solo con triangulos sueltos no lo vigila.
        """
        placa = caja(4.0, 3.0, 0.15, (0, 0, 0))
        for n in (1, 2, 3):
            m = trimesh.util.concatenate([placa, parche(n, (20.0, 0.0, 0.0))])
            _, cuerpos, astillas = placas.preparar_cuerpos(m)
            self.assertEqual(len(cuerpos), 1, 'un parche de %d caras se colo' % n)
            self.assertEqual(len(astillas), 1, 'parche de %d caras' % n)
        m = trimesh.util.concatenate([placa, parche(4, (20.0, 0.0, 0.0))])
        _, cuerpos, astillas = placas.preparar_cuerpos(m)
        self.assertEqual(len(cuerpos), 2, 'un parche de 4 caras NO es astilla')
        self.assertEqual(astillas, [])

    def test_area_despreciable_es_astilla(self):
        """Con caras de sobra pero area cero, sigue siendo basura."""
        placa = caja(4.0, 3.0, 0.15, (0, 0, 0))
        plano = parche(6, (20.0, 0.0, 0.0), radio=1e-7)   # area muy por debajo de 1e-9
        m = trimesh.util.concatenate([placa, plano])
        _, cuerpos, astillas = placas.preparar_cuerpos(m)
        self.assertEqual(len(cuerpos), 1, 'un parche de area cero se colo')
        self.assertEqual(len(astillas), 1)

    def test_malla_vacia(self):
        vacia = trimesh.Trimesh(vertices=np.zeros((0, 3)),
                                faces=np.zeros((0, 3), dtype=np.int64), process=False)
        _, cuerpos, astillas = placas.preparar_cuerpos(vacia)
        self.assertEqual((cuerpos, astillas), ([], []))


class ExtraerPlacasDaLoMismo(unittest.TestCase):
    """La prueba que de verdad importa: la salida del pipeline no se movio."""

    def _modelo(self):
        partes = [caja(4.0, 3.0, 0.15, (0, 0, 0)),
                  caja(4.0, 0.15, 2.5, (0, -1.5, 1.3)),
                  caja(0.15, 3.0, 2.5, (-2.0, 0, 1.3)),
                  caja(4.0, 0.15, 2.5, (0, 1.5, 1.3))]
        partes += confeti(desde=10.0, cuantos=60)
        return trimesh.util.concatenate(partes)

    def test_mismas_placas_y_mismo_conteo(self):
        mesh = self._modelo()
        nuevas, desc_nuevo = placas.extraer_placas(mesh, t_modelo=0.003)

        # el camino viejo, forzado: se le da la lista completa sin prefiltrar
        m_viejo, cuerpos_viejos = camino_viejo(mesh)
        preparado_crudo = (m_viejo, cuerpos_viejos, [])
        viejas, desc_viejo = placas.extraer_placas(mesh, t_modelo=0.003,
                                                   preparado=preparado_crudo)

        self.assertEqual(firma_placas(viejas), firma_placas(nuevas),
                         'las placas cambiaron')
        self.assertEqual(len(desc_viejo), len(desc_nuevo),
                         'cambio el conteo de descartados que ve el alumno')


if __name__ == '__main__':
    unittest.main()
