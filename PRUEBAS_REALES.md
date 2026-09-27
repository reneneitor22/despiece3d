# Pruebas con modelos reales

Los `gen_*.py` hacen modelos limpios: cuerpos sólidos, bien soldados, sin basura.
Ningún modelo de internet es así. Estas son las pruebas contra modelos que no
hicimos nosotros.

## Los modelos

Se bajaron de dos repositorios públicos, ninguno requiere cuenta ni pago.

| repo | licencia | qué trae |
|---|---|---|
| [ladybug-tools/3d-models](https://github.com/ladybug-tools/3d-models) | MIT | modelos de arquitectura, ingeniería y construcción (AEC) |
| [va3c/nasa-samples](https://github.com/va3c/nasa-samples) | dominio público (NASA 3D Resources) | topografía real de Marte y la Luna, y sondas |

Se clonan en `modelos_prueba/` (fuera de git, son 1.5 GB):

```bash
git clone --depth 1 https://github.com/ladybug-tools/3d-models.git modelos_prueba/ladybug
git clone --depth 1 https://github.com/va3c/nasa-samples.git modelos_prueba/nasa
```

| modelo | archivo | qué es | modo |
|---|---|---|---|
| Casa Engel | `ladybug/obj/engel-house/AngelHouse_Bauhaus-in-Israel.obj` | edificio Bauhaus de Tel Aviv, 44 × 34 × 18 m | casa |
| Main Street Place | `ladybug/stl-samples/MainStreetPlace.stl` | conjunto de edificios, 703 mil caras | casa |
| Distrito urbano | `ladybug/obj/urban_model_001/model.obj` | manzanas completas, 911 × 631 m | casa |
| Valles Marineris | `nasa/stl/mars_valles_mar.stl` | topografía de Marte, 247 mil caras | terreno |
| Cráter Gale | `nasa/stl/gale_crater.STL` | topografía de Marte, 526 mil caras | terreno |

### Cinco más, bajados el 27 sep 2026 (formatos que usan los alumnos)

Van en `modelos_prueba/nuevos_27sep/`. Ninguno pide cuenta:

```bash
D=modelos_prueba/nuevos_27sep; mkdir -p $D
curl -L -o $D/AC20-FZK-Haus.ifc https://www.ifcwiki.org/images/e/e3/AC20-FZK-Haus.ifc
curl -L -o $D/Revit_ARC.ifc https://github.com/youshengCode/IfcSampleFiles/raw/main/Ifc4_Revit_ARC.ifc
curl -L -o $D/Hearst_Tower.skp "https://github.com/SketchUp/testup-2/raw/HEAD/tests/SketchUp%20Ruby%20API/TC_Sketchup_Texture/Hearst+Tower+(New+York)-su2020.skp"
curl -L -o $D/Project_LoopS.3dm "https://github.com/MRAC-IAAC/Pavilion-Topology/raw/HEAD/Project%20LoopS/Project%20LoopS.3dm"
curl -L -o $D/kenney.zip https://kenney.nl/media/pages/assets/city-kit-suburban/2c871b7af2-1745479373/kenney_city-kit-suburban_20.zip
unzip -j $D/kenney.zip "Models/FBX format/building-type-t.fbx" -d $D && mv $D/building-type-t.fbx $D/Kenney_casa_t.fbx
```

| modelo | fuente | qué es | lo que enseñó |
|---|---|---|---|
| FZK-Haus (IFC, 2.6 MB) | KIT Karlsruhe, ArchiCAD | casa de 2 plantas con techo a dos aguas | 42 vigas del techo (`IfcMember`) salían muro y rebanaban el techo: 1.96% de interferencia. Ahora 0.00% |
| Revit ARC (IFC, 13.6 MB) | muestra de Revit | edificio 70 × 55 m, muro cortina | parteluces y escaleras salían placas ("techos" falsos) |
| Hearst Tower (SKP, 5 MB) | repo de pruebas de SketchUp | torre de 184 m, caras sin espesor y diagrid | en casa no cierra (0.65%): va en modo terreno |
| Project LoopS (3DM, 21 MB) | IAAC Barcelona, pabellón de alumnos | forma libre + 3 mallas perdidas a 1.4 km | el terreno moría sin memoria (7000 capas); ahora se quitan los perdidos |
| Kenney casa T (FBX, 82 KB) | Kenney, CC0 | casa de videojuego, maciza | a 1:200 salía UNA pieza de 7 mm sin aviso |

## Correr la batería

```bash
python3 pruebas_reales.py            # todo
python3 pruebas_reales.py engel      # uno solo
```
