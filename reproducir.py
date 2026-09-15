# -*- coding: utf-8 -*-
"""Repite un corte que fallo en la pantalla: mismo archivo, mismas opciones.

La app guarda cada falla en casos/<folio> (app._guardar_caso) y le enseña el
folio al alumno. Con ese folio:

    .venv/bin/python reproducir.py                      # lista los casos guardados
    .venv/bin/python reproducir.py <folio>              # lo repite con el log prendido
    .venv/bin/python reproducir.py <folio> --ensamble   # si ya sale, prueba que arme

Sale con 0 solo si el corte sale (y, con --ensamble, si ademas arma).
"""
import glob
import json
import os
import shutil
import sys
import uuid

import app
import dbg


def reproducir(folio):
    """(caso, resultado, avisos, carpeta, ruta_modelo). Si el motor truena, la
    excepcion sube tal cual: su traceback es justo lo que se busca."""
    carpeta_caso = os.path.join(app.CASOS, folio)
    with open(os.path.join(carpeta_caso, 'caso.json'), encoding='utf-8') as f:
        caso = json.load(f)
    modelos = glob.glob(os.path.join(carpeta_caso, 'entrada', '*'))
    if not modelos:
        raise SystemExit('el caso %s no trae el modelo (fallo antes de guardarlo)' % folio)
    job = uuid.uuid4().hex[:12]
    carpeta = os.path.join(app.JOBS, job)
    shutil.copytree(os.path.join(carpeta_caso, 'entrada'), os.path.join(carpeta, 'entrada'))
    ruta = os.path.join(carpeta, 'entrada', os.path.basename(modelos[0]))
    dbg.set_job(job)
    avisos = []
    try:
        r = app._procesar(ruta, caso['campos'], carpeta, job, caso['nombre'], avisos)
    except SystemExit as e:
        r = {'error': str(e)}
    return caso, r, avisos, carpeta, ruta


def _listar():
    rutas = sorted(glob.glob(os.path.join(app.CASOS, '*', 'caso.json')), key=os.path.getmtime)
    if not rutas:
        print('no hay casos en %s' % app.CASOS)
    for ruta in rutas:
        with open(ruta, encoding='utf-8') as f:
            c = json.load(f)
        motivo = c['error'] or 'TRONO: ' + ((c['traceback'] or '').strip().splitlines() or ['?'])[-1]
        print('%s  %s  %-28s  %s' % (c['folio'], c['fecha'], c['nombre'][:28], motivo[:90]))


if __name__ == '__main__':
    os.environ['DESPIECE_DEBUG'] = '1'      # cada etapa con su tiempo, a la consola
    folios = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not folios:
        _listar()
        sys.exit(0)
    caso, r, avisos, carpeta, ruta = reproducir(folios[0])
    print('\narchivo: %s' % os.path.basename(ruta))
    print('opciones del alumno: %s' % json.dumps(caso['campos'], ensure_ascii=False))
    print('en la pantalla le salio: %s' % (caso['error'] or caso['traceback']))
    for a in avisos:
        print('   aviso: %s' % a)
    if r.get('error'):
        print('\nSIGUE FALLANDO: %s' % r['error'])
        sys.exit(1)
    print('\nAHORA SALE: %s -> %s' % (json.dumps(r.get('stats'), ensure_ascii=False), carpeta))
    if '--ensamble' in sys.argv and r.get('modo') == 'estructura':
        import verificar_casa
        c = caso['campos']
        piso = str(c.get('piso') or '')
        v = verificar_casa.probar(ruta, escala=r['escala'], carton_mm=r['espesor'],
                                  unidades=r['unidades'],
                                  solo_envolvente=c.get('envolvente') in ('1', 'true', 'on'),
                                  piso=int(piso) if piso.isdigit() and int(piso) > 0 else None)
        sys.exit(0 if v['ok'] else 1)
