"""Measure selected encoder settings on representative clips, without creating jobs."""
import math
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time

from processVideo import _hidden_subprocess_kwargs, _probe_video, _tool_path, generate_ffmpeg_command


SAMPLE_SECONDS = 8.0
SAMPLE_TIMEOUT_SECONDS = 120
BATCH_SAMPLE_LIMIT = 3


def optional_duration(value):
    """Browser metadata is advisory; production still probes every source."""
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and 0 < duration < 1e9 else None


def project_batch(files, measured):
    """Extrapolate untested files, keeping them visibly distinct from trials."""
    trials = list(measured.values())
    duration_total = sum(t["duration_sec"] for t in trials)
    original_total = sum(t["original_bytes"] for t in trials)
    rates = {key: sum(t[key] for t in trials) / duration_total
             for key in ("estimated_bytes", "min_bytes", "max_bytes")}
    ratios = {key: sum(t[key] for t in trials) / original_total for key in rates}
    results = []
    for index, item in enumerate(files):
        if index in measured:
            results.append(measured[index])
            continue
        duration = optional_duration(item.get("duration_sec"))
        unit = duration if duration else item["size"]
        basis = rates if duration else ratios
        expected = basis["estimated_bytes"] * unit
        # Different recordings can have very different complexity, even at the
        # same resolution. Show a wider reference band for files we did not encode.
        low = min(expected * .6, min(t["min_bytes"] / (t["duration_sec"] if duration
                  else t["original_bytes"]) for t in trials) * unit * .75)
        high = max(expected * 1.6, max(t["max_bytes"] / (t["duration_sec"] if duration
                   else t["original_bytes"]) for t in trials) * unit * 1.25)
        results.append({"path": item["path"], "original_bytes": item["size"],
                        "duration_sec": duration, "estimated_bytes": max(1, round(expected)),
                        "min_bytes": max(1, round(low)), "max_bytes": max(1, round(high)),
                        "sampled": False, "estimate_method": "duration" if duration else "size_ratio",
                        "samples": [], "sample_count": 0})
    return results


class EstimateCanceled(Exception):
    pass


def media_duration(probe):
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise ValueError("文件不包含可编码的视频流")
    for raw in (video.get("duration"), (video.get("tags") or {}).get("DURATION"),
                (probe.get("format") or {}).get("duration")):
        try:
            parts = str(raw).split(":")
            duration = sum(float(part) * 60 ** i for i, part in enumerate(reversed(parts)))
            if math.isfinite(duration) and duration > 0:
                return duration
        except (ValueError, TypeError):
            pass
    raise ValueError("无法读取视频时长，不能可靠预估大小")


def sample_plan(duration):
    # A full trial gives a useful estimate for short videos without extrapolation.
    if duration <= SAMPLE_SECONDS * 3:
        return [(None, None)]
    return [((duration - SAMPLE_SECONDS) * fraction, SAMPLE_SECONDS)
            for fraction in (0.1, 0.5, 0.9)]


def run_sample(command, canceled, terminate):
    if canceled.is_set():
        raise EstimateCanceled()
    args = shlex.split(command, posix=True)
    progress = args.index("-progress")
    del args[progress:progress + 2]
    args[1:1] = ["-v", "error", "-xerror"]
    kwargs = _hidden_subprocess_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    # Drain diagnostics to a file: waiting on an undrained PIPE can deadlock FFmpeg.
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=log, **kwargs)
        deadline = time.monotonic() + SAMPLE_TIMEOUT_SECONDS
        try:
            while proc.poll() is None:
                if canceled.wait(0.2):
                    raise EstimateCanceled()
                if time.monotonic() >= deadline:
                    raise TimeoutError("试编码超时，请尝试更快的编码策略")
            if canceled.is_set():
                raise EstimateCanceled()
            if proc.returncode:
                log.seek(max(0, log.tell() - 4096))
                raise RuntimeError(log.read().decode("utf-8", "replace").strip() or "试编码失败")
        finally:
            if proc.poll() is None:
                terminate(proc)


