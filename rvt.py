# -*- coding: utf-8 -*-
"""Reconocer un .rvt de Revit y decirle al alumno que exporte IFC.

**Un .rvt no se puede leer sin Revit, y no es cosa de que falte una libreria.**
Es formato cerrado de Autodesk y no existe lector libre: el unico SDK que abre
el archivo por fuera es el BIM/Revit de la Open Design Alliance, que es de
licencia comercial y por instalacion. `openskp` existe para SketchUp e
`ifcopenshell` para IFC; para .rvt no hay equivalente, y no lo va a haber.

Lo que si se puede hacer, y es lo que hace este modulo, es **no dejar al alumno
con un error feo**. El .rvt es un contenedor OLE (los mismos primeros ocho
bytes que un .doc viejo: `D0 CF 11 E0 A1 B1 1A E1`) y adentro trae un flujo de
texto, `BasicFileInfo`, en UTF-16 con la version con que se guardo:

    Autodesk Revit
    Worksharing: Not enabled
    Format: 2020
    Build: 20200206_0915(x64)

Con eso se le contesta con su version en la mano y con los cinco clics exactos
del menu de Revit para sacar el IFC, que ese si lo leemos entero y ademas con
la semantica (ver ifc.py). Revit exporta IFC de fabrica: no hay que comprar ni
instalar nada.
"""
import os
import re

FIRMA_OLE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'

COMO_EXPORTAR = (
    'Revit exporta IFC solo, sin instalar nada:\n'
    '   1. Abre el proyecto en Revit y ponte en una vista 3D.\n'
    '   2. Menu Archivo -> Exportar -> IFC.\n'
    '   3. En "Configuracion actual" escoge IFC 2x3 Coordination View 2.0.\n'
    '   4. Guardar. Sale un .ifc al lado del .rvt.\n'
    '   5. Sube ese .ifc aqui.\n'
    'El IFC es mejor entrada que el .rvt: trae escrito cual elemento es muro, '
    'cual es losa y a que planta pertenece, asi que el despiece no tiene que '
    'adivinarlo.')


def es_rvt(ruta):
    """¿Es un archivo de Revit? Por extension, o por ser un OLE con Revit dentro.

    La familia `.rfa` (familias) y `.rte` (plantillas) son el mismo contenedor
    y tampoco se pueden leer, asi que caen aqui igual.
    """
    if os.path.splitext(ruta)[1].lower() in ('.rvt', '.rfa', '.rte', '.rft'):
        return True
    try:
        with open(ruta, 'rb') as f:
            cabeza = f.read(1 << 20)
    except OSError:
        return False
    if cabeza[:8] != FIRMA_OLE:
        return False
    return b'R\x00e\x00v\x00i\x00t\x00' in cabeza


# "Format:" en UTF-16LE. Se busca el texto y no el nombre del flujo: el nombre
# `BasicFileInfo` vive en la tabla de directorio del OLE (offset 4992 en los 20
# archivos medidos) pero su CONTENIDO puede estar en cualquier sector, y en
# esos mismos 20 cayo al final del archivo, no al principio. Leyendo solo el
# primer mega, 6 de 20 se quedaban sin version.
_AGUJA = 'Format:'.encode('utf-16-le')


def version_rvt(ruta):
    """(formato, build) de `BasicFileInfo`, o (None, None) si no se pudo leer.

    El archivo se recorre con `mmap`, no se carga: un .rvt de un proyecto real
    pesa cientos de megas y aqui nomas se ocupan dos renglones de texto.
    """
    import mmap
    try:
        with open(ruta, 'rb') as f:
            if os.path.getsize(ruta) < len(_AGUJA):
                return None, None
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                i = mm.find(_AGUJA)
                if i < 0:
                    return None, None
                trozo = mm[i:i + 400]
    except (OSError, ValueError):
        return None, None
    txt = trozo.decode('utf-16-le', 'replace')
    fmt = re.search(r'Format:\s*([\w.]+)', txt)
    bld = re.search(r'Build:\s*([\w.()]+)', txt)
    return (fmt.group(1) if fmt else None), (bld.group(1) if bld else None)


def rechazo(ruta):
    """El texto que se le enseña al alumno cuando sube un .rvt."""
    fmt, _bld = version_rvt(ruta)
    quien = 'Revit %s' % fmt if fmt else 'Revit'
    return ('%s es un archivo de %s, y ese formato es cerrado: no hay forma de '
            'abrirlo sin tener Revit instalado.\n%s'
            % (os.path.basename(ruta), quien, COMO_EXPORTAR))


if __name__ == '__main__':
    import sys
    for r in sys.argv[1:]:
        print(r, '->', es_rvt(r), version_rvt(r))
