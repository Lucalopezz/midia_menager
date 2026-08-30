#!/usr/bin/env python3
"""Baixa vídeos ou playlists sequencialmente para a biblioteca local."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
YOUTUBE_LIBRARY = PROJECT_ROOT / "library" / "youtube"
LOG_FILE = SCRIPT_DIR / "logs" / "download.log"
DOWNLOAD_ARCHIVE = SCRIPT_DIR / "downloaded.txt"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Baixa URLs de vídeos ou playlists sequencialmente para "
            "library/youtube e continua a fila quando uma URL falha."
        )
    )
    parser.add_argument(
        "--category",
        metavar="NOME",
        help=(
            "subpasta dentro de library/youtube (ex.: akita ou cursos/python)"
        ),
    )
    parser.add_argument(
        "urls",
        metavar="URL",
        nargs="+",
        help="uma ou mais URLs de vídeos ou playlists",
    )
    return parser.parse_args(argv)


def resolve_destination(category: str | None) -> Path:
    """Retorna um destino seguro, sempre dentro de library/youtube."""
    library_root = YOUTUBE_LIBRARY.resolve()

    if category is None:
        return library_root

    category_path = Path(category)
    if category_path.is_absolute() or any(
        part in {"", ".", ".."} for part in category_path.parts
    ):
        raise ValueError("a categoria deve ser um caminho relativo sem '.' ou '..'")

    destination = (library_root / category_path).resolve()
    try:
        destination.relative_to(library_root)
    except ValueError as error:
        raise ValueError("a categoria deve permanecer dentro de library/youtube") from error

    return destination


def setup_logging() -> logging.Logger:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("media_downloader")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(status)-7s | %(url)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


def build_download_options(destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)

    return {
        # Em playlists, prefixa o título com a posição (ex.: "1. Título").
        # O trecho após "|" mantém o prefixo vazio em downloads avulsos.
        "outtmpl": str(
            destination
            / "%(playlist_index&{}. |)s%(title).180B [%(id)s].%(ext)s"
        ),
        "download_archive": str(DOWNLOAD_ARCHIVE),
        "format": (
            "bestvideo[height<=1080][vcodec^=avc1]+"
            "bestaudio[acodec^=mp4a]/"
            "best[height<=1080][ext=mp4]/best[height<=1080]"
        ),
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        "noplaylist": False,
        # Continua os demais itens quando um vídeo da playlist está indisponível.
        "ignoreerrors": "only_download",
        "continuedl": True,
        "overwrites": False,
        "retries": 3,
        "fragment_retries": 3,
    }


def download_url(url: str, options: dict[str, object]) -> None:
    with YoutubeDL(options) as downloader:
        result = downloader.download([url])

    if result != 0:
        raise RuntimeError(f"yt-dlp terminou com código {result}")


def compact_error_message(error: Exception) -> str:
    return " ".join(str(error).splitlines()).strip() or error.__class__.__name__


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        destination = resolve_destination(args.category)
    except ValueError as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2

    logger = setup_logging()
    options = build_download_options(destination)
    failed_urls: list[str] = []
    success_count = 0
    total = len(args.urls)

    print(f"Destino: {destination}")

    for index, url in enumerate(args.urls, start=1):
        print(f"\n[{index}/{total}] Processando: {url}")

        try:
            download_url(url, options)
        except DownloadError as error:
            message = compact_error_message(error)
            failed_urls.append(url)
            logger.error(message, extra={"url": url, "status": "ERROR"})
            print(f"Falha: {message}", file=sys.stderr)
        except Exception as error:
            message = compact_error_message(error)
            failed_urls.append(url)
            logger.error(message, extra={"url": url, "status": "ERROR"})
            print(f"Falha inesperada: {message}", file=sys.stderr)
        else:
            success_count += 1
            logger.info(
                "Processamento concluído (download novo ou já presente no archive)",
                extra={"url": url, "status": "SUCCESS"},
            )
            print("Sucesso.")

    print("\nDownloads finalizados.\n")
    print(f"Sucessos: {success_count}")
    print(f"Erros: {len(failed_urls)}")

    if failed_urls:
        print("\nURLs com erro:")
        for url in failed_urls:
            print(f"- {url}")

    return 1 if failed_urls else 0


if __name__ == "__main__":
    raise SystemExit(main())
