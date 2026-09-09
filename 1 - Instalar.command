#!/bin/bash
# Doble clic para instalar. Es un envoltorio de instalar.sh que deja la ventana
# abierta al final, para que se pueda leer lo que paso.
cd "$(dirname "$0")"
clear
./instalar.sh
EST=$?
echo
if [ $EST -ne 0 ]; then
  echo "=============================================================="
  echo " ALGO FALLO. Mandale a Rene una foto de esta ventana completa."
  echo "=============================================================="
fi
echo
echo "Ya puedes cerrar esta ventana (Command-W)."
