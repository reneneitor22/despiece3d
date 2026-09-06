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
python3 cortar_casa.py modelo.stl --sin-uniones        # todo a tope, para pegar
python3 cortar_casa.py modelo.stl --solo-envolvente    # la caja, sin entrepisos
python3 cortar_casa.py modelo.stl --pisos              # que niveles trae
python3 cortar_casa.py modelo.stl --piso 4             # cortar solo ese nivel
python3 cortar_casa.py modelo.stl --laminar-macizos    # escaleras y muebles
```

`--solo-envolvente` tira un rayo desde cada placa en dirección de su normal: si por
algún lado se va sin chocar, esa cara mira a la calle. La casa Bauhaus completa son
143 placas en 2 hojas; su envolvente, 56 en 1.

`--piso N` corta un solo nivel: la maqueta se arma planta por planta y cada muro
recibe un puñado de ranuras en vez de todas las del edificio. Los muros se **cortan**
a la altura del nivel, no se descartan. `--pisos` lista los niveles de losa que trae
el modelo — el programa no adivina cuáles son "los pisos de verdad", los enseña y el
alumno escoge (ver el comentario en `niveles_de_piso`: agrupar con más holgura
encadena, y filtrar por área tumba las plantas de una torre).

`--laminar-macizos` rescata lo que no es lámina: escaleras, barandales, columnas y
muebles se **rebanan en horizontal y se apilan**, igual que el terreno. Sin la bandera
el programa nomás avisa cuántos cuerpos así encontró. Deja fuera lo que es demasiado
grande para ser un mueble: muchos modelos traen además el volumen macizo del edificio
entero como un cuerpo más.

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

# tambien con modelos de verdad
python3 verificar_casa.py casa.obj 100 --espesor 2
python3 verificar.py terreno.stl --escala 100 --espesor 3 --hoja 500x700
```

`verificar.py` revisa que ninguna pieza se salga de la hoja, que no se encimen, que
**no se pierda ninguna** por no caber y que el grabado caiga sobre material.
`verificar_casa.py` mide la interferencia como **fracción del material** y la compara
contra `--tolerancia` (0.5% por omisión): exigir cero reprobaba hasta la casa de
prueba, que lleva 0.02% desde siempre y arma bien. También afloja solo el paso del
muestreo cuando el modelo es grande: un edificio a 1:100 son 430 millones de vóxeles
a 0.4 mm.

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
| `placas.py` | saca muros/losas/techos: sección por plano medio si hay sólido, agrupación de caras coplanares si el modelo es de superficies |
| `uniones.py` | dientes, ranuras, marcas grabadas y recorte de choques |
| `estructura.py` | pipeline del modo casa + laminado de escaleras y muebles |
| `despiece.py` | modo curvas + nesting por geometría real (raster + FFT) + partido de piezas que no caben |
| `isometrica.py` | vistas armada y explotada |
| `exportar.py` | DXF (capas CORTE / GRABADO / HOJA), SVG, guías HTML |
| `previsualizar.py` | rasteriza una hoja a PNG (revisar sin depender del navegador) |
| `app.py` | servidor local + interfaz web |
| `verificar_casa.py` / `verificar.py` | auditorías |

## Dependencias

```bash
pip3 install --user trimesh shapely ezdxf networkx scipy rtree pillow numpy rectpack
```

## Modelos bajados de internet

Un modelo hecho por un alumno o bajado de Sketchfab no se parece a los que generan
los `gen_*.py`. Lo que trae y cómo se resuelve:

| lo que trae el modelo | qué pasaba | qué hace hoy |
|---|---|---|
| malla sin soldar (OBJ de SketchUp parte el vértice por textura y por normal) | la casa Bauhaus se veía como 1262 cuerpos de dos triángulos | `merge_vertices(merge_tex, merge_norm)` antes de separar cuerpos: quedan 334 |
| **muros sin espesor** (caras sueltas) | sección por plano medio vacía → 0 placas | se agrupan las caras por plano, se unen sus triángulos y se **funden las dos caras de cada muro** en una placa sobre el plano medio |
| cuerpos degenerados (vértices colineales) | `oriented_bounds` reventaba y tumbaba todo el despiece | se descarta ese cuerpo y sigue |
| pieza más grande que la hoja | se tiraba en silencio: el terreno salía sin base | `partir_grandes` la corta con una reja en trozos `<id>.1`, `<id>.2`, que van a tope |
| edificio con muchos muros interiores | un medianero recibía 154 ranuras y quedaba en encaje de bolillo | se cancelan uniones hasta que cada placa conserve el 55% de su área; esas juntas se pegan |
| dos placas paralelas casi pegadas (la losa y su firme, el muro y su aplanado) | salían como dos piezas: dos cartones en el mismo lugar | si están más juntas que el propio cartón, **no caben las dos** y se funden en una |
| edificio de varios pisos | daba entrepisos y muros interiores que nadie quiere | `--solo-envolvente` deja la caja de afuera; `--piso N` corta una planta |
| escaleras, muebles, columnas | se tiraban por no ser lámina | `--laminar-macizos` los rebana y apila |
| esos cuerpos casi nunca vienen cerrados | rebanarlos daba cero capas | se les cose faldón y fondo antes |
| modelo enorme (un distrito entero) | tope duro de 600 placas: no entregaba nada | se tira lo que no se corta a esa escala y se cortan las 400 más grandes, diciendo qué quedó fuera |

La regla que ordena varias de esas: **lo que manda es el tamaño en la MAQUETA, no en el
modelo**. Una placa de 1 m² es una pieza de 10×10 mm a 1:100 y de 2×2 mm a 1:500.

## Pendientes

1. **Entrada con semántica**: hoy lee mallas (STL/OBJ/DAE/PLY/GLB) y deduce las placas
   por geometría. Con **IFC** (ArchiCAD/Revit) o **.3dm** (Rhino) sabría que algo *es*
   un muro, y dejaría de depender de que el modelo esté bien hecho.
2. **El muro que ATRAVIESA la losa.** Es lo que sostiene la interferencia que queda
   (Bauhaus completa 1.23%, envolvente 0.15%, casa de prueba 0.02%). Ya se descartó
   que fuera la cancelación de uniones: midiendo un piso solo —29 placas, 31
   contactos, apenas 4 canceladas y **cero** recortes rechazados— los choques siguen
   ahí. Son pares donde el muro cruza la losa por en medio: unos salen en modo
   `dedos`, que es para canto con canto y no para un cruce, y otros ni siquiera se
   detectan como contacto. Hay que enseñarle a `uniones.py` el caso del cruce.
3. **La planta arquitectónica.** Ya se corta por piso y por envolvente; falta sacar
   la planta dibujada, que es lo que además le piden al alumno para entregar.
4. Cobro: preview gratis con marca de agua, pago para descargar.
