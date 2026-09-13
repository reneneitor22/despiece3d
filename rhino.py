# -*- coding: utf-8 -*-
"""Leer .3dm de Rhino sin Rhino, en metros y con Z arriba.

Cambio local (11 y 12 sep 2026), no viene en el zip del hermano.

rhino3dm es la libreria de McNeel (pip, con ruedas para Mac, Windows y Linux).
Lee el archivo completo pero NO triangula: una polisuperficie solo trae caras
si Rhino le guardo su malla de render, y eso depende de como se guardo (con
"Guardar pequeño", o sin haber pasado por vista sombreada, no hay). En los tres
.3dm de prueba del repo de McNeel, de 12 polisuperficies y extrusiones solo 2
traian malla.

Por eso las caras PLANAS sin malla se triangulan aqui: se sigue cada contorno
por sus aristas y se triangula en el plano de la cara, con huecos. En
arquitectura casi todo es plano. Las caras curvas sin malla se cuentan y se
avisa: la unica salida es que Rhino las malle.

Los bloques (InstanceReference) se vuelven malla una vez por definicion y se
colocan con su matriz, como en dxf.py.
"""
import os
import sys

import numpy as np

UNIDAD_M = {'Millimeters': 0.001, 'Centimeters': 0.01, 'Decimeters': 0.1, 'Meters': 1.0,
            'Kilometers': 1000.0, 'Inches': 0.0254, 'Feet': 0.3048, 'Yards': 0.9144,
            'Microns': 1e-6,
            # 12 sep 2026: sin estas el aviso decia que el archivo "no dice en que
            # unidades se dibujo" y adivinaba (en Mils, 4 m salian de 157 m)
            'Mils': 2.54e-5, 'Microinches': 2.54e-8, 'Miles': 1609.344,
            'Dekameters': 10.0, 'Hectometers': 100.0, 'Nanometers': 1e-9,
            'Angstroms': 1e-10, 'NauticalMiles': 1852.0,
            'PrinterPoints': 0.0254 / 72, 'PrinterPicas': 0.0254 / 6}
# Puntos por arista curva de una cara plana sin malla. Eran 17: una losa
# circular de 10 m de radio salia de 16 lados (-2.6 % de area) y una spline de
# 20 m se desviaba 1.42 m. Con 129 el error de una circunferencia de 10 m es de 3 mm.
PUNTOS_CURVA = 129


def es_3dm(ruta):
    if os.path.splitext(ruta)[1].lower() == '.3dm':
        return True
    try:
        with open(ruta, 'rb') as f:
            return f.read(24) == b'3D Geometry File Format '
    except OSError:
        return False


def _llamar(obj, nombre, *args):
    """rhino3dm expone unas cosas como propiedad y otras como metodo."""
    v = getattr(obj, nombre)
    return v(*args) if callable(v) else v


def _de_mesh(m):
    n = len(m.Vertices)
    if n == 0 or len(m.Faces) == 0:
        return None
    V = np.array([(p.X, p.Y, p.Z) for p in (m.Vertices[i] for i in range(n))], dtype=np.float64)
    F = []
    for i in range(len(m.Faces)):
        a, b, c, d = m.Faces[i]
        F.append((a, b, c))
        if c != d:
            F.append((a, c, d))
    return V, np.array(F, dtype=np.int64)


def _contorno(brep, lazo):
    """Puntos 3D de un lazo de cara, encadenando las aristas por sus puntas."""
    tramos = []
    for tr in lazo.Trims:
        ei = tr.EdgeIndex
        if ei < 0:
            continue                                 # recorte singular (polo)
        arista = brep.Edges[ei]
        dom = arista.Domain
        try:
            recta = bool(_llamar(arista, 'IsLinear'))
        except TypeError:
            recta = bool(arista.IsLinear(1e-6))
        ts = np.linspace(dom.T0, dom.T1, 2 if recta else PUNTOS_CURVA)
        tramos.append([(p.X, p.Y, p.Z) for p in (arista.PointAt(t) for t in ts)])
    if not tramos:
        return None
    if len(tramos) > 1:
        # La primera arista tambien puede venir al reves: se voltea si la segunda
        # toca su inicio y no su final (la cara de abajo de la caja de prueba
        # salia con medio rectangulo, 12 de 24 m2).
        ini, fin0 = np.array(tramos[0][0]), np.array(tramos[0][-1])
        sig = (np.array(tramos[1][0]), np.array(tramos[1][-1]))
        if min(np.linalg.norm(ini - x) for x in sig) < min(np.linalg.norm(fin0 - x) for x in sig):
            tramos[0] = tramos[0][::-1]
    pts = list(tramos[0])
    for tramo in tramos[1:]:
        fin = np.array(pts[-1])
        if np.linalg.norm(np.array(tramo[-1]) - fin) < np.linalg.norm(np.array(tramo[0]) - fin):
            tramo = tramo[::-1]
        pts.extend(tramo[1:])
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    return np.array(pts, dtype=np.float64) if len(pts) >= 3 else None


