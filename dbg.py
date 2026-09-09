# -*- coding: utf-8 -*-
"""Correa de debug: timing, tamanos, conteos y errores por etapa.

Apagado por default. Se prende con la variable de entorno DESPIECE_DEBUG o con
?debug=1 en la peticion (el handler llama set_request). Cada evento sale como una
linea JSON a stderr con prefijo [dbg] y, si hay un trabajo activo, tambien a
JOBS/<job>/debug.log. Los errores se registran siempre, este prendido o no.

Nada de lo que hay aqui debe tumbar una peticion: la escritura a disco va en
try/except silencioso.
"""
import contextlib
import datetime
import json
import os
import sys
import threading
import time
import traceback

_estado = threading.local()
_VERDAD = {'1', 'true', 'yes', 'on', 'si', 'sí'}

# Lo pone el handler en cuanto sabe la carpeta del trabajo. app.py lo importa.
JOBS_DIR = os.path.join(__import__('tempfile').gettempdir(), 'despiece3d_jobs')


def set_request(flag):
    """El handler lo llama al entrar, con el resultado de buscar debug=1."""
    _estado.req_debug = bool(flag)


def set_job(job):
    """Se llama en cuanto existe la carpeta JOBS/<job>."""
    _estado.job = job


def clear():
    """El handler lo llama en finally: deja el hilo limpio para el siguiente."""
    _estado.req_debug = False
    _estado.job = None


def _job():
    return getattr(_estado, 'job', None)


# ------------------------------------------------------------------ progreso
# En que etapa va cada trabajo, para que la pantalla pueda enseñar una barra de
# verdad y no una animacion inventada. Vive aparte del log porque el log se
# apaga y esto no: el diccionario lo lee OTRO hilo (el que atiende /progreso),
# de ahi el candado.
_PROGRESO = {}
_PLOCK = threading.Lock()


def marcar(etapa_nombre, job=None):
    job = job or _job()
    if not job:
        return
    with _PLOCK:
        _PROGRESO[job] = (etapa_nombre, time.time())


def progreso(job):
    """(etapa, ts) del trabajo, o None si no ha empezado o ya se olvido."""
    with _PLOCK:
        return _PROGRESO.get(job)


def olvidar(job):
    with _PLOCK:
        _PROGRESO.pop(job, None)
        # un servidor de escritorio no necesita mas historia que la del rato
        if len(_PROGRESO) > 64:
            for k in sorted(_PROGRESO, key=lambda k: _PROGRESO[k][1])[:32]:
                _PROGRESO.pop(k, None)


def activo():
    if os.environ.get('DESPIECE_DEBUG', '').strip().lower() in _VERDAD:
        return True
    return bool(getattr(_estado, 'req_debug', False))


def _ruta_log():
    job = _job()
    if not job:
        return None
    return os.path.join(JOBS_DIR, job, 'debug.log')


def log(evento, nivel='info', **campos):
    """Registra un evento. No-op si esta apagado, salvo nivel='error'."""
    if nivel != 'error' and not activo():
        return
    reg = {'ts': datetime.datetime.now().isoformat(timespec='milliseconds'),
           'job': _job(), 'nivel': nivel, 'evento': evento}
    reg.update(campos)
    linea = json.dumps(reg, ensure_ascii=False, default=str)
    try:
        sys.stderr.write('[dbg] ' + linea + '\n')
    except Exception:
        pass
    ruta = _ruta_log()
    if ruta:
        try:
            with open(ruta, 'a', encoding='utf-8') as f:
                f.write(linea + '\n')
        except Exception:
            pass


@contextlib.contextmanager
def etapa(nombre, **campos):
    """Envuelve un bloque: registra .inicio, .fin con ms, o .error con traceback."""
    marcar(nombre)
    log(nombre + '.inicio', **campos)
    t0 = time.perf_counter()
    try:
        yield
    except Exception as e:
        log(nombre + '.error', nivel='error',
            ms=round((time.perf_counter() - t0) * 1000, 1),
            excepcion=repr(e), traceback=traceback.format_exc())
        raise
    else:
        log(nombre + '.fin', ms=round((time.perf_counter() - t0) * 1000, 1))
