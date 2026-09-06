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

## Correr la batería

```bash
python3 pruebas_reales.py            # todo
python3 pruebas_reales.py engel      # uno solo
```
