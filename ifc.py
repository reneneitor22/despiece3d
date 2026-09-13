# -*- coding: utf-8 -*-
"""Leer archivos .ifc (ArchiCAD, Revit, Allplan, Vectorworks) CON su semantica.

Este lector es distinto a los otros dos. `skp.py` y `fbx.py` entregan una malla
y ya: el resto del programa tiene que adivinar por geometria que cosa es cada
cuerpo -- si la caja orientada parece lamina es placa, si su normal apunta
arriba es losa. Adivinar funciona cuando el modelo esta bien hecho y se
equivoca solo cuando no (ver README, "Los dos formatos que no lee ninguna
libreria").

El IFC no hay que adivinarlo. El archivo dice `IFCWALL`, `IFCSLAB`, `IFCROOF`,
dice a que planta pertenece cada elemento y dice cual es un mueble. Eso es
justo lo que el despiece anda deduciendo, asi que aqui se lee y se pasa hecho:

* **cada elemento es UN cuerpo**, no lo que salga de partir la malla por
  conectividad. Un muro con su recubrimiento en dos solidos sueltos sigue
  siendo un muro;
* **el tipo lo pone el archivo**, no la normal. Un muro inclinado (fachada de
  vidrio) se clasificaba como techo; aqui sale muro;
* **la planta la pone el archivo** (`IfcBuildingStorey`), no la agrupacion de
  losas por su Z. En un edificio con medio nivel o rampas, agrupar por Z
  encadena plantas;
* **los muebles, puertas, ventanas, sanitarios y luminarias no entran.** En el
  modelo `cira` son 153 muebles de 638 elementos: cuerpos que despues hay que
  descartar uno por uno.

Los huecos ya vienen restados: IfcOpenShell aplica los `IfcOpeningElement`
sobre el muro que los recibe (por eso el `IfcOpeningElement` suelto se descarta
-- es el volumen del hueco, no una pieza).

**Unidades y ejes: no hay que tocar nada, y esta medido.** El IFC declara su
unidad de largo en `IfcUnitAssignment`, e IfcOpenShell la aplica y entrega
metros SI pase lo que pase: el modelo `20200205Model_PNO.ifc` esta guardado en
milimetros y sale como 125.48 x 6.36 x 3.50 -- metros, la nave que es. Y el eje
de arriba en IFC es Z por definicion del formato, que es lo que suponen
`placas.py` y `niveles_de_piso`. Es el unico de los tres formatos que entra
derecho.

Meshear el modelo cuesta lo suyo (9.9 s los 206 muros y losas del `cira`), y el
CLI lo hace otra vez en cada corrida, asi que se guarda en `.cache_ifc/` con la
firma del archivo en el nombre, igual que el .skp.
"""
import hashlib
import json
import os
import sys

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.cache_ifc')

# Cambio local (12 sep 2026): "pip3 install --user" instalaba fuera del .venv.
_AYUDA_INSTALAR = (
    'para leer .ifc falta la libreria ifcopenshell. Instalala en el entorno del programa:\n'
    '    "%s" -m pip install ifcopenshell' % sys.executable)

# --- que se corta, que se lamina y que se tira -----------------------------
# Lo que es lamina y va a placa. IfcPlate es la chapa de una fachada tipo muro
# cortina; IfcCovering, el plafon o el recubrimiento de azotea.
ESTRUCTURA = {
    'IfcWall': 'muro',
    'IfcWallStandardCase': 'muro',
    'IfcWallElementedCase': 'muro',
    'IfcCurtainWall': 'muro',
    'IfcSlab': 'losa',
    'IfcSlabStandardCase': 'losa',
    'IfcSlabElementedCase': 'losa',
    'IfcPlate': 'losa',
    'IfcPlateStandardCase': 'losa',
    'IfcFooting': 'losa',
    'IfcRoof': 'techo',
    'IfcCovering': 'techo',
}

