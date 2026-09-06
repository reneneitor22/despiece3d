# Despiece 3D

Convierte un modelo 3D en piezas planas cortables, numeradas y acomodadas en hojas.
Salida: **DXF** (corte láser) y **SVG/HTML** (imprimir y cortar a mano).

```bash
cd ~/Desktop/Claude/despiece3d
python3 app.py 3561        # abre http://localhost:3561
```

## Dos modos

### 1. Casa / edificio — despiece estructural

Saca **muros, losas y techos** como placas, con **uniones de dientes** para que la
maqueta se ensamble sola.

Cómo funciona:

1. **Separa cuerpos** y descarta lo que no es lámina (relación espesor/largo > 0.34).
2. **Corta cada cuerpo por su plano medio.** Esa sección ES la pieza: trae ventanas y
   puertas ya recortadas, sin operaciones booleanas.
3. **Detecta encuentros** entre placas y les mete unión:
   - **ranura pasada** cuando una placa cae en medio de la otra;
   - **dientes alternados** cuando se encuentran canto con canto (esquinas, muro
     parado en el filo de la losa). Es el caso que domina en una casa real.
4. **Recorta choques** que no alcanzaron unión (el alero contra el remate del muro).
5. **Graba la huella** de cada pieza sobre la que la recibe: el alumno sabe dónde va.
6. Acomoda, compensa el kerf y exporta.

```bash
python3 cortar_casa.py modelo.stl --escala 100 --espesor 2 --hoja 500x700
python3 cortar_casa.py modelo.stl --sin-uniones      # todo a tope, para pegar
```

### 2. Terreno / topografía — curvas de nivel

Rebanadas horizontales apiladas. Solidifica superficies abiertas (le cose faldón y
fondo) y puede vaciar el interior tapado por la lámina de arriba: ahorra ~50% de
material y abre hueco donde el acomodo mete las piezas chicas.

```bash
python3 cortar.py terreno.stl --escala 500 --espesor 3 --hoja 500x700
```

## La cuenta que hay que tener clara

La placa **no es una superficie, es una losa de espesor t**. Para librar el cartón de
otra placa que llega en ángulo θ hay que avanzar

```
d = (t/2) · (1 + cos θ) / sen θ
```

No `(t/2)/sen θ`: eso sólo vale para el plano medio e ignora el material que queda
fuera de él. Con el techo a 28° del muro, la diferencia son 0.05 mm de interferencia
por pieza — poco, pero se acumula y la maqueta no cierra.

## Verificar

```bash
python3 gen_casa.py                       # casa de prueba con vanos, losa y techo
python3 verificar_casa.py out/casa_prueba.stl 100   # prueba de ENSAMBLE en 3D
python3 gen_terreno.py && python3 verificar.py      # nesting sin encimadas
```

`verificar_casa.py` reconstruye en 3D las piezas ya cortadas y busca pares que ocupen
el mismo volumen. Si dos piezas chocan, la maqueta no cierra por más bonito que se vea
el DXF. Medición actual sobre la casa de prueba a 1:100 con cartón de 2 mm:

| | |
|---|---|
| material | ~34,000 mm³ |
| interferencia | **7.9 mm³ (0.02%)** |
| penetración local máxima | ~0.04 mm |

Debajo del propio kerf del láser (0.15 mm), o sea: arma.

## Archivos

| archivo | qué es |
|---|---|
| `placas.py` | saca muros/losas/techos del modelo (sección por plano medio) |
| `uniones.py` | dientes, ranuras, marcas grabadas y recorte de choques |
| `estructura.py` | pipeline del modo casa |
| `despiece.py` | modo curvas + nesting por geometría real (raster + FFT) |
| `isometrica.py` | vistas armada y explotada |
| `exportar.py` | DXF (capas CORTE / GRABADO / HOJA), SVG, guías HTML |
| `previsualizar.py` | rasteriza una hoja a PNG (revisar sin depender del navegador) |
| `app.py` | servidor local + interfaz web |
| `verificar_casa.py` / `verificar.py` | auditorías |

## Dependencias

```bash
pip3 install --user trimesh shapely ezdxf networkx scipy rtree pillow numpy rectpack
```

## Pendientes

1. **Entrada con semántica**: hoy lee mallas (STL/OBJ/DAE/PLY/GLB) y deduce las placas
   por geometría. Con **IFC** (ArchiCAD/Revit) o **.3dm** (Rhino) sabría que algo *es*
   un muro, y dejaría de depender de que el modelo esté bien hecho.
2. Muros modelados como **caras sin espesor** (muy común en SketchUp): hoy se descartan.
   Habría que darles espesor sintético.
3. Escaleras, barandales y muebles: se ignoran por no ser láminas.
4. Cobro: preview gratis con marca de agua, pago para descargar.
