"""Probe NVENC with real encodes, and share device selection across API and workers."""
import csv
import math
import os
import subprocess
import threading
import time

from processVideo import _tool_path, _hidden_subprocess_kwargs


def env_int(name, default, low, high):
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


DEFAULT_ENCODER_DEVICE = os.getenv("VIDEO_PROCESSOR_ENCODER_DEVICE", "auto").lower()
if DEFAULT_ENCODER_DEVICE not in {"auto", "cpu", "nvidia"}:
    DEFAULT_ENCODER_DEVICE = "auto"
GPU_MAX_CONCURRENT = env_int("VIDEO_PROCESSOR_GPU_MAX_CONCURRENT", 8, 1, 16)
GPU_CPU_THREADS = env_int("VIDEO_PROCESSOR_GPU_CPU_THREADS", 4, 1, 64)
_lock = threading.Lock()
_metrics_lock = threading.Lock()
_capabilities = None
_checked_at = 0.0
_metrics = None
_metrics_at = 0.0


def _run(args, timeout=10):
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, **_hidden_subprocess_kwargs(),
    )


def get_gpu_capabilities(force=False):
    global _capabilities, _checked_at
    with _lock:
        if not force and _capabilities is not None:
            # Probing a working GPU while jobs run consumes another NVENC session.
            # Keep successful startup probes; actual encode errors handle device loss.
            if _capabilities["available"] or time.monotonic() - _checked_at < 300:
                return _capabilities
        codecs = {}
        for kind, encoder in (("h264", "h264_nvenc"), ("h265", "hevc_nvenc")):
            try:
                result = _run([
                    _tool_path("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin",
                    "-f", "lavfi", "-i", "color=size=320x240:rate=1",
                    "-frames:v", "1", "-c:v", encoder, "-gpu", "0", "-preset", "p4",
                    "-pix_fmt", "yuv420p", "-f", "null", "-",
                ])
                available = result.returncode == 0
                reason = "" if available else "GPU 编码不可用，请检查驱动、容器 GPU 透传及编码器支持"
            except (OSError, subprocess.TimeoutExpired):
                available, reason = False, "GPU 试编码未成功，请检查 FFmpeg、驱动及 GPU 连接"
            codecs[kind] = {"available": available, "encoder": encoder, "reason": reason}
        available = any(item["available"] for item in codecs.values())
        name = "NVIDIA GPU" if available else ""
        if available:
            try:
                result = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=3)
                if result.returncode == 0 and result.stdout.strip():
                    name = result.stdout.splitlines()[0].strip()
            except (OSError, subprocess.TimeoutExpired):
                pass
        _capabilities = {"available": available, "name": name, "codecs": codecs}
        _checked_at = time.monotonic()
        return _capabilities


def select_encoder(requested, format_type):
    # Old saved jobs stay on CPU when they have no device selection.
    if requested == "cpu":
        return "cpu", ""
    kind = "h265" if format_type == "h265" else "h264"
    capability = get_gpu_capabilities()["codecs"][kind]
    if capability["available"]:
        return "nvidia", ""
    if requested == "nvidia":
        raise ValueError(capability["reason"])
    return "cpu", capability["reason"]


def is_gpu_error(error):
    error = error.lower()
    return any(marker in error for marker in (
        "cannot load libcuda", "cannot load nvcuda", "cannot load libnvidia-encode",
        "cannot load nvencodeapi", "no nvenc capable devices", "no capable devices found",
        "driver does not support the required nvenc", "minimum required nvidia driver",
        "openencodesessionex failed", "initializeencoder failed", "cuda_error",
        "unsupported device", "no encode device", "failed to initialise nvenc",
    ))


def get_gpu_metrics(force=False):
    global _metrics, _metrics_at
    if not get_gpu_capabilities()["available"]:
        return None
    with _metrics_lock:
        if not force and time.monotonic() - _metrics_at < 3:
            return _metrics
        try:
            result = _run([
                "nvidia-smi", "--query-gpu=name,utilization.gpu,utilization.encoder,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ], timeout=2)
            row = next(csv.reader(result.stdout.splitlines())) if result.returncode == 0 else []
            def number(value):
                try:
                    parsed = float(value.strip())
                    return parsed if math.isfinite(parsed) and parsed >= 0 else None
                except ValueError:
                    return None
            _metrics = {
                "name": row[0].strip(), "utilization_percent": number(row[1]),
                "encoder_percent": number(row[2]), "memory_used_mb": number(row[3]),
                "memory_total_mb": number(row[4]),
            } if len(row) == 5 else None
        except (OSError, subprocess.TimeoutExpired, StopIteration):
            _metrics = None
        _metrics_at = time.monotonic()
        return _metrics
