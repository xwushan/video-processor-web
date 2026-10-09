import asyncio
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

from processVideo import generate_ffmpeg_command
from web_app import main, size_estimation
from test_job_regressions import FORM, TemporaryAppTestCase


class EstimationApiTest(TemporaryAppTestCase):
    async def upload(self, client):
        session = (await client.post("/api/uploads/init", json={"files": [
            {"path": "lesson/clip.mp4", "size": 6}]})).json()["id"]
        response = await client.put(f"/api/uploads/{session}/chunks/0/0", content=b"source")
        self.assertEqual(response.status_code, 200)
        return session

    async def finish(self, client, session):
        for _ in range(500):
            state = (await client.get(f"/api/uploads/{session}/estimate")).json()
            if state["status"] not in {"running", "canceling"}:
                return state
            await asyncio.sleep(0.01)
        self.fail("Estimation did not finish")

    def trial(self, source, work_dir, settings, device, *_args):
        self.assertEqual(source.read_bytes(), b"source")
        (work_dir / "preview-0.mp4").write_bytes(b"processed-video")
        (work_dir / "thumbnail-0.jpg").write_bytes(b"processed-thumbnail")
        return {"estimated_bytes": settings["crf"] * 100, "min_bytes": 2000, "max_bytes": 5000,
                "encoder_device": device, "encoder_name": "libx264", "full_trial": False,
                "duration_sec": 60, "sample_count": 1, "samples": [{"start_sec": 5, "duration_sec": 8}]}

    def test_cached_trial_parameter_change_and_reuse_for_actual_job(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                form = {k: v for k, v in FORM.items() if isinstance(v, str)}
                for crf in ("32", "32", "26"):
                    response = await client.post(f"/api/uploads/{session}/estimate", data={**form, "crf": crf})
                    self.assertEqual(response.status_code, 200, response.text)
                    state = await self.finish(client, session)
                    self.assertEqual(state["status"], "done")
                    self.assertEqual(state["totals"]["estimated_bytes"], int(crf) * 100)
                    url = f"/api/uploads/{session}/estimate/{state['id']}/files/0/samples/0/video"
                    self.assertEqual((await client.get(url)).content, b"processed-video")
                self.assertIsNone(main.current_job()["job"])
                self.assertEqual(list((main.RESUMABLE_UPLOAD_DIR / session).glob("estimate-work-*")), [])
                response = await client.post(f"/api/uploads/{session}/complete", data=form)
                self.assertEqual(response.status_code, 200, response.text)
                files = main.get_job_files(response.json()["id"])
                self.assertEqual(Path(files[0]["input_path"]).read_bytes(), b"source")
                self.assertFalse((main.RESUMABLE_UPLOAD_DIR / session).exists())
                self.assertEqual((await client.get(url)).status_code, 404)
        with patch.object(main, "estimate_file", side_effect=self.trial) as trial:
            asyncio.run(scenario())
        self.assertEqual(trial.call_count, 2)

    def test_preview_range_snapshot_validation_and_missing_sample_cache(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                form = {"encoder_device": "cpu"}
                await client.post(f"/api/uploads/{session}/estimate", data=form)
                state = await self.finish(client, session)
                base = f"/api/uploads/{session}/estimate/{state['id']}/files/0/samples/0"
                response = await client.get(base + "/video", headers={"Range": "bytes=0-8"})
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.content, b"processed")
                self.assertEqual(response.headers["content-type"], "video/mp4")
                self.assertIn("no-store", response.headers["cache-control"])
                self.assertEqual((await client.get(base + "/thumbnail")).content, b"processed-thumbnail")
                for bad in (base.replace("/files/0", "/files/-1"), base.replace("/samples/0", "/samples/9"),
                            base.replace(state["id"], "0" * 32), base + "/other"):
                    self.assertEqual((await client.get(bad + ("/video" if not bad.endswith("other") else ""))).status_code, 404)
                with patch.object(main, "AUTH_PASSWORD", "test-password"):
                    self.assertEqual((await client.get(base + "/video")).status_code, 401)
                root = main.RESUMABLE_UPLOAD_DIR / session / "estimate-previews" / state["id"]
                (root / "file-0/preview-0.mp4").unlink()
                await client.post(f"/api/uploads/{session}/estimate", data=form)
                newer = await self.finish(client, session)
                self.assertEqual(newer["status"], "done")
                self.assertNotEqual(state["id"], newer["id"])
                self.assertFalse(root.exists())
                self.assertEqual((await client.get(base + "/thumbnail")).status_code, 404)
        with patch.object(main, "estimate_file", side_effect=self.trial) as trial:
            asyncio.run(scenario())
        self.assertEqual(trial.call_count, 2)

    def test_preview_publish_failure_reports_error_and_releases_session(self):
        original_replace = Path.replace
        def replace(path, target):
            if path.name == "file-0":
                raise OSError("Cannot publish trial sample")
            return original_replace(path, target)
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": "cpu"})
                state = await self.finish(client, session)
                self.assertEqual(state["status"], "error")
                self.assertEqual(state["failed_count"], 1)
                self.assertNotIn("totals", state)
                self.assertIn("Cannot publish", state["files"][0]["error"])
                self.assertFalse(main.encoding_work_lock.locked())
                self.assertNotIn(session, main.completing_upload_sessions)
                self.assertEqual(list((main.RESUMABLE_UPLOAD_DIR / session).glob("estimate-work-*")), [])
        with patch.object(main, "estimate_file", side_effect=self.trial), patch.object(Path, "replace", replace):
            asyncio.run(scenario())

    def test_cancel_keeps_source_and_blocks_conflicting_session_operations(self):
        started = threading.Event()
        job_started = threading.Event()
        def trial(_source, _work, _settings, _device, _threads, canceled, *_args):
            started.set()
            if not canceled.wait(5):
                raise TimeoutError("Cancellation was not delivered")
            raise size_estimation.EstimateCanceled()
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                try:
                    response = await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": "cpu"})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    queued_worker = threading.Thread(target=main.process_job, args=("queued-during-trial",))
                    queued_worker.start()
                    self.assertFalse(await asyncio.to_thread(job_started.wait, .1))
                    self.assertEqual((await asyncio.wait_for(client.get("/health"), 1)).status_code, 200)
                    for url, method in (("estimate", "post"), ("complete", "post"), ("chunks/0/0", "put")):
                        args = {"data": {"encoder_device": "cpu"}} if method == "post" else {"content": b"source"}
                        self.assertEqual((await getattr(client, method)(f"/api/uploads/{session}/{url}", **args)).status_code, 409)
                    self.assertEqual((await client.delete(f"/api/uploads/{session}/estimate")).status_code, 200)
                    self.assertEqual((await self.finish(client, session))["status"], "canceled")
                    await asyncio.to_thread(queued_worker.join, 2)
                    self.assertFalse(queued_worker.is_alive())
                    self.assertTrue(job_started.is_set())
                    source = main.RESUMABLE_UPLOAD_DIR / session / "files/lesson/clip.mp4"
                    self.assertEqual(source.read_bytes(), b"source")
                    self.assertFalse(main.encoding_work_lock.locked())
                finally:
                    await client.delete(f"/api/uploads/{session}/estimate")
                    await self.finish(client, session)
        with patch.object(main, "estimate_file", side_effect=trial), \
             patch.object(main, "_process_job", side_effect=lambda _job: job_started.set()):
            asyncio.run(scenario())

    def test_replacing_custom_watermark_invalidates_cache(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                identities = []
                for color in ("red", "red", "blue"):
                    image = io.BytesIO()
                    Image.new("RGB", (16, 16), color).save(image, format="PNG")
                    response = await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": "cpu"},
                        files={"fixed_watermark": ("custom.png", image.getvalue(), "image/png")})
                    self.assertEqual(response.status_code, 200)
                    state = await self.finish(client, session)
                    self.assertEqual(state["status"], "done")
                    identities.append(state["id"])
                self.assertEqual(identities[0], identities[1])
                self.assertNotEqual(identities[1], identities[2])
        with patch.object(main, "estimate_file", side_effect=self.trial) as trial:
            asyncio.run(scenario())
        self.assertEqual(trial.call_count, 2)

    def test_incomplete_upload_and_busy_job_rejected_without_lock_leak(self):
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = (await client.post("/api/uploads/init", json={"files": [{"path": "clip.mp4", "size": 6}]})).json()["id"]
                self.assertEqual((await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": "cpu"})).status_code, 409)
                self.assertFalse(main.encoding_work_lock.locked())
                job = await asyncio.to_thread(self.create_direct, ["busy.mp4"])
                self.assertEqual(main.get_job(job)["status"], "queued")
                self.assertEqual((await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": "cpu"})).status_code, 409)
                self.assertFalse(main.encoding_work_lock.locked())
        asyncio.run(scenario())

    def test_auto_gpu_failure_uses_cpu_parameters_and_strict_gpu_reports_error(self):
        def trial(source, work, settings, device, *args):
            if device == "nvidia":
                raise RuntimeError("OpenEncodeSessionEx failed: out of memory")
            return self.trial(source, work, settings, device, *args)
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                for requested in ("auto", "nvidia"):
                    response = await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": requested, "crf": "32"})
                    self.assertEqual(response.status_code, 200)
                    state = await self.finish(client, session)
                    if requested == "auto":
                        self.assertEqual(state["status"], "done")
                        self.assertEqual(state["files"][0]["encoder_device"], "cpu")
                        self.assertEqual(state["totals"]["estimated_bytes"], 3200)
                    else:
                        self.assertEqual(state["status"], "error")
                        self.assertNotIn("totals", state)
        with patch.object(main, "select_encoder", return_value=("nvidia", "")), \
             patch.object(main, "estimate_file", side_effect=trial):
            asyncio.run(scenario())

    def test_cpu_fallback_estimate_is_not_cached_as_working_gpu(self):
        gpu_attempts = []
        def trial(source, work, settings, device, *args):
            if device == "nvidia":
                gpu_attempts.append(device)
                if len(gpu_attempts) == 1:
                    raise RuntimeError("OpenEncodeSessionEx failed")
            return self.trial(source, work, settings, device, *args)
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                session = await self.upload(client)
                results = []
                for _ in range(3):
                    await client.post(f"/api/uploads/{session}/estimate", data={"encoder_device": "auto"})
                    state = await self.finish(client, session)
                    self.assertEqual(state["status"], "done")
                    results.append(state)
                self.assertEqual(results[0]["files"][0]["encoder_device"], "cpu")
                self.assertEqual(results[1]["files"][0]["encoder_device"], "nvidia")
                self.assertNotEqual(results[0]["id"], results[1]["id"])
                self.assertEqual(results[1]["id"], results[2]["id"])
        with patch.object(main, "select_encoder", return_value=("nvidia", "")), \
             patch.object(main, "estimate_file", side_effect=trial):
            asyncio.run(scenario())


class MediaValidationTest(unittest.TestCase):
    def test_cancel_during_metadata_probe_does_not_start_an_encoder(self):
        canceled = threading.Event()
        canceled.set()
        with patch.object(size_estimation.subprocess, "Popen") as spawn, self.assertRaises(size_estimation.EstimateCanceled):
            size_estimation.run_sample("unused", canceled, main.terminate_process)
        spawn.assert_not_called()

    def test_invalid_duration_is_not_extrapolated(self):
        for duration in ("NaN", "inf", "0", "-2", None):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                size_estimation.media_duration({"streams": [{"codec_type": "video", "duration": duration}]})
        self.assertAlmostEqual(size_estimation.media_duration({"streams": [
            {"codec_type": "video", "tags": {"DURATION": "00:02:03.500"}}]}), 123.5)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class EstimationFfmpegTest(unittest.TestCase):
    def test_retained_samples_preserve_encoded_pixels_audio_and_resolution(self):
        settings = main.build_job_settings("true", "true", "top-right", "10", "2", "32", "h264",
                                           "veryfast", "6.25", "6.25", "cpu")
        original_prepare = size_estimation.prepare_preview
        checked = []
        def verify_preview(output, index, duration, canceled, terminate):
            original_prepare(output, index, duration, canceled, terminate)
            preview = output.parent / f"preview-{index}.mp4"
            def pixels(path):
                return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                    "-f", "hash", "-hash", "sha256", "-"], check=True, capture_output=True, timeout=30).stdout
            self.assertEqual(pixels(output), pixels(preview))
            streams = size_estimation._probe_video(str(preview))["streams"]
            video = next(s for s in streams if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (320, 240))
            self.assertTrue(any(s["codec_type"] == "audio" for s in streams))
            with Image.open(output.parent / f"thumbnail-{index}.jpg") as thumbnail:
                self.assertEqual(thumbnail.size, (320, 240))
            checked.append(index)
        with tempfile.TemporaryDirectory() as folder, patch.object(size_estimation, "prepare_preview", side_effect=verify_preview):
            folder = Path(folder)
            source = folder / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "30", "-c:v", "libx264", "-c:a", "aac",
                str(source)], check=True, capture_output=True, timeout=30)
            for fmt in ("h264", "h265", "mkv"):
                with self.subTest(format=fmt):
                    work = folder / fmt
                    work.mkdir()
                    result = size_estimation.estimate_file(source, work, {**settings, "format_type": fmt}, "cpu", 4,
                        threading.Event(), main.terminate_process, lambda *_args: None, True)
                    self.assertEqual(len(result["samples"]), 3)
                    self.assertAlmostEqual(result["samples"][1]["start_sec"], 11)
                    self.assertEqual(len(list(work.glob("preview-*.mp4"))), 3)
                    self.assertFalse(list(work.glob("sample-*")))
        self.assertEqual(len(checked), 9)

    def test_real_estimates_cover_actual_h264_h265_and_mkv_with_audio_and_watermarks(self):
        settings = main.build_job_settings("true", "true", "top-right", "10", "2", "32", "h264",
                                           "veryfast", "6.25", "6.25", "cpu")
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for seconds in (2, 30):
                source = folder / f"source-{seconds}.mp4"
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
                                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(seconds),
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "aac", str(source)],
                               capture_output=True, check=True, timeout=30)
                for fmt in ("h264", "h265", "mkv"):
                    with self.subTest(seconds=seconds, format=fmt):
                        chosen = {**settings, "format_type": fmt}
                        estimate = size_estimation.estimate_file(source, folder, chosen, "cpu", 4,
                            threading.Event(), main.terminate_process, lambda *_args: None)
                        self.assertEqual(estimate["sample_count"], 1 if seconds == 2 else 3)
                        self.assertFalse(list(folder.glob("sample-*")))
                        output = folder / ("actual.mkv" if fmt == "mkv" else "actual.mp4")
                        command = generate_ffmpeg_command(str(source), str(output), crf=32, interval=10,
                            watermark_duration_seconds=2, format_type=fmt, encoder_preset="veryfast", encoder_threads=4)
                        subprocess.run(command if os.name == "nt" else shlex.split(command),
                                       capture_output=True, check=True, timeout=30)
                        actual = output.stat().st_size
                        self.assertLessEqual(estimate["min_bytes"], actual)
                        self.assertGreaterEqual(estimate["max_bytes"], actual)

    def test_sample_dynamic_watermark_uses_original_timeline(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=320x240:rate=15",
                            "-t", "15", "-c:v", "libx264", str(source)], check=True, capture_output=True)
            sampled = Path(folder) / "sample.mp4"
            command = generate_ffmpeg_command(str(source), str(sampled), fixed_watermark_enabled=False,
                dynamic_watermark_enabled=True, interval=10, watermark_duration_seconds=2,
                sample_start=6, sample_duration=2, encoder_threads=2)
            subprocess.run(command if os.name == "nt" else shlex.split(command),
                           check=True, capture_output=True, timeout=30)
            # The watermark is off at t=6..8; restarting its clock at zero would
            # visibly change the image and inflate the sample's predicted bitrate.
            frame = subprocess.run(["ffmpeg", "-v", "error", "-i", str(sampled), "-frames:v", "1",
                                    "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
            self.assertLessEqual(max(frame), 2)


if __name__ == "__main__":
    unittest.main()