def _cara_plana(brep, cara):
    """Triangula una cara plana sin malla guardada. (V, F) o None."""
    import rhino3dm as r
    import shapely
    from shapely.geometry import Polygon

    exterior, huecos = None, []
    for lazo in cara.Loops:
        pts = _contorno(brep, lazo)
        if pts is None:
            continue
        if lazo.LoopType == r.BrepLoopType.Outer and exterior is None:
            exterior = pts
        else:
            huecos.append(pts)
    if exterior is None:
        return None
    c = exterior.mean(axis=0)
    _, _, vt = np.linalg.svd(exterior - c)
    u, v = vt[0], vt[1]
    a2 = lambda P: np.column_stack(((P - c) @ u, (P - c) @ v))
    poli = Polygon(a2(exterior), [a2(h) for h in huecos])
    if not poli.is_valid:
        poli = poli.buffer(0)
    if poli.is_empty or poli.area <= 0:
        return None
    tris = shapely.constrained_delaunay_triangles(poli)
    V, F = [], []
    for t in getattr(tris, 'geoms', []):
        xy = np.asarray(t.exterior.coords)[:3]
        k = len(V)
        V.extend(c + xy[:, :1] * u + xy[:, 1:2] * v)
        F.append((k, k + 1, k + 2))
    if not F:
        return None
    return np.array(V, dtype=np.float64), np.array(F, dtype=np.int64)


def _de_brep(brep, cuenta):
    import rhino3dm as r
    partes = []
    for i in range(len(brep.Faces)):
        cara = brep.Faces[i]
        mm = cara.GetMesh(r.MeshType.Any)
        hecho = _de_mesh(mm) if mm is not None else None
        if hecho is None:
            try:
                plana = bool(_llamar(cara, 'IsPlanar'))
            except TypeError:
                plana = bool(cara.IsPlanar(1e-6))
            if plana:
                hecho = _cara_plana(brep, cara)
                if hecho is not None:
                    cuenta['planas_propias'] += 1
            else:
                cuenta['curvas_sin_malla'] += 1
        if hecho is not None:
            partes.append(hecho)
    return _juntar(partes)


def _juntar(partes):
    partes = [p for p in partes if p is not None and len(p[1])]
    if not partes:
        return None
    V, F, n = [], [], 0
    for v, f in partes:
        V.append(v)
        F.append(f + n)
        n += len(v)
    return np.vstack(V), np.vstack(F)


def _xform(x):
    return np.array([[getattr(x, 'M%d%d' % (i, j)) for j in range(4)] for i in range(4)])


def _capas_ocultas(f):
    """Indices de capa que no se ven: apagadas, o hijas de una capa apagada.

    Antes solo se miraba la capa propia y una hija visible bajo un padre apagado
    entraba (cambio local, 12 sep 2026).
    """
    capas = [f.Layers[i] for i in range(len(f.Layers))]
    por_id = {}
    for c in capas:
        try:
            por_id[str(c.Id)] = c
        except Exception:
            pass
    ocultas = set()
    for i, c in enumerate(capas):
        actual, saltos = c, 0
        while actual is not None and saltos < 64:
            try:
                if not _llamar(actual, 'Visible'):
                    ocultas.add(i)
                    break
            except Exception:
                break
            padre = str(getattr(actual, 'ParentLayerId', '') or '')
            actual = por_id.get(padre) if padre != str(getattr(actual, 'Id', '')) else None
            saltos += 1
    return ocultas


