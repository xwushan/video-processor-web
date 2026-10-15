import asyncio
import base64
import json
import os
import sqlite3
import ssl
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import httpx
from test_job_regressions import TemporaryAppTestCase, FORM
from web_app import main


class ReleaseRegressionTest(TemporaryAppTestCase):
    def test_pause_during_process_creation_stops_ffmpeg_before_waiting_for_output(self):
        job_id = self.create_direct(["clip.mp4"])
        file_id = main.get_job_files(job_id)[0]["id"]
        event = main.get_cancel_event(job_id)

        class SilentProcess:
            @property
            def stderr(self):
                raise AssertionError("Paused FFmpeg must be stopped before waiting for output")
            def poll(self):
                return 0

        proc = SilentProcess()
        def start(*args, **kwargs):
            event.set()
            return proc
        with patch.object(main.subprocess, "Popen", side_effect=start), \
             patch.object(main, "terminate_process") as terminate:
            self.assertEqual(main.run_ffmpeg_command("ffmpeg -version", job_id, file_id, 1), "paused")
        terminate.assert_called_once_with(proc)
        self.assertNotIn(job_id, main.active_processes)
        with patch.object(main.subprocess, "Popen") as start:
            self.assertEqual(main.run_ffmpeg_command("ffmpeg -version", job_id, file_id, 1), "paused")
        start.assert_not_called()

    def test_upload_publishes_the_entire_batch_only_after_all_files_are_valid(self):
        observed = []
        def probe(_path):
            observed.append(main.current_job()["job"])
            return {"resolution": "320x240", "duration_sec": 1}
        with patch.object(main, "probe_video", side_effect=probe):
            job_id = self.create_direct(["one.mp4", "two.mp4"])
        self.assertEqual(observed, [None, None])
        self.assertEqual(main.get_job(job_id)["total_count"], 2)
        self.assertEqual(len(main.get_job_files(job_id)), 2)

    def test_batch_database_failure_rolls_back_both_job_and_files(self):
        row = ("same-id", "job", "clip.mp4", "input.mp4", "output.mp4", "320x240", 1, 1)
        with self.assertRaises(sqlite3.IntegrityError):
            main.persist_job("job", {}, main.UPLOAD_DIR, main.OUTPUT_DIR, [row, row])
        self.assertIsNone(main.get_job("job"))
        self.assertEqual(main.get_job_files("job"), [])

    def test_resumable_batch_probes_each_file_once_and_restores_sources_on_database_failure(self):
        session = main.RESUMABLE_UPLOAD_DIR / ("a" * 32)
        source = session / "files" / "clip.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        with patch.object(main, "persist_job", side_effect=sqlite3.OperationalError("test disk failure")), \
             patch.object(main, "probe_video", return_value={"resolution": "320x240", "duration_sec": 1}) as probe:
            with self.assertRaises(sqlite3.OperationalError):
                main.create_job_from_resumable_files(session, {"files": [{"path": "clip.mp4", "size": 6}]}, {"format_type": "h264"})
        probe.assert_called_once()
        self.assertEqual(source.read_bytes(), b"source")
        self.assertIsNone(main.current_job()["job"])

    def assert_health_during_probe(self, resumable):
        started, release = threading.Event(), threading.Event()
        def slow_probe(_path):
            started.set()
            if not release.wait(5):
                raise TimeoutError("probe was not released")
            return {"resolution": "320x240", "duration_sec": 1}
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                if resumable:
                    session = (await client.post("/api/uploads/init", json={"files": [{"path": "clip.mp4", "size": 1}]})).json()["id"]
                    self.assertEqual((await client.put(f"/api/uploads/{session}/chunks/0/0", content=b"x")).status_code, 200)
                    request = client.post(f"/api/uploads/{session}/complete", data={"encoder_device": "cpu"})
                else:
                    request = client.post("/api/jobs", data={"encoder_device": "cpu"}, files={"videos": ("clip.mp4", b"x", "video/mp4")})
                upload = asyncio.create_task(request)
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    self.assertFalse(upload.done(), "Video probing blocked the HTTP event loop")
                    response = await asyncio.wait_for(client.get("/health"), 1)
                    self.assertEqual(response.status_code, 200)
                    self.assertIsNone(main.current_job()["job"])
                finally:
                    release.set()
                self.assertEqual((await upload).status_code, 200)
        with patch.object(main, "probe_video", side_effect=slow_probe):
            asyncio.run(scenario())

    def test_health_responds_while_ordinary_upload_is_probing(self):
        self.assert_health_during_probe(False)

    def test_health_responds_while_resumable_upload_is_probing(self):
        self.assert_health_during_probe(True)

    def test_oversized_chunk_is_rejected_before_creating_files(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.put('/api/uploads/' + 'a' * 32 + '/chunks/0/0', content=b"x" * (main.UPLOAD_CHUNK_SIZE + 1))
                self.assertEqual(response.status_code, 413)
        asyncio.run(scenario())
        self.assertEqual(list(main.RESUMABLE_UPLOAD_DIR.iterdir()), [])

    def test_short_record_retention_cleans_files_but_preserves_active_jobs(self):
        old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        for status in ("done", "queued", "running", "paused"):
            with self.subTest(status=status):
                job_id = self.create_direct(["clip.mp4"])
                main.update_job(job_id, status=status, updated_at=old)
                # update_job intentionally refreshes updated_at; set the historical fixture directly.
                with main.db_connect() as conn:
                    conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (old, job_id))
                    conn.commit()
                directory = Path(main.get_job(job_id)["upload_dir"])
                with patch.object(main, "RECORD_RETENTION_DAYS", 1), patch.object(main, "FILE_RETENTION_DAYS", 14):
                    main.cleanup_expired_files()
                if status == "done":
                    self.assertIsNone(main.get_job(job_id))
                    self.assertFalse(directory.exists())
                else:
                    self.assertEqual(main.get_job(job_id)["status"], status)
                    self.assertTrue(directory.exists())