# No son lamina, pero para la maqueta si se ocupan: salen en rebanadas apiladas
# con --laminar-macizos. Entran a la malla para que `cuerpos_macizos` los vea.
MACIZOS = {
    'IfcColumn', 'IfcColumnStandardCase', 'IfcBeam', 'IfcBeamStandardCase',
    'IfcMember', 'IfcMemberStandardCase', 'IfcStair', 'IfcStairFlight',
    'IfcRamp', 'IfcRampFlight', 'IfcRailing', 'IfcPile',
    'IfcBuildingElementProxy',      # el cajon de sastre: si es lamina, sale placa
}

# Todo lo demas se queda fuera y se cuenta para decirlo en pantalla.

# De que sirve el tipo del IFC y de que no. El archivo sabe que un elemento es
# muro aunque este inclinado --una fachada de vidrio la clasificaba `clasificar`
# como techo por su normal-- y eso hay que creerselo. Lo que NO conviene
# creerle es losa contra techo: un `IfcRoof` plano de azotea es una losa para
# armar la maqueta, y llamarlo techo lo saca de `huellas_en_losas`, que graba
# la planta de los muros sobre las losas. Asi que **el IFC decide si es muro y
# la geometria decide entre losa y techo.**
FORZAR_MURO = {'IfcWall', 'IfcWallStandardCase', 'IfcWallElementedCase',
               'IfcCurtainWall'}


def es_ifc(ruta):
    """¿Es un .ifc? Por extension o por la cabecera, si le cambiaron el nombre.

    El IFC de toda la vida es texto STEP y empieza con `ISO-10303-21;`. El
    IFCZIP y el IFC4 en XML no los lee este camino: se avisa aparte.
    """
    if os.path.splitext(ruta)[1].lower() in ('.ifc', '.ifczip', '.ifcxml'):
        return True
    try:
        with open(ruta, 'rb') as f:
            cabeza = f.read(256)
    except OSError:
        return False
    return cabeza.lstrip()[:13] == b'ISO-10303-21;'


def _firma(ruta):
    """Huella del contenido. Era ruta + tamaño + fecha, y en la pagina cada subida
    vive en su carpeta: el cache nunca se reusaba y crecia sin fin. Cambio local,
    12 sep 2026."""
    h = hashlib.sha1()
    with open(ruta, 'rb') as f:
        for trozo in iter(lambda: f.read(1 << 20), b''):
            h.update(trozo)
    return h.hexdigest()[:12]


def _callar_ruido_de_ifcopenshell():
    """Tapa el traceback que suelta ifcopenshell 0.8.5 al cerrar un open fallido.

    Cuando `ifcopenshell.open()` truena --un IFC 2.0 de los noventa, por
    ejemplo-- el objeto a medio construir se recolecta y su `__del__` revienta
    con `KeyError` sobre su propia tabla de archivos. Python no puede propagar
    una excepcion desde `__del__`, asi que la imprime como
    "Exception ignored in: <function file.__del__>" con todo y traceback.

    Es un bug de la libreria y no dice nada del archivo del alumno, pero sale
    en pantalla ANTES del mensaje bueno y se lee como si el programa se hubiera
    caido. Se filtra solo ese caso; cualquier otro error no atrapable sigue
    saliendo como siempre.
    """
    import sys
    if getattr(sys, '_despiece_hook_ifc', False):
        return
    anterior = sys.unraisablehook

    def hook(dato):
        obj = getattr(dato, 'object', None)
        modulo = getattr(getattr(obj, '__module__', ''), 'lower', lambda: '')()
        if dato.exc_type is KeyError and 'ifcopenshell' in modulo:
            return
        anterior(dato)

    sys.unraisablehook = hook
    sys._despiece_hook_ifc = True


def _abrir(ruta):
    _callar_ruido_de_ifcopenshell()
    ext = os.path.splitext(ruta)[1].lower()
    if ext == '.ifczip':
        raise SystemExit('esto es un IFCZIP (el IFC comprimido). Descomprimelo y '
                         'pasa el .ifc de adentro: %s' % ruta)
    try:
        import ifcopenshell
    except ImportError:
        raise SystemExit(_AYUDA_INSTALAR)
    try:
        return ifcopenshell.open(ruta)
    except Exception as e:
        raise SystemExit('no se pudo abrir el .ifc (%s). Vuelve a exportarlo desde tu '
                         'programa como IFC2x3 o IFC4: %s' % (e, ruta))


