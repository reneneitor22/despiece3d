#!/bin/bash
# Doble clic para usar el programa. Se abre solo en el navegador.
cd "$(dirname "$0")"
clear
if [ ! -x .venv/bin/python ]; then
  echo "Todavia no esta instalado."
  echo "Primero dale doble clic a «1 - Instalar»."
  echo
  echo "Cierra esta ventana con Command-W."
  exit 1
fi
echo "Abriendo Despiece 3D… tarda unos 10 segundos la primera vez."
echo "NO cierres esta ventana mientras lo uses."
echo "Para cerrar el programa: Control-C aqui."
echo
./correr.sh
