# Correa de debug — despiece3d

Fecha: 2026-09-07

## Problema

`app.py` es un servidor HTTP de un solo archivo. No tiene logging estructurado:
solo `print()` sueltos y `traceback.print_exc()` en los dos `except` de los
handlers. Cuando una subida falla o el despiece sale raro, no hay rastro de qué
etapa se rompió, con qué tamaños, ni cuánto tardó cada paso. El diagnóstico es a
ciegas contra la consola del servidor.

## Objetivo

Instrumentar toda la ruta de trabajo con una "correa de debug" — timing,
tamaños, conteos y errores por etapa — con énfasis en la subida de datos
(recepción multipart → parseo → escritura a disco). El detalle queda en un log
por trabajo, descargable, y en un panel colapsable en el navegador.

No-objetivos: niveles de verbosidad configurables, rotación de logs, formato
legible para humanos (se usa JSON lines), y trazas dentro de los módulos del
pipeline (`despiece.py`, `estructura.py`, `exportar.py` quedan intactos).

## Arquitectura

### Módulo nuevo: `dbg.py`

~70 líneas, solo stdlib (`json`, `time`, `os`, `sys`, `threading`, `traceback`,
`contextlib`, `datetime`).

Estado por hilo (`threading.local`), porque el servidor es
`ThreadingHTTPServer` y cada request corre en su hilo:

- `_estado.job` — id del trabajo activo, o `None`.
- `_estado.req_debug` — bool, si la request trae `?debug=1`.

API:

- `set_request(flag: bool)` — el handler lo llama al entrar, con el resultado de
  buscar `debug=1` en la query string.
- `set_job(job: str)` — se llama en cuanto se crea la carpeta del trabajo.
- `clear()` — el handler lo llama en `finally`, deja el hilo limpio.
- `activo() -> bool` — `True` si `os.environ.get('DESPIECE_DEBUG')` es truthy
  (`'1'`, `'true'`, `'yes'`, sin distinguir mayúsculas) **o** `_estado.req_debug`.
- `log(evento: str, *, nivel: str = 'info', **campos)` — arma un dict
  `{'ts', 'job', 'nivel', 'evento', **campos}`, lo serializa como una línea JSON
  (`json.dumps(..., ensure_ascii=False, default=str)`) y la escribe a:
  1. `sys.stderr`, con prefijo `[dbg] `.
  2. `JOBS/<job>/debug.log` (append), si `_estado.job` está puesto.
  Si `activo()` es `False`, es no-op — **salvo** `nivel='error'`, que siempre se
  escribe (a stderr siempre; al archivo si hay job).
- `etapa(nombre: str, **campos)` — context manager (`@contextlib.contextmanager`):
  - al entrar: `log(nombre + '.inicio', **campos)` y arranca `time.perf_counter()`.
  - al salir normal: `log(nombre + '.fin', ms=<transcurrido>)`.
  - si sale excepción: `log(nombre + '.error', nivel='error', ms=<transcurrido>,
    excepcion=repr(e), traceback=traceback.format_exc())` y re-lanza.

`log` y `etapa` nunca deben tumbar una request: el cuerpo que escribe a archivo
va en `try/except Exception: pass` (si el disco falla, el trabajo sigue).

### Cambios en `app.py`

#### Handler `H`

- `do_GET` y `do_POST`: al entrar, `dbg.set_request('debug=1' in (self.path.split('?',1)[1] if '?' in self.path else ''))`; en `finally`, `dbg.clear()`.
- `do_GET` para `/r/<job>/<archivo>`: añadir `text/plain; charset=utf-8` como tipo
  cuando `nombre` termina en `.log` (hoy cae en `application/octet-stream`).

#### Subida — `do_POST` ruta `/cortar`

Envolver con `dbg.etapa`/`dbg.log`:

- `recibir`: antes de leer el cuerpo, `dbg.log('recibir.cabeceras',
  content_length=n, content_type=ctype, boundary_ok=bool(mb))`. Validación de
  `MAX` y boundary loguean `recibir.rechazo` con el motivo antes del `return`.
- lectura del cuerpo: dentro del `while`, cada vez que `leido` cruza un múltiplo
  de 4 MiB, `dbg.log('recibir.progreso', leido=leido, total=n)`.
- `parse`: `parse_multipart` pasa a devolver `(campos, archivo, meta)` donde
  `meta = {'n_partes': int, 'vacias': [nombres de campo con valor '']}`.
  Tras llamar: `dbg.log('parse.fin', n_partes=meta['n_partes'],
  campos=sorted(campos), vacias=meta['vacias'],
  archivo=nombre_orig if archivo else None,
  bytes_archivo=len(archivo[1]) if archivo else 0)`.
