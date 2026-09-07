# -*- coding: utf-8 -*-
"""Comprueba que el .fbx entra al despiece en metros y con Z arriba.

No hay FBX de verdad en el repo --pesan y traen licencia-- asi que la prueba se
fabrica sola: una caja de 4 x 2 x 10 m, escrita como FBX con las dos cabeceras
que se ven en la calle, y se exige que las dos den la MISMA caja al cargarla.

    caja alta 10 m, Z arriba, en metros     (3ds Max, SketchUp)
    caja alta 10 m, Y arriba, en centimetros (Maya, Unity, Blender)

Si assimp cambia como normaliza los ejes en una version futura, esta prueba es
la que se cae, y no un despiece que sale en rebanadas sin que nadie sepa por que.

    python3 prueba_fbx.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import trimesh

import fbx as _fbx

ESPERADO = np.array([4.0, 2.0, 10.0])       # metros, Z arriba
TOL = 0.01


def _prop(datos, nombre):
    """Posicion del valor de una propiedad de GlobalSettings, o None."""
    aguja = b'S' + struct.pack('<I', len(nombre)) + nombre
    i = datos.find(aguja)
    if i < 0:
        return None
    p = i + len(aguja)
    for _ in range(3):
        n = struct.unpack('<I', datos[p + 1:p + 5])[0]
        p += 5 + n
    return p


def _poner(datos, nombre, valor, entero=True):
    p = _prop(datos, nombre)
    if p is None:
        raise SystemExit('el FBX de prueba no trae %s' % nombre.decode())
    if entero:
        datos[p + 1:p + 5] = struct.pack('<i', int(valor))
    else:
        datos[p + 1:p + 9] = struct.pack('<d', float(valor))
    return datos


def _fbx_de(caja, tmp, nombre):
    """Escribe la caja como FBX pasando por OBJ (assimp no exporta desde memoria)."""
    obj = os.path.join(tmp, nombre + '.obj')
    salida = os.path.join(tmp, nombre + '.fbx')
    caja.export(obj)
    r = subprocess.run([shutil.which('assimp'), 'export', obj, salida],
                       capture_output=True)
    if r.returncode != 0 or not os.path.exists(salida):
        raise SystemExit('assimp no pudo escribir el FBX de prueba')
    return salida


def main():
    if not shutil.which('assimp'):
        raise SystemExit(_fbx._AYUDA_INSTALAR)

    tmp = tempfile.mkdtemp(prefix='prueba_fbx_')
    fallas = []
    try:
        # --- caso Max: los datos ya vienen Z arriba y en metros
        caja = trimesh.creation.box(extents=[4, 2, 10])
        ruta = _fbx_de(caja, tmp, 'zup_m')
        d = bytearray(open(ruta, 'rb').read())
        # assimp escribe la cabecera de Maya (Y arriba, cm) sin importar los
        # datos; hay que dejarla coherente o assimp la lee como base degenerada.
        for n, v in ((b'UpAxis', 2), (b'UpAxisSign', 1),
                     (b'FrontAxis', 1), (b'FrontAxisSign', -1),
                     (b'CoordAxis', 0), (b'CoordAxisSign', 1)):
            d = _poner(d, n, v)
        d = _poner(d, b'UnitScaleFactor', 100.0, entero=False)
        open(ruta, 'wb').write(bytes(d))
        casos = [('Z arriba, metros', ruta)]

        # --- caso Maya: Y arriba (el alto va en Y) y en centimetros
        caja = trimesh.creation.box(extents=[400, 1000, 200])
        ruta = _fbx_de(caja, tmp, 'yup_cm')
        d = bytearray(open(ruta, 'rb').read())
        d = _poner(d, b'UnitScaleFactor', 1.0, entero=False)
        open(ruta, 'wb').write(bytes(d))
        casos.append(('Y arriba, centimetros', ruta))

        for etiqueta, ruta in casos:
            m = _fbx.cargar_fbx(ruta, usar_cache=False, avisar=False)
            e = m.extents
            ok = np.allclose(e, ESPERADO, atol=TOL)
            print('%-24s -> %6.2f x %5.2f x %6.2f m   %s'
                  % (etiqueta, e[0], e[1], e[2], 'ok' if ok else 'NO'))
            if not ok:
                fallas.append('%s dio %s, se esperaba %s' % (etiqueta, e, ESPERADO))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fallas:
        print('\nNO PASA:')
        for f in fallas:
            print('   ' + f)
        sys.exit(1)
    print('\npasa: las dos cabeceras entran igual, en metros y con Z arriba')


if __name__ == '__main__':
    main()