class AuthenticationAndTlsTest(unittest.TestCase):
    def test_unicode_credentials_work_and_invalid_login_json_returns_400(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                self.assertEqual((await client.get("/api/jobs")).status_code, 401)
                self.assertEqual((await client.post("/api/login", json=[])).status_code, 400)
                self.assertEqual((await client.post("/api/login", json={"username": "管理员", "password": "测试密码"})).status_code, 200)
                self.assertTrue(main.verify_auth_token(client.cookies[main.AUTH_COOKIE_NAME]))
        with patch.object(main, "AUTH_USER", "管理员"), patch.object(main, "AUTH_PASSWORD", "测试密码"):
            token = base64.b64encode("管理员:测试密码".encode()).decode()
            self.assertTrue(main.verify_basic_auth("Basic " + token))
            asyncio.run(scenario())

    def test_tls_failure_never_retries_with_certificate_verification_disabled(self):
        with patch.object(main, "WECHAT_WEBHOOK", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-only"), \
             patch.object(main, "post_wecom_payload", side_effect=URLError(ssl.SSLCertVerificationError("test certificate error"))) as send:
            ok, message = main.send_wecom_notification("test", [])
        self.assertFalse(ok)
        self.assertIn("证书", message)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1].verify_mode, ssl.CERT_REQUIRED)

    def test_malformed_and_future_session_tokens_are_rejected(self):
        with patch.object(main, "AUTH_PASSWORD", "test-only"):
            invalid_signature = base64.urlsafe_b64encode(f"{main.AUTH_USER}:{int(main.time.time())}:错误签名".encode()).decode()
            self.assertFalse(main.verify_auth_token(invalid_signature))
            self.assertFalse(main.verify_auth_token("错误编码"))
            with patch.object(main.time, "time", return_value=1000):
                future_token = main.make_auth_token(main.AUTH_USER)
            with patch.object(main.time, "time", return_value=900):
                self.assertFalse(main.verify_auth_token(future_token))

    def test_webhook_rejects_similar_host_and_cleartext_urls(self):
        for url in ("http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
                    "https://evil.invalid/qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
                    "https://[invalid/cgi-bin/webhook/send?key=test"):
            with self.subTest(url=url), patch.object(main, "WECHAT_WEBHOOK", url):
                self.assertTrue(main.wecom_config_error())


if __name__ == "__main__":
    unittest.main()