def info_ifc(ruta):
    """Version del esquema, unidad declarada y conteo por clase, sin meshear."""
    import collections
    f = _abrir(ruta)
    unidad = None
    try:
        for u in f.by_type('IfcUnitAssignment')[0].Units:
            if getattr(u, 'UnitType', None) == 'LENGTHUNIT':
                unidad = '%s%s' % (u.Prefix or '', getattr(u, 'Name', '?'))
    except Exception:
        pass
    clases = collections.Counter(e.is_a() for e in f.by_type('IfcProduct'))
    return {
        'esquema': f.schema,
        'unidad_archivo': unidad,
        'clases': dict(clases),
        'n_estructura': sum(n for c, n in clases.items() if c in ESTRUCTURA),
        'n_plantas': len(f.by_type('IfcBuildingStorey')),
    }


# ------------------------------------------------------------------ lectura
def _malla_del_elemento(verts, caras, min_caras=4):
    """La malla de UN elemento, soldada y entera.

    **Un elemento del IFC es UN cuerpo, aunque venga en pedazos sueltos.** Eso
    no es obvio y esta medido: en el modelo `cira`, partir cada elemento por
    conectividad -- que es lo que hace `preparar_cuerpos` con cualquier malla--
    convierte 91 `IfcWall` en 531 cuerpos. La razon es que un muro real se
    exporta **por capas de material**: aplanado, block, aislante y acabado
    salen como ocho solidos encimados con casi la misma caja, mas basura de
    milimetros que deja la resta de los huecos.

    Partirlo da ocho placas identicas por muro, que `fundir_pegadas` tiene que
    volver a juntar despues. Dejarlo entero da una sola placa y ademas con el
    espesor bueno: la caja orientada del muro completo mide el muro completo,
    que es lo que se corta en carton. Por eso aqui no se parte nada.
    """
    import numpy as np
    import trimesh

    m = trimesh.Trimesh(vertices=np.asarray(verts, dtype=np.float64).reshape(-1, 3),
                        faces=np.asarray(caras, dtype=np.int64).reshape(-1, 3),
                        process=False)
    m.merge_vertices()
    return m if len(m.faces) >= min_caras else None


def _del_tipo(f, clase):
    """Los elementos de esa clase exacta, o nada si el esquema no la tiene.

    Los *StandardCase y *ElementedCase de la lista son de IFC4. En un IFC2x3, que
    es lo que Revit exporta de fabrica, by_type revienta con RuntimeError en vez
    de regresar vacio, y el archivo entero no entraba.
    """
    try:
        return f.by_type(clase, include_subtypes=False)
    except RuntimeError:
        return []


