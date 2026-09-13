#!/bin/bash
# Prueba de humo: corta una casa de ejemplo de punta a punta y revisa la salida.
# Si esto pasa, la instalacion sirve. Si falla, dice exactamente en que paso.
set -e
# Cambio local (12 sep 2026): sin pipefail, "unittest | tail" daba OK aunque
# las pruebas fallaran.
set -o pipefail
cd "$(dirname "$0")"
PY=./.venv/bin/python
[ -x "$PY" ] || { echo "FALLA: no hay .venv. Corre ./instalar.sh"; exit 1; }

T="$(mktemp -d)"
mkdir -p out

echo "   1/6 generando la casa de ejemplo"
"$PY" gen_casa.py >/dev/null

echo "   2/6 despiece + exportacion"
"$PY" cortar_casa.py out/casa_prueba.stl --escala 100 --espesor 2 --hoja 500x700 --salida "$T" >/dev/null

echo "   3/6 revisando archivos"
for f in casa_prueba_hoja01.dxf casa_prueba.pdf casa_prueba_hoja01.svg; do
  [ -s "$T/$f" ] || { echo "FALLA: no salio $f"; exit 1; }
done
# El DWG solo sale con el ODA File Converter o con libredwg (dwgwrite). Sin
# ninguno el programa entrega DXF y lo avisa en pantalla, asi que aqui es aviso
# y no falla (cambio local, 12 sep 2026).
HAY_DWG=0
if "$PY" -c "import shutil, sys, exportar; sys.exit(0 if (exportar._oda_convertidor() or shutil.which('dwgwrite')) else 1)" 2>/dev/null; then
  HAY_DWG=1
  [ -s "$T/casa_prueba_hoja01.dwg" ] || { echo "FALLA: hay convertidor de DWG y no salio casa_prueba_hoja01.dwg"; exit 1; }
else
  echo "       (sin ODA File Converter ni libredwg: sale DXF y no DWG. No es falla.)"
fi

echo "   4/6 revisando que el DWG traiga dibujo (no vacio)"
if [ "$HAY_DWG" = 1 ] && command -v dwgread >/dev/null 2>&1; then
  dwgread -O DXF -o "$T/_chk.dxf" "$T/casa_prueba_hoja01.dwg" >/dev/null 2>&1 \
    || { echo "FALLA: el DWG no se puede leer"; exit 1; }
  N=$(grep -c "AcDbEntity" "$T/_chk.dxf" || true)
  [ "$N" -ge 50 ] || { echo "FALLA: el DWG salio casi vacio ($N entidades)"; exit 1; }
  tr -d '\r' < "$T/_chk.dxf" > "$T/_chk.txt"   # dwgread escribe con CRLF
  for capa in CORTE GRABADO MARCADO; do
    grep -q "^$capa$" "$T/_chk.txt" || { echo "FALLA: al DWG le falta la capa $capa"; exit 1; }
  done
  echo "       DWG con $N entidades y las capas CORTE / GRABADO / MARCADO"
else
  echo "       (sin DWG o sin dwgread: se salta)"
fi

echo "   5/6 revisando el lector de IFC y el rechazo de RVT"
# Va en la prueba de humo a proposito: el motor de geometria de ifcopenshell es
# un binario compilado, y de esos solo se sabe si sirven corriendolos en un
# .venv limpio. Lo mismo paso con mapbox_earcut y rtree (ver README).
if ! "$PY" -m unittest -q test_ifc > "$T/test_ifc.txt" 2>&1; then
  tail -15 "$T/test_ifc.txt"
  echo "FALLA: el lector de IFC no paso sus pruebas"
  exit 1
fi
tail -3 "$T/test_ifc.txt"

echo "   6/6 lectores de formatos nuevos (DAE, 3DM, STEP, FBX, 3MF)"
for mod in collada rhino3dm cascadio assimp_py lxml; do
  if "$PY" -c "import $mod" 2>/dev/null; then
    echo "       $mod: ok"
  else
    echo "       AVISO: falta $mod; ese formato no se va a poder leer"
  fi
done

echo "OK — la prueba paso. Salida en: $T"
