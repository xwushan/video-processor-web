import base64
import binascii
import hmac
import hashlib
import json
import math
import os
import queue
import re
import shlex
import signal
import shutil
import ssl
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager, contextmanager
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request as UrlRequest, urlopen

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import psutil

try:
    import certifi
except ImportError:  # pragma: no cover - runtime fallback for older deployments
    certifi = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processVideo import _find_ffmpeg_dir, generate_ffmpeg_command  # noqa: E402
from web_app.encoding import (  # noqa: E402
    DEFAULT_ENCODER_DEVICE, GPU_MAX_CONCURRENT, GPU_CPU_THREADS,
    get_gpu_capabilities, get_gpu_metrics, select_encoder, is_gpu_error,
)
from web_app.size_estimation import EstimateCanceled, estimate_file  # noqa: E402


APP_ROOT = Path(os.getenv("VIDEO_PROCESSOR_ROOT", "/data/video-processor"))
UPLOAD_DIR = APP_ROOT / "uploads"
OUTPUT_DIR = APP_ROOT / "outputs"
TMP_DIR = APP_ROOT / "tmp"
WATERMARK_DIR = APP_ROOT / "watermarks"
RESUMABLE_UPLOAD_DIR = TMP_DIR / "resumable_uploads"
DB_PATH = APP_ROOT / "video_processor.db"

FILE_RETENTION_DAYS = int(os.getenv("VIDEO_PROCESSOR_FILE_RETENTION_DAYS", "14"))
RECORD_RETENTION_DAYS = int(os.getenv("VIDEO_PROCESSOR_RECORD_RETENTION_DAYS", "90"))
MAX_UPLOAD_MB = int(os.getenv("VIDEO_PROCESSOR_MAX_UPLOAD_MB", "8192"))
CPU_RESERVE_RATIO = 0.20
GPU_TARGET_PERCENT = 80
MAX_CONCURRENT_VIDEO_TASKS = 8
MAX_ENCODER_THREADS_PER_TASK = 64
ENCODER_PRESETS = {"ultrafast", "veryfast", "fast", "medium", "slow"}
RESOURCE_SAMPLE_INTERVAL_SECONDS = 3.0
RESOURCE_WARMUP_SECONDS = 5.0
ARCHIVE_CHUNK_SIZE = 4 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
ARCHIVE_RETENTION_HOURS = max(1, int(os.getenv("VIDEO_PROCESSOR_ARCHIVE_RETENTION_HOURS", "24")))
UPLOAD_SESSION_RETENTION_HOURS = max(1, int(os.getenv("VIDEO_PROCESSOR_UPLOAD_SESSION_RETENTION_HOURS", "24")))
MIN_FREE_DISK_GB = max(0, float(os.getenv("VIDEO_PROCESSOR_MIN_FREE_GB", "20")))
MIN_FREE_DISK_BYTES = int(MIN_FREE_DISK_GB * 1024 * 1024 * 1024)
MAX_FFMPEG_ATTEMPTS = 2
WECHAT_WEBHOOK = os.getenv("VIDEO_PROCESSOR_WECHAT_WEBHOOK", "").strip()
PUBLIC_WEB_URL = os.getenv("VIDEO_PROCESSOR_PUBLIC_URL", "").strip().rstrip("/")
AUTH_USER = os.getenv("VIDEO_PROCESSOR_AUTH_USER", "admin").strip() or "admin"
AUTH_PASSWORD = os.getenv("VIDEO_PROCESSOR_AUTH_PASSWORD", "")
AUTH_COOKIE_NAME = "video_processor_session"
AUTH_SESSION_TTL_SECONDS = 12 * 60 * 60
APP_VERSION = "1.3.1"
APP_REVISION = os.getenv("VIDEO_PROCESSOR_REVISION", "unknown")

THIS_DIR = Path(__file__).resolve().parent
STATIC_DIR = THIS_DIR / "static"
TEMPLATE_PATH = THIS_DIR / "templates" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cleanup_expired_files()
    get_gpu_capabilities()
    ensure_worker()
    recover_unfinished_jobs()
    threading.Thread(target=cleanup_worker, daemon=True).start()
    yield
    with upload_sessions_lock:
        for event in estimation_tasks.values():
            event.set()


app = FastAPI(title="视频处理器", version=APP_VERSION, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Serialize short job state transitions, while encoding remains outside this lock.
job_control_lock = threading.RLock()


def serialized_job_control(function):
    @wraps(function)
    def controlled(*args, **kwargs):
        with job_control_lock:
            return function(*args, **kwargs)
    return controlled


def auth_public_path(path: str) -> bool:
    return (
        path == "/"
        or path == "/health"
        or path == "/api/config"
        or path == "/api/login"
        or path == "/api/logout"
        or path.startswith("/static/")
        or path.startswith("/assets/")
    )


def make_auth_token(username: str) -> str:
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    signature = hmac.new(AUTH_PASSWORD.encode("utf-8"), payload.encode("utf-8"), "sha256").hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")


def verify_auth_token(token: str) -> bool:
    if not token:
        return False
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, timestamp, signature = decoded.rsplit(":", 2)
        issued_at = int(timestamp)
    except (binascii.Error, UnicodeError, ValueError):
        return False
    if username != AUTH_USER or not 0 <= time.time() - issued_at <= AUTH_SESSION_TTL_SECONDS:
        return False
    payload = f"{username}:{timestamp}"
    expected = hmac.new(AUTH_PASSWORD.encode("utf-8"), payload.encode("utf-8"), "sha256").hexdigest()
    return hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii"))


def verify_basic_auth(authorization: str) -> bool:
    if not authorization.startswith("Basic "):
        return False
    try:
        username, password = base64.b64decode(authorization[6:]).decode("utf-8").split(":", 1)
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    return hmac.compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8")) and hmac.compare_digest(password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8"))


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    """Protect the internal tool when an administrator configures a password."""
    if not AUTH_PASSWORD or auth_public_path(request.url.path):
        return await call_next(request)
    if verify_auth_token(request.cookies.get(AUTH_COOKIE_NAME, "")):
        return await call_next(request)
    if verify_basic_auth(request.headers.get("authorization", "")):
        response = await call_next(request)
        response.set_cookie(
            AUTH_COOKIE_NAME,
            make_auth_token(AUTH_USER),
            max_age=AUTH_SESSION_TTL_SECONDS,
            httponly=True,
            samesite="lax",
        )
        return response
    return JSONResponse(
        {"detail": "需要登录后才能访问视频处理器"},
        status_code=401,
    )

db_lock = threading.RLock()
db_schema_ready = False
job_queue: "queue.Queue[str]" = queue.Queue()
worker_lock = threading.Lock()
worker_thread: Optional[threading.Thread] = None
active_processes: dict[str, set[subprocess.Popen]] = {}
active_processes_lock = threading.Lock()
job_cancel_events: dict[str, threading.Event] = {}
job_cancel_lock = threading.Lock()
archive_tasks: dict[str, dict] = {}
archive_tasks_lock = threading.Lock()
upload_sessions_lock = threading.Lock()
completing_upload_sessions: set[str] = set()
# Jobs already run one batch at a time. A trial shares this gate so it cannot
# consume another NVENC session while the resource scheduler is encoding.
encoding_work_lock = threading.Lock()
estimation_tasks: dict[str, threading.Event] = {}
resource_metrics_lock = threading.Lock()
resource_metrics_previous: Optional[tuple[float, int, int]] = None
TERMINAL_JOB_STATUS = {"done", "error", "canceled"}


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def wecom_config_error() -> str:
    if not WECHAT_WEBHOOK:
        return "未配置 VIDEO_PROCESSOR_WECHAT_WEBHOOK"
    try:
        address = urlsplit(WECHAT_WEBHOOK)
    except ValueError:
        return "企业微信 webhook 地址格式不正确"
    if address.scheme != "https" or address.netloc != "qyapi.weixin.qq.com" or address.path != "/cgi-bin/webhook/send":
        return "企业微信 webhook 地址格式不正确"
    if not parse_qs(address.query).get("key", [""])[0].strip():
        return "企业微信 webhook 缺少完整 key，请检查宝塔环境变量是否被截断"
    return ""


def https_ssl_context() -> ssl.SSLContext:
    """Use certifi when installed so minimal Linux images have a reliable CA store."""
    custom_ca = os.getenv("SSL_CERT_FILE", "").strip()
    if custom_ca or certifi:
        return ssl.create_default_context(cafile=custom_ca or certifi.where())
    return ssl.create_default_context()


def post_wecom_payload(request: UrlRequest, context: ssl.SSLContext) -> dict:
    with urlopen(request, timeout=8, context=context) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def check_wecom_response(response_data: dict) -> tuple[bool, str]:
    if response_data.get("errcode", 0) != 0:
        return False, f"企业微信拒绝通知：{response_data.get('errmsg', response_data)}"
    return True, "企业微信通知已发送"


