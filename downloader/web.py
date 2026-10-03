"""Interface local e fila persistente para o downloader existente."""

from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .download import (
    SCRIPT_DIR,
    YOUTUBE_LIBRARY,
    build_download_options,
    compact_error_message,
    download_url,
    resolve_destination,
    setup_logging,
)

ACTIVE = {"preparing", "downloading", "processing"}
TERMINAL = {"completed", "failed"}
STATIC_DIR = SCRIPT_DIR / "static"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def number(value: object) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return max(0, value)
    return None


class QueueRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=120)


class DownloadQueue:
    def __init__(self, state_dir: Path, download: Callable = download_url):
        state_dir.mkdir(parents=True, exist_ok=True)
        self.database = state_dir / "queue.sqlite3"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.download = download
        self.logger = setup_logging()
        self.jobs: dict[str, dict] = {}
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            for job_id, data in connection.execute("SELECT id, data FROM jobs ORDER BY rowid"):
                self.jobs[job_id] = json.loads(data)
        for job in self.jobs.values():
            if job["status"] in ACTIVE:
                job.update(status="queued", percent=None, speed=None, eta=None,
                           detail="Retomando após reiniciar o serviço.")
                self.save(job)
        self.worker = threading.Thread(target=self.run, name="downloads", daemon=True)

    def save(self, job: dict) -> None:
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO jobs (id, data) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (job["id"], json.dumps(job, ensure_ascii=False, allow_nan=False)),
            )

    def update(self, job_id: str, *, persist: bool = True, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(fields, updated_at=now())
            if persist:
                self.save(job)

    def add(self, urls: list[str], category: str | None) -> list[dict]:
        destination = resolve_destination(category)
        relative = str(destination.relative_to(YOUTUBE_LIBRARY.parent.parent))
        added = []
        with self.lock:
            pending = sum(job["status"] not in TERMINAL for job in self.jobs.values())
            if pending + len(urls) > 500:
                raise ValueError("A fila comporta até 500 URLs pendentes.")
            # Uma URL já em andamento não precisa de outra entrada na mesma pasta.
            existing = {(j["url"], j["destination"]) for j in self.jobs.values()
                        if j["status"] not in TERMINAL}
            for url in dict.fromkeys(urls):
                if (url, relative) in existing:
                    continue
                job = {
                    "id": uuid.uuid4().hex, "url": url, "category": category,
                    "destination": relative, "title": None, "status": "queued",
                    "percent": None, "downloaded_bytes": 0, "total_bytes": None,
                    "speed": None, "eta": None, "error": None,
                    "detail": "Aguardando sua vez na fila.", "playlist_index": None,
                    "playlist_count": None, "created_at": now(), "updated_at": now(),
                }
                self.save(job)
                self.jobs[job["id"]] = job
                added.append(dict(job))
        self.wake.set()
        return added

    def retry(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job["status"] != "failed":
                raise ValueError("Somente downloads com falha podem ser tentados novamente.")
            if any(j["url"] == job["url"] and j["destination"] == job["destination"]
                   and j["status"] not in TERMINAL for j in self.jobs.values()):
                raise ValueError("Esta URL já está na fila.")
            self.jobs.pop(job_id)
            self.jobs[job_id] = job
            self.update(job_id, status="queued", percent=None, downloaded_bytes=0,
                        total_bytes=None, speed=None, eta=None, error=None,
                        detail="Aguardando uma nova tentativa.", created_at=now())
        self.wake.set()

    def snapshot(self) -> dict:
        with self.lock:
            jobs = [dict(job) for job in self.jobs.values()]
        pending = [job for job in jobs if job["status"] not in TERMINAL]
        history = [job for job in reversed(jobs) if job["status"] in TERMINAL][:100]
        usage = shutil.disk_usage(YOUTUBE_LIBRARY)
        return {
            "jobs": pending + history,
            "counts": {
                "queued": sum(j["status"] == "queued" for j in jobs),
                "active": sum(j["status"] in ACTIVE for j in jobs),
                "completed": sum(j["status"] == "completed" for j in jobs),
                "failed": sum(j["status"] == "failed" for j in jobs),
            },
            "storage": {"free": usage.free, "total": usage.total},
            "cookies_available": Path(os.environ.get("YTDLP_COOKIES_FILE", "" )).is_file(),
        }

    def run(self) -> None:
        while not self.stop.is_set():
            self.wake.clear()
            with self.lock:
                job = next((dict(j) for j in self.jobs.values() if j["status"] == "queued"), None)
                if job:
                    self.update(job["id"], status="preparing", detail="Buscando informações do vídeo…")
            if job is None:
                self.wake.wait(timeout=1)
                continue
            tracker = ProgressTracker(self, job["id"])
            try:
                options = build_download_options(resolve_destination(job["category"]), browser_cookies=False)
                options.update(progress_hooks=[tracker.progress],
                               postprocessor_hooks=[tracker.postprocess],
                               logger=tracker, quiet=True, noprogress=True)
                self.download(job["url"], options)
                if self.stop.is_set():
                    self.update(job["id"], status="queued", detail="Download será retomado ao reiniciar.")
                    continue
                self.update(job["id"], status="completed", percent=100, speed=None,
                            eta=None, error=None, detail="Concluído. Arquivos disponíveis na biblioteca.")
                self.logger.info("Processamento concluído (download novo ou já presente no archive)",
                                 extra={"url": job["url"], "status": "SUCCESS"})
            except Exception as error:
                message = compact_error_message(error)
                if self.stop.is_set():
                    self.update(job["id"], status="queued", percent=None, speed=None, eta=None,
                                detail="Download será retomado ao reiniciar.")
                else:
                    self.update(job["id"], status="failed", speed=None, eta=None,
                                error=message, detail="Não foi possível concluir o download.")
                    self.logger.error(message, extra={"url": job["url"], "status": "ERROR"})

    def start(self) -> None:
        YOUTUBE_LIBRARY.mkdir(parents=True, exist_ok=True)
        self.worker.start()

    def close(self) -> None:
        self.stop.set()
        self.wake.set()
        self.worker.join(timeout=3)


class ProgressTracker:
    def __init__(self, queue: DownloadQueue, job_id: str):
        self.queue = queue
        self.job_id = job_id
        self.last_saved = 0.0

    def progress(self, data: dict) -> None:
        if self.queue.stop.is_set():
            raise RuntimeError("Serviço encerrando; download será retomado.")
        info = data.get("info_dict") or {}
        downloaded = number(data.get("downloaded_bytes")) or 0
        total = number(data.get("total_bytes")) or number(data.get("total_bytes_estimate"))
        percent = min(100, downloaded / total * 100) if total else None
        finished = data.get("status") == "finished"
        current_time = time.monotonic()
        persist = finished or current_time - self.last_saved >= 1
        if persist:
            self.last_saved = current_time
        audio = info.get("vcodec") == "none"
        self.queue.update(
            self.job_id, persist=persist,
            status="processing" if finished else "downloading",
            title=info.get("title"), percent=100 if finished else percent,
            downloaded_bytes=downloaded, total_bytes=total,
            speed=None if finished else number(data.get("speed")),
            eta=None if finished else number(data.get("eta")),
            playlist_index=info.get("playlist_index"),
            playlist_count=info.get("playlist_count") or info.get("n_entries"),
            detail="Preparando arquivo…" if finished else ("Baixando áudio" if audio else "Baixando vídeo"),
        )

    def postprocess(self, data: dict) -> None:
        if self.queue.stop.is_set():
            raise RuntimeError("Serviço encerrando; download será retomado.")
        self.queue.update(self.job_id, status="processing", speed=None, eta=None,
                          detail="Finalizando arquivo para a biblioteca…")

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.queue.logger.warning(message, extra={"url": self.queue.jobs[self.job_id]["url"], "status": "WARNING"})

    def error(self, message: str) -> None:
        self.queue.update(self.job_id, error=compact_error_message(RuntimeError(message)))


def validate_urls(urls: list[str]) -> list[str]:
    cleaned = []
    for raw in urls:
        url = raw.strip()
        try:
            parsed = urlsplit(url)
            valid = (len(url) <= 4096 and parsed.scheme in {"http", "https"}
                     and parsed.hostname and not parsed.username and not parsed.password
                     and not any(character.isspace() for character in url))
            parsed.port  # Também valida portas malformadas.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use URLs completas que comecem com http:// ou https://, uma por linha.")
        cleaned.append(url)
    return cleaned


def create_app(state_dir: Path | None = None, download: Callable = download_url) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        queue = DownloadQueue(state_dir or Path(os.environ.get("DOWNLOAD_STATE_DIR", SCRIPT_DIR / "state")), download)
        application.state.queue = queue
        queue.start()
        yield
        queue.close()

    application = FastAPI(title="Mídia • Downloads", lifespan=lifespan, docs_url=None, redoc_url=None)

    @application.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if (request.headers.get("sec-fetch-site") == "cross-site"
                    or (origin and urlsplit(origin).netloc != request.headers.get("host"))):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Envie o pedido pela interface local."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @application.get("/api/health")
    def health(request: Request):
        if not request.app.state.queue.worker.is_alive():
            raise HTTPException(503, "O worker de downloads está indisponível.")
        return {"status": "ok"}

    @application.get("/api/status")
    def status(request: Request):
        return request.app.state.queue.snapshot()

    @application.post("/api/jobs", status_code=201)
    def add_jobs(body: QueueRequest, request: Request):
        try:
            category = body.category.strip() if body.category else None
            if category and ("\\" in category or any(p in {"", ".", ".."} for p in category.split("/"))):
                raise ValueError("Use uma categoria como musica ou cursos/python, sem '.' ou '..'.")
            jobs = request.app.state.queue.add(validate_urls(body.urls), category or None)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"jobs": jobs, "added": len(jobs)}

    @application.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str, request: Request):
        try:
            request.app.state.queue.retry(job_id)
        except KeyError as error:
            raise HTTPException(404, "Download não encontrado.") from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"status": "queued"}

    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return application


app = create_app()