- `validar`: `dbg.log('validar', ext=ext, ok=ext in EXT_OK)`; el `return` de
  formato no soportado loguea `nivel='error'`.
- `guardar`: `with dbg.etapa('guardar', ruta=ruta_modelo):` alrededor del
  `open(...).write(datos)`, y después
  `dbg.log('guardar.verif', bytes_pedidos=len(datos),
  bytes_en_disco=os.path.getsize(ruta_modelo))`.
- `set_job(job)` se llama justo después de `os.makedirs(carpeta)`.

#### Subida de ejemplo — `_ejemplo`

- `dbg.log('ejemplo.pedido', id=pedido.get('id'))`.
- si no existe: `dbg.log('ejemplo.rechazo', nivel='error', id=...)`.
- `set_job(job)` tras `makedirs`.
- `with dbg.etapa('ejemplo.copiar', origen=elegido['ruta'], destino=destino):`
  alrededor del `shutil.copyfile`, y después `dbg.log('ejemplo.verif',
  bytes=os.path.getsize(destino))`.

#### Pipeline — `procesar` y `_estructural`

`dbg.etapa(...)` alrededor de cada llamada (sin tocar los módulos por dentro):

- `cargar_modelo` / detección skp / fbx. Al terminar:
  `dbg.log('modelo.cargado', vertices=len(m.vertices), caras=len(m.faces),
  extents=list(m.extents), watertight=m.is_watertight, vacio=m.is_empty)`.
- `solidificar` (solo si corre) — `dbg.etapa('solidificar')`.
- `rebanar` — `dbg.etapa('rebanar')`; después `dbg.log('rebanar.fin',
  n_capas=len(capas))`. Los `return {'error': ...}` de "sin capas" y ">400
  láminas" loguean `nivel='error'`.
- `armar_piezas` — `dbg.log('armar.fin', n_piezas=len(piezas))`.
- `partir_grandes` — `dbg.log('partir.fin', n_piezas=len(piezas),
  partidas=partidas)`.
- `acomodar` — `dbg.log('acomodar.fin', n_hojas=len(hojas),
  grandes=grandes)`.
- export: `with dbg.etapa('export.hoja', i=i+1):` por hoja alrededor de
  `hoja_a_dxf` + `hoja_a_svg`. `dbg.etapa('export.pdf')`,
  `dbg.etapa('export.dwg')` en `_extras` (y el `err` de `dxf_a_dwg` ya se
  loguea: `dbg.log('export.dwg.aviso', nivel='error', err=err)`).
- zip: `dbg.log('zip.fin', archivos=<lista>, bytes=os.path.getsize(zip_path))`.
- al final: `dbg.log('resumen', ms_total=<perf desde el inicio de procesar>,
  n_piezas=..., n_hojas=..., archivos=[{nombre,bytes}, ...])`.

Para `_estructural`: mismo trato en `despiece_estructural`
(`dbg.log('estructural.fin', n_piezas=len(piezas), uniones=info.get('n_uniones'),
descartados=info.get('descartados'))`), `acomodar`, export y `resumen`.

#### Errores de los handlers

Los dos `except Exception as e` de `do_POST`/`_ejemplo` conservan
`traceback.print_exc()` y añaden
`dbg.log('handler.error', nivel='error', excepcion=repr(e),
traceback=traceback.format_exc())`. La respuesta 500 gana campo `job` cuando ya
se había creado la carpeta (variable `job` inicializada a `None` al principio del
`try`), para que el navegador pueda pedir `/r/<job>/debug.log`.

#### `debug.log` en el zip

En `procesar` y `_estructural`, el bucle que arma el zip incluye `debug.log` si
existe (además de dxf/dwg/pdf/svg/guia.html).

### Frontend — `PAGINA`

- Toggle "debug": un `<button>` o `<a>` chico junto al botón "Generar" que hace
  `location.search = '?debug=1'` (o lo quita). Cuando `location.search` contiene
  `debug=1`: el `<details id="dbg">` se muestra y arranca abierto, y las peticiones
  van a `/cortar?debug=1` y `/ejemplo?debug=1`.
- `<details id="dbg">` (oculto salvo modo debug), bajo el bloque de resultado,
  con: tamaño del archivo, barra de progreso de subida, tiempo de subida, tiempo
  total (cliente), status HTTP, respuesta cruda (`<pre>` con
  `JSON.stringify(d, null, 2)`), y `<a>` a `/r/<job>/debug.log` cuando la
  respuesta trae `job`.
