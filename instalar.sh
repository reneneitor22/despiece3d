#!/bin/bash
# Instalador de Despiece 3D para macOS (Apple Silicon).
# Se corre UNA vez. Deja todo dentro de esta carpeta: no toca el Python del sistema.
set -e
cd "$(dirname "$0")"
echo "== Despiece 3D — instalacion =="
echo

BREW=""
for cand in /opt/homebrew/bin/brew /usr/local/bin/brew "$(command -v brew 2>/dev/null)"; do
  [ -x "$cand" ] && { BREW="$cand"; break; }
done

# 1. Homebrew (el gestor de programas de linea de comandos de Mac)
if [ -z "$BREW" ]; then
  cat <<'AVISO'
Para escribir el DWG y leer FBX hacen falta dos programas que en Mac se instalan
con Homebrew, y Homebrew todavia no esta en esta computadora.

Se va a instalar ahora. Es el instalador oficial (brew.sh), lo usa medio mundo.
Te va a pedir LA CONTRASENA DE TU MAC (la de iniciar sesion). Al escribirla no
se ve nada en pantalla: es normal, escribela y dale Enter.
Tarda unos minutos.

AVISO
  printf 'Escribe SI y dale Enter para continuar (cualquier otra cosa cancela): '
  read -r RESP
  [ "$RESP" = "SI" ] || [ "$RESP" = "si" ] || { echo "Cancelado. No se instalo nada."; exit 1; }
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  for cand in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$cand" ] && { BREW="$cand"; break; }
  done
  [ -n "$BREW" ] || { echo; echo "FALLA: Homebrew no quedo instalado. Mandame esta ventana."; exit 1; }
fi

# 2. Python 3.11 + los dos binarios nativos (FBX y DWG)
echo "-- Instalando: python@3.11, assimp (FBX), libredwg (DWG). Tarda unos minutos."
"$BREW" install python@3.11 assimp libredwg

PY="$("$BREW" --prefix python@3.11)/bin/python3.11"
[ -x "$PY" ] || { echo "FALLA: no encuentro $PY"; exit 1; }

# 3. Entorno propio: nada se instala global
echo "-- Entorno virtual .venv ($("$PY" -V))"
rm -rf .venv
"$PY" -m venv .venv
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

# 4. Prueba de verdad antes de darlo por bueno
echo "-- Prueba de humo (corta una casa de ejemplo)"
./probar.sh

echo
echo "LISTO. Para abrir el programa: doble clic en «2 - Abrir Despiece 3D»"
