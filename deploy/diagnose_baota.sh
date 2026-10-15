#!/usr/bin/env bash
# Read-only host/container diagnostics: no creation, restart, removal or package install.
# Run in the Ubuntu host terminal: bash diagnose_baota.sh [project] [image]
# Does not print .env or container environment variables.
set -u
project="${1:-video-processor-web}"
image="${2:-video-processor-web:1.3.8-787b7c9}"

section() { printf '\n=== %s ===\n' "$1"; }
section 'Host and Docker'
uname -smr
docker version
docker compose version
if command -v docker-compose >/dev/null 2>&1; then docker-compose version; fi
docker context show
docker info --format 'Architecture={{.Architecture}} Root={{.DockerRootDir}} Runtimes={{json .Runtimes}}'

section 'Imported application image'
docker image inspect "$image" --format 'ID={{.Id}} OS={{.Os}} Arch={{.Architecture}} User={{.Config.User}} Command={{json .Config.Cmd}} Version={{index .Config.Labels "org.opencontainers.image.version"}} Revision={{index .Config.Labels "org.opencontainers.image.revision"}}'

section 'Host NVIDIA driver and container toolkit'
if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi; else printf 'nvidia-smi is unavailable on the host\n'; fi
if command -v nvidia-ctk >/dev/null 2>&1; then nvidia-ctk --version; else printf 'nvidia-ctk is unavailable on PATH\n'; fi
if command -v nvidia-container-cli >/dev/null 2>&1; then nvidia-container-cli --version; fi
if command -v ldconfig >/dev/null 2>&1; then ldconfig -p 2>/dev/null | grep -E 'libcuda\.so|libnvidia-encode\.so|libnvidia-ml\.so' || true; fi

section 'Named application data volume'
docker volume inspect video-processor-web-data --format 'Name={{.Name}} Driver={{.Driver}} Mountpoint={{.Mountpoint}}'
volume_path="$(docker volume inspect video-processor-web-data --format '{{.Mountpoint}}' 2>/dev/null || true)"
if [ -n "$volume_path" ] && [ -d "$volume_path" ]; then
    stat -c 'Permissions=%a Owner=%u:%g Path=%n' "$volume_path"
    df -h "$volume_path"
fi

section 'Application containers including failed/stopped containers'
containers="$(docker ps -aq --filter "label=com.docker.compose.project=$project" 2>/dev/null || true)"
if [ -z "$containers" ]; then
    containers="$(docker ps -aq --filter "ancestor=$image" 2>/dev/null || true)"
fi
if [ -z "$containers" ]; then
    printf 'No application container exists: inspect BaoTa project creation/deployment logs.\n'
    printf 'Possible creation-stage errors include YAML/Compose compatibility, image lookup, occupied port, or GPU device allocation.\n'
fi
for container in $containers; do
    section "Container $container"
    docker inspect "$container" --format 'Name={{.Name}} Image={{.Config.Image}} User={{.Config.User}} Status={{.State.Status}} ExitCode={{.State.ExitCode}} Error={{.State.Error}} Restarts={{.RestartCount}} Health={{if .State.Health}}{{.State.Health.Status}}{{else}}not-configured{{end}}'
    docker inspect "$container" --format 'GPURequests={{json .HostConfig.DeviceRequests}} ReadOnly={{.HostConfig.ReadonlyRootfs}} Mounts={{json .Mounts}} Ports={{json .HostConfig.PortBindings}}'
    docker inspect "$container" --format '{{if .State.Health}}{{range .State.Health.Log}}HealthExit={{.ExitCode}} Output={{.Output}}{{end}}{{end}}'
    section 'Last 80 application log lines'
    docker logs --tail 80 "$container" 2>&1
    running="$(docker inspect "$container" --format '{{.State.Running}}' 2>/dev/null || true)"
    if [ "$running" = true ]; then
        section 'Application health and data permissions (no password output)'
        docker exec -i "$container" python - <<'PY'
import json
import os
from pathlib import Path
import shutil
from urllib.request import ProxyHandler, build_opener
root = Path(os.getenv('VIDEO_PROCESSOR_ROOT', '/data/video-processor'))
try:
    print(json.dumps({'uid': os.getuid(), 'gid': os.getgid(), 'root': str(root),
        'root_uid': root.stat().st_uid, 'root_mode': oct(root.stat().st_mode & 0o777),
        'root_writable': os.access(root, os.W_OK),
        'disk_free_gib': round(shutil.disk_usage(root).free / 1024**3, 1),
        'database_exists': (root / 'video_processor.db').is_file(),
        'encoder_setting': os.getenv('VIDEO_PROCESSOR_ENCODER_DEVICE', 'auto'),
        'driver_capabilities': os.getenv('NVIDIA_DRIVER_CAPABILITIES', ''),
        'auth_enabled': bool(os.getenv('VIDEO_PROCESSOR_AUTH_PASSWORD'))}))
    with build_opener(ProxyHandler({})).open('http://127.0.0.1:8899/health', timeout=5) as response:
        print('HTTP health:', response.read().decode())
except Exception as error:
    print(type(error).__name__ + ':', str(error))
PY
    fi
done

section 'How to interpret this report'
printf '%s\n' \
  'No container: inspect the BaoTa deployment log; application logs cannot exist yet.' \
  'Exited/restarting: use State.Error, ExitCode and application log traceback.' \
  'Running+healthy: check access address, mapped port, firewall/proxy and BaoTa project status.' \
  'Disk free <20 GiB: uploads return HTTP 507; this alone does not prevent app startup.' \
  'GPU allocation errors occur before app startup; auto CPU fallback cannot handle those.' \
  'CPU-stage containers intentionally have no GPU request.' \
  'Review filenames/internal addresses in this report before sharing; passwords are not intentionally collected.'
