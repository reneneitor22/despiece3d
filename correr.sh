#!/bin/bash
# Abre Despiece 3D en el navegador. Se cierra con Control-C en esta ventana.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Falta instalar. Corre primero: ./instalar.sh"; exit 1; }
PUERTO="${1:-3561}"

# El programa tarda unos segundos en levantar (carga trimesh/scipy). Abrimos el
# navegador HASTA que conteste, no a ciegas: si no, sale "no se puede conectar".
(
  for _ in $(seq 1 60); do
    if curl -s --max-time 3 -o /dev/null "http://127.0.0.1:$PUERTO/"; then open "http://localhost:$PUERTO"; exit 0; fi
    sleep 1
  done
  echo "El programa no levanto en 60s. Revisa los mensajes de arriba."
) &

exec ./.venv/bin/python app.py "$PUERTO"
