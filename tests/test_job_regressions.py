import asyncio
import io
import json
import queue
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi import UploadFile
from web_app import main


FORM = dict(
    fixed_watermark=None, dynamic_watermark=None,
    fixed_watermark_enabled="false", dynamic_watermark_enabled="false",
    fixed_watermark_pos="top-right", interval="60", duration="5", crf="32",
    format_type="h264", encoder_preset="veryfast", fixed_watermark_size="6.25",
    dynamic_watermark_size="6.25", encoder_device="cpu", gpu_quality="26",
    gpu_preset="p4", gpu_concurrency="2",
)


class TemporaryAppTestCase(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, path in {
            "APP_ROOT": self.root, "UPLOAD_DIR": self.root / "uploads",
            "OUTPUT_DIR": self.root / "outputs", "TMP_DIR": self.root / "tmp",
            "WATERMARK_DIR": self.root / "watermarks",
            "RESUMABLE_UPLOAD_DIR": self.root / "tmp" / "resumable_uploads",
            "DB_PATH": self.root / "jobs.db",
        }.items():
            self.stack.enter_context(patch.object(main, name, path))
        self.stack.enter_context(patch.object(main, "db_schema_ready", False))
        self.stack.enter_context(patch.object(main, "job_queue", queue.Queue()))
        self.stack.enter_context(patch.object(main, "job_cancel_events", {}))
        self.stack.enter_context(patch.object(main, "ensure_worker"))
        self.stack.enter_context(patch.object(main, "ensure_disk_headroom"))
        self.stack.enter_context(patch.object(main, "notify_job_result"))
        self.stack.enter_context(patch.object(main, "terminate_job_processes"))
        self.stack.enter_context(patch.object(main, "probe_video", return_value={
            "resolution": "320x240", "duration_sec": 1,
        }))
        main.init_db()

    def create_direct(self, paths, fmt="h264"):
        videos = [UploadFile(filename=Path(path).name, file=io.BytesIO(str(i).encode()))
                  for i, path in enumerate(paths)]
        return asyncio.run(main.create_job(
            videos=videos, video_paths=paths, **{**FORM, "format_type": fmt},
        ))["id"]

    def assert_unique_outputs(self, job_id, count):
        files = main.get_job_files(job_id)
        self.assertEqual(len(files), count)
        self.assertEqual(len({row["output_path"].casefold() for row in files}), count)
        self.assertEqual(len({row["input_path"] for row in files}), count)
        self.assertTrue(all(Path(row["input_path"]).exists() for row in files))


class JobRegressionTest(TemporaryAppTestCase):
    def test_direct_upload_keeps_same_stem_videos_separate(self):
        for fmt in ("h264", "h265", "mkv"):
            for folder in ("", "lesson/"):
                with self.subTest(fmt=fmt, folder=folder):
                    paths = [folder + name for name in ("clip.mp4", "clip.mov", "clip_2.mp4")]
                    self.assert_unique_outputs(self.create_direct(paths, fmt), 3)

    def test_resumable_upload_keeps_same_stem_videos_separate(self):
        for fmt in ("h264", "h265", "mkv"):
            for folder in ("", "lesson/"):
                with self.subTest(fmt=fmt, folder=folder):
                    session = self.root / ("session-" + fmt + str(bool(folder)))
                    paths = [folder + name for name in ("clip.mp4", "clip.mov", "clip_2.mp4")]
                    for i, path in enumerate(paths):
                        source = session / "files" / path
                        source.parent.mkdir(parents=True, exist_ok=True)
                        source.write_bytes(str(i).encode())
                    settings = json.loads(main.get_job(self.create_direct(["seed.mp4"], fmt))["settings_json"])
                    job_id, *_ = main.create_job_from_resumable_files(
                        session, {"files": [{"path": p, "size": 1} for p in paths]}, settings,
                    )
                    self.assert_unique_outputs(job_id, 3)

    def test_file_exception_marks_job_failed_and_other_files_still_finish(self):
        job_id = self.create_direct(["bad.mp4", "good.mp4"])
        def encode(_job_id, row, *_args):
            if row["original_name"] == "bad.mp4":
                raise OSError("test output unavailable")
            main.update_file(row["id"], status="done", progress=100)
        with patch.object(main, "process_file", side_effect=encode), \
             patch.object(main, "resources_allow_next_task", return_value=(True, "", 0)), \
             patch.object(main, "RESOURCE_WARMUP_SECONDS", 0):
            main.process_job(job_id)
        job = main.get_job(job_id)
        self.assertEqual(job["status"], "error")
        self.assertEqual((job["done_count"], job["failed_count"]), (1, 1))
        self.assertEqual(job["worker_count"], 0)
        bad = next(row for row in main.get_job_files(job_id) if row["original_name"] == "bad.mp4")
        self.assertEqual(bad["status"], "error")
        self.assertIn("test output unavailable", bad["error"])

    def test_immediate_resume_waits_for_old_run_to_stop(self):
        job_id = self.create_direct(["clip.mp4"])
        started, release = threading.Event(), threading.Event()
        def old_encode(_job_id, row, *_args):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test did not release encoder")
            main.update_file(row["id"], status="paused", error="已暂停")
        with patch.object(main, "process_file", side_effect=old_encode), \
             patch.object(main, "resources_allow_next_task", return_value=(True, "", 0)):
            worker = threading.Thread(target=main.process_job, args=(job_id,))
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                main.pause_job(job_id)
                main.resume_job(job_id)
                self.assertTrue(main.get_cancel_event(job_id).is_set())
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(main.get_job(job_id)["status"], "queued")
        def new_encode(_job_id, row, *_args):
            self.assertFalse(main.get_cancel_event(job_id).is_set())
            main.update_file(row["id"], status="done", progress=100)
        with patch.object(main, "process_file", side_effect=new_encode), \
             patch.object(main, "resources_allow_next_task", return_value=(True, "", 0)):
            main.process_job(job_id)
        self.assertEqual(main.get_job(job_id)["status"], "done")

    def test_gpu_scheduler_expands_stops_at_threshold_and_resumes_when_load_drops(self):
        job_id = self.create_direct([f"clip-{i}.mp4" for i in range(5)])
        release, blocked, expanded, lower_load = (threading.Event() for _ in range(4))
        state_lock = threading.Lock()
        active, peak = 0, 0
        def encode(_job_id, row, *_args):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
                if active == 5:
                    expanded.set()
            if not release.wait(5):
                raise TimeoutError("test did not release encoders")
            main.update_file(row["id"], status="done", progress=100)
            with state_lock:
                active -= 1
        def metrics(force=False):
            with state_lock:
                high = active >= 3 and not lower_load.is_set()
            if high:
                blocked.set()
            return dict(encoder_percent=80 if high else 10, utilization_percent=10,
                        memory_used_mb=100, memory_total_mb=1000)
        with patch.object(main, "process_file", side_effect=encode), \
             patch.object(main, "select_encoder", return_value=("nvidia", "")), \
             patch.object(main, "get_gpu_metrics", side_effect=metrics), \
             patch.object(main.psutil, "cpu_percent", return_value=10), \
             patch.object(main.psutil, "virtual_memory") as ram, \
             patch.object(main, "linux_iowait_percent", return_value=0), \
             patch.object(main, "disk_free_bytes", return_value=main.MIN_FREE_DISK_BYTES + 1000), \
             patch.object(main, "RESOURCE_WARMUP_SECONDS", 0), \
             patch.object(main, "RESOURCE_SAMPLE_INTERVAL_SECONDS", .01), \
             patch.object(main, "GPU_MAX_CONCURRENT", 8):
            ram.return_value.percent = 10
            worker = threading.Thread(target=main.process_job, args=(job_id,))
            worker.start()
            try:
                self.assertTrue(blocked.wait(5))
                with state_lock:
                    self.assertEqual(active, 3)
                    self.assertEqual(peak, 3)
                lower_load.set()
                self.assertTrue(expanded.wait(5))
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(peak, 5)
        self.assertEqual(main.get_job(job_id)["status"], "done")
        self.assertEqual(main.get_job(job_id)["worker_count"], 0)

    def test_delete_cleans_output_created_by_a_worker_still_finishing_setup(self):
        job_id = self.create_direct(["clip.mp4"])
        started, release = threading.Event(), threading.Event()
        output_dir = Path(main.get_job(job_id)["output_dir"])
        def delayed_setup(_job_id, row, *_args):
            started.set()
            if not release.wait(5):
                raise TimeoutError("test did not release setup")
            # Reproduce setup racing with the API's initial cleanup pass.
            output = Path(row["output_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"incomplete output")
        with patch.object(main, "process_file", side_effect=delayed_setup), \
             patch.object(main, "resources_allow_next_task", return_value=(True, "", 0)):
            worker = threading.Thread(target=main.process_job, args=(job_id,))
            worker.start()
            try:
                self.assertTrue(started.wait(5))
                main.delete_job(job_id)
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertIsNone(main.get_job(job_id))
        self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
