import subprocess
import unittest
from unittest.mock import patch

from web_app import encoding, main


class ResourceSchedulingTest(unittest.TestCase):
    def check(self, active=1, gpu=None, cpu=10, memory=10, iowait=0, free=None, device="nvidia"):
        if gpu is None:
            gpu = dict(encoder_percent=10, memory_used_mb=100, memory_total_mb=1000, utilization_percent=10)
        with patch.object(main.psutil, "cpu_percent", return_value=cpu), \
             patch.object(main.psutil, "virtual_memory") as ram, \
             patch.object(main, "linux_iowait_percent", return_value=iowait), \
             patch.object(main, "disk_free_bytes", return_value=free if free is not None else main.MIN_FREE_DISK_BYTES + 1000), \
             patch.object(main, "get_gpu_metrics", return_value=gpu) as probe:
            ram.return_value.percent = memory
            result = main.resources_allow_next_task(active, 100, device)
        return result, probe

    def test_gpu_grows_below_threshold_and_stops_at_each_80_percent_limit(self):
        result, probe = self.check()
        self.assertTrue(result[0])
        probe.assert_called_once_with(force=True)
        for field, value, reason in (("encoder_percent", 80, "编码器"),
                                      ("memory_used_mb", 800, "显存"),
                                      ("utilization_percent", 80, "GPU")):
            gpu = dict(encoder_percent=10, memory_used_mb=100, memory_total_mb=1000, utilization_percent=10)
            gpu[field] = value
            with self.subTest(field=field):
                result, _ = self.check(gpu=gpu)
                self.assertFalse(result[0])
                self.assertIn(reason, result[1])

    def test_gpu_also_obeys_cpu_ram_and_disk_guards(self):
        for values, reason in (({"cpu": 80}, "CPU"), ({"memory": 80}, "内存"),
                               ({"iowait": 20}, "I/O"), ({"free": main.MIN_FREE_DISK_BYTES}, "磁盘")):
            with self.subTest(values=values):
                result, _ = self.check(**values)
                self.assertFalse(result[0])
                self.assertIn(reason, result[1])

    def test_missing_gpu_metrics_allow_one_task_but_do_not_expand(self):
        for gpu in ({}, dict(encoder_percent=None, memory_used_mb=0, memory_total_mb=1000),
                    dict(encoder_percent=10, memory_used_mb=0, memory_total_mb=0)):
            with self.subTest(gpu=gpu):
                self.assertTrue(self.check(active=0, gpu=gpu)[0][0])
                self.assertFalse(self.check(active=1, gpu=gpu)[0][0])

    def test_cpu_scheduling_does_not_depend_on_gpu_monitoring(self):
        result, probe = self.check(device="cpu", gpu={})
        self.assertTrue(result[0])
        probe.assert_not_called()


class GpuMonitoringTest(unittest.TestCase):
    def test_working_gpu_does_not_open_extra_probe_sessions_while_scheduling(self):
        capabilities = {"available": True, "codecs": {}}
        with patch.object(encoding, "_capabilities", capabilities), \
             patch.object(encoding, "_checked_at", 0), \
             patch.object(encoding.time, "monotonic", return_value=600), \
             patch.object(encoding, "_run") as run:
            self.assertIs(encoding.get_gpu_capabilities(), capabilities)
            run.assert_not_called()

    def test_unavailable_and_non_finite_metrics_are_not_reported_as_zero(self):
        result = subprocess.CompletedProcess([], 0, "GPU, NaN, N/A, -1, 24000\n", "")
        with patch.object(encoding, "get_gpu_capabilities", return_value={"available": True}), \
             patch.object(encoding, "_metrics", None), \
             patch.object(encoding, "_metrics_at", 0), \
             patch.object(encoding, "_run", return_value=result):
            metrics = encoding.get_gpu_metrics(force=True)
        self.assertIsNone(metrics["encoder_percent"])
        self.assertIsNone(metrics["utilization_percent"])
        self.assertIsNone(metrics["memory_used_mb"])
        self.assertEqual(metrics["memory_total_mb"], 24000)


if __name__ == "__main__":
    unittest.main()
