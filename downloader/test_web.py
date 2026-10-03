"""Testes da fila e API sem acessar serviços externos ou a biblioteca real."""

import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from downloader import download, web


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "library" / "youtube"
        self.library.mkdir(parents=True)
        self.patches = [
            patch.object(web, "YOUTUBE_LIBRARY", self.library),
            patch.object(download, "YOUTUBE_LIBRARY", self.library),
            patch.object(download, "DOWNLOAD_ARCHIVE", self.root / "downloaded.txt"),
            patch.object(web, "setup_logging", lambda: logging.getLogger("test-downloads")),
        ]
        for item in self.patches:
            item.start()
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def wait_for(self, client, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            data = client.get("/api/status").json()
            if predicate(data):
                return data
            time.sleep(0.02)
        self.fail(f"Estado esperado não encontrado: {data}")

    def test_live_progress_deduplication_and_completion(self):
        def fake_download(url, options):
            options["progress_hooks"][0]({
                "status": "downloading", "downloaded_bytes": 75, "total_bytes": 100,
                "speed": 25, "eta": 1, "info_dict": {"title": "Vídeo de teste", "playlist_index": 2, "playlist_count": 4},
            })
            self.release.wait(timeout=4)
            options["postprocessor_hooks"][0]({"status": "started"})

        app = web.create_app(self.root / "state", fake_download)
        with TestClient(app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/static/app.js").status_code, 200)
            self.assertEqual(client.get("/api/health").status_code, 200)
            response = client.post("/api/jobs", json={"urls": ["https://example.com/video"], "category": "cursos/python"})
            self.assertEqual(response.status_code, 201)
            data = self.wait_for(client, lambda d: d["jobs"][0]["status"] == "downloading")
            job = data["jobs"][0]
            self.assertEqual(job["percent"], 75)
            self.assertEqual(job["speed"], 25)
            self.assertEqual(job["playlist_count"], 4)
            self.assertEqual(job["destination"], "library/youtube/cursos/python")
            duplicate = client.post("/api/jobs", json={"urls": [job["url"], job["url"]], "category": "cursos/python"})
            self.assertEqual(duplicate.json()["added"], 0)
            self.release.set()
            data = self.wait_for(client, lambda d: d["counts"]["completed"] == 1)
            self.assertEqual(data["jobs"][0]["percent"], 100)
        with TestClient(web.create_app(self.root / "state", fake_download)) as client:
            self.assertEqual(client.get("/api/status").json()["counts"]["completed"], 1)

    def test_failure_does_not_stop_queue_and_retry_works(self):
        failures = {"https://example.com/fail"}
        def fake_download(url, options):
            if url in failures:
                raise RuntimeError("Falha de teste")

        with TestClient(web.create_app(self.root / "state", fake_download)) as client:
            response = client.post("/api/jobs", json={"urls": ["https://example.com/fail", "https://example.com/ok"]})
            failed_id = response.json()["jobs"][0]["id"]
            data = self.wait_for(client, lambda d: d["counts"]["failed"] == 1 and d["counts"]["completed"] == 1)
            failed = next(j for j in data["jobs"] if j["id"] == failed_id)
            self.assertEqual(failed["error"], "Falha de teste")
            failures.clear()
            self.assertEqual(client.post(f"/api/jobs/{failed_id}/retry").status_code, 200)
            self.wait_for(client, lambda d: d["counts"]["completed"] == 2)
            self.assertEqual(client.post(f"/api/jobs/{failed_id}/retry").status_code, 409)
            self.assertEqual(client.post("/api/jobs/missing/retry").status_code, 404)

    def test_active_download_is_restored_after_restart(self):
        queue = web.DownloadQueue(self.root / "state")
        job = queue.add(["https://example.com/video"], None)[0]
        queue.update(job["id"], status="downloading", percent=42)
        queue = web.DownloadQueue(self.root / "state")
        restored = queue.snapshot()["jobs"][0]
        self.assertEqual(restored["status"], "queued")
        self.assertIsNone(restored["percent"])

    def test_validation_is_atomic_and_blocks_escaping_destination(self):
        with TestClient(web.create_app(self.root / "state", lambda *_: None)) as client:
            for urls in [["file:///etc/passwd"], ["https://example.com/ok", "invalid"], ["https://user:pass@example.com/video"], ["https://example.com:bad/video"]]:
                self.assertEqual(client.post("/api/jobs", json={"urls": urls}).status_code, 400)
            for category in ["../fora", "/tmp", "cursos/../fora", "cursos//python", ".", "cursos\\python"]:
                self.assertEqual(client.post("/api/jobs", json={"urls": ["https://example.com/ok"], "category": category}).status_code, 400)
            self.assertEqual(client.post("/api/jobs", json={"urls": []}).status_code, 422)
            self.assertEqual(client.post("/api/jobs", json={"urls": ["https://example.com/ok"]}, headers={"Origin": "https://another-site.test"}).status_code, 403)
            self.assertEqual(client.get("/api/status").json()["jobs"], [])

    def test_unknown_size_and_nonfinite_numbers_are_safe(self):
        queue = web.DownloadQueue(self.root / "state")
        job = queue.add(["https://example.com/video"], None)[0]
        tracker = web.ProgressTracker(queue, job["id"])
        tracker.progress({"status": "downloading", "downloaded_bytes": 100,
                          "speed": float("nan"), "eta": float("inf")})
        current = queue.snapshot()["jobs"][0]
        self.assertIsNone(current["percent"])
        self.assertIsNone(current["speed"])
        self.assertIsNone(current["eta"])


if __name__ == "__main__":
    unittest.main()