def cargar_3dm(ruta, avisar=True):
    """Malla del .3dm en metros, con metadata['despiece_avisos']."""
    try:
        import rhino3dm as r
    except ImportError:
        raise SystemExit('para leer .3dm falta la libreria rhino3dm. Instalala en el entorno '
                         'del programa:\n    "%s" -m pip install rhino3dm' % sys.executable)
    import trimesh
    from dxf import adivinar_unidad

    f = r.File3dm.Read(ruta)
    if f is None:
        raise SystemExit('no se pudo abrir el .3dm: o se subio o se bajo a medias (vuelve a '
                         'guardarlo y subelo otra vez), o viene de un Rhino mas nuevo que esta '
                         'biblioteca (guardalo como version 7 u 8): %s' % ruta)

    ocultas = _capas_ocultas(f)
    por_id = {str(o.Attributes.Id): o for o in f.Objects}
    defs = {str(d.Id): d for d in f.InstanceDefinitions}
    de_bloque = {str(i) for d in f.InstanceDefinitions for i in d.GetObjectIds()}
    cuenta = {'planas_propias': 0, 'curvas_sin_malla': 0, 'otros': 0, 'subd': 0,
              'bloques_vacios': 0}
    memo = {}
    SubD = getattr(r, 'SubD', None)

    def visible(o):
        a = o.Attributes
        return a.Visible and a.LayerIndex not in ocultas

    def malla(g, pila):
        if isinstance(g, r.Mesh):
            return _de_mesh(g)
        if isinstance(g, r.Extrusion):
            mm = g.GetMesh(r.MeshType.Any)
            if mm is not None:
                return _de_mesh(mm)
            b = g.ToBrep(True)
            return _de_brep(b, cuenta) if b is not None else None
        if isinstance(g, r.Brep):
            return _de_brep(g, cuenta)
        if SubD is not None and isinstance(g, SubD):
            # rhino3dm no malla SubD: se cuenta para decirlo por su nombre
            cuenta['subd'] += 1
            return None
        if isinstance(g, r.InstanceReference):
            clave = str(g.ParentIdefId)
            if clave in pila or clave not in defs:
                return None
            if clave not in memo:
                ids = [str(i) for i in defs[clave].GetObjectIds()]
                presentes = [por_id[i] for i in ids if i in por_id]
                if not presentes:
                    # bloque vinculado a otro archivo: su contenido no viene aqui
                    cuenta['bloques_vacios'] += 1
                memo[clave] = _juntar([malla(o.Geometry, pila | {clave})
                                       for o in presentes if visible(o)])
            hecho = memo[clave]
            if hecho is None:
                return None
            X = _xform(g.Xform)
            return hecho[0] @ X[:3, :3].T + X[:3, 3], hecho[1]
        cuenta['otros'] += 1                        # curvas, puntos, textos, luces
        return None

    partes = []
    for o in f.Objects:
        if str(o.Attributes.Id) in de_bloque or not visible(o):
            continue
        partes.append(malla(o.Geometry, frozenset()))
    junto = _juntar(partes)
    if junto is None:
        extras = []
        if cuenta['curvas_sin_malla']:
            extras.append('%d caras curvas sin malla guardada' % cuenta['curvas_sin_malla'])
        if cuenta['subd']:
            extras.append('%d objetos SubD, que hay que convertir a polisuperficie o malla '
                          'en Rhino' % cuenta['subd'])
        if cuenta['bloques_vacios']:
            extras.append('%d bloques vinculados a otro archivo' % cuenta['bloques_vacios'])
        raise SystemExit('el .3dm no trae superficies con caras (%d curvas, puntos o textos%s). '
                         'Necesito polisuperficies, extrusiones o mallas: %s'
                         % (cuenta['otros'], ''.join(', ' + e for e in extras), ruta))

    m = trimesh.Trimesh(vertices=junto[0], faces=junto[1], process=False)
    avisos = []
    unidad = getattr(f.Settings.ModelUnitSystem, 'name', str(f.Settings.ModelUnitSystem))
    factor, aviso = adivinar_unidad(UNIDAD_M.get(unidad), float(max(m.extents)),
                                    os.path.basename(ruta), minimo=0.01)
    if aviso:
        avisos.append(aviso)
    if cuenta['curvas_sin_malla']:
        avisos.append('%d caras curvas se quedaron fuera porque el .3dm no trae su malla. '
                      'En Rhino ponlo en vista Sombreado, guarda sin "Guardar pequeño" y '
                      'vuelve a subirlo.' % cuenta['curvas_sin_malla'])
    if cuenta['subd']:
        avisos.append('%d objetos SubD se quedaron fuera: aqui no se pueden leer. En Rhino '
                      'conviertelos a polisuperficie (ToNURBS) o a malla y vuelve a guardar.'
                      % cuenta['subd'])
    if cuenta['bloques_vacios']:
        avisos.append('%d bloques estan vinculados a otro archivo y su contenido no viene en el '
                      '.3dm: se quedaron fuera. En Rhino incrustalos (Administrador de bloques > '
                      'Incrustar) y vuelve a guardar.' % cuenta['bloques_vacios'])
    m.apply_scale(factor)
    m.merge_vertices()
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    if cuenta['planas_propias']:
        # La triangulacion propia sale con el giro que dio el plano de cada cara,
        # no con el de la cara: la caja de prueba daba volumen de -72 m3.
        m.fix_normals()
    m.metadata['despiece_metros'] = True
    m.metadata['despiece_avisos'] = avisos
    if avisar:
        print('   %s: %d caras (%d caras planas trianguladas aqui), %.2f x %.2f x %.2f m'
              % (os.path.basename(ruta), len(m.faces), cuenta['planas_propias'],
                 m.extents[0], m.extents[1], m.extents[2]))
    return m
