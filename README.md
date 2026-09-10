# Despiece 3D

Convierte un modelo 3D en piezas planas cortables, numeradas y acomodadas en hojas.
Salida: **DXF** y **DWG** (corte láser), **PDF** a tamaño real y **SVG/HTML**
(imprimir y cortar a mano).

Lee **IFC** (ArchiCAD, Revit, Allplan), STL, OBJ, PLY, GLB, DAE, **SKP**
(SketchUp) y **FBX** (Autodesk). El `.rvt` de Revit no se puede leer —formato
cerrado— y el programa lo reconoce para decir cómo exportar el IFC.

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

## Los formatos que no lee ninguna librería de mallas

Un alumno no exporta STL: entrega el archivo de su programa. Los que llegan son
IFC, SKP y FBX, y trimesh no sabe que existen. Cada uno tiene su lector aparte
(`ifc.py`, `skp.py`, `fbx.py`) y los tres entregan lo mismo: **metros y Z
arriba**, que es lo único que el resto del programa entiende.

### IFC — el único que entra con semántica

Es la entrada buena, y no por la geometría sino por lo que trae **escrito**. Los
otros formatos son mallas: el programa tiene que deducir qué es cada cuerpo por
su caja orientada y por su normal, o sea adivinar, y adivinar se equivoca solo
cuando el modelo no está bien hecho. El IFC dice `IFCWALL`, dice a qué
`IfcBuildingStorey` pertenece cada elemento y dice cuál es un sofá.

Lo que cambia, medido en `202103162102_cira.ifc` (IFC4, 638 elementos):

| lo que hacía adivinando | lo que hace con el IFC |
|---|---|
| partía la malla por conectividad: 91 `IfcWall` daban **531 cuerpos**, porque un muro real se exporta **por capas de material** (8 sólidos encimados) más basura de milímetros de restar los huecos | un elemento es **un cuerpo**: 311 elementos → 311 cuerpos, y la caja orientada mide el muro completo, que es lo que se corta en cartón |
| clasificaba por la normal, así que una fachada inclinada salía «techo» | `IFCWALL` es muro aunque esté a 20° |
| agrupaba losas por su Z para inventar los niveles | las plantas salen del archivo, con su nombre |
| 153 muebles, 27 ventanas y 17 puertas entraban como cuerpos que después había que descartar uno por uno | no entran: **327 elementos** se quedan fuera desde el principio |

**Losa contra techo la sigue decidiendo la geometría, a propósito.** Un `IfcRoof`
plano de azotea es una losa para armar la maqueta, y llamarlo «techo» lo saca de
`huellas_en_losas`, que graba la planta de los muros sobre las losas. Así que el
IFC decide *si es muro* y la normal decide entre losa y techo.

Dos cosas que **no** hay que tocar, y está medido: IfcOpenShell aplica la unidad
declarada en `IfcUnitAssignment` y entrega **metros SI** pase lo que pase
(`20200205Model_PNO.ifc` está guardado en milímetros y sale como 125.48 × 6.36 ×
3.50 m, la nave que es), y el eje de arriba en IFC es Z por definición del
formato. Es el único de los tres que entra derecho.

Los huecos ya vienen restados —IfcOpenShell aplica los `IfcOpeningElement` sobre
el muro que los recibe— y por eso el `IfcOpeningElement` suelto se descarta: es
el volumen del hueco, no una pieza.

Meshear cuesta lo suyo (9.9 s los 206 muros y losas del `cira`) y el CLI lo
repite en cada corrida, así que se guarda en `.cache_ifc/` con la firma del
archivo en el nombre: 9.9 s → 0.7 s.

**Trampa que costó encontrar:** el repliegue de «modelo de caras sin espesor» de
`extraer_placas` se disparaba con el IFC y tiraba todo el trabajo. Los 311
sólidos daban 237 placas cuya área suma menos del 15% del área de la malla
—normal: la malla cuenta las **dos** caras de cada sólido y la placa es una sola
sección— así que el repliegue las cambiaba por 678 parches de superficie **sin
tipo ni planta**, y el .ifc terminaba adivinando igual que un STL. Con semántica
ese repliegue va apagado (`superficies=False`).

