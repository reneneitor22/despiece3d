# -*- coding: utf-8 -*-
"""Bateria contra modelos bajados de internet.

Los gen_*.py hacen modelos limpios y ninguno de internet lo es. Esto corre el
pipeline completo y su auditoria sobre modelos que no hicimos nosotros.

    python3 pruebas_reales.py           # todos
    python3 pruebas_reales.py engel     # uno
"""
import os
import subprocess
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
MODELOS = 'modelos_prueba'

CASOS = [
    # nombre, modo, ruta, escala, espesor, hoja, extra
    ('casa_prueba', 'casa', 'out/casa_prueba.stl', 100, 2, '600x900'),
    ('engel', 'casa', MODELOS + '/ladybug/obj/engel-house/AngelHouse_Bauhaus-in-Israel.obj',
     100, 2, '600x900'),
    ('engel_env', 'envolvente',
     MODELOS + '/ladybug/obj/engel-house/AngelHouse_Bauhaus-in-Israel.obj',
     100, 2, '600x900'),
    ('mainstreet', 'casa', MODELOS + '/ladybug/stl-samples/MainStreetPlace.stl',
     500, 2, '600x900'),
    ('urban', 'casa', MODELOS + '/ladybug/obj/urban_model_001/model.obj',
     500, 2, '600x900'),
    ('terreno_prueba', 'terreno', 'out/terreno_prueba.stl', 500, 3, '500x700'),
    ('valles', 'terreno', MODELOS + '/nasa/stl/mars_valles_mar.stl', 100, 3, '500x700'),
    ('gale', 'terreno', MODELOS + '/nasa/stl/gale_crater.STL', 200, 3, '500x700'),
]


def correr(cmd):
    p = subprocess.run([sys.executable] + cmd, cwd=RAIZ, capture_output=True, text=True)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def main(filtro=None):
    fallas = 0
    for nombre, modo, ruta, escala, espesor, hoja in CASOS:
        if filtro and filtro != nombre:
            continue
        if not os.path.exists(os.path.join(RAIZ, ruta)):
            print('%-16s SALTADO (falta %s)' % (nombre, ruta))
            continue

        t0 = time.time()
        salida = 'out_real/' + nombre
        if modo in ('casa', 'envolvente'):
            args = ['cortar_casa.py', ruta, '--escala', str(escala),
                    '--espesor', str(espesor), '--hoja', hoja, '--salida', salida]
            if modo == 'envolvente':
                args.append('--solo-envolvente')
            cod, txt = correr(args)
            resumen = next((l for l in txt.splitlines() if l.startswith('placas ')), txt.strip()[:120])
            if modo == 'envolvente':
                cod2, txt2, auditoria = 0, '', '(ensamble: se mide en el caso completo)'
            else:
                cod2, txt2 = correr(['verificar_casa.py', ruta, str(escala),
                                     '--espesor', str(espesor)])
                auditoria = next((l for l in txt2.splitlines()
                                  if l.startswith('PASA') or l.startswith('NO PASA')), '')
        else:
            cod, txt = correr(['cortar.py', ruta, '--escala', str(escala),
                               '--espesor', str(espesor), '--hoja', hoja,
                               '--salida', salida])
            resumen = next((l for l in txt.splitlines() if l.startswith('capas ')), txt.strip()[:120])
            cod2, txt2 = correr(['verificar.py', ruta, '--escala', str(escala),
                                 '--espesor', str(espesor), '--hoja', hoja])
            auditoria = next((l for l in txt2.splitlines() if l.startswith('piezas ')), '')

        mal = cod != 0 or cod2 != 0
        fallas += 1 if mal else 0
        print('%-16s %-4s %s  (%.0fs)' % (nombre, 'MAL' if mal else 'ok', resumen,
                                          time.time() - t0))
        if auditoria:
            print('%-16s      %s' % ('', auditoria))
        for linea in txt.splitlines():
            if 'no cabia' in linea or linea.strip().startswith('aviso: ') and (
                    'no se pueden cortar' in linea or 'quedan' in linea):
                print('%-16s      %s' % ('', linea.strip()))
        if mal:
            print((txt + txt2).strip()[-600:])

    print('---')
    print('fallas: %d' % fallas)
    return 1 if fallas else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
