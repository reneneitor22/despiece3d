#!/bin/bash
# Doble clic. Corta una casa de ejemplo y revisa que todo salga bien.
cd "$(dirname "$0")"
clear
echo "Revisando la instalacion…"
echo
./probar.sh
EST=$?
echo
if [ $EST -eq 0 ]; then
  echo "El programa esta bien. Si tu modelo no sale, el problema es el modelo:"
  echo "mandaselo a Rene con el nombre del programa donde lo hiciste."
else
  echo "=============================================================="
  echo " La instalacion tiene algo mal. Mandale a Rene una foto de"
  echo " esta ventana completa."
  echo "=============================================================="
fi
echo
echo "Ya puedes cerrar esta ventana (Command-W)."
