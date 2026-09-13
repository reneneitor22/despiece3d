# Despiece 3D — instalar en tu Mac

Corre local, en tu propia máquina. Nada se sube a internet: el modelo y los
archivos de corte no salen de tu Mac.

## Instalar (una sola vez, ~10 min)

Abre **Terminal** (Aplicaciones → Utilidades → Terminal) y pega esto, línea por línea:

```bash
cd ~/Desktop/despiece3d
./instalar.sh
```

El instalador hace todo solo: baja lo que falta, arma su propio entorno y **al
final corta una casa de ejemplo para comprobar que quedó bien**. Si algo falla,
te dice en qué paso — mándame ese renglón.

Si te dice que falta **Homebrew**, pega el comando que él mismo te muestra
(te va a pedir la contraseña de tu Mac), y vuelve a correr `./instalar.sh`.

## Usar

```bash
cd ~/Desktop/despiece3d
./correr.sh
```

Se abre solo en el navegador (tarda ~10 seg en levantar la primera vez). Subes
tu modelo, eliges lámina/hoja/escala, y descargas el zip con **DWG, DXF, PDF y
la guía de armado**.

Para cerrarlo: **Control-C** en la ventana de Terminal.

## Si algo se ve raro

```bash
./probar.sh
```

Corta la casa de ejemplo y revisa la salida (que el DWG traiga dibujo y las capas
CORTE / GRABADO / MARCADO). Si esto pasa, el programa está bien y el problema es
el modelo que subiste; si falla, es la instalación.

## Cuando yo suba cambios

```bash
cd ~/Desktop/despiece3d
git pull
./instalar.sh     # solo si te aviso que cambió algo de lo que instala
```

## Lo que instala (para que sepas qué está entrando)

- **Homebrew** — el instalador de programas de línea de comandos de Mac.
- **python@3.13** — el motor; queda aparte, no toca el Python del sistema.
- **assimp** — para leer FBX.
- **libredwg** — para escribir el DWG.
- **ifcopenshell** — para leer IFC. Se baja con las demás librerías de Python,
  no hace falta nada de Homebrew para el IFC.
- Las librerías de Python van dentro de la carpeta `.venv` del propio proyecto.
  Si borras la carpeta del proyecto, no queda nada regado.