def send_wecom_notification(title: str, lines: list[str]) -> tuple[bool, str]:
    """Send a concise notification without exposing the configured webhook."""
    config_error = wecom_config_error()
    if config_error:
        print(f"Enterprise WeChat notification skipped: {config_error}", file=sys.stderr)
        return False, config_error
    content = "\n".join([f"### {title}", *[f"> {line}" for line in lines]])
    payload = json.dumps(
        {"msgtype": "markdown", "markdown": {"content": content}},
        ensure_ascii=True,
    ).encode("ascii")
    request = UrlRequest(
        WECHAT_WEBHOOK,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        ok, detail = check_wecom_response(post_wecom_payload(request, https_ssl_context()))
        if not ok:
            print(detail, file=sys.stderr)
        return ok, detail
    except HTTPError as exc:
        detail = f"企业微信接口 HTTP {exc.code}"
        print(detail, file=sys.stderr)
        return False, detail
    except (ssl.SSLCertVerificationError, URLError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            detail = "企业微信证书校验失败，请更新 CA 证书；内网自签 CA 可通过 SSL_CERT_FILE 配置"
        else:
            detail = f"企业微信接口连接失败：{reason}"
        print(detail, file=sys.stderr)
        return False, detail
    except TimeoutError:
        detail = "企业微信接口连接超时"
        print(detail, file=sys.stderr)
        return False, detail
    except (ValueError, OSError) as exc:
        detail = f"企业微信通知发送异常：{exc}"
        print(detail, file=sys.stderr)
        return False, detail


def notify_job_result(job_id: str, result: str, job: Optional[sqlite3.Row] = None) -> bool:
    """Notify only terminal task results: completed, completed with errors, or canceled."""
    if not WECHAT_WEBHOOK:
        return False
    job = job or get_job(job_id)
    if not job:
        return False
    if result == "canceled":
        title = "视频处理任务已取消"
        summary = "本次上传文件、成品文件和处理记录已删除"
    elif result == "error":
        title = "视频处理任务已完成，但存在失败"
        summary = f"成功 {job['done_count']} 个，失败 {job['failed_count']} 个"
    else:
        title = "视频处理任务已完成"
        summary = f"已成功处理 {job['done_count']} 个视频"
    lines = [
        f"处理时间：{now_iso()}",
        f"任务数量：{job['total_count']} 个视频",
        summary,
    ]
    if PUBLIC_WEB_URL and result != "canceled":
        lines.append(f"[打开处理记录]({PUBLIC_WEB_URL})")
    ok, detail = send_wecom_notification(title, lines)
    if not ok:
        print(f"Job {job_id} notification failed: {detail}", file=sys.stderr)
    return ok


def ensure_dirs() -> None:
    for path in (APP_ROOT, UPLOAD_DIR, OUTPUT_DIR, TMP_DIR, WATERMARK_DIR, RESUMABLE_UPLOAD_DIR):
        path.mkdir(parents=True, exist_ok=True)


def initialize_db_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0,
            done_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            worker_count INTEGER NOT NULL DEFAULT 1,
            settings_json TEXT NOT NULL,
            upload_dir TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS job_files (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            original_name TEXT NOT NULL,
            input_path TEXT NOT NULL,
            output_path TEXT NOT NULL,
            status TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0,
            resolution TEXT NOT NULL DEFAULT 'N/A',
            duration_sec REAL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            encoder_threads INTEGER NOT NULL DEFAULT 0,
            speed REAL NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(job_files)").fetchall()
    }
    if "encoder_threads" not in existing_columns:
        conn.execute("ALTER TABLE job_files ADD COLUMN encoder_threads INTEGER NOT NULL DEFAULT 0")
    if "speed" not in existing_columns:
        conn.execute("ALTER TABLE job_files ADD COLUMN speed REAL NOT NULL DEFAULT 0")
    if "attempts" not in existing_columns:
        conn.execute("ALTER TABLE job_files ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    for column in ("encoder_device", "encoder_name", "fallback_reason"):
        if column not in existing_columns:
            default = "cpu" if column == "encoder_device" else ""
            conn.execute(f"ALTER TABLE job_files ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
    conn.commit()


@contextmanager
def db_connect():
    """Open a short-lived SQLite connection and always release its file handle."""
    global db_schema_ready
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    if not db_schema_ready:
        with db_lock:
            if not db_schema_ready:
                initialize_db_schema(conn)
                db_schema_ready = True
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    global db_schema_ready
    ensure_dirs()
    with db_lock, db_connect() as conn:
        initialize_db_schema(conn)
        db_schema_ready = True


def sanitize_filename(name: str) -> str:
    name = Path(name or "video").name
    stem = re.sub(r'[\\/:*?"<>|$`;&!\r\n]+', "_", name).strip(" ._")
    return stem or f"file-{uuid.uuid4().hex[:8]}"


def sanitize_relative_upload_path(value: str, fallback_name: str) -> Path:
    raw = str(value or fallback_name).replace("\\", "/").strip()
    raw = re.sub(r"^[A-Za-z]:/?", "", raw).lstrip("/")
    parts: list[str] = []
    for part in raw.split("/"):
        if not part:
            continue
        if part in {".", ".."}:
            raise HTTPException(status_code=400, detail="文件夹路径不安全")
        parts.append(sanitize_filename(part))
    return Path(*parts) if parts else Path(sanitize_filename(fallback_name))


def bool_from_form(value: str) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on", "开启"}


def safe_encoder_preset(value: str) -> str:
    selected = str(value or "").strip().lower()
    return selected if selected in ENCODER_PRESETS else "veryfast"


def allocate_output_path(relative_path: Path, output_dir: Path, format_type: str, used: set[str]) -> Path:
    extension = {"h264": ".mp4", "h265": ".mp4", "mkv": ".mkv"}[format_type]
    candidate = (relative_path.with_suffix(extension) if len(relative_path.parts) > 1
                 else relative_path.with_name(f"{relative_path.stem}_processed{extension}"))
    original = candidate
    suffix = 2
    # Reserve names before any encoders run, including on case-insensitive clients.
    while candidate.as_posix().casefold() in used:
        candidate = original.with_name(f"{original.stem}_{suffix}{extension}")
        suffix += 1
    used.add(candidate.as_posix().casefold())
    return output_dir / candidate


def build_job_settings(
    fixed_watermark_enabled: str,
    dynamic_watermark_enabled: str,
    fixed_watermark_pos: str,
    interval: str,
    duration: str,
    crf: str,
    format_type: str,
    encoder_preset: str,
    fixed_watermark_size: str,
    dynamic_watermark_size: str,
    encoder_device: str = DEFAULT_ENCODER_DEVICE,
    gpu_quality: str = "26",
    gpu_preset: str = "p4",
    gpu_concurrency: str = str(GPU_MAX_CONCURRENT),
) -> dict:
    if encoder_device not in {"auto", "cpu", "nvidia"}:
        raise HTTPException(status_code=400, detail="编码设备参数无效")
    if encoder_device == "nvidia":
        try:
            select_encoder(encoder_device, format_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = {
        "fixed_watermark_enabled": bool_from_form(fixed_watermark_enabled),
        "dynamic_watermark_enabled": bool_from_form(dynamic_watermark_enabled),
        "fixed_watermark_pos": safe_watermark_position(fixed_watermark_pos),
        "interval": parse_int(interval, 60, 10, 600),
        "duration": parse_int(duration, 5, 1, 30),
        "crf": parse_int(crf, 32, 20, 51),
        "format_type": format_type if format_type in {"h264", "h265", "mkv"} else "h264",
        "encoder_preset": safe_encoder_preset(encoder_preset),
        "encoder_device": encoder_device,
        "gpu_quality": parse_int(gpu_quality, 26, 1, 51),
        "gpu_preset": gpu_preset if gpu_preset in {f"p{i}" for i in range(1, 8)} else "p4",
        # Accept legacy form fields, but all GPU jobs now use live resource scheduling.
        "gpu_concurrency": GPU_MAX_CONCURRENT,
        "gpu_concurrency_mode": "auto",
        "gpu_target_percent": GPU_TARGET_PERCENT,
        "fixed_watermark_size": parse_float(fixed_watermark_size, 6.25, 2, 20),
        "dynamic_watermark_size": parse_float(dynamic_watermark_size, 6.25, 2, 20),
        "fixed_watermark_path": None,
        "dynamic_watermark_path": None,
    }
    if settings["duration"] > settings["interval"]:
        settings["duration"] = settings["interval"]
    return settings


async def save_upload_file(
    upload: UploadFile,
    target_dir: Path,
    relative_path: Optional[Path] = None,
) -> Path:
    relative_path = relative_path or Path(sanitize_filename(upload.filename or "upload.bin"))
    original_target = target_dir / relative_path
    if not is_safe_child(original_target, target_dir):
        raise HTTPException(status_code=400, detail="文件保存路径不安全")
    original_target.parent.mkdir(parents=True, exist_ok=True)
    target = original_target
    index = 1
    while target.exists():
        target = original_target.with_name(f"{original_target.stem}_{index}{original_target.suffix}")
        index += 1
    size = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                ensure_disk_headroom(len(chunk))
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="上传文件超过限制")
                out.write(chunk)
    except Exception:
        delete_path_safely(target, target_dir)
        raise
    return target


def parse_int(value, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def parse_float(value, default: float, min_value: float, max_value: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def ffprobe_path() -> str:
    ffmpeg_dir = _find_ffmpeg_dir()
    name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    return str(Path(ffmpeg_dir) / name) if ffmpeg_dir else name


def hidden_subprocess_kwargs() -> dict:
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": startupinfo}


def get_cancel_event(job_id: str) -> threading.Event:
    with job_cancel_lock:
        event = job_cancel_events.get(job_id)
        if event is None:
            event = threading.Event()
            job_cancel_events[job_id] = event
        return event


def clear_cancel_event(job_id: str) -> threading.Event:
    event = get_cancel_event(job_id)
    event.clear()
    return event


def terminate_process(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is not None:
            return
        if sys.platform == "win32":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass


def register_process(job_id: str, proc: subprocess.Popen) -> None:
    with active_processes_lock:
        active_processes.setdefault(job_id, set()).add(proc)


def unregister_process(job_id: str, proc: subprocess.Popen) -> None:
    with active_processes_lock:
        processes = active_processes.get(job_id)
        if processes:
            processes.discard(proc)
        if processes is not None and not processes:
            active_processes.pop(job_id, None)


def terminate_job_processes(job_id: str) -> None:
    with active_processes_lock:
        processes = list(active_processes.get(job_id, set()))
    for proc in processes:
        terminate_process(proc)


def is_safe_child(path: Path, parent: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_parent = parent.resolve()
    except OSError:
        return False
    return resolved_path == resolved_parent or resolved_parent in resolved_path.parents


def cleanup_job_files(job_id: str, job: Optional[sqlite3.Row] = None) -> None:
    if job is None:
        job = get_job(job_id)
    # Signal an archive worker before removing any of its working files.
    with archive_tasks_lock:
        archive_tasks.pop(job_id, None)
    candidates: list[tuple[Path, Path]] = []
    if job:
        candidates.extend([
            (Path(job["upload_dir"]), UPLOAD_DIR),
            (Path(job["output_dir"]), OUTPUT_DIR),
        ])
    candidates.extend([
        (WATERMARK_DIR / job_id, WATERMARK_DIR),
        (TMP_DIR / f"{job_id}_outputs.zip", TMP_DIR),
        (TMP_DIR / f"{job_id}_outputs.zip.part", TMP_DIR),
        (TMP_DIR / job_id, TMP_DIR),
    ])
    for path, parent in candidates:
        delete_path_safely(path, parent)


def delete_path_safely(path: Path, parent: Path) -> bool:
    if not is_safe_child(path, parent) or not path.exists():
        return False
    for attempt in range(6):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return True
        except OSError:
            if attempt == 5:
                return False
            time.sleep(0.25)
    return False


def disk_free_bytes() -> int:
    return shutil.disk_usage(APP_ROOT).free


def ensure_disk_headroom(required_bytes: int = 0) -> None:
    required_bytes = max(0, int(required_bytes))
    if disk_free_bytes() - required_bytes < MIN_FREE_DISK_BYTES:
        raise HTTPException(
            status_code=507,
            detail=(
                f"服务器磁盘空间不足，需保留至少 {MIN_FREE_DISK_GB:g} GB 可用空间。"
                "请清理旧成品或压缩包后再试。"
            ),
        )


def archive_filename(job: sqlite3.Row) -> str:
    completed_at = re.sub(r"\D", "", job["updated_at"] or job["created_at"])
    return f"视频处理记录_{completed_at}.zip"


def archive_status_payload(state: dict) -> dict:
    return {
        "status": state["status"],
        "progress": round(float(state.get("progress", 0)), 1),
        "message": state.get("message", ""),
        "file_count": state.get("file_count", 0),
        "total_bytes": state.get("total_bytes", 0),
        "processed_bytes": state.get("processed_bytes", 0),
        "filename": state.get("filename", ""),
    }


def get_archive_status(job_id: str) -> Optional[dict]:
    with archive_tasks_lock:
        state = archive_tasks.get(job_id)
        return dict(state) if state else None


def archive_is_active(job_id: str) -> bool:
    with archive_tasks_lock:
        return job_id in archive_tasks


def update_archive_status(job_id: str, **fields) -> None:
    with archive_tasks_lock:
        state = archive_tasks.get(job_id)
        if state:
            state.update(fields)


def archive_arcname(output: Path, job: sqlite3.Row, file_id: str, used_names: set[str]) -> str:
    try:
        arcname = output.relative_to(Path(job["output_dir"])).as_posix()
    except ValueError:
        arcname = output.name
    if arcname in used_names:
        arc_path = Path(arcname)
        arcname = str(
            arc_path.with_name(f"{arc_path.stem}_{file_id[:6]}{arc_path.suffix}")
        ).replace("\\", "/")
    used_names.add(arcname)
    return arcname


def build_download_archive(job_id: str, job: sqlite3.Row, files: list[sqlite3.Row]) -> None:
    """Create an archive in the background and report progress by copied bytes."""
    zip_path = TMP_DIR / f"{job_id}_outputs.zip"
    temporary_path = TMP_DIR / f"{job_id}_outputs.zip.part"
    try:
        if not archive_is_active(job_id):
            return
        delete_path_safely(temporary_path, TMP_DIR)
        delete_path_safely(zip_path, TMP_DIR)
        processed_bytes = 0
        used_names: set[str] = set()
        # Video streams are already compressed. Storing them avoids a slow, ineffective second compression pass.
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, row in enumerate(files, start=1):
                if not archive_is_active(job_id):
                    return
                output = Path(row["output_path"])
                if not output.exists():
                    raise FileNotFoundError(f"成品文件不存在：{row['original_name']}")
                arcname = archive_arcname(output, job, row["id"], used_names)
                update_archive_status(
                    job_id,
                    message=f"正在打包 {index}/{len(files)}：{row['original_name']}",
                )
                with output.open("rb") as source, archive.open(arcname, "w", force_zip64=True) as target:
                    while chunk := source.read(ARCHIVE_CHUNK_SIZE):
                        if not archive_is_active(job_id):
                            return
                        target.write(chunk)
                        processed_bytes += len(chunk)
                        status = get_archive_status(job_id)
                        total_bytes = max(1, int(status.get("total_bytes", 1))) if status else 1
                        update_archive_status(
                            job_id,
                            processed_bytes=processed_bytes,
                            progress=min(99.9, processed_bytes * 100 / total_bytes),
                        )
        with archive_tasks_lock:
            if job_id not in archive_tasks:
                return
            temporary_path.replace(zip_path)
        update_archive_status(
            job_id,
            status="ready",
            progress=100,
            processed_bytes=processed_bytes,
            message="压缩包已准备完成，正在发起下载",
        )
    except Exception as exc:
        delete_path_safely(temporary_path, TMP_DIR)
        if archive_is_active(job_id):
            update_archive_status(job_id, status="error", message=f"打包失败：{exc}")
    finally:
        if not archive_is_active(job_id):
            delete_path_safely(temporary_path, TMP_DIR)


def remove_empty_upload_dir(path: Path) -> None:
    if not is_safe_child(path, UPLOAD_DIR) or not path.exists() or not path.is_dir():
        return
    try:
        path.rmdir()
    except OSError:
        pass


def safe_watermark_position(value: str) -> str:
    allowed = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}
    if value in allowed:
        return value
    if value.startswith("ratio:"):
        try:
            x_text, y_text = value[6:].split(",", 1)
            x = max(0.0, min(1.0, float(x_text)))
            y = max(0.0, min(1.0, float(y_text)))
            return f"ratio:{x:.6f},{y:.6f}"
        except (TypeError, ValueError):
            pass
    return "top-right"


def probe_video(path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                ffprobe_path(), "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"resolution": "N/A", "duration_sec": None}
    if result.returncode != 0:
        return {"resolution": "N/A", "duration_sec": None}
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"resolution": "N/A", "duration_sec": None}
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    width = stream.get("width") if stream else None
    height = stream.get("height") if stream else None
    resolution = f"{width}x{height}" if width and height else "N/A"
    duration = None
    for candidate in [
        stream.get("duration") if stream else None,
        (data.get("format") or {}).get("duration"),
    ]:
        try:
            if candidate:
                duration = float(candidate)
                break
        except (TypeError, ValueError):
            pass
    return {"resolution": resolution, "duration_sec": duration}


def recommend_worker_limit(files: list[sqlite3.Row], encoder_device="cpu", gpu_concurrency=None) -> int:
    """Let live CPU, memory and I/O sampling decide how far to scale up."""
    limit = GPU_MAX_CONCURRENT if encoder_device == "nvidia" else MAX_CONCURRENT_VIDEO_TASKS
    return max(1, min(len(files), limit))


def recommend_ffmpeg_threads(
    active_encoder_threads: int,
    cpu_percent: Optional[float] = None,
) -> int:
    """Predict the highest safe thread budget for the next FFmpeg encoder."""
    if cpu_percent is None or active_encoder_threads <= 0:
        return MAX_ENCODER_THREADS_PER_TASK

    cpu_target = 100 * (1 - CPU_RESERVE_RATIO)
    current_cpu = max(0.1, cpu_percent)
    full_speed_prediction = current_cpu * (
        1 + MAX_ENCODER_THREADS_PER_TASK / active_encoder_threads
    )
    if full_speed_prediction <= cpu_target:
        return MAX_ENCODER_THREADS_PER_TASK

    # Keep predicted aggregate load within the target using measured encoder load,
    # rather than distributing a fixed thread count by task count.
    available_threads = active_encoder_threads * (cpu_target / current_cpu - 1)
    return max(1, min(MAX_ENCODER_THREADS_PER_TASK, math.floor(available_threads)))


def recommend_filter_threads(encoder_threads: int) -> int:
    return max(1, min(8, (encoder_threads + 7) // 8))


def linux_iowait_percent(interval: float = 0.25) -> Optional[float]:
    """Measure Linux disk I/O wait. It is unavailable on non-Linux hosts."""
    if sys.platform != "linux":
        return None

    def read_cpu_times() -> tuple[int, int]:
        with open("/proc/stat", "r", encoding="utf-8") as source:
            fields = source.readline().split()[1:]
        values = [int(value) for value in fields]
        return sum(values), values[4] if len(values) > 4 else 0

    try:
        total_before, iowait_before = read_cpu_times()
        time.sleep(interval)
        total_after, iowait_after = read_cpu_times()
        total_delta = total_after - total_before
        if total_delta <= 0:
            return 0.0
        return (iowait_after - iowait_before) * 100 / total_delta
    except (OSError, ValueError, IndexError):
        return None


def resources_allow_next_task(
    active_count: int,
    reserved_output_bytes: int = 0,
    encoder_device: str = "cpu",
) -> tuple[bool, str, Optional[float]]:
    """Keep CPU, memory and storage headroom before increasing concurrency."""
    cpu_percent = psutil.cpu_percent(interval=0.25)
    memory_percent = psutil.virtual_memory().percent
    iowait_percent = linux_iowait_percent()
    reserve_threshold = CPU_RESERVE_RATIO * 100
    if cpu_percent >= 100 - reserve_threshold:
        return False, f"CPU 已使用 {cpu_percent:.0f}%", cpu_percent
    if memory_percent >= 100 - reserve_threshold:
        return False, f"内存已使用 {memory_percent:.0f}%", cpu_percent
    if disk_free_bytes() - max(0, reserved_output_bytes) < MIN_FREE_DISK_BYTES:
        return False, "可用磁盘空间不足，已保留安全余量", cpu_percent
    if iowait_percent is not None and iowait_percent >= reserve_threshold:
        return False, f"磁盘 I/O 等待 {iowait_percent:.0f}%", cpu_percent
    if encoder_device == "nvidia":
        # Request a fresh sample after warmup; UI polling may share a cached sample.
        gpu = get_gpu_metrics(force=True)
        encoder = gpu.get("encoder_percent") if gpu else None
        used = gpu.get("memory_used_mb") if gpu else None
        total = gpu.get("memory_total_mb") if gpu else None
        utilization = gpu.get("utilization_percent") if gpu else None
        if encoder is None or used is None or total is None or total <= 0:
            if active_count > 0:
                return False, "GPU 监控不可用，保持单任务处理", cpu_percent
            return True, "GPU 监控不可用，先以单任务处理", cpu_percent
        if encoder >= GPU_TARGET_PERCENT:
            return False, f"GPU 编码器已使用 {encoder:.0f}%", cpu_percent
        memory = used * 100 / total
        if memory >= GPU_TARGET_PERCENT:
            return False, f"GPU 显存已使用 {memory:.0f}%", cpu_percent
        if utilization is not None and utilization >= GPU_TARGET_PERCENT:
            return False, f"GPU 已使用 {utilization:.0f}%", cpu_percent
    return True, "资源充足", cpu_percent


def get_job(job_id: str) -> Optional[sqlite3.Row]:
    with db_lock, db_connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_job_files(job_id: str) -> list[sqlite3.Row]:
    with db_lock, db_connect() as conn:
        return conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? ORDER BY rowid", (job_id,)
        ).fetchall()


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    assignments = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [job_id]
    with db_lock, db_connect() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
        conn.commit()


def update_file(file_id: str, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [file_id]
    with db_lock, db_connect() as conn:
        conn.execute(f"UPDATE job_files SET {assignments} WHERE id = ?", values)
        conn.commit()


def refresh_job_progress(job_id: str) -> None:
    files = get_job_files(job_id)
    total = len(files)
    done = sum(1 for row in files if row["status"] == "done")
    failed = sum(1 for row in files if row["status"] == "error")
    progress = sum(float(row["progress"] or 0) for row in files) / max(total, 1)
    unfinished = any(row["status"] in {"queued", "running", "paused"} for row in files)
    if unfinished:
        progress = min(99.9, progress)
    update_job(
        job_id,
        total_count=total,
        done_count=done,
        failed_count=failed,
        progress=round(progress, 2),
    )


def summarize_ffmpeg_error(lines: list[str]) -> str:
    """Keep FFmpeg failures useful in the UI without persisting giant filter expressions."""
    relevant: list[str] = []
    for line in lines:
        if not line or line.startswith(("frame=", "fps=", "bitrate=", "out_time", "progress=")):
            continue
        if "Error when evaluating the expression" in line and "Parsed_overlay" in line:
            relevant.append("动态水印表达式解析失败。请更新到最新版后重新制作。")
            continue
        relevant.append(line if len(line) <= 420 else f"{line[:417]}...")
    return "\n".join(relevant[-8:]) or "FFmpeg 制作失败，未返回可用的错误详情。"


def is_transient_ffmpeg_error(error: str) -> bool:
    normalized = error.lower()
    return any(marker in normalized for marker in (
        "resource temporarily unavailable",
        "temporarily unavailable",
        "connection reset",
        "connection timed out",
        "broken pipe",
        "input/output error",
        "i/o error",
    ))


def run_ffmpeg_command(
    command: str,
    job_id: str,
    file_id: str,
    duration: Optional[float],
    progress_floor: float = 0,
) -> str:
    cancel_event = get_cancel_event(job_id)
    if cancel_event.is_set():
        update_file(file_id, status="paused", error="已暂停")
        return "paused"
    popen_kwargs = hidden_subprocess_kwargs()
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            shlex.split(command, posix=True),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )
    except OSError as exc:
        error = f"无法启动 FFmpeg：{exc}"
        update_file(file_id, status="error", error=error)
        return "retry" if is_transient_ffmpeg_error(error) else "error"
    register_process(job_id, proc)
    stderr_tail: list[str] = []
    started_at = time.monotonic()
    try:
        # Pause may have arrived between process creation and registration.
        if cancel_event.is_set():
            terminate_process(proc)
            update_file(file_id, status="paused", error="已暂停")
            return "paused"
        assert proc.stderr is not None
        for line in proc.stderr:
            if cancel_event.is_set():
                terminate_process(proc)
                update_file(file_id, status="paused", error="已暂停")
                return "paused"
            line = line.strip()
            if not line:
                continue
            stderr_tail.append(line)
            stderr_tail = stderr_tail[-80:]
            if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                try:
                    current = int(line.split("=", 1)[1]) / 1_000_000
                    if duration:
                        attempt_progress = min(99.9, current / duration * 100)
                        if progress_floor > 0:
                            progress = progress_floor + (100 - progress_floor) * attempt_progress / 100
                        else:
                            progress = attempt_progress
                        progress = min(99.9, progress)
                        elapsed = max(0.001, time.monotonic() - started_at)
                        speed = max(0.0, current / elapsed)
                        update_file(file_id, progress=progress, speed=round(speed, 2))
                        refresh_job_progress(job_id)
                except (TypeError, ValueError):
                    pass
            elif line == "progress=end":
                # FFmpeg still needs to finalize the container and exit successfully.
                update_file(file_id, progress=99.9)
                refresh_job_progress(job_id)
        proc.wait()
    finally:
        if proc.poll() is None:
            terminate_process(proc)
        unregister_process(job_id, proc)
    if cancel_event.is_set():
        job = get_job(job_id)
        if job and job["status"] == "canceled":
            update_file(file_id, status="canceled", error="已取消")
        else:
            update_file(file_id, status="paused", error="已暂停")
        return "paused"
    if proc.returncode == 0:
        update_file(file_id, status="done", progress=100, error="")
        return "done"
    error = summarize_ffmpeg_error(stderr_tail)
    update_file(file_id, status="error", error=error)
    if is_gpu_error("\n".join(stderr_tail)):
        return "gpu_error"
    return "retry" if is_transient_ffmpeg_error(error) else "error"


def process_file(
    job_id: str,
    file_row: sqlite3.Row,
    settings: dict,
    ffmpeg_threads: int,
    filter_threads: int,
    encoder_device: str = "cpu",
    fallback_reason: str = "",
) -> None:
    if get_cancel_event(job_id).is_set():
        job = get_job(job_id)
        if job and job["status"] == "canceled":
            update_file(file_row["id"], status="canceled", error="已取消")
        else:
            update_file(file_row["id"], status="paused", error="已暂停")
        return
    if file_row["status"] == "done":
        return
    progress_floor = float(file_row["progress"] or 0)
    output_path = Path(file_row["output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ensure_disk_headroom(int(file_row["size_bytes"] or 0))
    except HTTPException as exc:
        update_file(file_row["id"], status="error", error=str(exc.detail))
        refresh_job_progress(job_id)
        return
    def make_command():
        return generate_ffmpeg_command(
            file_row["input_path"], str(output_path),
            interval=settings["interval"], watermark_duration_seconds=settings["duration"],
            crf=settings["crf"], fixed_watermark_enabled=settings["fixed_watermark_enabled"],
            fixed_watermark_pos=settings["fixed_watermark_pos"],
            dynamic_watermark_enabled=settings["dynamic_watermark_enabled"],
            format_type=settings["format_type"],
            fixed_watermark_path=settings.get("fixed_watermark_path"),
            dynamic_watermark_path=settings.get("dynamic_watermark_path"),
            fixed_watermark_width_ratio=settings.get("fixed_watermark_size", 6.25) / 100,
            dynamic_watermark_width_ratio=settings.get("dynamic_watermark_size", 6.25) / 100,
            encoder_threads=ffmpeg_threads, filter_threads=filter_threads,
            encoder_preset=settings.get("encoder_preset", "veryfast"),
            encoder_device=encoder_device, gpu_quality=settings.get("gpu_quality", 26),
            gpu_preset=settings.get("gpu_preset", "p4"),
        )
    command = make_command()
    if not command:
        update_file(file_row["id"], status="error", error="无法生成 FFmpeg 命令")
        refresh_job_progress(job_id)
        return
    attempts = int(file_row["attempts"] or 0)
    device_attempts = 0
    while True:
        attempts += 1
        device_attempts += 1
        update_file(
            file_row["id"],
            status="running",
            progress=progress_floor,
            encoder_threads=ffmpeg_threads,
            speed=0,
            attempts=attempts,
            error="",
            encoder_device=encoder_device,
            encoder_name=("hevc_nvenc" if settings["format_type"] == "h265" else "h264_nvenc")
                if encoder_device == "nvidia" else ("libx265" if settings["format_type"] == "h265" else "libx264"),
            fallback_reason=fallback_reason,
        )
        result = run_ffmpeg_command(
            command,
            job_id,
            file_row["id"],
            file_row["duration_sec"],
            progress_floor,
        )
        if (result == "gpu_error" and encoder_device == "nvidia"
                and settings.get("encoder_device", "cpu") == "auto"
                and not get_cancel_event(job_id).is_set()):
            encoder_device = "cpu"
            device_attempts = 0
            fallback_reason = "GPU 编码失败，已改用 CPU 重新制作"
            progress_floor = 0
            update_file(file_row["id"], progress=0, speed=0, fallback_reason=fallback_reason)
            command = make_command()
            if not command:
                update_file(file_row["id"], status="error", error="无法生成 CPU 编码命令")
                break
            continue
        if result != "retry" or device_attempts >= MAX_FFMPEG_ATTEMPTS or get_cancel_event(job_id).is_set():
            break
        update_file(
            file_row["id"],
            status="queued",
            speed=0,
            error=f"本轮第 {device_attempts} 次制作遇到临时错误，正在自动重试",
        )
        time.sleep(1)
    if result == "done":
        input_path = Path(file_row["input_path"])
        delete_path_safely(input_path, UPLOAD_DIR)
        remove_empty_upload_dir(input_path.parent)
    refresh_job_progress(job_id)


def process_job(job_id: str) -> None:
    with encoding_work_lock:
        _process_job(job_id)


def _process_job(job_id: str) -> None:
    with job_control_lock:
        job = get_job(job_id)
        if not job or job["status"] != "queued":
            return
        clear_cancel_event(job_id)
        update_job(job_id, status="running", worker_count=0, message="正在准备吞吐优先调度")
    original_job = job
    files = [row for row in get_job_files(job_id) if row["status"] != "done"]
    if not files:
        update_job(job_id, status="done", progress=100, message="全部处理完成")
        if job["status"] not in TERMINAL_JOB_STATUS:
            notify_job_result(job_id, "done")
        return
    settings = json.loads(job["settings_json"])
    try:
        encoder_device, fallback_reason = select_encoder(
            settings.get("encoder_device", "cpu"), settings["format_type"]
        )
    except ValueError as exc:
        with job_control_lock:
            if get_cancel_event(job_id).is_set():
                return
            for row in files:
                update_file(row["id"], status="error", error=str(exc))
            refresh_job_progress(job_id)
            update_job(job_id, status="error", worker_count=0, message=str(exc))
            notify_job_result(job_id, "error")
        return
    worker_limit = recommend_worker_limit(files, encoder_device)
    with job_control_lock:
        if get_cancel_event(job_id).is_set():
            return
        for row in files:
            update_file(row["id"], status="queued")
    pending_files = list(files)
    with ThreadPoolExecutor(max_workers=worker_limit) as executor:
        active_futures = {}
        next_launch_at = 0.0
        while pending_files or active_futures:
            if get_cancel_event(job_id).is_set():
                break

            now = time.monotonic()
            if (
                pending_files
                and len(active_futures) < worker_limit
                and now >= next_launch_at
            ):
                reserved_output_bytes = sum(
                    int(file_row["size_bytes"] or 0)
                    for file_row, _thread_budget in active_futures.values()
                ) + int(pending_files[0]["size_bytes"] or 0)
                allowed, _reason, cpu_percent = resources_allow_next_task(
                    len(active_futures),
                    reserved_output_bytes,
                    encoder_device,
                )
                if allowed:
                    file_row = pending_files.pop(0)
                    active_encoder_threads = sum(
                        thread_budget for _file, thread_budget in active_futures.values()
                    )
                    ffmpeg_threads = GPU_CPU_THREADS if encoder_device == "nvidia" else recommend_ffmpeg_threads(active_encoder_threads, cpu_percent)
                    filter_threads = recommend_filter_threads(ffmpeg_threads)
                    future = executor.submit(
                        process_file,
                        job_id,
                        file_row,
                        settings,
                        ffmpeg_threads,
                        filter_threads,
                        encoder_device,
                        fallback_reason,
                    )
                    active_futures[future] = (file_row, ffmpeg_threads)
                    active_count = len(active_futures)
                    with job_control_lock:
                        if not get_cancel_event(job_id).is_set():
                            update_job(
                                job_id,
                                status="running",
                                worker_count=active_count,
                                message=f"正在处理，{'NVIDIA GPU' if encoder_device == 'nvidia' else 'CPU'}，任务数 {active_count}",
                            )
                    # Let the newly started encoder reach a stable load before sampling again.
                    next_launch_at = now + RESOURCE_WARMUP_SECONDS
                    continue
                with job_control_lock:
                    if not get_cancel_event(job_id).is_set():
                        update_job(job_id, worker_count=len(active_futures), message=f"保持当前并发，等待资源：{_reason}")
                next_launch_at = now + RESOURCE_SAMPLE_INTERVAL_SECONDS

            if not active_futures:
                update_job(job_id, message=f"等待资源：{_reason}")
                # Back off while resources are busy instead of continuously sampling.
                get_cancel_event(job_id).wait(RESOURCE_SAMPLE_INTERVAL_SECONDS)
                continue

            timeout = max(0.1, min(1.0, next_launch_at - time.monotonic()))
            completed, _ = wait(
                active_futures,
                timeout=timeout,
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                file_row, _thread_budget = active_futures.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    update_file(file_row["id"], status="error", speed=0, error=f"处理异常：{str(exc)[:500]}")
                refresh_job_progress(job_id)
                update_job(job_id, worker_count=len(active_futures))
                # A completed task releases resources; check immediately before starting another.
                next_launch_at = 0.0
    with job_control_lock:
        job = get_job(job_id)
        if not job:
            # A file worker may have been finishing setup when deletion was requested.
            cleanup_job_files(job_id, original_job)
            return
        if get_cancel_event(job_id).is_set():
            # Resume queues another run; its worker clears the stop flag only after
            # all of this run's futures have exited. Do not undo that queued state.
            if job["status"] == "queued":
                return
            if job["status"] == "canceled":
                cleanup_job_files(job_id, job)
                with db_lock, db_connect() as conn:
                    conn.execute(
                        "UPDATE job_files SET status='canceled', progress=0, error='已取消，文件已删除' WHERE job_id=?",
                        (job_id,),
                    )
                    conn.commit()
                refresh_job_progress(job_id)
                update_job(job_id, status="canceled", progress=0, message="已取消，上传文件和输出文件已删除")
                return
            with db_lock, db_connect() as conn:
                conn.execute(
                    "UPDATE job_files SET status='paused', error='已暂停' WHERE job_id=? AND status IN ('queued','running')",
                    (job_id,),
                )
                conn.commit()
            refresh_job_progress(job_id)
            update_job(job_id, status="paused", worker_count=0, message="已暂停，可继续处理未完成的视频")
            return
        files = get_job_files(job_id)
        # Completion requires every file to have a terminal result.
        for row in files:
            if row["status"] not in {"done", "error"}:
                update_file(row["id"], status="error", error="处理结束但未生成完整结果，请重试")
        refresh_job_progress(job_id)
        files = get_job_files(job_id)
        failed = sum(1 for row in files if row["status"] == "error")
        status = "error" if failed else "done"
        message = f"完成，失败 {failed} 个" if failed else "全部处理完成"
        update_job(job_id, status=status, worker_count=0, progress=100, message=message)
    notify_job_result(job_id, status)


def job_worker() -> None:
    while True:
        job_id = job_queue.get()
        try:
            process_job(job_id)
        except Exception as exc:
            # A single malformed job or transient system failure must not stop all future jobs.
            print(f"Video job {job_id} failed in the background worker: {exc}", file=sys.stderr)
            try:
                job = get_job(job_id)
                if job and job["status"] not in {"canceled", "done"}:
                    update_job(
                        job_id,
                        status="error",
                        message=f"后台任务异常：{str(exc)[:180]}",
                    )
                    refresh_job_progress(job_id)
            except Exception as report_exc:
                print(f"Could not record failed video job {job_id}: {report_exc}", file=sys.stderr)
        finally:
            job_queue.task_done()


def ensure_worker() -> None:
    global worker_thread
    with worker_lock:
        if worker_thread and worker_thread.is_alive():
            return
        worker_thread = threading.Thread(target=job_worker, daemon=True, name="video-job-worker")
        worker_thread.start()


def recover_unfinished_jobs() -> None:
    with db_lock, db_connect() as conn:
        conn.execute(
            "UPDATE job_files SET status='queued', error='' WHERE status='running'"
        )
        conn.execute(
            "UPDATE jobs SET status='queued', message='服务重启后已恢复排队' WHERE status='running'"
        )
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status='queued' ORDER BY created_at"
        ).fetchall()
        conn.commit()
    for row in rows:
        clear_cancel_event(row["id"])
        job_queue.put(row["id"])


def cleanup_expired_files() -> None:
    now = datetime.now()
    file_cutoff = now - timedelta(days=FILE_RETENTION_DAYS)
    record_cutoff = now - timedelta(days=RECORD_RETENTION_DAYS)
    archive_cutoff = now - timedelta(hours=ARCHIVE_RETENTION_HOURS)
    upload_session_cutoff = now - timedelta(hours=UPLOAD_SESSION_RETENTION_HOURS)
    with db_lock, db_connect() as conn:
        jobs = conn.execute("SELECT * FROM jobs").fetchall()
    for snapshot in jobs:
        # Re-read under the same lock as resume/delete, so cleanup cannot race a restart.
        with job_control_lock:
            job = get_job(snapshot["id"])
            if not job or job["status"] in {"queued", "running", "paused"}:
                continue
            try:
                updated = datetime.strptime(job["updated_at"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if job["status"] == "canceled" or updated < record_cutoff:
                # Record retention may be shorter than file retention. Clean files first.
                cleanup_job_files(job["id"], job)
                with db_lock, db_connect() as conn:
                    conn.execute("DELETE FROM job_files WHERE job_id=?", (job["id"],))
                    conn.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
                    conn.commit()
            elif updated < file_cutoff and job["status"] != "cleaned":
                cleanup_job_files(job["id"], job)
                with db_lock, db_connect() as conn:
                    conn.execute(
                        "UPDATE jobs SET status='cleaned', message=? WHERE id=?",
                        ("视频文件已超过保存时间并自动删除，处理记录保留。", job["id"]),
                    )
                    conn.execute(
                        "UPDATE job_files SET status='cleaned', error='视频文件已自动删除' WHERE job_id=?",
                        (job["id"],),
                    )
                    conn.commit()
    if TMP_DIR.exists():
        for child in TMP_DIR.iterdir():
            try:
                if child == RESUMABLE_UPLOAD_DIR:
                    for session_dir in child.iterdir():
                        with upload_sessions_lock:
                            if (session_dir.name not in completing_upload_sessions
                                    and session_dir.is_dir()
                                    and datetime.fromtimestamp(session_dir.stat().st_mtime) < upload_session_cutoff):
                                delete_path_safely(session_dir, RESUMABLE_UPLOAD_DIR)
                    continue
                is_archive = child.name.endswith(("_outputs.zip", "_outputs.zip.part"))
                cutoff = archive_cutoff if is_archive else file_cutoff
                if datetime.fromtimestamp(child.stat().st_mtime) < cutoff:
                    if is_archive and archive_is_active(child.name.split("_outputs", 1)[0]):
                        continue
                    delete_path_safely(child, TMP_DIR)
            except OSError:
                pass


def cleanup_worker() -> None:
    while True:
        try:
            cleanup_expired_files()
        except Exception as exc:
            print(f"Scheduled cleanup failed: {exc}", file=sys.stderr)
        time.sleep(3600)




@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/assets/{name}")
def bundled_asset(name: str):
    assets = {
        "rt.png": ROOT / "rt.png",
        "dt.png": ROOT / "dt.png",
    }
    path = assets.get(name)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(path)


@app.post("/api/login")
async def login(request: Request):
    if not AUTH_PASSWORD:
        return {"ok": True}
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        return JSONResponse({"detail": "登录信息格式无效"}, status_code=400)
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not (hmac.compare_digest(username.encode("utf-8"), AUTH_USER.encode("utf-8")) and hmac.compare_digest(password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8"))):
        return JSONResponse({"detail": "账号或密码不正确"}, status_code=401)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        make_auth_token(username),
        max_age=AUTH_SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response


@app.get("/api/config")
def config() -> dict:
    return {
        "root": str(APP_ROOT),
        "port": 8899,
        "file_retention_days": FILE_RETENTION_DAYS,
        "record_retention_days": RECORD_RETENTION_DAYS,
        "max_upload_mb": MAX_UPLOAD_MB,
        "cpu_count": os.cpu_count() or 1,
        "min_free_disk_gb": MIN_FREE_DISK_GB,
        "archive_retention_hours": ARCHIVE_RETENTION_HOURS,
        "auth_enabled": bool(AUTH_PASSWORD),
        "wecom_enabled": bool(WECHAT_WEBHOOK),
        "encoder_device_default": DEFAULT_ENCODER_DEVICE,
        "gpu": get_gpu_capabilities(),
        "gpu_max_concurrent": GPU_MAX_CONCURRENT,
        "gpu_concurrency_mode": "auto",
        "gpu_target_percent": GPU_TARGET_PERCENT,
    }


@app.post("/api/notifications/wecom/test")
def test_wecom_notification() -> dict:
    config_error = wecom_config_error()
    if config_error:
        raise HTTPException(status_code=400, detail=config_error)
    ok, detail = send_wecom_notification(
        "视频处理器测试通知",
        [
            f"发送时间：{now_iso()}",
            "如果你看到这条消息，说明企业微信机器人配置已生效。",
            f"访问地址：{PUBLIC_WEB_URL or '未配置 VIDEO_PROCESSOR_PUBLIC_URL'}",
        ],
    )
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return {"ok": True, "message": detail}


def system_status_payload() -> dict:
    global resource_metrics_previous
    now = time.monotonic()
    io = psutil.disk_io_counters()
    read_bytes_per_sec = 0.0
    write_bytes_per_sec = 0.0
    with resource_metrics_lock:
        if io and resource_metrics_previous:
            previous_time, previous_read, previous_write = resource_metrics_previous
            elapsed = max(0.001, now - previous_time)
            read_bytes_per_sec = max(0.0, (io.read_bytes - previous_read) / elapsed)
            write_bytes_per_sec = max(0.0, (io.write_bytes - previous_write) / elapsed)
        if io:
            resource_metrics_previous = (now, io.read_bytes, io.write_bytes)
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage(APP_ROOT)
    with active_processes_lock:
        active_ffmpeg = sum(
            1
            for processes in active_processes.values()
            for process in processes
            if process.poll() is None
        )
    return {
        "cpu_percent": round(psutil.cpu_percent(interval=0.1), 1),
        "memory_percent": round(memory.percent, 1),
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "disk_read_bytes_per_sec": round(read_bytes_per_sec, 1),
        "disk_write_bytes_per_sec": round(write_bytes_per_sec, 1),
        "active_ffmpeg": active_ffmpeg,
        "gpu": get_gpu_metrics(),
    }


@app.get("/api/system-status")
def system_status() -> dict:
    return system_status_payload()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "version": APP_VERSION, "revision": APP_REVISION}


def upload_session_path(session_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id or ""):
        raise HTTPException(status_code=404, detail="上传会话不存在")
    path = RESUMABLE_UPLOAD_DIR / session_id
    if not path.exists() or not is_safe_child(path, RESUMABLE_UPLOAD_DIR):
        raise HTTPException(status_code=404, detail="上传会话不存在或已过期")
    return path


def upload_manifest_path(session_dir: Path) -> Path:
    return session_dir / "manifest.json"


def load_upload_manifest(session_dir: Path) -> dict:
    try:
        return json.loads(upload_manifest_path(session_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail="上传会话数据损坏，请重新选择文件") from exc


def save_upload_manifest(session_dir: Path, manifest: dict) -> None:
    manifest["updated_at"] = time.time()
    target = upload_manifest_path(session_dir)
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def upload_session_payload(session_id: str, manifest: dict) -> dict:
    return {
        "id": session_id,
        "chunk_size": UPLOAD_CHUNK_SIZE,
        "files": [
            {
                "path": item["path"],
                "size": item["size"],
                "chunk_count": item["chunk_count"],
                "received_chunks": item.get("received_chunks", []),
            }
            for item in manifest["files"]
        ],
    }


@app.post("/api/uploads/init")
async def init_resumable_upload(request: Request) -> dict:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="上传文件信息格式无效") from exc
    raw_files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(raw_files, list) or not raw_files:
        raise HTTPException(status_code=400, detail="请至少选择一个视频文件")
    if len(raw_files) > 500:
        raise HTTPException(status_code=400, detail="单次最多上传 500 个视频文件")

    files = []
    paths: set[str] = set()
    total_size = 0
    for item in raw_files:
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail="上传文件信息格式无效")
        try:
            size = int(item.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        relative_path = sanitize_relative_upload_path(
            str(item.get("path", "")),
            str(item.get("name", "video.mp4")),
        ).as_posix()
        if size <= 0 or size > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"文件大小不符合限制：{Path(relative_path).name}")
        if relative_path in paths:
            raise HTTPException(status_code=400, detail=f"存在重复文件路径：{relative_path}")
        paths.add(relative_path)
        total_size += size
        files.append({
            "path": relative_path,
            "size": size,
            "chunk_count": math.ceil(size / UPLOAD_CHUNK_SIZE),
            "received_chunks": [],
        })
    ensure_disk_headroom(total_size)
    session_id = uuid.uuid4().hex
    session_dir = RESUMABLE_UPLOAD_DIR / session_id
    (session_dir / "files").mkdir(parents=True, exist_ok=False)
    manifest = {"created_at": time.time(), "updated_at": time.time(), "files": files}
    save_upload_manifest(session_dir, manifest)
    return upload_session_payload(session_id, manifest)


@app.get("/api/uploads/{session_id}")
def resumable_upload_status(session_id: str) -> dict:
    session_dir = upload_session_path(session_id)
    with upload_sessions_lock:
        manifest = load_upload_manifest(session_dir)
    return upload_session_payload(session_id, manifest)


@app.put("/api/uploads/{session_id}/chunks/{file_index}/{chunk_index}")
async def upload_resumable_chunk(
    session_id: str,
    file_index: int,
    chunk_index: int,
    request: Request,
) -> dict:
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > UPLOAD_CHUNK_SIZE:
            raise HTTPException(status_code=413, detail="上传分块超过大小限制")
        payload.extend(chunk)
    session_dir = upload_session_path(session_id)
    with upload_sessions_lock:
        if session_id in completing_upload_sessions:
            raise HTTPException(status_code=409, detail="该上传正在预估或创建任务，请稍后")
        manifest = load_upload_manifest(session_dir)
        if file_index < 0 or file_index >= len(manifest["files"]):
            raise HTTPException(status_code=404, detail="上传文件不存在")
        item = manifest["files"][file_index]
        if chunk_index < 0 or chunk_index >= item["chunk_count"]:
            raise HTTPException(status_code=400, detail="上传分块序号无效")
        received_chunks = {int(value) for value in item.get("received_chunks", [])}
        if chunk_index in received_chunks:
            return {"ok": True, "received": len(received_chunks), "already_received": True}
        offset = chunk_index * UPLOAD_CHUNK_SIZE
        expected_size = min(UPLOAD_CHUNK_SIZE, int(item["size"]) - offset)
        if len(payload) != expected_size:
            raise HTTPException(status_code=400, detail="上传分块大小不正确")
        ensure_disk_headroom(len(payload))
        target = session_dir / "files" / Path(item["path"])
        if not is_safe_child(target, session_dir / "files"):
            raise HTTPException(status_code=400, detail="上传文件路径不安全")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("r+b" if target.exists() else "wb") as output:
            output.seek(offset)
            output.write(payload)
        received_chunks.add(chunk_index)
        item["received_chunks"] = sorted(received_chunks)
        save_upload_manifest(session_dir, manifest)
    return {"ok": True, "received": len(item["received_chunks"]), "already_received": False}


def persist_job(job_id: str, settings: dict, upload_dir: Path, output_dir: Path, files: list[tuple]) -> None:
    """Publish a complete batch atomically so restart recovery never sees half a job."""
    created = now_iso()
    expires = (datetime.now() + timedelta(days=FILE_RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    with db_lock, db_connect() as conn:
        conn.execute(
            """INSERT INTO jobs (
                id, created_at, updated_at, expires_at, status, progress,
                total_count, done_count, failed_count, worker_count,
                settings_json, upload_dir, output_dir, message
            ) VALUES (?, ?, ?, ?, 'queued', 0, ?, 0, 0, 0, ?, ?, ?, ?)""",
            (job_id, created, created, expires, len(files), json.dumps(settings, ensure_ascii=False),
             str(upload_dir), str(output_dir), "等待处理"),
        )
        conn.executemany(
            """INSERT INTO job_files (
                id, job_id, original_name, input_path, output_path, status,
                progress, resolution, duration_sec, size_bytes
            ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)""", files,
        )
        conn.commit()


def create_job_from_resumable_files(
    session_dir: Path,
    manifest: dict,
    settings: dict,
    job_id: Optional[str] = None,
    upload_dir: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    watermark_dir: Optional[Path] = None,
) -> tuple[str, Path, Path, Path]:
    job_id = job_id or uuid.uuid4().hex
    upload_dir = upload_dir or UPLOAD_DIR / job_id
    output_dir = output_dir or OUTPUT_DIR / job_id
    watermark_dir = watermark_dir or WATERMARK_DIR / job_id
    for path in (upload_dir, output_dir, watermark_dir):
        path.mkdir(parents=True, exist_ok=True)
    moved_files: list[tuple[Path, Path]] = []
    used_outputs: set[str] = set()
    files: list[tuple] = []
    metadata: list[dict] = []
    try:
        source_root = session_dir / "files"
        # Validate every staged source before moving anything into the task directory.
        # A failed preflight leaves the resumable session intact for a retry.
        for item in manifest["files"]:
            relative_path = Path(item["path"])
            source = source_root / relative_path
            if not is_safe_child(source, source_root) or not source.exists() or source.stat().st_size != int(item["size"]):
                raise HTTPException(status_code=409, detail=f"上传文件不完整：{relative_path.name}")
            meta = probe_video(source)
            if meta["resolution"] == "N/A":
                raise HTTPException(status_code=400, detail=f"无法识别视频文件：{relative_path.name}")
            metadata.append(meta)
        for item, meta in zip(manifest["files"], metadata):
            relative_path = Path(item["path"])
            source = source_root / relative_path
            input_path = upload_dir / relative_path
            input_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(input_path))
            moved_files.append((source, input_path))
            output_path = allocate_output_path(relative_path, output_dir, settings["format_type"], used_outputs)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            files.append((uuid.uuid4().hex, job_id, relative_path.as_posix(), str(input_path),
                          str(output_path), meta["resolution"], meta["duration_sec"], input_path.stat().st_size))
        persist_job(job_id, settings, upload_dir, output_dir, files)
    except Exception:
        # Restore staged videos when task creation aborts. This makes a transient
        # database or filesystem failure recoverable through the same upload session.
        for source, input_path in reversed(moved_files):
            if input_path.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(input_path), str(source))
                except OSError:
                    pass
        cleanup_job_files(
            job_id,
            {"upload_dir": str(upload_dir), "output_dir": str(output_dir)},
        )
        with db_lock, db_connect() as conn:
            conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()
        raise
    return job_id, upload_dir, output_dir, watermark_dir


def read_estimate_state(session_dir: Path) -> dict:
    try:
        return json.loads((session_dir / "estimate.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="尚未进行体积预估")


def write_estimate_state(session_dir: Path, state: dict) -> None:
    target = session_dir / "estimate.json"
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    # Active sessions are also protected from retention cleanup by their lease.
    os.utime(session_dir, None)


def run_size_estimate(session_id, session_dir, manifest, settings, device, threads,
                      work_dir, state, canceled):
    terminal = "done"
    preview_root = session_dir / "estimate-previews" / state["id"]
    try:
        for index, item in enumerate(manifest["files"]):
            if canceled.is_set():
                raise EstimateCanceled()
            source = session_dir / "files" / item["path"]
            def on_sample(sample_index, count):
                if canceled.is_set():
                    raise EstimateCanceled()
                state["progress"] = round((index + sample_index / count) / len(manifest["files"]) * 100)
                state["message"] = f"{item['path']}：试编码 {sample_index + 1}/{count}"
                with upload_sessions_lock:
                    write_estimate_state(session_dir, state)
            result = {"path": item["path"], "original_bytes": item["size"]}
            file_work = work_dir / f"file-{index}"
            try:
                ensure_disk_headroom()
                file_work.mkdir()
                try:
                    result.update(estimate_file(source, file_work, settings, device, threads,
                                                canceled, terminate_process, on_sample, True))
                except RuntimeError as exc:
                    if settings["encoder_device"] != "auto" or device != "nvidia" or not is_gpu_error(str(exc)):
                        raise
                    delete_path_safely(file_work, work_dir)
                    file_work.mkdir()
                    result.update(estimate_file(source, file_work, settings, "cpu", recommend_ffmpeg_threads(0),
                                                canceled, terminate_process, on_sample, True))
                    result["fallback_reason"] = "GPU 试编码失败，已按 CPU 参数预估"
                preview_root.mkdir(parents=True, exist_ok=True)
                file_work.replace(preview_root / f"file-{index}")
            except EstimateCanceled:
                raise
            except Exception as exc:
                result["error"] = str(exc)[-400:] or "预估失败"
            state["files"].append(result)
        good = [item for item in state["files"] if "estimated_bytes" in item and not item.get("error")]
        state["failed_count"] = len(state["files"]) - len(good)
        if state["failed_count"]:
            terminal = "error"
            state["message"] = "部分视频预估失败，请查看列表；未计算整批大小"
        else:
            state["totals"] = {key: sum(item[key] for item in good)
                               for key in ("original_bytes", "estimated_bytes", "min_bytes", "max_bytes")}
            state["message"] = "预估完成，实际大小可能超出参考范围"
        state["progress"] = 100
    except EstimateCanceled:
        terminal = "canceled"
        state["message"] = "已取消预估，已上传的视频可继续用于制作"
        delete_path_safely(preview_root, session_dir)
        for item in state["files"]:
            item["samples"] = []
    except Exception as exc:
        terminal = "error"
        state["message"] = f"预估失败：{str(exc)[-250:]}"
    finally:
        try:
            delete_path_safely(work_dir, session_dir)
        finally:
            with upload_sessions_lock:
                estimation_tasks.pop(session_id, None)
                completing_upload_sessions.discard(session_id)
                encoding_work_lock.release()
                state["status"] = terminal
                write_estimate_state(session_dir, state)


@app.get("/api/uploads/{session_id}/estimate")
def size_estimate_status(session_id: str) -> dict:
    session_dir = upload_session_path(session_id)
    with upload_sessions_lock:
        state = read_estimate_state(session_dir)
        if state["status"] in {"running", "canceling"} and session_id not in estimation_tasks:
            state.update(status="error", message="服务已重启，请重新预估")
        return state


@app.delete("/api/uploads/{session_id}/estimate")
def cancel_size_estimate(session_id: str) -> dict:
    session_dir = upload_session_path(session_id)
    with upload_sessions_lock:
        event = estimation_tasks.get(session_id)
        if event:
            event.set()
        state = read_estimate_state(session_dir)
        if event:
            state.update(status="canceling", message="正在停止试编码…")
            write_estimate_state(session_dir, state)
        return state


def estimate_previews_exist(session_dir, state):
    root = session_dir / "estimate-previews" / state.get("id", "")
    return bool(state.get("files")) and all(
        item.get("samples") and all((root / f"file-{index}" / f"{kind}-{sample}.{ext}").is_file()
            for sample in range(len(item["samples"])) for kind, ext in (("preview", "mp4"), ("thumbnail", "jpg")))
        for index, item in enumerate(state["files"]))


@app.get("/api/uploads/{session_id}/estimate/{estimate_id}/files/{file_index}/samples/{sample_index}/{kind}")
def size_estimate_preview(session_id: str, estimate_id: str, file_index: int, sample_index: int, kind: str):
    session_dir = upload_session_path(session_id)
    with upload_sessions_lock:
        state = read_estimate_state(session_dir)
        files = state.get("files", [])
        if (not re.fullmatch(r"[0-9a-f]{32}", estimate_id) or state.get("id") != estimate_id
                or state.get("status") not in {"done", "error"}
                or not 0 <= file_index < len(files)
                or not 0 <= sample_index < len(files[file_index].get("samples", []))
                or kind not in {"video", "thumbnail"}):
            raise HTTPException(status_code=404, detail="样片已失效，请重新预估")
        filename = f"preview-{sample_index}.mp4" if kind == "video" else f"thumbnail-{sample_index}.jpg"
        path = session_dir / "estimate-previews" / estimate_id / f"file-{file_index}" / filename
        if not is_safe_child(path, session_dir) or not path.is_file():
            raise HTTPException(status_code=404, detail="样片已清理，请重新预估")
    return FileResponse(path, media_type="video/mp4" if kind == "video" else "image/jpeg",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.post("/api/uploads/{session_id}/estimate")
async def start_size_estimate(
    session_id: str,
    fixed_watermark: Optional[UploadFile] = File(None),
    dynamic_watermark: Optional[UploadFile] = File(None),
    fixed_watermark_enabled: str = Form("true"),
    dynamic_watermark_enabled: str = Form("true"),
    fixed_watermark_pos: str = Form("top-right"),
    interval: str = Form("60"), duration: str = Form("5"), crf: str = Form("32"),
    format_type: str = Form("h264"), encoder_preset: str = Form("veryfast"),
    fixed_watermark_size: str = Form("6.25"), dynamic_watermark_size: str = Form("6.25"),
    encoder_device: str = Form(DEFAULT_ENCODER_DEVICE), gpu_quality: str = Form("26"),
    gpu_preset: str = Form("p4"),
) -> dict:
    session_dir = upload_session_path(session_id)
    settings = build_job_settings(
        fixed_watermark_enabled, dynamic_watermark_enabled, fixed_watermark_pos,
        interval, duration, crf, format_type, encoder_preset,
        fixed_watermark_size, dynamic_watermark_size, encoder_device, gpu_quality, gpu_preset,
    )
    device, reason = select_encoder(encoder_device, settings["format_type"])
    threads = GPU_CPU_THREADS if device == "nvidia" else recommend_ffmpeg_threads(0)
    with job_control_lock:
        if not encoding_work_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="服务器正在编码或预估，请完成后再试")
        try:
            with db_lock, db_connect() as conn:
                busy = conn.execute("SELECT 1 FROM jobs WHERE status IN ('queued', 'running') LIMIT 1").fetchone()
            if busy:
                raise HTTPException(status_code=409, detail="请等待正在制作的任务完成后再预估")
            with upload_sessions_lock:
                if session_id in completing_upload_sessions:
                    raise HTTPException(status_code=409, detail="该上传正在预估或创建任务，请稍后")
                manifest = load_upload_manifest(session_dir)
                for item in manifest["files"]:
                    source = session_dir / "files" / item["path"]
                    if (not is_safe_child(source, session_dir / "files")
                            or len(item.get("received_chunks", [])) != item["chunk_count"]
                            or not source.is_file() or source.stat().st_size != item["size"]):
                        raise HTTPException(status_code=409, detail="仍有文件尚未上传完成")
                completing_upload_sessions.add(session_id)
        except BaseException:
            encoding_work_lock.release()
            raise
    work_dir = session_dir / ("estimate-work-" + uuid.uuid4().hex)
    handed_off = False
    try:
        work_dir.mkdir()
        hashes = {}
        for kind, upload, default in (("fixed", fixed_watermark, "rt.png"),
                                       ("dynamic", dynamic_watermark, "dt.png")):
            path = await save_upload_file(upload, work_dir) if upload and upload.filename else ROOT / default
            settings[f"{kind}_watermark_path"] = str(path)
            # Image content, rather than its temporary filename, identifies a trial.
            with path.open("rb") as image:
                hashes[kind] = hashlib.file_digest(image, "sha256").hexdigest()
        cache_key = hashlib.sha256(json.dumps({
            "schema": 2, "version": APP_VERSION,
            "settings": {k: v for k, v in settings.items() if not k.endswith("_path")},
            "watermarks": hashes, "device": device, "threads": threads,
            "files": [{"path": f["path"], "size": f["size"]} for f in manifest["files"]],
        }, sort_keys=True).encode()).hexdigest()
        with upload_sessions_lock:
            try:
                cached = read_estimate_state(session_dir)
            except HTTPException:
                cached = {}
            if (cached.get("status") == "done" and cached.get("cache_key") == cache_key
                    and all(item.get("encoder_device") == device for item in cached.get("files", []))
                    and estimate_previews_exist(session_dir, cached)):
                return cached
            state = {"id": uuid.uuid4().hex, "status": "running", "progress": 0,
                     "message": "正在准备试编码…", "files": [], "cache_key": cache_key,
                     "encoder_device": device, "fallback_reason": reason}
            canceled = threading.Event()
            write_estimate_state(session_dir, state)
            estimation_tasks[session_id] = canceled
            # Return a separate object: the worker immediately mutates its state.
            response = json.loads(json.dumps(state))
        # A new snapshot invalidates old samples; uploaded sources remain reusable.
        delete_path_safely(session_dir / "estimate-previews", session_dir)
        thread = threading.Thread(target=run_size_estimate, args=(session_id, session_dir,
            manifest, settings, device, threads, work_dir, state, canceled), daemon=True,
            name="video-size-estimate")
        thread.start()
        handed_off = True
        return response
    finally:
        if not handed_off:
            try:
                delete_path_safely(work_dir, session_dir)
            finally:
                with upload_sessions_lock:
                    estimation_tasks.pop(session_id, None)
                    completing_upload_sessions.discard(session_id)
                    encoding_work_lock.release()


@app.post("/api/uploads/{session_id}/complete")
async def complete_resumable_upload(
    session_id: str,
    fixed_watermark: Optional[UploadFile] = File(None),
    dynamic_watermark: Optional[UploadFile] = File(None),
    fixed_watermark_enabled: str = Form("true"),
    dynamic_watermark_enabled: str = Form("true"),
    fixed_watermark_pos: str = Form("top-right"),
    interval: str = Form("60"),
    duration: str = Form("5"),
    crf: str = Form("32"),
    format_type: str = Form("h264"),
    encoder_preset: str = Form("veryfast"),
    fixed_watermark_size: str = Form("6.25"),
    dynamic_watermark_size: str = Form("6.25"),
    encoder_device: str = Form(DEFAULT_ENCODER_DEVICE),
    gpu_quality: str = Form("26"),
    gpu_preset: str = Form("p4"),
    gpu_concurrency: str = Form(str(GPU_MAX_CONCURRENT)),
) -> dict:
    session_dir = upload_session_path(session_id)
    settings = build_job_settings(
        fixed_watermark_enabled, dynamic_watermark_enabled, fixed_watermark_pos,
        interval, duration, crf, format_type, encoder_preset,
        fixed_watermark_size, dynamic_watermark_size,
        encoder_device, gpu_quality, gpu_preset, gpu_concurrency,
    )
    with upload_sessions_lock:
        if session_id in completing_upload_sessions:
            raise HTTPException(status_code=409, detail="该上传正在预估或创建任务，请稍后")
        manifest = load_upload_manifest(session_dir)
        incomplete = [
            item["path"]
            for item in manifest["files"]
            if len(item.get("received_chunks", [])) != int(item["chunk_count"])
        ]
        if not incomplete:
            completing_upload_sessions.add(session_id)
    if incomplete:
        raise HTTPException(status_code=409, detail="仍有文件尚未上传完成")
    job_id = uuid.uuid4().hex
    upload_dir = UPLOAD_DIR / job_id
    output_dir = OUTPUT_DIR / job_id
    watermark_dir = WATERMARK_DIR / job_id
    try:
        # Persist custom watermarks before moving staged source videos. If this
        # request is interrupted, the resumable video session remains usable.
        watermark_dir.mkdir(parents=True, exist_ok=True)
        if fixed_watermark and fixed_watermark.filename:
            settings["fixed_watermark_path"] = str(await save_upload_file(fixed_watermark, watermark_dir))
        else:
            settings["fixed_watermark_path"] = str(ROOT / "rt.png")
        if dynamic_watermark and dynamic_watermark.filename:
            settings["dynamic_watermark_path"] = str(await save_upload_file(dynamic_watermark, watermark_dir))
        else:
            settings["dynamic_watermark_path"] = str(ROOT / "dt.png")
        job_id, _upload_dir, _output_dir, _watermark_dir = await run_in_threadpool(
            create_job_from_resumable_files,
            session_dir,
            manifest,
            settings,
            job_id=job_id,
            upload_dir=upload_dir,
            output_dir=output_dir,
            watermark_dir=watermark_dir,
        )
    except Exception:
        if get_job(job_id):
            cleanup_job_files(job_id)
            with db_lock, db_connect() as conn:
                conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
                conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                conn.commit()
        else:
            delete_path_safely(watermark_dir, WATERMARK_DIR)
        raise
    finally:
        with upload_sessions_lock:
            completing_upload_sessions.discard(session_id)
    delete_path_safely(session_dir, RESUMABLE_UPLOAD_DIR)
    ensure_worker()
    job_queue.put(job_id)
    return {"id": job_id}


@app.post("/api/jobs")
async def create_job(
    videos: list[UploadFile] = File(...),
    video_paths: list[str] = Form([]),
    fixed_watermark: Optional[UploadFile] = File(None),
    dynamic_watermark: Optional[UploadFile] = File(None),
    fixed_watermark_enabled: str = Form("true"),
    dynamic_watermark_enabled: str = Form("true"),
    fixed_watermark_pos: str = Form("top-right"),
    interval: str = Form("60"),
    duration: str = Form("5"),
    crf: str = Form("32"),
    format_type: str = Form("h264"),
    encoder_preset: str = Form("veryfast"),
    fixed_watermark_size: str = Form("6.25"),
    dynamic_watermark_size: str = Form("6.25"),
    encoder_device: str = Form(DEFAULT_ENCODER_DEVICE),
    gpu_quality: str = Form("26"),
    gpu_preset: str = Form("p4"),
    gpu_concurrency: str = Form(str(GPU_MAX_CONCURRENT)),
) -> dict:
    if not videos:
        raise HTTPException(status_code=400, detail="请至少上传一个视频")
    settings = build_job_settings(
        fixed_watermark_enabled, dynamic_watermark_enabled, fixed_watermark_pos,
        interval, duration, crf, format_type, encoder_preset,
        fixed_watermark_size, dynamic_watermark_size,
        encoder_device, gpu_quality, gpu_preset, gpu_concurrency,
    )
    job_id = uuid.uuid4().hex
    upload_dir = UPLOAD_DIR / job_id
    output_dir = OUTPUT_DIR / job_id
    watermark_dir = WATERMARK_DIR / job_id
    for path in (upload_dir, output_dir, watermark_dir):
        path.mkdir(parents=True, exist_ok=True)

    try:
        if fixed_watermark and fixed_watermark.filename:
            settings["fixed_watermark_path"] = str(await save_upload_file(fixed_watermark, watermark_dir))
        else:
            settings["fixed_watermark_path"] = str(ROOT / "rt.png")
        if dynamic_watermark and dynamic_watermark.filename:
            settings["dynamic_watermark_path"] = str(await save_upload_file(dynamic_watermark, watermark_dir))
        else:
            settings["dynamic_watermark_path"] = str(ROOT / "dt.png")
    except Exception:
        delete_path_safely(upload_dir, UPLOAD_DIR)
        delete_path_safely(output_dir, OUTPUT_DIR)
        delete_path_safely(watermark_dir, WATERMARK_DIR)
        raise

    used_outputs: set[str] = set()
    files: list[tuple] = []
    try:
        for index, upload in enumerate(videos):
            relative_path = sanitize_relative_upload_path(
                video_paths[index] if index < len(video_paths) else "",
                upload.filename or "upload.bin",
            )
            input_path = await save_upload_file(upload, upload_dir, relative_path)
            meta = await run_in_threadpool(probe_video, input_path)
            if meta["resolution"] == "N/A":
                raise HTTPException(status_code=400, detail=f"无法识别视频文件：{relative_path.name}")
            stored_relative_path = input_path.relative_to(upload_dir)
            output_path = allocate_output_path(stored_relative_path, output_dir, settings["format_type"], used_outputs)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            files.append((uuid.uuid4().hex, job_id, stored_relative_path.as_posix(), str(input_path),
                          str(output_path), meta["resolution"], meta["duration_sec"], input_path.stat().st_size))
        await run_in_threadpool(persist_job, job_id, settings, upload_dir, output_dir, files)
    except Exception:
        with db_lock, db_connect() as conn:
            conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()
        delete_path_safely(upload_dir, UPLOAD_DIR)
        delete_path_safely(output_dir, OUTPUT_DIR)
        delete_path_safely(watermark_dir, WATERMARK_DIR)
        raise

    ensure_worker()
    job_queue.put(job_id)
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs(
    query: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(1, page)
    page_size = max(5, min(50, page_size))
    allowed_statuses = {"queued", "running", "paused", "done", "error", "canceled", "cleaned"}
    where: list[str] = []
    params: list[str] = []
    if status:
        if status not in allowed_statuses:
            raise HTTPException(status_code=400, detail="处理状态筛选无效")
        where.append("j.status=?")
        params.append(status)
    query = query.strip()
    if query:
        like = f"%{query}%"
        where.append(
            "(j.id LIKE ? OR EXISTS (SELECT 1 FROM job_files f WHERE f.job_id=j.id AND f.original_name LIKE ?))"
        )
        params.extend([like, like])
    if date_from:
        where.append("date(j.created_at) >= date(?)")
        params.append(date_from)
    if date_to:
        where.append("date(j.created_at) <= date(?)")
        params.append(date_to)
    where_sql = f" WHERE {' AND '.join(where)}" if where else ""
    with db_lock, db_connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS count FROM jobs j{where_sql}", params).fetchone()["count"]
        rows = conn.execute(
            f"SELECT j.* FROM jobs j{where_sql} ORDER BY j.created_at DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        job_ids = [row["id"] for row in rows]
        files_by_job: dict[str, list[dict]] = {job_id: [] for job_id in job_ids}
        if job_ids:
            placeholders = ",".join("?" for _ in job_ids)
            file_rows = conn.execute(
                f"SELECT * FROM job_files WHERE job_id IN ({placeholders}) ORDER BY rowid",
                job_ids,
            ).fetchall()
            for row in file_rows:
                item = dict(row)
                item["output_exists"] = Path(row["output_path"]).exists()
                files_by_job[row["job_id"]].append(item)
    return {
        "jobs": [{**dict(row), "files": files_by_job[row["id"]]} for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, math.ceil(total / page_size)),
    }


@app.get("/api/current-job")
def current_job() -> dict:
    with db_lock, db_connect() as conn:
        job = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('queued', 'running', 'paused')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).fetchone()
    if not job:
        return {"job": None, "files": []}
    return job_detail(job["id"])


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    files = get_job_files(job_id)
    file_items = []
    for row in files:
        item = dict(row)
        item["output_exists"] = Path(row["output_path"]).exists()
        file_items.append(item)
    return {"job": dict(job), "files": file_items}


@app.post("/api/jobs/{job_id}/pause")
@serialized_job_control
def pause_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    if job["status"] in TERMINAL_JOB_STATUS:
        return {"ok": True, "status": job["status"]}
    get_cancel_event(job_id).set()
    terminate_job_processes(job_id)
    with db_lock, db_connect() as conn:
        conn.execute(
            "UPDATE job_files SET status='paused', error='已暂停' WHERE job_id=? AND status IN ('queued','running')",
            (job_id,),
        )
        conn.commit()
    refresh_job_progress(job_id)
    update_job(job_id, status="paused", worker_count=0, message="已暂停，可继续处理未完成的视频")
    return {"ok": True, "status": "paused"}


@app.post("/api/jobs/{job_id}/resume")
@serialized_job_control
def resume_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    if job["status"] not in {"paused", "error"}:
        return {"ok": True, "status": job["status"]}
    with db_lock, db_connect() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM job_files WHERE job_id=? AND status!='done'",
            (job_id,),
        ).fetchone()["count"]
        conn.execute(
            """
            UPDATE job_files
            SET status='queued', error='', attempts=CASE WHEN status='error' THEN 0 ELSE attempts END
            WHERE job_id=? AND status!='done'
            """,
            (job_id,),
        )
        conn.commit()
    if remaining <= 0:
        update_job(job_id, status="done", progress=100, message="全部处理完成")
        notify_job_result(job_id, "done")
        return {"ok": True, "status": "done"}
    # Keep the previous run stopped until the single job worker starts this run.
    update_job(job_id, status="queued", worker_count=0, message="已继续，等待处理")
    ensure_worker()
    job_queue.put(job_id)
    return {"ok": True, "status": "queued"}


@app.post("/api/jobs/{job_id}/cancel")
@serialized_job_control
def cancel_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    get_cancel_event(job_id).set()
    terminate_job_processes(job_id)
    cleanup_job_files(job_id, job)
    with db_lock, db_connect() as conn:
        conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
    notify_job_result(job_id, "canceled", job)
    return {"ok": True, "status": "deleted"}


@app.delete("/api/jobs/{job_id}")
@serialized_job_control
def delete_job(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    get_cancel_event(job_id).set()
    terminate_job_processes(job_id)
    cleanup_job_files(job_id, job)
    with db_lock, db_connect() as conn:
        conn.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        conn.commit()
    return {"ok": True, "status": "deleted"}


@app.get("/api/jobs/{job_id}/files/{file_id}/download")
def download_file(job_id: str, file_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    with db_lock, db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM job_files WHERE id=? AND job_id=?", (file_id, job_id)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = Path(row["output_path"])
    if not is_safe_child(path, Path(job["output_dir"])) or not path.exists():
        raise HTTPException(status_code=410, detail="文件已清理或不存在")
    return FileResponse(path, filename=path.name)


@app.get("/api/jobs/{job_id}/files/{file_id}/play")
def play_file(job_id: str, file_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    with db_lock, db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM job_files WHERE id=? AND job_id=?", (file_id, job_id)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="文件不存在")
    path = Path(row["output_path"])
    if not is_safe_child(path, Path(job["output_dir"])) or not path.exists():
        raise HTTPException(status_code=410, detail="文件已清理或不存在")
    return FileResponse(path)


@app.post("/api/jobs/{job_id}/archive")
def start_archive(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    if job["status"] in {"queued", "running", "paused"}:
        raise HTTPException(status_code=409, detail="任务结束后才能打包下载")
    current = get_archive_status(job_id)
    zip_path = TMP_DIR / f"{job_id}_outputs.zip"
    if current and current["status"] == "building":
        return archive_status_payload(current)
    if current and current["status"] == "ready" and zip_path.exists():
        return archive_status_payload(current)
    files = get_job_files(job_id)
    done_files = [row for row in files if row["status"] == "done" and Path(row["output_path"]).exists()]
    if not done_files:
        raise HTTPException(status_code=410, detail="没有可下载的成品文件")
    ensure_disk_headroom(sum(max(0, Path(row["output_path"]).stat().st_size) for row in done_files))
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = sum(max(0, Path(row["output_path"]).stat().st_size) for row in done_files)
    state = {
        "status": "building",
        "progress": 0,
        "message": "正在准备压缩包",
        "file_count": len(done_files),
        "total_bytes": total_bytes,
        "processed_bytes": 0,
        "filename": archive_filename(job),
    }
    with archive_tasks_lock:
        archive_tasks[job_id] = state
    threading.Thread(
        target=build_download_archive,
        args=(job_id, job, done_files),
        daemon=True,
    ).start()
    return archive_status_payload(state)


@app.get("/api/jobs/{job_id}/archive")
def archive_status(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    state = get_archive_status(job_id)
    if not state:
        return {"status": "idle", "progress": 0, "message": "尚未开始打包"}
    return archive_status_payload(state)


@app.get("/api/jobs/{job_id}/download-all")
def download_all(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="记录不存在")
    state = get_archive_status(job_id)
    zip_path = TMP_DIR / f"{job_id}_outputs.zip"
    if not state or state["status"] != "ready" or not zip_path.exists():
        raise HTTPException(status_code=409, detail="压缩包尚未准备完成")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=state.get("filename") or archive_filename(job),
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/cleanup")
def cleanup_now() -> dict:
    cleanup_expired_files()
    return {"ok": True}