def prepare_preview(output, index, duration, canceled, terminate):
    """Keep the encoded bitstream intact while making a seekable browser sample."""
    preview = output.parent / f"preview-{index}.mp4"
    thumbnail = output.parent / f"thumbnail-{index}.jpg"
    args = [_tool_path("ffmpeg"), "-nostdin", "-y", "-progress", "pipe:2", "-i", str(output),
            "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart"]
    video = next(s for s in _probe_video(str(output))["streams"] if s["codec_type"] == "video")
    if video.get("codec_name") == "hevc":
        args += ["-tag:v", "hvc1"]
    run_sample(shlex.join(args + [str(preview)]), canceled, terminate)
    run_sample(shlex.join([_tool_path("ffmpeg"), "-nostdin", "-y", "-progress", "pipe:2",
        "-ss", str(min(1, duration / 2)), "-i", str(preview), "-frames:v", "1",
        "-vf", "scale=320:-2", "-q:v", "3", "-update", "1", str(thumbnail)]), canceled, terminate)


def estimate_file(source, work_dir, settings, encoder_device, encoder_threads,
                  canceled, terminate, on_sample, retain_previews=False):
    if canceled.is_set():
        raise EstimateCanceled()
    duration = media_duration(_probe_video(str(source)))
    plan = sample_plan(duration)
    rates = []
    samples = []
    full_size = None
    for index, (start, length) in enumerate(plan):
        if canceled.is_set():
            raise EstimateCanceled()
        on_sample(index, len(plan))
        ext = ".mkv" if settings["format_type"] == "mkv" else ".mp4"
        output = Path(work_dir) / (f"sample-{index}" + ext)
        command = generate_ffmpeg_command(
            str(source), str(output),
            interval=settings["interval"], watermark_duration_seconds=settings["duration"],
            crf=settings["crf"], fixed_watermark_enabled=settings["fixed_watermark_enabled"],
            fixed_watermark_pos=settings["fixed_watermark_pos"],
            dynamic_watermark_enabled=settings["dynamic_watermark_enabled"],
            format_type=settings["format_type"],
            fixed_watermark_path=settings.get("fixed_watermark_path"),
            dynamic_watermark_path=settings.get("dynamic_watermark_path"),
            fixed_watermark_width_ratio=settings["fixed_watermark_size"] / 100,
            dynamic_watermark_width_ratio=settings["dynamic_watermark_size"] / 100,
            encoder_threads=encoder_threads,
            filter_threads=max(1, min(8, (encoder_threads + 7) // 8)),
            encoder_preset=settings["encoder_preset"], encoder_device=encoder_device,
            gpu_quality=settings["gpu_quality"], gpu_preset=settings["gpu_preset"],
            sample_start=start, sample_duration=length,
        )
        if not command:
            raise ValueError("无法生成试编码命令")
        try:
            run_sample(command, canceled, terminate)
            encoded_duration = media_duration(_probe_video(str(output)))
            if length and encoded_duration < length * 0.8:
                raise ValueError("试编码片段不完整，无法可靠预估大小")
            size = output.stat().st_size
            rates.append(size / encoded_duration)
            if length is None:
                full_size = size
            if retain_previews:
                prepare_preview(output, index, encoded_duration, canceled, terminate)
                samples.append({"start_sec": start or 0, "duration_sec": encoded_duration})
        finally:
            output.unlink(missing_ok=True)
    expected = full_size if full_size is not None else sum(rates) / len(rates) * duration
    # This is a reference range, not a statistical confidence interval. Allow at
    # least 25% for unseen scenes, fresh GOPs and clip container overhead.
    low = expected * 0.95 if full_size is not None else min(expected * 0.75, min(rates) * duration * 0.9)
    high = expected * 1.05 if full_size is not None else max(expected * 1.25, max(rates) * duration * 1.1)
    codec = "hevc" if settings["format_type"] == "h265" else "h264"
    encoder = ("hevc_nvenc" if codec == "hevc" else "h264_nvenc") if encoder_device == "nvidia" else (
        "libx265" if codec == "hevc" else "libx264")
    return {
        "estimated_bytes": round(expected), "min_bytes": max(1, round(low)), "max_bytes": round(high),
        "duration_sec": duration, "sample_count": len(plan), "full_trial": full_size is not None,
        "encoder_device": encoder_device, "encoder_name": encoder,
        "samples": samples,
    }
