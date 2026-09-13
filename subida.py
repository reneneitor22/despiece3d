# -*- coding: utf-8 -*-
"""Separar la subida (multipart/form-data) MIENTRAS llega, con el archivo a disco.

Cambio local (12 sep 2026), no viene en el zip del hermano.

Da lo mismo que app.parse_multipart (campos, archivo, meta), pero el archivo no
pasa por la RAM: se escribe en `destino(nombre)` conforme llega y en memoria
solo se guarda una cola del largo del separador. Medido: 150 MB costaban 750 MB
de pico con parse_multipart y aqui 3 MB. Comparado contra parse_multipart con
400 cuerpos al azar leidos en trozos de 1 byte a 1 MB: da lo mismo salvo cuando
el separador entero aparece dentro del archivo sin su salto de linea (cosa que
un navegador no hace); ahi parse_multipart cortaba el archivo y esto no.
"""
import re

CAB_MAX = 16 << 10        # cabecera de una parte
CAMPO_MAX = 1 << 20       # un campo de texto del formulario


class SubidaMala(Exception):
    """La subida se corto o llego rota."""


def leer_multipart(leer, n, boundary, destino, trozo=1 << 20, al_avance=None):
    """`leer(k)` da hasta k bytes del cuerpo (self.rfile.read); `n` es el largo.

    `destino(nombre_original)` regresa la ruta donde escribir el archivo
    (os.devnull para leerlo y tirarlo). Regresa (campos, archivo, meta, leido)
    con archivo = (nombre, ruta, bytes). Si el cuerpo no llega completo lanza
    SubidaMala: una subida cortada ya no se procesa como si fuera buena.
    """
    sep = b'\r\n--' + boundary
    buf = b'\r\n'                 # asi el primer separador tambien va tras un \r\n
    leido = 0
    campos, archivo = {}, None
    meta = {'n_partes': 0, 'vacias': []}

    def mas():
        nonlocal buf, leido
        if leido >= n:
            return False
        c = leer(min(trozo, n - leido))
        if not c:
            return False
        leido += len(c)
        buf += c
        if al_avance:
            al_avance(leido)
        return True

    # hasta el primer separador (el preambulo se tira)
    while True:
        i = buf.find(sep)
        if i >= 0:
            buf = buf[i + len(sep):]
            break
        buf = buf[-(len(sep) - 1):]
        if not mas():
            raise SubidaMala('no llego el separador del formulario')

    while True:
        while len(buf) < 2:
            if not mas():
                raise SubidaMala('la subida se corto')
        if buf[:2] == b'--':
            break                                       # fin del formulario
        while b'\r\n\r\n' not in buf:
            if len(buf) > CAB_MAX or not mas():
                raise SubidaMala('cabecera de la subida rota o cortada')
        cab, buf = buf.split(b'\r\n\r\n', 1)
        cab_s = cab.decode('utf-8', 'replace')
        mname = re.search(r'name="([^"]*)"', cab_s)
        mfile = re.search(r'filename="([^"]*)"', cab_s)
        es_archivo = bool(mfile and mfile.group(1))
        salida, ruta, total, texto = None, None, 0, []
        if es_archivo and archivo is None:
            ruta = destino(mfile.group(1))
            salida = open(ruta, 'wb')
        try:
            while True:
                i = buf.find(sep)
                if i >= 0:
                    dato, buf = buf[:i], buf[i + len(sep):]
                else:
                    corte = max(0, len(buf) - (len(sep) - 1))
                    dato, buf = buf[:corte], buf[corte:]
                if dato:
                    total += len(dato)
                    if salida:
                        salida.write(dato)
                    elif not es_archivo:
                        if total > CAMPO_MAX:
                            raise SubidaMala('un campo del formulario es demasiado largo')
                        texto.append(dato)
                if i >= 0:
                    break
                if not mas():
                    raise SubidaMala('la subida se corto')
        finally:
            if salida:
                salida.close()
        if mname:
            meta['n_partes'] += 1
            if es_archivo:
                if archivo is None:
                    archivo = (mfile.group(1), ruta, total)
            else:
                v = b''.join(texto).decode('utf-8', 'replace').strip()
                campos[mname.group(1)] = v
                if not v:
                    meta['vacias'].append(mname.group(1))
        # tras el separador viene "\r\n" (otra parte) o "--" (fin)
        while len(buf) < 2:
            if not mas():
                raise SubidaMala('la subida se corto')
        if buf[:2] == b'\r\n':
            buf = buf[2:]
    # Lo que venga despues del cierre ("\r\n" y epilogo) se lee y se tira: la
    # conexion es HTTP/1.1 y lo que se quede en el socket se volveria la
    # siguiente peticion.
    while leido < n:
        buf = b''
        if not mas():
            break
    return campos, archivo, meta, leido