### RVT (Revit) — no se lee, y no es que falte una librería

Es formato cerrado de Autodesk y **no existe lector libre**: el único SDK que
abre el archivo por fuera es el BIM/Revit de la Open Design Alliance, comercial
y por instalación. `openskp` existe para SketchUp e `ifcopenshell` para IFC;
para `.rvt` no hay equivalente.

Lo que sí se hace es no dejar al alumno con un error feo. El `.rvt` es un
contenedor OLE (`D0 CF 11 E0 A1 B1 1A E1`) con un flujo de texto,
`BasicFileInfo`, en UTF-16 con la versión con que se guardó. Se lee, y se
contesta con su versión en la mano y los cinco clics del menú de Revit para
sacar el IFC —que Revit exporta de fábrica, sin comprar ni instalar nada—.

El contenido de ese flujo **no está al principio del archivo**: en los 21 `.rvt`
medidos, el nombre `BasicFileInfo` cae en el offset 4992 (la tabla de directorio
del OLE) pero su contenido cayó al final. Leyendo sólo el primer mega, 6 de 21
se quedaban sin versión; se recorre el archivo con `mmap`, que no lo carga.

### SKP (SketchUp)

Formato cerrado. El SDK oficial de Trimble pide cuenta de desarrollador y
compilar contra un framework de C: no es algo que un alumno instale. Se usa
[`openskp`](https://pypi.org/project/openskp/), un lector hecho por ingeniería
inversa que se instala con pip y abre los dos contenedores que existen — VFF
(2021 en adelante) y el CArchive de MFC (2013-2020).

Dos conversiones a la salida, y ninguna es cosmética:

* **Ejes.** `openskp` entrega Y arriba (convención glTF). Con Y arriba, cada
  muro se clasifica como losa — `placas.py` decide muro o losa por la
  componente Z de la normal — y la casa sale en rebanadas horizontales.
* **Unidades.** SketchUp guarda todo en pulgadas por dentro sin importar lo que
  diga la regla en pantalla. `openskp` ya lo pasa a metros; la unidad que trae
  el archivo (`Inches`) es nomás cómo se le enseña al usuario y **no** se debe
  usar para escalar.

Parsear y hornear una casa completa toma ~15 s y el CLI lo haría en cada
corrida, así que la malla convertida se guarda en `.cache_skp/` con la firma del
archivo (tamaño + fecha) en el nombre: si se vuelve a exportar desde SketchUp,
la firma cambia y se relee solo.

### FBX (Autodesk)

La conversión la hace `assimp`. Lo que hace y lo que no está **medido**, no
supuesto — `prueba_fbx.py` lo vuelve a comprobar en cualquier máquina:

* **El eje de arriba lo arregla assimp.** Con cabecera de 3ds Max (Z arriba)
  una caja de 4 × 2 × 10 sale de assimp como 4 × 10 × 2: la giró. Con cabecera
  Y arriba la deja igual. O sea que la salida **siempre viene Y arriba**, y de
  ahí se pasa a Z arriba. Ojo: no sirve leer `UpAxis` y rotar por cuenta propia,
  porque assimp ya rotó.
* **La unidad NO la toca.** Una caja de 400 × 1000 × 200 con `UnitScaleFactor`
  = 1 (centímetros) sale con esos mismos números. Ese campo dice cuántos
  centímetros mide una unidad del archivo: 1 son centímetros (lo normal saliendo
  de Max), 100 metros, 2.54 pulgadas. Sin aplicarlo, una casa de 10 m entra como
  si midiera 10 cm y no queda ni una placa cortable.

El paso intermedio va en STL y no en PLY: el PLY que escribe assimp para un
modelo con materiales trae la tabla de colores incompleta — declara `red` y
`green` sin `blue` — y trimesh truena con `KeyError: 'blue'`.

## Las salidas

| archivo | para qué |
|---|---|
| `<modelo>_hojaNN.dxf` | lo que lee la máquina de corte; una capa por operación + la tabla de corte |
| `<modelo>_hojaNN.dwg` | lo mismo en DWG, que es lo que piden las cabinas de corte |
| `<modelo>.pdf` | todas las hojas a tamaño real, para imprimir y cortar a mano |
| `<modelo>_hojaNN.svg` | igual, para el navegador |
| `<modelo>_guia.html` | vistas armada y explotada, tabla de piezas y avisos |

El PDF trae la hoja como **tamaño de página**, así que se manda a imprimir a
escala 100% y las piezas miden lo que dicen. El SVG también imprime, pero el
navegador lo reescala al papel y el corte sale a otra medida.

### Listo para mandar, sin abrir AutoCAD

El archivo sale ya preparado para el taller. Lo que antes se hacía a mano en
AutoCAD —pintar cada línea del color de su operación— viene hecho:

- **Una capa por operación**, con nombre y color que se eligen en la pantalla
  (*Cómo lo quiere tu taller*). Se escribe el **índice de color de AutoCAD** y el
  **color verdadero (RGB)**: las cabinas viejas (RDWorks) mapean la operación por
  índice y las nuevas (LightBurn) por RGB, así que con los dos puestos el archivo
  cae bien en las dos. De fábrica: `CORTE` rojo, `GRABADO` azul, `MARCADO` verde.
- **El número de pieza va en MARCADO**, aparte del grabado, para que el taller lo
  pueda bajar de potencia o apagarlo sin tocar las huellas de ensamble.
- **Marco** del tamaño de lámina elegido, para alinear el material.
- **Tabla de corte** dentro del dibujo: proyecto, hoja, escala, material, espesor,
  medida de hoja, kerf, piezas y la equivalencia capa → operación.

El marco y la tabla van en la capa **HOJA**, que la cabina no tiene asignada a
ninguna operación: se ven al abrir el plano pero no se cortan ni se graban. La
tabla se dibuja **debajo del marco**, fuera del área de corte, para no comerse
material.

Todo eso también aplica al DXF, que es el que sí sale siempre.

### El DWG, y el bug que lo tuvo muerto meses

Las cabinas de corte piden DWG. ezdxf no lo escribe — es formato cerrado de
Autodesk — así que hay que salir a una herramienta de afuera:

1. **ODA File Converter** (opendesign.com). Gratis, pero se baja a mano dando un
   correo. Escribe hasta ACAD2018.
2. **`dwgwrite` (LibreDWG 0.14)**, que se instala con brew y sólo escribe r2000.

Durante meses el camino 2 entregó un archivo que **AutoCAD abría en negro**:
todos los nombres recortados a su primera letra — las capas `CORTE` / `GRABADO`
/ `HOJA` en `C` / `G` / `H`, y el bloque `*Model_Space` en `*`, que ya no es
cosmético: deja las entidades colgando de un bloque que ningún layout
referencia, con `$EXTMIN` en el valor de "dibujo vacío". El archivo pesaba,
`dwgread` lo releía y hasta contaba las polilíneas.

No era el tamaño ni el escritor: era **la versión del DXF de entrada**. De R2007
en adelante el DXF guarda los nombres en UTF-8, y LibreDWG los relee como si
fueran de dos bytes, se topa con el NUL y corta ahí. Medido, con el mismo
dibujo:

| DXF de entrada | capas en el DWG |
|---|---|
| R2000, R2004 | `0 Defpoints CORTE GRABADO HOJA` ✅ |
| R2010, R2013, R2018 | `0 D C G H` ❌ |

De ahí que `hoja_a_dxf` escriba **R2004**: es la versión más vieja que todavía
guarda color verdadero (RGB) en la capa. Con eso el DWG sale completo — medido
en la casa Bauhaus, 500 entidades y las cuatro capas idénticas entre el DXF y el
DWG. **Si algún día se cambia esa versión, el DWG se rompe en silencio**; lo
único que lo caza es la verificación de abajo.

Lo que sí se pierde por este camino: el **color verdadero (RGB)** de las capas,
porque el DWG r2000 es anterior a él. Queda el índice de color de AutoCAD, que
es exacto para la convención normal (rojo 1, azul 5, verde 3) y es lo único que
miran las cabinas viejas. El DXF que va en el mismo zip lleva los dos.

De todo esto quedó cableada una regla: **el DWG se verifica abriéndolo y
contando lo que hay en el espacio modelo**, no buscando palabras en un volcado
de texto. Esa cuenta de texto es justamente la que dejó pasar un DWG muerto
hasta las manos de un arquitecto. Si la verificación falla, el archivo se borra
y se avisa en pantalla — nadie se entera de que el DWG está vacío estando frente
a la máquina.

## La cuenta que hay que tener clara

La placa **no es una superficie, es una losa de espesor t**. Para librar el cartón de
otra placa que llega en ángulo θ hay que avanzar

```
d = (t/2) · (1 + cos θ) / sen θ
```

No `(t/2)/sen θ`: eso sólo vale para el plano medio e ignora el material que queda
fuera de él. Con el techo a 28° del muro, la diferencia son 0.05 mm de interferencia
por pieza — poco, pero se acumula y la maqueta no cierra.

## Debug

Correa de trazas por etapa: tiempo, tamaños, conteos y errores de toda la ruta
—recepción del multipart, parseo, escritura a disco, y cada paso del despiece—.

- Apagada por omisión. Se prende con `DESPIECE_DEBUG=1` en el entorno, o con
  `?debug=1` en la URL (botón «mostrar debug» bajo el botón de generar).
- Con `?debug=1` la página muestra un panel: tamaño del archivo, barra de subida,
  tiempos, status HTTP, respuesta cruda y enlace al log del servidor.
- Cada trabajo escribe `debug.log` (una línea JSON por evento) en su carpeta; se
  ve en `/r/<job>/debug.log` y va dentro del .zip.
- Los errores se registran siempre, esté prendida o no.

```bash
DESPIECE_DEBUG=1 python3 app.py 3561     # todo a stderr y al debug.log de cada job
python3 -m unittest test_dbg test_subida # pruebas de la correa
```

## Verificar

```bash
python3 gen_casa.py                       # casa de prueba con vanos, losa y techo
python3 verificar_casa.py out/casa_prueba.stl 100   # prueba de ENSAMBLE en 3D
python3 gen_terreno.py && python3 verificar.py      # nesting sin encimadas
python3 prueba_fbx.py                     # que el FBX entre en metros y Z arriba

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
| `exportar.py` | DXF (capas CORTE / GRABADO / HOJA), DWG, PDF, SVG, guías HTML |
| `previsualizar.py` | rasteriza una hoja a PNG (revisar sin depender del navegador) |
| `app.py` | servidor local + interfaz web |
| `ifc.py` | leer IFC **con semántica**: tipo, planta y qué no va en la maqueta |
| `rvt.py` | reconocer un archivo de Revit, sacarle la versión y decir cómo exportar IFC |
| `skp.py` / `fbx.py` | leer SketchUp y FBX: ejes, unidades y caché |
| `verificar_casa.py` / `verificar.py` | auditorías |

## Dependencias

```bash
pip3 install --user trimesh shapely ezdxf networkx scipy rtree pillow numpy rectpack \
                    reportlab openskp ifcopenshell
brew install assimp libredwg          # solo para FBX y para el DWG
```

`reportlab` es para el PDF, `openskp` para leer SketchUp, `ifcopenshell` para
leer IFC, `assimp` para leer FBX y `libredwg` para escribir DWG. Sin los
últimos el programa sigue sirviendo: avisa qué falta y entrega DXF, que es lo
que lee casi toda máquina de corte.

`ifcopenshell` trae su propio motor de geometría **compilado** (~42 MB de rueda)
y se baja de PyPI como cualquier otra: para el IFC **no** hace falta brew. Por
ser binario, su prueba va dentro de `probar.sh`: de esos sólo se sabe si sirven
corriéndolos en un `.venv` limpio, que es la lección que dejaron `mapbox_earcut`
y `rtree`.

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

1. **Entrada con semántica**: ~~IFC~~ hecho (ver arriba). Falta **.3dm** (Rhino),
   que también trae sólidos con capas y nombres. Y del IFC falta aprovechar lo
   que todavía se ignora: el material de cada elemento (`IfcMaterialLayerSet`
   dice de qué está hecho el muro y en qué capas) y los `IfcSpace`, que darían
   los cuartos sin deducirlos.
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