def _leer(ruta, avisar=True, hilos=4):
    """Mesha el IFC y devuelve (cuerpos, tipos, plantas_por_cuerpo, resumen)."""
    import collections
    import ifcopenshell.geom as G
    import ifcopenshell.util.element as UE

    f = _abrir(ruta)
    clases = collections.Counter(e.is_a() for e in f.by_type('IfcProduct'))
    quiero = set(ESTRUCTURA) | MACIZOS
    elementos = [e for c in sorted(quiero) for e in _del_tipo(f, c)]
    fuera = {c: n for c, n in clases.items()
             if c not in quiero and not c.startswith(('IfcBuilding', 'IfcSite', 'IfcProject'))}

    if not elementos:
        raise SystemExit('el .ifc no trae muros, losas ni techos (%s). ¿Exportaste solo '
                         'los espacios o el terreno?: %s'
                         % (', '.join('%s x%d' % (c, n) for c, n in
                                      clases.most_common(4)) or 'sin elementos', ruta))

    if avisar:
        print('leyendo %s (IFC %s, %d elementos, primera vez tarda)...'
              % (os.path.basename(ruta), f.schema, len(elementos)))

    ajustes = G.settings()
    ajustes.set('use-world-coords', True)     # cada elemento en su lugar del edificio
    ajustes.set('weld-vertices', True)        # sin esto cada triangulo trae sus 3 vertices
    it = G.iterator(ajustes, f, hilos, include=elementos)

    cuerpos, tipos, planta_de, sin_geo = [], {}, {}, 0
    z_medidas = {}          # clave de planta -> z de cada cuerpo que le toco
    if it.initialize():
        while True:
            sh = it.get()
            g = sh.geometry
            try:
                cuerpo = _malla_del_elemento(g.verts, g.faces)
            except Exception:
                cuerpo = None
            if cuerpo is None:
                sin_geo += 1
                if not it.next():
                    break
                continue
            tipo = 'muro' if sh.type in FORZAR_MURO else None
            try:
                cont = UE.get_container(f.by_id(sh.id))
            except Exception:
                cont = None
            clave = cont.id() if cont is not None else None
            idx = len(cuerpos)
            cuerpos.append(cuerpo)
            if tipo:
                tipos[idx] = tipo
            planta_de[idx] = clave
            z_medidas.setdefault(clave, []).append(float(cuerpo.bounds[0][2]))
            if not it.next():
                break

    if not cuerpos:
        raise SystemExit('el .ifc trae %d muros y losas pero ninguno con geometria '
                         'que se pueda cortar. Vuelve a exportarlo con la vista 3D '
                         'abierta y con "Body" entre las representaciones: %s'
                         % (len(elementos), ruta))

    # Las plantas se numeran de abajo hacia arriba. La altura buena es el
    # `Elevation` de la `IfcBuildingStorey` --pasado a metros con la escala de
    # unidad del archivo--, y solo si no viene se mide de la geometria.
    #
    # Medir no basta: en el `cira`, la zapata que cuelga de la planta baja llega
    # mas abajo que todo el sotano, asi que ordenar por el minimo dejaba la
    # planta baja DEBAJO del sotano. Por eso el respaldo es la MEDIANA de las
    # alturas de sus cuerpos y no el minimo.
    try:
        import ifcopenshell.util.unit as UU
        a_metros = float(UU.calculate_unit_scale(f))
    except Exception:
        a_metros = 1.0
    z_planta = {}
    for clave, zs in z_medidas.items():
        elev = None
        if clave is not None:
            try:
                e = f.by_id(clave)
                if e.is_a('IfcBuildingStorey') and e.Elevation is not None:
                    elev = float(e.Elevation) * a_metros
            except Exception:
                elev = None
        z_planta[clave] = elev if elev is not None else sorted(zs)[len(zs) // 2]
    orden = sorted(z_planta, key=lambda k: z_planta[k])
    numero = {k: i + 1 for i, k in enumerate(orden)}
    nombres = []
    for k in orden:
        try:
            nombres.append(str(f.by_id(k).Name or 'planta') if k is not None else 'sin planta')
        except Exception:
            nombres.append('planta')
    plantas = {i: numero[k] for i, k in planta_de.items()}

    resumen = {
        'esquema': f.schema,
        'n_elementos': len(elementos),
        'n_cuerpos': len(cuerpos),
        'sin_geometria': sin_geo,
        'fuera': fuera,
        'plantas': nombres,
        'por_clase': dict(collections.Counter(
            e.is_a() for e in elementos if e.is_a() in ESTRUCTURA)),
    }
    return cuerpos, tipos, plantas, resumen


# ------------------------------------------------------------------- cache
def _guardar_cache(destino, cuerpos, tipos, plantas, resumen):
    import numpy as np
    verts, caras, cortes, base = [], [], [], 0
    for c in cuerpos:
        verts.append(np.asarray(c.vertices, dtype=np.float32))
        caras.append(np.asarray(c.faces, dtype=np.int64) + base)
        cortes.append(len(c.faces))
        base += len(c.vertices)
    os.makedirs(CACHE, exist_ok=True)
    np.savez_compressed(
        destino,
        vertices=np.vstack(verts), caras=np.vstack(caras),
        cortes=np.asarray(cortes, dtype=np.int64),
        meta=np.frombuffer(json.dumps({
            'tipos': {str(k): v for k, v in tipos.items()},
            'plantas': {str(k): v for k, v in plantas.items()},
            'resumen': resumen}).encode('utf-8'), dtype=np.uint8))


def _cargar_cache(origen):
    import numpy as np
    import trimesh
    d = np.load(origen, allow_pickle=False)
    meta = json.loads(bytes(d['meta']).decode('utf-8'))
    V, F, cortes = d['vertices'].astype(np.float64), d['caras'], d['cortes']
    cuerpos, i = [], 0
    for n in cortes:
        f = F[i:i + n]
        i += n
        # cada cuerpo se guardo con los indices corridos: se vuelven locales
        usados, inv = np.unique(f, return_inverse=True)
        cuerpos.append(trimesh.Trimesh(vertices=V[usados],
                                       faces=inv.reshape(-1, 3), process=False))
    tipos = {int(k): v for k, v in meta['tipos'].items()}
    plantas = {int(k): v for k, v in meta['plantas'].items()}
    return cuerpos, tipos, plantas, meta['resumen']


# ------------------------------------------------------------------ salida
def cargar_ifc(ruta, usar_cache=True, avisar=True):
    """Devuelve la malla del .ifc en metros y con Z arriba.

    La malla es la union de los cuerpos, como cualquier otro formato, para que
    todo lo que ya existe siga funcionando igual. Lo que trae de mas va en
    `malla.metadata['semantica']`: los cuerpos por separado, el tipo de cada
    uno y su planta, que es lo que `despiece_estructural` usa para no adivinar
    (ver estructura.py).
    """
    import numpy as np
    import trimesh

    guardada = os.path.join(
        CACHE, '%s-%s.npz' % (os.path.splitext(os.path.basename(ruta))[0].replace(' ', '_'),
                              _firma(ruta)))
    datos = None
    if usar_cache and os.path.exists(guardada):
        try:
            datos = _cargar_cache(guardada)
        except Exception:
            datos = None
    if datos is None:
        datos = _leer(ruta, avisar=avisar)
        if usar_cache:
            try:
                _guardar_cache(guardada, *datos)
            except Exception:
                pass

    cuerpos, tipos, plantas, resumen = datos

    verts, caras, base = [], [], 0
    for c in cuerpos:
        verts.append(np.asarray(c.vertices, dtype=np.float64))
        caras.append(np.asarray(c.faces, dtype=np.int64) + base)
        base += len(c.vertices)
    m = trimesh.Trimesh(vertices=np.vstack(verts), faces=np.vstack(caras), process=False)

    m.metadata['semantica'] = {
        'origen': 'ifc',
        'cuerpos': cuerpos,
        'tipos': tipos,
        'plantas': plantas,
        'resumen': resumen,
    }
    if avisar:
        print('   %s: %d elementos -> %d cuerpos (%s), %d planta(s): %s'
              % (resumen['esquema'], resumen['n_elementos'], resumen['n_cuerpos'],
                 ', '.join('%d %s' % (n, c.replace('Ifc', '').lower())
                           for c, n in sorted(resumen['por_clase'].items(),
                                              key=lambda x: -x[1])),
                 len(resumen['plantas']), ', '.join(resumen['plantas'])))
        if resumen['fuera']:
            top = sorted(resumen['fuera'].items(), key=lambda x: -x[1])[:4]
            print('   fuera de la maqueta: %s'
                  % ', '.join('%d %s' % (n, c.replace('Ifc', '').lower()) for c, n in top))
    return m


if __name__ == '__main__':
    import sys
    m = cargar_ifc(sys.argv[1])
    print('malla: %d caras, %.1f x %.1f x %.1f m' % ((len(m.faces),) + tuple(m.extents)))
