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

# 2. Python 3.13 + los dos binarios nativos (FBX y DWG)
# Cambio local (12 sep 2026): era python@3.11, y assimp_py (FBX) solo trae
# ruedas para 3.12 en adelante; las versiones de requirements.txt son las que
# corren con 3.13.
echo "-- Instalando: python@3.13, assimp (FBX), libredwg (DWG). Tarda unos minutos."
"$BREW" install python@3.13 assimp libredwg

PY="$("$BREW" --prefix python@3.13)/bin/python3.13"
[ -x "$PY" ] || { echo "FALLA: no encuentro $PY"; exit 1; }

# 3. Entorno propio: nada se instala global.
# Cambio local (12 sep 2026): antes se borraba .venv ANTES de instalar, y un
# pip fallido dejaba el programa levantando sin paquetes (cada subida daba
# error 500). Ahora el .venv que ya funcionaba se guarda aparte y regresa si
# algo falla.
echo "-- Entorno virtual .venv ($("$PY" -V))"
rm -rf .venv_respaldo
if [ -d .venv ]; then mv .venv .venv_respaldo; fi
regresar() {
  echo
  echo "FALLA: $1"
  rm -rf .venv
  if [ -d .venv_respaldo ]; then
    mv .venv_respaldo .venv
    echo "   Se dejo la instalacion que ya estaba: el programa sigue como antes."
  fi
  exit 1
}
"$PY" -m venv .venv || regresar "no se pudo crear el entorno .venv"
./.venv/bin/python -m pip install -q --upgrade pip || regresar "no se pudo actualizar pip"
./.venv/bin/python -m pip install -q -r requirements.txt \
  || regresar "pip no pudo instalar requirements.txt (arriba dice cual paquete)"

# Formatos nuevos (DAE, 3DM, STEP, FBX), uno por uno: si uno no entra, solo ese
# formato se queda sin leer y el resto del programa funciona.
FALTAN=""
while read -r PAQ; do
  case "$PAQ" in ''|\#*) continue;; esac
  ./.venv/bin/python -m pip install -q "$PAQ" || FALTAN="$FALTAN $PAQ"
done < requirements-formatos.txt

# 4. Prueba de verdad antes de darlo por bueno
echo "-- Prueba de humo (corta una casa de ejemplo)"
./probar.sh || regresar "la prueba de humo no paso"
rm -rf .venv_respaldo

echo
if [ -n "$FALTAN" ]; then
  echo "AVISO: no se pudieron instalar:$FALTAN"
  echo "   Esos formatos no se van a poder leer; todo lo demas funciona."
  echo
fi
echo "LISTO. Para abrir el programa: doble clic en «2 - Abrir Despiece 3D»"
