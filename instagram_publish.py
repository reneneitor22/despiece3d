#!/usr/bin/env python3
"""Publica una imagen o video en @despiece.studio via Instagram Graph API.

Uso:
    python3 instagram_publish.py --image-url URL --caption "texto"
    python3 instagram_publish.py --video-url URL --caption "texto" --reel
    python3 instagram_publish.py --image-url URL --dry-run   # solo crea el contenedor, no publica
    python3 instagram_publish.py --carousel URL1 URL2 ... --caption "texto"   # 2 a 10 imagenes JPEG

Credenciales en .env junto a este archivo: IG_USER_ID, PAGE_ACCESS_TOKEN.
"""
import argparse
import os
import sys
import time

import requests

GRAPH = "https://graph.facebook.com/v21.0"


def cargar_env():
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    valores = {}
    with open(ruta) as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            valores[k] = v
    return valores["IG_USER_ID"], valores["PAGE_ACCESS_TOKEN"]


def crear_contenedor(ig_user_id, token, *, image_url=None, video_url=None, caption, es_reel, cover_url=None):
    params = {"access_token": token, "caption": caption}
    if video_url:
        params["media_type"] = "REELS" if es_reel else "VIDEO"
        params["video_url"] = video_url
        if cover_url:
            params["cover_url"] = cover_url
    else:
        params["image_url"] = image_url
    r = requests.post(f"{GRAPH}/{ig_user_id}/media", data=params, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def crear_carrusel(ig_user_id, token, urls, caption):
    hijos = []
    for url in urls:
        r = requests.post(f"{GRAPH}/{ig_user_id}/media", data={"access_token": token, "image_url": url, "is_carousel_item": "true"}, timeout=60)
        r.raise_for_status()
        hijos.append(r.json()["id"])
    r = requests.post(f"{GRAPH}/{ig_user_id}/media", data={"access_token": token, "media_type": "CAROUSEL", "children": ",".join(hijos), "caption": caption}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def esperar_listo(contenedor_id, token, *, intentos=60, espera=5):   # un reel tarda mas de 1 min
    for _ in range(intentos):
        r = requests.get(f"{GRAPH}/{contenedor_id}", params={"fields": "status_code", "access_token": token}, timeout=30)
        r.raise_for_status()
        estado = r.json()["status_code"]
        if estado == "FINISHED":
            return
        if estado == "ERROR":
            raise RuntimeError(f"Meta rechazo el contenedor {contenedor_id}")
        time.sleep(espera)
    raise TimeoutError(f"Contenedor {contenedor_id} no termino de procesar a tiempo")


def publicar(ig_user_id, token, contenedor_id):
    r = requests.post(f"{GRAPH}/{ig_user_id}/media_publish", data={"creation_id": contenedor_id, "access_token": token}, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-url")
    ap.add_argument("--video-url")
    ap.add_argument("--carousel", nargs="+", metavar="URL", help="2 a 10 URLs de imagen JPEG, en orden")
    ap.add_argument("--caption", default="")
    ap.add_argument("--cover-url", help="portada del video/reel (JPEG publico)")
    ap.add_argument("--reel", action="store_true", help="marca el video como Reel (si no, sube como video normal)")
    ap.add_argument("--dry-run", action="store_true", help="crea el contenedor pero no publica")
    args = ap.parse_args()

    if not (args.image_url or args.video_url or args.carousel):
        sys.exit("da --image-url, --video-url o --carousel")
    if args.carousel and not 2 <= len(args.carousel) <= 10:
        sys.exit("un carrusel lleva de 2 a 10 imagenes")

    ig_user_id, token = cargar_env()

    print("creando contenedor...")
    if args.carousel:
        contenedor_id = crear_carrusel(ig_user_id, token, args.carousel, args.caption)
    else:
        contenedor_id = crear_contenedor(
            ig_user_id, token,
            image_url=args.image_url, video_url=args.video_url,
            caption=args.caption, es_reel=args.reel, cover_url=args.cover_url,
        )
    print(f"contenedor: {contenedor_id}")

    if args.video_url or args.carousel:
        print("esperando que Meta procese el contenedor...")
        esperar_listo(contenedor_id, token)

    if args.dry_run:
        print("dry-run: contenedor listo, NO se publico")
        return

    post_id = publicar(ig_user_id, token, contenedor_id)
    r = requests.get(f"{GRAPH}/{post_id}", params={"fields": "permalink", "access_token": token}, timeout=30)
    print(f"publicado: {r.json().get('permalink', '(sin link)')} (id {post_id})")


if __name__ == "__main__":
    main()
