#!/bin/bash
# Prueba de humo: corta una casa de ejemplo de punta a punta y revisa la salida.
# Si esto pasa, la instalacion sirve. Si falla, dice exactamente en que paso.
set -e
cd "$(dirname "$0")"
PY=./.venv/bin/python
[ -x "$PY" ] || { echo "FALLA: no hay .venv. Corre ./instalar.sh"; exit 1; }

T="$(mktemp -d)"
mkdir -p out

echo "   1/5 generando la casa de ejemplo"
"$PY" gen_casa.py >/dev/null

echo "   2/5 despiece + exportacion"
"$PY" cortar_casa.py out/casa_prueba.stl --escala 100 --espesor 2 --hoja 500x700 --salida "$T" >/dev/null

echo "   3/5 revisando archivos"
for f in casa_prueba_hoja01.dxf casa_prueba_hoja01.dwg casa_prueba.pdf casa_prueba_hoja01.svg; do
  [ -s "$T/$f" ] || { echo "FALLA: no salio $f"; exit 1; }
done

echo "   4/5 revisando que el DWG traiga dibujo (no vacio)"
if command -v dwgread >/dev/null 2>&1; then
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
  echo "       (sin dwgread: brew install libredwg para revisar el DWG)"
fi

echo "   5/5 revisando el lector de IFC y el rechazo de RVT"
# Va en la prueba de humo a proposito: el motor de geometria de ifcopenshell es
# un binario compilado, y de esos solo se sabe si sirven corriendolos en un
# .venv limpio. Lo mismo paso con mapbox_earcut y rtree (ver README).
"$PY" -m unittest -q test_ifc 2>&1 | tail -3

echo "OK — la prueba paso. Salida en: $T"
