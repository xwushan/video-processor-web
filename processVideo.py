"""
视频处理模块 - 生成 FFmpeg 命令
支持固定水印、动态间隔水印、多种导出格式、水印透明度
"""
import os
import shutil
import sys
import random
import json
import subprocess

try:
    from PIL import Image
except ImportError:
    Image = None

WATERMARK_WIDTH_RATIO = 0.0625
MIN_WATERMARK_WIDTH = 32
MAX_WATERMARK_WIDTH = 320
MAX_WATERMARK_HEIGHT_RATIO = 0.18


def _get_resource_path(relative_path):
    """获取资源文件的绝对路径，兼容 PyInstaller 打包"""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def _app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _find_ffmpeg_dir():
    """查找 FFmpeg 目录：优先程序目录下的 ffmpeg/bin，其次系统 PATH。"""
    app_dir = _app_dir()
    for directory in (
        os.path.join(app_dir, 'ffmpeg', 'bin'),
        os.path.join(app_dir, 'ffmpeg'),
    ):
        ffmpeg_exe = os.path.join(directory, 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg')
        if os.path.exists(ffmpeg_exe):
            return directory

    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return os.path.dirname(ffmpeg_path)
    return ''


def _tool_path(tool_name):
    ffmpeg_dir = _find_ffmpeg_dir()
    exe_name = f"{tool_name}.exe" if sys.platform == "win32" else tool_name
    return os.path.join(ffmpeg_dir, exe_name) if ffmpeg_dir else exe_name


def _hidden_subprocess_kwargs():
    if sys.platform != "win32":
        return {"creationflags": 0, "startupinfo": None}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


def _probe_video(video_path):
    ffprobe_exe = _tool_path("ffprobe")
    result = subprocess.run(
        [
            ffprobe_exe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, **_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or f"ffprobe exited {result.returncode}")
    return json.loads(result.stdout or "{}")


def random_x_y(width, height, watermark_w=120, watermark_h=40, margin_x=100, margin_y=100):
    """生成随机的x, y坐标"""
    max_x = max(0, width - watermark_w)
    max_y = max(0, height - watermark_h)
    min_x = min(margin_x, max_x)
    min_y = min(margin_y, max_y)
    high_x = max(min_x, max_x - min(margin_x, max_x))
    high_y = max(min_y, max_y - min(margin_y, max_y))
    x = random.randint(min_x, high_x)
    y = random.randint(min_y, high_y)
    return x, y


def _get_image_size(image_path):
    if Image is not None and image_path and os.path.exists(image_path):
        try:
            with Image.open(image_path) as img:
                return img.width, img.height
        except Exception:
            pass
    return 120, 40


def _scaled_watermark_size(video_width, video_height, image_path=None, width_ratio=None):
    src_w, src_h = _get_image_size(image_path)
    src_w = max(src_w, 1)
    src_h = max(src_h, 1)

    try:
        ratio = float(width_ratio) if width_ratio is not None else WATERMARK_WIDTH_RATIO
    except (TypeError, ValueError):
        ratio = WATERMARK_WIDTH_RATIO
    ratio = max(0.02, min(0.20, ratio))
    target_w = int(video_width * ratio)
    target_w = max(MIN_WATERMARK_WIDTH, target_w)
    target_w = min(target_w, MAX_WATERMARK_WIDTH, max(1, int(video_width * 0.35)))
    target_h = max(1, int(target_w * src_h / src_w))

    max_h = max(1, int(video_height * MAX_WATERMARK_HEIGHT_RATIO))
    if target_h > max_h:
        target_h = max_h
        target_w = max(1, int(target_h * src_w / src_h))

    return max(1, target_w), max(1, target_h)


def _prepare_watermark_source(filter_parts, input_label, prefix, label_seq,
                              target_w, target_h, watermark_opacity):
    scaled_label = f"{prefix}_scaled{label_seq}"
    # Let FFmpeg derive the height from the source image so every watermark
    # keeps its original aspect ratio, even when image metadata is unavailable.
    filter_parts.append(f'[{input_label}]scale={target_w}:-2:flags=lanczos[{scaled_label}]')
    label_seq += 1

    if watermark_opacity < 1.0:
        opacity_label = f"{prefix}_opaque{label_seq}"
        filter_parts.append(
            f'[{scaled_label}]format=rgba,colorchannelmixer=aa={watermark_opacity}[{opacity_label}]'
        )
        label_seq += 1
        return opacity_label, label_seq

    return scaled_label, label_seq


def _safe_float(value):
    if value in (None, "", "N/A"):
        return None
    value = str(value).strip()
    if ":" in value:
        try:
            total = 0.0
            for part in value.split(":"):
                total = total * 60 + float(part)
            return total
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe_duration(probe, video_stream):
    tags = video_stream.get("tags") or {}
    fmt = probe.get("format") or {}
    fmt_tags = fmt.get("tags") or {}
    candidates = (
        video_stream.get("duration"),
        tags.get("DURATION"),
        tags.get("duration"),
        fmt.get("duration"),
        fmt_tags.get("DURATION"),
        fmt_tags.get("duration"),
    )
    for value in candidates:
        parsed = _safe_float(value)
        if parsed and parsed > 0:
            return int(parsed)
    return 0


def parse_watermark_position(pos_str, width, height, watermark_w=120, watermark_h=40):
    """
    解析水印位置字符串，返回 (x, y) 坐标。
    支持格式：
      - "random" -> 随机位置
      - "top-left" / "top-right" / "bottom-left" / "bottom-right" / "center" -> 预设位置
      - "x,y" 如 "200,300" -> 自定义坐标
      - "W-w-60,60" 等表达式 -> 支持 ffmpeg overlay 表达式
    """
    margin = 60

    def clamp_xy(x, y):
        max_x = max(0, width - watermark_w)
        max_y = max(0, height - watermark_h)
        return (max(0, min(int(x), max_x)), max(0, min(int(y), max_y)))

    if pos_str == "random":
        return random_x_y(width, height, watermark_w, watermark_h)
    elif pos_str == "top-left":
        return clamp_xy(margin, margin)
    elif pos_str == "top-right":
        return clamp_xy(width - watermark_w - margin, margin)
    elif pos_str == "bottom-left":
        return clamp_xy(margin, height - watermark_h - margin)
    elif pos_str == "bottom-right":
        return clamp_xy(width - watermark_w - margin, height - watermark_h - margin)
    elif pos_str == "center":
        return clamp_xy((width - watermark_w) // 2, (height - watermark_h) // 2)
    elif pos_str.startswith("ratio:"):
        try:
            ratio_x, ratio_y = pos_str[6:].split(",", 1)
            return clamp_xy(width * float(ratio_x), height * float(ratio_y))
        except (ValueError, TypeError):
            return clamp_xy(margin, margin)
    elif "," in pos_str:
        parts = pos_str.split(",")
        try:
            x_val = int(parts[0])
        except ValueError:
            x_val = parts[0].strip()
        try:
            y_val = int(parts[1])
        except ValueError:
            y_val = parts[1].strip()
        if isinstance(x_val, int) and isinstance(y_val, int):
            return clamp_xy(x_val, y_val)
        return (x_val, y_val)
    else:
        return random_x_y(width, height)


def _encoder_preset(env_name, default, override=None):
    allowed = {
        "ultrafast", "superfast", "veryfast", "faster", "fast",
        "medium", "slow", "slower", "veryslow", "placebo",
    }
    if override:
        selected = str(override).strip().lower()
        if selected in allowed:
            return selected
    value = os.getenv(env_name, default).strip().lower()
    return value if value in allowed else default


def _get_format_config(format_type, encoder_preset=None):
    """
    根据导出格式类型返回对应的编码器配置
    format_type: "h264" | "h265" | "mkv"
    """
    configs = {
        "h264": {
            "codec": "libx264",
            "ext": ".mp4",
            "extra": f'-preset {_encoder_preset("VIDEO_PROCESSOR_H264_PRESET", "veryfast", encoder_preset)} -profile:v high',
        },
        "h265": {
            "codec": "libx265",
            "ext": ".mp4",
            "extra": f'-preset {_encoder_preset("VIDEO_PROCESSOR_H265_PRESET", "fast", encoder_preset)} -tag:v hvc1',
        },
        "mkv": {
            "codec": "libx264",
            "ext": ".mkv",
            "extra": f'-preset {_encoder_preset("VIDEO_PROCESSOR_MKV_PRESET", "veryfast", encoder_preset)}',
        },
    }
    return configs.get(format_type, configs["h264"])



def _get_video_codec_args(format_type, quality, encoder_preset=None):
    """Return CPU video encoder args."""
    common = "-pix_fmt yuv420p"
    fmt = _get_format_config(format_type, encoder_preset)
    return f'-c:v {fmt["codec"]} -crf {quality} {fmt["extra"]} {common}'


def _bounded_int(value, default, min_value, max_value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(min_value, min(max_value, result))


def _dynamic_watermark_expressions(interval, hold_seconds, video_width, video_height,
                                   watermark_w, watermark_h):
    """Return constant-size FFmpeg expressions for time-segmented watermark positions."""
    max_x = max(0, video_width - watermark_w)
    max_y = max(0, video_height - watermark_h)
    margin_x = min(100, max_x // 4)
    margin_y = min(100, max_y // 4)
    range_x = max(0, max_x - margin_x * 2)
    range_y = max(0, max_y - margin_y * 2)
    slot = f"floor(t/{interval})"
    x_expr = f"{margin_x}+trunc({range_x}*abs(sin(({slot}+1)*12.9898)))"
    y_expr = f"{margin_y}+trunc({range_y}*abs(sin(({slot}+1)*78.233)))"
    enable_expr = f"lt(mod(t,{interval}),{hold_seconds})"
    return x_expr, y_expr, enable_expr


def generate_ffmpeg_command(video_path, output_path, interval=60,
                            watermark_duration_seconds=3, crf=30,
                            fixed_watermark_enabled=True, fixed_watermark_pos="top-right",
                            dynamic_watermark_enabled=True,
                            format_type="h264", watermark_opacity=1.0,
                            fixed_watermark_path=None, dynamic_watermark_path=None,
                            fixed_watermark_width_ratio=None, dynamic_watermark_width_ratio=None,
                            encoder_threads=None, filter_threads=1,
                            encoder_preset=None):
    """
    生成带有水印的FFmpeg命令。

    参数:
        video_path: 输入视频路径
        output_path: 输出视频路径
        interval: 动态水印间隔（秒）
        watermark_duration_seconds: 动态水印持续时长（秒）
        crf: 压缩率 (仅 H.264/H.265)
        fixed_watermark_enabled: 是否启用固定水印 (rt.png)
        fixed_watermark_pos: 固定水印位置
        dynamic_watermark_enabled: 是否启用动态水印 (dt.png)
        format_type: 导出格式 "h264" / "h265" / "mkv"
        watermark_opacity: 水印透明度 0.0 ~ 1.0
        fixed_watermark_width_ratio: 固定水印宽度占视频宽度比例
        dynamic_watermark_width_ratio: 动态水印宽度占视频宽度比例
    """
    watermarks_rt_path = fixed_watermark_path or _get_resource_path("rt.png")
    watermarks_dt_path = dynamic_watermark_path or _get_resource_path("dt.png")
    interval = _bounded_int(interval, 60, 1, 86400)
    watermark_duration_seconds = _bounded_int(watermark_duration_seconds, 3, 1, 86400)
    encoder_threads = _bounded_int(encoder_threads, 0, 0, 64)
    filter_threads = _bounded_int(filter_threads, 1, 1, 8)

    ffmpeg_exe = _tool_path("ffmpeg")

    # 获取视频信息
    try:
        probe = _probe_video(video_path)
    except Exception:
        return None
    video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)

    if video_stream is None:
        return None

    duration = _probe_duration(probe, video_stream)
    if duration <= 0:
        duration = max(int(interval) + int(watermark_duration_seconds), int(watermark_duration_seconds), 10)
    width = int(video_stream.get('width') or video_stream.get('coded_width') or 1920)
    height = int(video_stream.get('height') or video_stream.get('coded_height') or 1080)

    # 获取格式配置
    fmt = _get_format_config(format_type, encoder_preset)

    # 修正输出路径扩展名
    if not output_path.lower().endswith(fmt["ext"]):
        base = os.path.splitext(output_path)[0]
        output_path = base + fmt["ext"]

    filter_complex_parts = []
    input_files = f'-i "{video_path}"'
    current_label = "0:v"
    label_seq = 1
    next_input_index = 1

    # --- 固定水印 (rt.png) ---
    if fixed_watermark_enabled and os.path.exists(watermarks_rt_path):
        input_files += f' -i "{watermarks_rt_path}"'
        rt_w, rt_h = _scaled_watermark_size(
            width, height, watermarks_rt_path, fixed_watermark_width_ratio
        )
        rt_x, rt_y = parse_watermark_position(fixed_watermark_pos, width, height, rt_w, rt_h)
        rt_x_str = str(rt_x) if isinstance(rt_x, int) else rt_x
        rt_y_str = str(rt_y) if isinstance(rt_y, int) else rt_y
        rt_input_idx = next_input_index
        next_input_index += 1
        rt_source, label_seq = _prepare_watermark_source(
            filter_complex_parts, f"{rt_input_idx}:v", "rt", label_seq,
            rt_w, rt_h, watermark_opacity,
        )

        next_label = f"v{label_seq}"
        label_seq += 1
        filter_complex_parts.append(
            f'[{current_label}][{rt_source}]overlay=x={rt_x_str}:y={rt_y_str}[{next_label}]'
        )
        current_label = next_label

    # --- 动态水印 (dt.png) ---
    if dynamic_watermark_enabled and os.path.exists(watermarks_dt_path):
        input_files += f' -i "{watermarks_dt_path}"'
        dt_input_idx = next_input_index
        next_input_index += 1
        dt_w, dt_h = _scaled_watermark_size(
            width, height, watermarks_dt_path, dynamic_watermark_width_ratio
        )
        dt_source, label_seq = _prepare_watermark_source(
            filter_complex_parts, f"{dt_input_idx}:v", "dt", label_seq,
            dt_w, dt_h, watermark_opacity,
        )

        x_expr, y_expr, enable_expr = _dynamic_watermark_expressions(
            interval, watermark_duration_seconds, width, height, dt_w, dt_h,
        )
        next_label = f"v{label_seq}"
        label_seq += 1
        filter_complex_parts.append(
            f"[{current_label}][{dt_source}]overlay=x='{x_expr}':y='{y_expr}':enable='{enable_expr}'[{next_label}]"
        )
        current_label = next_label

    # Build either the watermark filter graph or a direct video mapping when
    # both watermark switches are off.
    if filter_complex_parts:
        filter_complex_str = ";".join(filter_complex_parts)
        filter_args = (
            f'-filter_threads {filter_threads} -filter_complex_threads {filter_threads} '
            f'-filter_complex "{filter_complex_str}" -map [{current_label}] '
        )
    else:
        filter_args = '-map 0:v:0 '

    # 构建最终命令
    video_codec_args = _get_video_codec_args(format_type, crf, encoder_preset)
    ffmpeg_command = (
        f'"{ffmpeg_exe}" -hide_banner -nostdin -nostats -progress pipe:2 -y {input_files} '
        f'{filter_args}-map 0:a? '
        f'{video_codec_args} '
        f'{f"-threads {encoder_threads} " if encoder_threads else ""}'
        f'-max_muxing_queue_size 1024 -c:a aac -b:a 128k '
        f'"{output_path}"'
    )

    return ffmpeg_command