- Reemplazar `fetch('/cortar', {method:'POST', body:fd})` por un helper
  `subir(url, fd, onprog)` basado en `XMLHttpRequest`:
  - `xhr.upload.onprogress = e => onprog(e.loaded, e.total)` → actualiza barra y
    texto "subiendo X / Y MB".
  - resuelve `{status, text}`; el llamador hace `JSON.parse`.
  - se usa tanto en `$('go').onclick` como en el flujo de ejemplos.
- En modo no-debug, el helper sigue funcionando igual (la barra de progreso vive
  dentro del `<details>` oculto; no molesta).

## Flujo de datos

```
navegador (XHR, ?debug=1)
  → do_POST /cortar
    → dbg.set_request(true)
    → recibir  (log cabeceras, progreso)
    → parse_multipart → (campos, archivo, meta)  (log)
    → validar ext  (log)
    → makedirs + dbg.set_job(job)
    → guardar a disco  (etapa + verif)
    → procesar(...)
        → cargar_modelo (etapa) → log modelo.cargado
        → rebanar / armar / partir / acomodar (etapas + logs)
        → export hoja/pdf/dwg (etapas)
        → zip (log)  ← incluye debug.log
        → log resumen
    → respuesta JSON { ..., job }
    → dbg.clear()  (finally)

JOBS/<job>/debug.log  ← una línea JSON por evento
  servido por  GET /r/<job>/debug.log  (text/plain)
```

## Manejo de errores

- `dbg.log`/`dbg.etapa` nunca propagan fallos de I/O propios (escritura a
  `debug.log` en `try/except: pass`).
- Errores de negocio (`return {'error': ...}`) se loguean con `nivel='error'`
  antes del return, así quedan en el log aunque debug esté apagado.
- Excepciones no controladas: el `except` del handler las loguea con traceback y
  responde 500 + `job` si lo hay.
- `activo()` falso + `nivel != 'error'` = no-op total (cero overhead de I/O; sí
  se construye el dict, costo despreciable).

## Pruebas

### `test_dbg.py`

- `activo()` respeta `DESPIECE_DEBUG` (varias formas truthy/falsy) vía
  `monkeypatch.setenv`.
- `activo()` respeta `set_request(True)` sin env.
- `log()` no escribe archivo cuando `activo()` es falso y `nivel='info'`.
- `log(nivel='error')` escribe aunque `activo()` sea falso.
- `etapa()` escribe `.inicio` y `.fin`, y el `ms` del `.fin` es coherente con un
  `time.sleep` corto.
- `etapa()` ante excepción: escribe `.error` con traceback y re-lanza la misma
  excepción.
- Aislamiento por hilo: dos hilos con `set_job` distinto escriben a archivos
  distintos (arrancar 2 `threading.Thread`, comprobar los dos `debug.log`).

### `test_subida.py`

- `parse_multipart` con un cuerpo multipart armado a mano (2 campos + 1 archivo,
  1 campo vacío): devuelve `campos`, `archivo` correctos y
  `meta == {'n_partes': 3, 'vacias': ['<nombre>']}`.
- POST real: levantar `H` en `ThreadingHTTPServer` en un puerto libre en un hilo,
  `DESPIECE_DEBUG=1`, mandar un STL sintético chico (el generador
  `gen_casa`/`out/casa_prueba.stl` si existe, o un cubo STL ASCII mínimo) a
  `/cortar` con `urllib`/`http.client` y `multipart` a mano → status 200,
  respuesta trae `job`, existe `JOBS/<job>/debug.log`, y el log contiene las
  líneas `recibir.cabeceras`, `parse.fin`, `guardar.fin`, `modelo.cargado`,
  `resumen`.
- POST con extensión no soportada (`.txt`) → status 400 y el log (si se creó
  carpeta) o stderr contiene `validar` con `ok=false`.

### Manual

- `DESPIECE_DEBUG=1 python3 app.py 3561`, correr el ejemplo `casa` desde la UI en
  `?debug=1`, verificar el panel (progreso, tiempos, respuesta cruda) y abrir el
  `debug.log` enlazado.

## Archivos

| Archivo | Cambio |
|---|---|
| `dbg.py` | nuevo |
| `test_dbg.py` | nuevo |
| `test_subida.py` | nuevo |
| `app.py` | handler (set_request/clear, tipo `.log`), `parse_multipart` firma `(campos, archivo, meta)`, `do_POST`/`_ejemplo` correa de subida, `procesar`/`_estructural` correa de pipeline, `except` con `dbg.log`, `job` en la respuesta 500, `debug.log` en el zip, `PAGINA` (helper XHR, `<details>` debug, toggle) |
| `README.md` | sección "Debug" (env var, `?debug=1`, dónde queda el log) |

`.gitignore` sin cambios: `debug.log` vive en `JOBS/`, ya ignorado.
