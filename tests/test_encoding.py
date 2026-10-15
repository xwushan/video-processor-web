import threading
import unittest
from unittest.mock import patch

from web_app import encoding
from web_app import main


class DeviceSelectionTest(unittest.TestCase):
    def test_auto_selects_supported_codec_and_strict_gpu_rejects_unsupported_codec(self):
        capabilities = {"codecs": {"h264": {"available": True, "reason": ""},
                                   "h265": {"available": False, "reason": "HEVC unsupported"}}}
        with patch.object(encoding, "get_gpu_capabilities", return_value=capabilities):
            self.assertEqual(encoding.select_encoder("auto", "mkv"), ("nvidia", ""))
            self.assertEqual(encoding.select_encoder("auto", "h265"), ("cpu", "HEVC unsupported"))
            with self.assertRaisesRegex(ValueError, "HEVC unsupported"):
                encoding.select_encoder("nvidia", "h265")
            self.assertEqual(encoding.select_encoder("cpu", "h265"), ("cpu", ""))

    def test_file_storage_and_filter_failures_are_not_gpu_failures(self):
        for error in ("Invalid data found when processing input", "No space left on device",
                      "Error initializing complex filters", "Permission denied"):
            self.assertFalse(encoding.is_gpu_error(error), error)
        self.assertTrue(encoding.is_gpu_error("OpenEncodeSessionEx failed: out of memory (10)"))
        self.assertTrue(encoding.is_gpu_error("Driver does not support the required nvenc API version"))

    def test_automatic_gpu_scheduling_uses_server_ceiling_and_ignores_old_manual_limit(self):
        with patch.object(main, "GPU_MAX_CONCURRENT", 8):
            self.assertEqual(main.recommend_worker_limit([{}] * 12, "nvidia", 2), 8)
            self.assertEqual(main.recommend_worker_limit([{}], "nvidia", 2), 1)


class FallbackTest(unittest.TestCase):
    def run_file(self, requested, results, canceled=False, previous_attempts=0):
        state = {}
        settings = main.build_job_settings(
            "true", "true", "top-right", "60", "5", "32", "h264", "veryfast", "6.25", "6.25",
            encoder_device="auto",
        )
        settings["encoder_device"] = requested
        row = {"id": "file", "input_path": "input.mp4", "output_path": "output.mp4",
               "status": "queued", "progress": 0, "size_bytes": 100, "attempts": previous_attempts, "duration_sec": 1}
        event = threading.Event()
        def run(*args):
            result = results.pop(0)
            if canceled:
                event.set()
            return result
        with patch.object(main, "get_cancel_event", return_value=event), \
             patch.object(main, "ensure_disk_headroom"), \
             patch.object(main, "generate_ffmpeg_command", return_value="command") as generate, \
             patch.object(main, "run_ffmpeg_command", side_effect=run) as runner, \
             patch.object(main, "update_file", side_effect=lambda _id, **fields: state.update(fields)), \
             patch.object(main, "refresh_job_progress"), \
             patch.object(main.time, "sleep"), \
             patch.object(main, "delete_path_safely"), \
             patch.object(main, "remove_empty_upload_dir"):
            main.process_file("job", row, settings, 4, 1, "nvidia")
        return state, generate, runner

    def test_auto_reencodes_on_cpu_after_gpu_failure(self):
        state, generate, runner = self.run_file("auto", ["gpu_error", "done"])
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(generate.call_args.kwargs["encoder_device"], "cpu")
        self.assertEqual(generate.call_args.kwargs["crf"], 32)
        self.assertEqual(state["encoder_device"], "cpu")
        self.assertEqual(state["encoder_name"], "libx264")
        self.assertTrue(state["fallback_reason"])

    def test_strict_gpu_and_unrelated_failures_do_not_fall_back(self):
        for requested, result in (("nvidia", "gpu_error"), ("auto", "error")):
            state, generate, runner = self.run_file(requested, [result])
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(state["encoder_device"], "nvidia")

    def test_cancel_does_not_start_cpu_fallback(self):
        _, generate, runner = self.run_file("auto", ["gpu_error"], canceled=True)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(generate.call_count, 1)

    def test_resume_has_a_fresh_retry_budget(self):
        state, _, runner = self.run_file("nvidia", ["retry", "done"], previous_attempts=7)
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(state["attempts"], 9)

    def test_cpu_fallback_has_its_own_retry_budget(self):
        _, generate, runner = self.run_file("auto", ["gpu_error", "retry", "done"])
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(generate.call_args.kwargs["encoder_device"], "cpu")


if __name__ == "__main__":
    unittest.main()
