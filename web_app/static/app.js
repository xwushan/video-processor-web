const form = document.querySelector("#jobForm");
const processTab = document.querySelector("#processTab");
const recordsTab = document.querySelector("#recordsTab");
const processPage = document.querySelector("#processPage");
const recordsPage = document.querySelector("#recordsPage");
const jobsEl = document.querySelector("#jobs");
const refreshBtn = document.querySelector("#refreshBtn");
const testWecomBtn = document.querySelector("#testWecomBtn");
const configText = document.querySelector("#configText");
const recordsFeedback = document.querySelector("#recordsFeedback");
const videoInput = document.querySelector("#videoInput");
const folderInput = document.querySelector("#folderInput");
const videoDropZone = document.querySelector("#videoDropZone");
const videoPickerMeta = document.querySelector("#videoPickerMeta");
const selectedSummary = document.querySelector("#selectedSummary");
const selectedVideosEl = document.querySelector("#selectedVideos");
const clearVideosBtn = document.querySelector("#clearVideosBtn");
const fixedWatermarkInput = document.querySelector("#fixedWatermarkInput");
const dynamicWatermarkInput = document.querySelector("#dynamicWatermarkInput");
const fixedWatermarkName = document.querySelector("#fixedWatermarkName");
const dynamicWatermarkName = document.querySelector("#dynamicWatermarkName");
const fixedWatermarkEnabled = document.querySelector('input[name="fixed_watermark_enabled"]');
const dynamicWatermarkEnabled = document.querySelector('input[name="dynamic_watermark_enabled"]');
const fixedPreset = document.querySelector("#fixedPreset");
const fixedWatermarkPos = document.querySelector("#fixedWatermarkPos");
const customX = document.querySelector("#customX");
const customY = document.querySelector("#customY");
const fixedWatermarkSize = document.querySelector("#fixedWatermarkSize");
const dynamicWatermarkSize = document.querySelector("#dynamicWatermarkSize");
const fixedWatermarkSizeValue = document.querySelector("#fixedWatermarkSizeValue");
const dynamicWatermarkSizeValue = document.querySelector("#dynamicWatermarkSizeValue");
const previewVideo = document.querySelector("#previewVideo");
const previewWatermark = document.querySelector("#previewWatermark");
const previewDynamicWatermark = document.querySelector("#previewDynamicWatermark");
const previewStage = document.querySelector("#previewStage");
const previewEmpty = document.querySelector("#previewEmpty");
const previewHint = document.querySelector("#previewHint");
const uploadProgress = document.querySelector("#uploadProgress");
const uploadProgressBar = uploadProgress.querySelector(".bar i");
const uploadProgressText = uploadProgress.querySelector("strong");
const currentStatusText = document.querySelector("#currentStatusText");
const currentProgressText = document.querySelector("#currentProgressText");
const currentProgressBar = document.querySelector("#currentProgressBar");
const currentJobMeta = document.querySelector("#currentJobMeta");
const currentResourceMeta = document.querySelector("#currentResourceMeta");
const currentFiles = document.querySelector("#currentFiles");
const pauseJobBtn = document.querySelector("#pauseJobBtn");
const resumeJobBtn = document.querySelector("#resumeJobBtn");
const cancelJobBtn = document.querySelector("#cancelJobBtn");
const processingOverlay = document.querySelector("#processingOverlay");
const confirmDialog = document.querySelector("#confirmDialog");
const confirmTitle = document.querySelector("#confirmTitle");
const confirmMessage = document.querySelector("#confirmMessage");
const confirmCancelBtn = document.querySelector("#confirmCancelBtn");
const confirmOkBtn = document.querySelector("#confirmOkBtn");
const videoPreviewDialog = document.querySelector("#videoPreviewDialog");
const videoPreviewTitle = document.querySelector("#videoPreviewTitle");
const videoPreviewPlayer = document.querySelector("#videoPreviewPlayer");
const closeVideoPreviewBtn = document.querySelector("#closeVideoPreviewBtn");
const errorDialog = document.querySelector("#errorDialog");
const errorDialogMessage = document.querySelector("#errorDialogMessage");
const closeErrorDialogBtn = document.querySelector("#closeErrorDialogBtn");
const archiveDialog = document.querySelector("#archiveDialog");
const archiveTitle = document.querySelector("#archiveTitle");
const archiveMessage = document.querySelector("#archiveMessage");
const archiveProgressBar = document.querySelector("#archiveProgressBar");
const archiveProgressText = document.querySelector("#archiveProgressText");
const archiveDownloadFallback = document.querySelector("#archiveDownloadFallback");
const closeArchiveDialogBtn = document.querySelector("#closeArchiveDialogBtn");
const recordFiltersForm = document.querySelector("#recordFilters");
const recordSearch = document.querySelector("#recordSearch");
const recordStatus = document.querySelector("#recordStatus");
const recordDateFrom = document.querySelector("#recordDateFrom");
const recordDateTo = document.querySelector("#recordDateTo");
const clearRecordFiltersBtn = document.querySelector("#clearRecordFiltersBtn");
const recordsPagination = document.querySelector("#recordsPagination");
const toastHost = document.querySelector("#toastHost");
const loginDialog = document.querySelector("#loginDialog");
const loginForm = document.querySelector("#loginForm");
const loginUser = document.querySelector("#loginUser");
const loginPassword = document.querySelector("#loginPassword");
const loginError = document.querySelector("#loginError");

const nativeFetch = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const response = await nativeFetch(input, { ...init, credentials: init.credentials || "same-origin" });
  const url = typeof input === "string" ? input : input?.url || "";
  if (response.status === 401 && !url.includes("/api/login")) {
    showLoginDialog();
  }
  return response;
};

let selectedFiles = [];
let selectedVideoUrls = [];
let fixedWatermarkUrl = "/assets/rt.png";
let dynamicWatermarkUrl = "/assets/dt.png";
let previewVideoMeta = null;
let draggingWatermark = false;
let currentJobId = localStorage.getItem("currentJobId");
let controlsLocked = false;
let recordsSignature = "";
let expandedJobs = new Set(JSON.parse(localStorage.getItem("expandedJobs") || "[]"));
let collapsedTreeFolders = new Set(JSON.parse(localStorage.getItem("collapsedTreeFolders") || "[]"));
let confirmResolver = null;
let recordsFeedbackTimer = null;
let recordErrorDetails = new Map();
let recordsPageNumber = 1;
let systemStatusRequestInFlight = false;

function showToast(message, tone = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${tone}`.trim();
  toast.textContent = message;
  toastHost.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-8px)";
    setTimeout(() => toast.remove(), 180);
  }, 2400);
}

function showLoginDialog(message = "") {
  loginError.textContent = message;
  loginDialog.hidden = false;
  requestAnimationFrame(() => {
    if (!loginUser.value) {
      loginUser.focus();
    } else {
      loginPassword.focus();
    }
  });
}

function hideLoginDialog() {
  loginDialog.hidden = true;
  loginError.textContent = "";
  loginPassword.value = "";
}

function statusText(status) {
  return {
    queued: "排队中",
    running: "处理中",
    paused: "已暂停",
    done: "已完成",
    canceled: "已取消",
    cleaned: "文件已清理",
    error: "失败"
  }[status] || status;
}

function fmtSize(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value.toFixed(idx ? 1 : 0)} ${units[idx]}`;
}

function fmtRate(bytesPerSecond) {
  return `${fmtSize(bytesPerSecond)}/s`;
}

async function loadSystemStatus() {
  if (systemStatusRequestInFlight) return;
  systemStatusRequestInFlight = true;
  try {
    const res = await fetch("/api/system-status");
    if (!res.ok) return;
    const status = await res.json();
    currentResourceMeta.textContent =
      `CPU ${status.cpu_percent}% · 内存 ${status.memory_percent}% · ` +
      `磁盘可用 ${fmtSize(status.disk_free_bytes)} · ` +
      `读 ${fmtRate(status.disk_read_bytes_per_sec)} · 写 ${fmtRate(status.disk_write_bytes_per_sec)} · ` +
      `FFmpeg ${status.active_ffmpeg} 路`;
  } catch {
    // Keep the last successful resource snapshot rather than flashing an error.
  } finally {
    systemStatusRequestInFlight = false;
  }
}

function fmtDuration(seconds) {
  if (!Number.isFinite(seconds)) return "-";
  const value = Math.max(0, Math.round(seconds));
  const h = String(Math.floor(value / 3600)).padStart(2, "0");
  const m = String(Math.floor((value % 3600) / 60)).padStart(2, "0");
  const s = String(value % 60).padStart(2, "0");
  return `${h}:${m}:${s}`;
}

function fmtSpeed(value) {
  const speed = Number(value) || 0;
  return speed > 0 ? `${speed.toFixed(2)}x` : "测速中";
}

function displayProgress(value, isComplete) {
  const progress = Math.max(0, Math.min(100, Number(value) || 0));
  return isComplete ? Math.round(progress) : Math.min(99, Math.floor(progress));
}

async function postJobAction(url) {
  const res = await fetch(url, { method: "POST" });
  let payload = {};
  try {
    payload = await res.json();
  } catch {
    // A reverse proxy may return a non-JSON error page. Keep a useful fallback.
  }
  if (!res.ok) throw new Error(payload.detail || "操作失败，请稍后重试");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatErrorDetail(error) {
  const message = String(error || "未返回可用的错误详情").trim();
  if (message.includes("Error when evaluating the expression") && message.includes("Parsed_overlay")) {
    return "动态水印表达式过长，导致 FFmpeg 无法初始化水印滤镜。最新版已改为固定长度表达式，请点击“继续”重新制作。";
  }
  return message.length > 2400 ? `${message.slice(0, 2400)}\n\n错误内容已截断。` : message;
}

function showErrorDetail(error) {
  errorDialogMessage.textContent = formatErrorDetail(error);
  errorDialog.hidden = false;
}

function closeErrorDialog() {
  errorDialog.hidden = true;
}

function closeConfirm(result) {
  confirmDialog.hidden = true;
  if (confirmResolver) {
    confirmResolver(result);
    confirmResolver = null;
  }
}

function showConfirm({ title, message, okText = "确认", cancelText = "再想想" }) {
  confirmTitle.textContent = title;
  confirmMessage.textContent = message;
  confirmOkBtn.textContent = okText;
  confirmCancelBtn.textContent = cancelText;
  confirmDialog.hidden = false;
  confirmOkBtn.focus();
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

function openVideoPreview(index) {
  const file = selectedFiles[index];
  const url = selectedVideoUrls[index];
  if (!file || !url) return;
  videoPreviewTitle.textContent = file.name;
  videoPreviewPlayer.src = url;
  videoPreviewDialog.hidden = false;
  videoPreviewPlayer.play().catch(() => {});
}

function closeVideoPreview() {
  videoPreviewPlayer.pause();
  videoPreviewPlayer.removeAttribute("src");
  videoPreviewPlayer.load();
  videoPreviewDialog.hidden = true;
}

function showPage(name) {
  const records = name === "records";
  processPage.hidden = records;
  recordsPage.hidden = !records;
  processPage.classList.toggle("active", !records);
  recordsPage.classList.toggle("active", records);
  processTab.classList.toggle("active", !records);
  recordsTab.classList.toggle("active", records);
  if (records) loadJobs();
}

function setControlsLocked(locked) {
  controlsLocked = locked;
  form.classList.toggle("is-locked", locked);
  form.setAttribute("aria-disabled", locked ? "true" : "false");
  previewWatermark.style.pointerEvents = locked ? "none" : "auto";
  previewDynamicWatermark.style.pointerEvents = "none";
}

function setProcessingVisible(visible) {
  form.classList.toggle("is-processing", visible);
}

function scrollToProcessingProgress() {
  if (!form.classList.contains("is-processing")) return;
  const top = Math.max(0, window.scrollY + processingOverlay.getBoundingClientRect().top - 18);
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top, behavior: reduceMotion ? "auto" : "smooth" });
}

function saveExpandedJobs() {
  localStorage.setItem("expandedJobs", JSON.stringify([...expandedJobs]));
}

function fileKey(file) {
  return `${relativePathFor(file)}|${file.size}|${file.lastModified}`;
}

function relativePathFor(file) {
  return (file.webkitRelativePath || file.name || "video")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
}

function folderStats(files) {
  const paths = files.map(relativePathFor).filter(path => path.includes("/"));
  const roots = new Set(paths.map(path => path.split("/")[0]));
  const folders = new Set(paths.map(path => path.split("/").slice(0, -1).join("/")));
  return { rootCount: roots.size, folderCount: folders.size };
}

function saveCollapsedTreeFolders() {
  localStorage.setItem("collapsedTreeFolders", JSON.stringify([...collapsedTreeFolders]));
}

function createTreeNode(name = "", path = "") {
  return { name, path, children: new Map(), files: [] };
}

function buildFileTree(items, pathFor) {
  const root = createTreeNode();
  for (const item of items) {
    const rawPath = String(pathFor(item) || "video").replace(/\\/g, "/");
    const parts = rawPath.split("/").filter(Boolean);
    const fileName = parts.pop() || "video";
    let node = root;
    const pathParts = [];
    for (const part of parts) {
      pathParts.push(part);
      const path = pathParts.join("/");
      if (!node.children.has(part)) node.children.set(part, createTreeNode(part, path));
      node = node.children.get(part);
    }
    node.files.push({ item, name: fileName, path: [...pathParts, fileName].join("/") });
  }
  return root;
}

function treeNodeStats(node, getSize, isDone = () => false) {
  const stats = {
    count: node.files.length,
    totalSize: node.files.reduce((sum, entry) => sum + Number(getSize(entry.item) || 0), 0),
    done: node.files.filter(entry => isDone(entry.item)).length,
  };
  for (const child of node.children.values()) {
    const childStats = treeNodeStats(child, getSize, isDone);
    stats.count += childStats.count;
    stats.totalSize += childStats.totalSize;
    stats.done += childStats.done;
  }
  return stats;
}

function renderTreeNodes(node, options, depth = 0) {
  let html = "";
  for (const child of node.children.values()) {
    const stats = treeNodeStats(child, options.getSize, options.isDone);
    const key = `${options.scope}:${child.path}`;
    const expanded = !collapsedTreeFolders.has(key);
    const removeButton = options.allowFolderRemove
      ? `<button class="text remove-folder" type="button" data-folder-prefix="${escapeHtml(child.path)}">删除文件夹</button>`
      : "";
    html += `
      <div class="tree-folder" style="--tree-depth:${depth}">
        <button class="tree-toggle" type="button" data-tree-key="${escapeHtml(key)}" aria-expanded="${expanded}">
          <span>${expanded ? "⌄" : "›"}</span>
        </button>
        <strong title="${escapeHtml(child.path)}">${escapeHtml(child.name)}</strong>
        <span class="tree-folder-meta">${escapeHtml(options.folderMeta(stats))}</span>
        ${removeButton}
      </div>
    `;
    if (expanded) html += renderTreeNodes(child, options, depth + 1);
  }
  for (const entry of node.files) html += options.renderFile(entry, depth);
  return html;
}

function toggleTreeFolder(key) {
  if (collapsedTreeFolders.has(key)) collapsedTreeFolders.delete(key);
  else collapsedTreeFolders.add(key);
  saveCollapsedTreeFolders();
}

function syncFileInput() {
  const transfer = new DataTransfer();
  for (const file of selectedFiles) transfer.items.add(file);
  videoInput.files = transfer.files;
}

async function clearSelectedVideoList() {
  selectedFiles = [];
  syncFileInput();
  folderInput.value = "";
  await renderSelectedVideos();
}

function describeFiles(files, fallback) {
  if (!files.length) return fallback;
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);
  const stats = folderStats(files);
  const structure = stats.folderCount
    ? ` · ${stats.rootCount} 个根目录 · ${stats.folderCount} 个目录`
    : "";
  return `${files.length} 个视频 · ${fmtSize(totalSize)}${structure}`;
}

function updatePickerText() {
  videoPickerMeta.textContent = describeFiles(selectedFiles, "支持多选，也可以拖拽视频到这里");
}

function addVideoFiles(files) {
  const existing = new Set(selectedFiles.map(fileKey));
  for (const file of files) {
    const isVideo = file.type.startsWith("video/") || /\.(mp4|m4v|mov|avi|mkv|webm|flv|wmv|ts)$/i.test(file.name);
    if (isVideo && !existing.has(fileKey(file))) {
      selectedFiles.push(file);
      existing.add(fileKey(file));
    }
  }
  syncFileInput();
}

function revokeVideoUrls() {
  if (!videoPreviewDialog.hidden) closeVideoPreview();
  for (const url of selectedVideoUrls) URL.revokeObjectURL(url);
  selectedVideoUrls = [];
}

function readVideoMeta(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    selectedVideoUrls.push(url);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => {
      resolve({
        url,
        width: video.videoWidth || 0,
        height: video.videoHeight || 0,
        duration: video.duration
      });
    };
    video.onerror = () => resolve({ url, width: 0, height: 0, duration: NaN });
    video.src = url;
  });
}

async function renderSelectedVideos() {
  revokeVideoUrls();
  selectedVideosEl.innerHTML = "";
  selectedVideosEl.classList.toggle("empty", selectedFiles.length === 0);
  const totalSize = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  const stats = folderStats(selectedFiles);
  const structure = stats.folderCount
    ? `，保留 ${stats.rootCount} 个根目录与 ${stats.folderCount} 个目录结构`
    : "";
  selectedSummary.textContent = selectedFiles.length
    ? `已选择 ${selectedFiles.length} 个视频，总大小 ${fmtSize(totalSize)}${structure}。可删除单个视频，确认后再开始上传制作。`
    : "选择后会先在这里展示待处理列表，确认参数后再开始上传制作";
  if (!selectedFiles.length) {
    selectedVideosEl.textContent = "拖拽视频到此处，或点击上方选择视频";
    updatePickerText();
    clearPreview();
    return;
  }

  const items = [];
  for (let index = 0; index < selectedFiles.length; index += 1) {
    const file = selectedFiles[index];
    const displayPath = relativePathFor(file);
    const meta = await readVideoMeta(file);
    if (index === 0) setupPreviewVideo(meta);
    items.push({ file, meta, index, path: displayPath });
  }
  const tree = buildFileTree(items, item => item.path);
  selectedVideosEl.innerHTML = renderTreeNodes(tree, {
    scope: "selected",
    getSize: item => item.file.size,
    folderMeta: stats => `${stats.count} 个视频 · ${fmtSize(stats.totalSize)}`,
    allowFolderRemove: true,
    renderFile: (entry, depth) => {
      const { file, meta, index, path } = entry.item;
      const resolution = meta.width && meta.height ? `${meta.width}x${meta.height}` : "读取失败";
      return `
        <div class="video-row tree-video-row" style="--tree-depth:${depth}">
          <div class="video-name-cell">
            <button class="video-thumb-button" type="button" data-preview-index="${index}" aria-label="预览 ${escapeHtml(path)}">
              <video class="video-thumb" src="${escapeHtml(meta.url)}" muted preload="metadata"></video>
            </button>
            <strong title="${escapeHtml(path)}">${escapeHtml(entry.name)}</strong>
          </div>
          <span>${resolution}</span>
          <span>${fmtDuration(meta.duration)}</span>
          <span>${fmtSize(file.size)}</span>
          <span>待上传</span>
          <button class="text remove-video" type="button" data-index="${index}">删除</button>
        </div>
      `;
    },
  });
  updatePickerText();
  updateWatermarkPreview();
}

function setupPreviewVideo(meta) {
  previewVideoMeta = meta;
  previewVideo.src = meta.url;
  previewVideo.currentTime = 0;
  previewVideo.style.display = "block";
  previewEmpty.style.display = "none";
  if (meta.width && meta.height) {
    previewStage.style.aspectRatio = `${meta.width} / ${meta.height}`;
  }
  previewHint.textContent = meta.width && meta.height
    ? `${meta.width}x${meta.height}，拖动水印可自定义位置`
    : "视频预览";
}

function clearPreview() {
  previewVideoMeta = null;
  previewVideo.removeAttribute("src");
  previewVideo.style.display = "none";
  previewWatermark.style.display = "none";
  previewDynamicWatermark.style.display = "none";
  previewEmpty.style.display = "grid";
  previewHint.textContent = "选择视频后显示固定与动态水印，固定水印可拖动定位";
  previewStage.style.aspectRatio = "16 / 9";
}

function computedPositionValue() {
  if (fixedPreset.value === "custom") {
    const x = Math.max(0, Math.min(100, Number(customX.value || 0))) / 100;
    const y = Math.max(0, Math.min(100, Number(customY.value || 0))) / 100;
    return `ratio:${x.toFixed(6)},${y.toFixed(6)}`;
  }
  return fixedPreset.value;
}

function updatePositionField() {
  fixedWatermarkPos.value = computedPositionValue();
}

function previewVideoMetrics() {
  const stage = previewStage.getBoundingClientRect();
  const sourceWidth = Math.max(1, previewVideoMeta?.width || 1920);
  const sourceHeight = Math.max(1, previewVideoMeta?.height || 1080);
  const contentWidth = Math.max(1, previewStage.clientWidth || stage.width);
  const contentHeight = Math.max(1, previewStage.clientHeight || stage.height);
  const scale = Math.min(contentWidth / sourceWidth, contentHeight / sourceHeight);
  const width = sourceWidth * scale;
  const height = sourceHeight * scale;
  return {
    scale,
    left: (contentWidth - width) / 2,
    top: (contentHeight - height) / 2,
    width,
    height,
  };
}

function watermarkSizeForStage(watermark, sizeControl) {
  if (!previewVideoMeta || !previewVideoMeta.width || !previewVideoMeta.height) {
    return { width: 80, height: 30 };
  }
  const metrics = previewVideoMetrics();
  const naturalW = Math.max(1, watermark.naturalWidth || 160);
  const naturalH = Math.max(1, watermark.naturalHeight || 60);
  const percentage = Math.max(2, Math.min(20, Number(sizeControl.value || 6.25)));
  let targetW = Math.floor(previewVideoMeta.width * percentage / 100);
  targetW = Math.max(32, targetW);
  targetW = Math.min(targetW, 320, Math.max(1, Math.floor(previewVideoMeta.width * 0.35)));
  let targetH = Math.max(1, targetW * naturalH / naturalW);
  const maxH = Math.max(1, previewVideoMeta.height * 0.18);
  if (targetH > maxH) {
    targetH = maxH;
    targetW = Math.max(1, Math.floor(targetH * naturalW / naturalH));
  }
  return {
    width: targetW * metrics.scale,
    height: targetH * metrics.scale,
  };
}

function updateWatermarkPreview() {
  updatePositionField();
  if (!previewVideoMeta) return;
  const fixedEnabled = fixedWatermarkEnabled.checked;
  const dynamicEnabled = dynamicWatermarkEnabled.checked;
  if (!fixedEnabled) previewWatermark.style.display = "none";
  if (!dynamicEnabled) previewDynamicWatermark.style.display = "none";
  if (fixedEnabled) {
    if (previewWatermark.getAttribute("src") !== fixedWatermarkUrl) {
      previewWatermark.onload = placeWatermark;
      previewWatermark.src = fixedWatermarkUrl;
    } else {
      placeWatermark();
    }
  }
  if (dynamicEnabled) {
    if (previewDynamicWatermark.getAttribute("src") !== dynamicWatermarkUrl) {
      previewDynamicWatermark.onload = placeDynamicWatermark;
      previewDynamicWatermark.src = dynamicWatermarkUrl;
    } else {
      placeDynamicWatermark();
    }
  }
  if (fixedEnabled && dynamicEnabled) {
    previewHint.textContent = "固定水印可拖动定位，动态水印展示随机出现时的示意位置";
  } else if (fixedEnabled) {
    previewHint.textContent = "固定水印可拖动定位，显示大小会同步到成片";
  } else if (dynamicEnabled) {
    previewHint.textContent = "动态水印展示随机出现时的示意位置，显示大小会同步到成片";
  } else {
    previewHint.textContent = "水印已关闭，预览不显示水印";
  }
}

function placeWatermark() {
  if (!previewVideoMeta || !fixedWatermarkEnabled.checked) return;
  const metrics = previewVideoMetrics();
  const size = watermarkSizeForStage(previewWatermark, fixedWatermarkSize);
  const marginX = Math.min(60 * metrics.scale, metrics.width * 0.2);
  const marginY = Math.min(60 * metrics.scale, metrics.height * 0.2);
  let x = metrics.left + marginX;
  let y = metrics.top + marginY;
  const maxX = Math.max(metrics.left, metrics.left + metrics.width - size.width);
  const maxY = Math.max(metrics.top, metrics.top + metrics.height - size.height);

  if (fixedPreset.value === "top-right") {
    x = maxX - marginX;
    y = metrics.top + marginY;
  } else if (fixedPreset.value === "bottom-left") {
    x = metrics.left + marginX;
    y = maxY - marginY;
  } else if (fixedPreset.value === "bottom-right") {
    x = maxX - marginX;
    y = maxY - marginY;
  } else if (fixedPreset.value === "center") {
    x = metrics.left + (metrics.width - size.width) / 2;
    y = metrics.top + (metrics.height - size.height) / 2;
  } else if (fixedPreset.value === "custom") {
    x = metrics.left + metrics.width * Math.max(0, Math.min(100, Number(customX.value || 0))) / 100;
    y = metrics.top + metrics.height * Math.max(0, Math.min(100, Number(customY.value || 0))) / 100;
  }

  previewWatermark.style.width = `${Math.max(12, size.width)}px`;
  previewWatermark.style.height = `${Math.max(8, size.height)}px`;
  previewWatermark.style.left = `${Math.max(metrics.left, Math.min(maxX, x))}px`;
  previewWatermark.style.top = `${Math.max(metrics.top, Math.min(maxY, y))}px`;
  previewWatermark.style.display = "block";
}

function placeDynamicWatermark() {
  if (!previewVideoMeta || !dynamicWatermarkEnabled.checked) return;
  const metrics = previewVideoMetrics();
  const size = watermarkSizeForStage(previewDynamicWatermark, dynamicWatermarkSize);
  const maxX = Math.max(metrics.left, metrics.left + metrics.width - size.width);
  const maxY = Math.max(metrics.top, metrics.top + metrics.height - size.height);
  const x = Math.min(maxX, metrics.left + metrics.width * 0.14);
  const y = Math.min(maxY, metrics.top + metrics.height * 0.72);
  previewDynamicWatermark.style.width = `${Math.max(12, size.width)}px`;
  previewDynamicWatermark.style.height = `${Math.max(8, size.height)}px`;
  previewDynamicWatermark.style.left = `${Math.max(metrics.left, x)}px`;
  previewDynamicWatermark.style.top = `${Math.max(metrics.top, y)}px`;
  previewDynamicWatermark.style.display = "block";
}

function setCustomFromStagePoint(clientX, clientY) {
  if (!previewVideoMeta) return;
  const rect = previewStage.getBoundingClientRect();
  const metrics = previewVideoMetrics();
  const size = watermarkSizeForStage(previewWatermark, fixedWatermarkSize);
  const left = Math.max(metrics.left, Math.min(metrics.left + metrics.width - size.width, clientX - rect.left - size.width / 2));
  const top = Math.max(metrics.top, Math.min(metrics.top + metrics.height - size.height, clientY - rect.top - size.height / 2));
  fixedPreset.value = "custom";
  customX.value = Math.round((left - metrics.left) / Math.max(1, metrics.width) * 100);
  customY.value = Math.round((top - metrics.top) / Math.max(1, metrics.height) * 100);
  updateWatermarkPreview();
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    configText.textContent = `目录 ${cfg.root}，文件保留 ${cfg.file_retention_days} 天，记录保留 ${cfg.record_retention_days} 天`;
    testWecomBtn.hidden = !cfg.wecom_enabled;
  } catch {
    configText.textContent = "配置读取失败，请确认服务正在运行";
  }
}

async function loadJobs() {
  let data;
  try {
    const params = new URLSearchParams({ page: String(recordsPageNumber), page_size: "20" });
    if (recordSearch.value.trim()) params.set("query", recordSearch.value.trim());
    if (recordStatus.value) params.set("status", recordStatus.value);
    if (recordDateFrom.value) params.set("date_from", recordDateFrom.value);
    if (recordDateTo.value) params.set("date_to", recordDateTo.value);
    const res = await fetch(`/api/jobs?${params.toString()}`);
    if (!res.ok) throw new Error("records request failed");
    data = await res.json();
  } catch {
    jobsEl.innerHTML = `<div class="empty-state"><strong>记录读取失败</strong><p>请检查服务状态或稍后刷新。</p></div>`;
    return false;
  }
  const validDetails = data.jobs.map(job => ({ job, files: job.files || [] }));
  const nextSignature = JSON.stringify({
    page: data.page,
    total: data.total,
    totalPages: data.total_pages,
    jobs: validDetails.map(detail => ({
    job: {
      id: detail.job.id,
      status: detail.job.status,
      progress: detail.job.progress,
      total_count: detail.job.total_count,
      done_count: detail.job.done_count,
      failed_count: detail.job.failed_count,
      worker_count: detail.job.worker_count,
      updated_at: detail.job.updated_at,
      message: detail.job.message
    },
    files: detail.files.map(file => ({
      id: file.id,
      status: file.status,
      progress: file.progress,
      error: file.error,
      output_exists: file.output_exists,
      original_name: file.original_name,
      resolution: file.resolution,
      size_bytes: file.size_bytes
    }))
    })),
  });
  if (nextSignature === recordsSignature) return true;
  recordsSignature = nextSignature;
  if (!data.jobs.length) {
    jobsEl.innerHTML = `
      <div class="empty-state">
        <strong>暂无处理记录</strong>
        <p>上传并开始制作后，记录会显示在这里。即使当天没有下载，之后也可以通过记录找到成品文件。</p>
      </div>
    `;
    recordsPagination.hidden = true;
    return true;
  }
  recordErrorDetails = new Map();
  const html = validDetails.map(detail => {
    const job = detail.job;
    const expanded = expandedJobs.has(job.id);
    const fileTree = buildFileTree(detail.files, file => file.original_name);
    const filesHtml = renderTreeNodes(fileTree, {
      scope: `record:${job.id}`,
      getSize: file => file.size_bytes,
      isDone: file => file.status === "done",
      folderMeta: stats => `${stats.done}/${stats.count} 已完成 · ${fmtSize(stats.totalSize)}`,
      renderFile: (entry, depth) => {
        const file = entry.item;
        const fileCleaned = file.status === "cleaned" || (file.status === "done" && file.output_exists === false);
        const displayStatus = fileCleaned ? "cleaned" : file.status;
        const canDownload = file.status === "done" && file.output_exists !== false;
        const errorKey = `${job.id}:${file.id}`;
        const hasError = file.status === "error" && Boolean(file.error);
        if (hasError) recordErrorDetails.set(errorKey, file.error);
        const download = canDownload
          ? `<a href="/api/jobs/${job.id}/files/${file.id}/download">下载</a>`
          : hasError
            ? `<button class="show-error-detail" type="button" data-error-key="${escapeHtml(errorKey)}">失败详情</button>`
            : `<span class="meta">${fileCleaned ? "文件已自动删除" : "-"}</span>`;
        return `
          <div class="file record-tree-file" style="--tree-depth:${depth}">
            <div>
              <strong title="${escapeHtml(file.original_name)}">${escapeHtml(entry.name)}</strong>
              <div class="meta">${escapeHtml(file.resolution)} · ${fmtSize(file.size_bytes)}</div>
            </div>
            <span class="status-${displayStatus}">${statusText(displayStatus)}</span>
            <span>${displayProgress(file.progress, file.status === "done")}%</span>
            ${download}
          </div>
        `;
      },
    });
    const taskActive = ["queued", "running", "paused"].includes(job.status);
    const jobProgress = displayProgress(job.progress, !taskActive);
    const canDownloadAll = !taskActive && detail.files.some(file => file.status === "done" && file.output_exists !== false);
    const canResume = job.status === "paused" || job.status === "error";
    return `
      <article class="job ${expanded ? "expanded" : ""}" data-job-id="${escapeHtml(job.id)}">
        <div class="job-summary">
          <button class="drawer-toggle" type="button" data-job-id="${escapeHtml(job.id)}" aria-expanded="${expanded ? "true" : "false"}">
            <span>›</span>
          </button>
          <div class="job-main">
            <strong>${escapeHtml(job.created_at)}</strong>
            <div class="meta">任务 ${escapeHtml(job.id)} · ${statusText(job.status)} · ${escapeHtml(job.message || "")}</div>
          </div>
          <div class="job-actions">
            <span class="meta">任务数 ${job.worker_count} · ${job.done_count}/${job.total_count}</span>
            ${canResume ? `<button class="resume-record" type="button" data-job-id="${job.id}">继续</button>` : ""}
            ${canDownloadAll ? `<button class="download-all" type="button" data-job-id="${job.id}">打包下载</button>` : `<button class="download-all" type="button" disabled title="任务完成后可打包下载">打包下载</button>`}
            <button class="delete-record" type="button" data-job-id="${job.id}" ${taskActive ? "disabled title=\"任务结束后可删除\"" : ""}>删除</button>
          </div>
        </div>
        <div class="bar"><i style="width:${jobProgress}%"></i></div>
        <div class="files job-files">${filesHtml}</div>
      </article>
    `;
  }).join("");
  jobsEl.innerHTML = html;
  const totalPages = Number(data.total_pages) || 1;
  recordsPagination.hidden = totalPages <= 1;
  recordsPagination.innerHTML = `
    <span>共 ${data.total || 0} 条，第 ${data.page || recordsPageNumber}/${totalPages} 页</span>
    <button type="button" data-record-page="${Math.max(1, recordsPageNumber - 1)}" ${recordsPageNumber <= 1 ? "disabled" : ""}>上一页</button>
    <button type="button" data-record-page="${Math.min(totalPages, recordsPageNumber + 1)}" ${recordsPageNumber >= totalPages ? "disabled" : ""}>下一页</button>
  `;
  return true;
}

function setRecordsFeedback(message, tone = "") {
  recordsFeedback.textContent = message;
  recordsFeedback.className = `records-feedback ${tone}`.trim();
  clearTimeout(recordsFeedbackTimer);
  if (message) {
    recordsFeedbackTimer = setTimeout(() => {
      recordsFeedback.textContent = "";
      recordsFeedback.className = "records-feedback";
    }, 3200);
  }
}

function setArchiveDialog(state) {
  const progress = Math.max(0, Math.min(100, Number(state.progress) || 0));
  archiveDialog.hidden = false;
  archiveProgressBar.style.width = `${progress}%`;
  archiveProgressText.textContent = `${Math.round(progress)}%`;
  archiveMessage.textContent = state.message || "正在准备压缩包...";
  if (state.total_bytes) {
    archiveMessage.textContent += ` · ${fmtSize(state.processed_bytes || 0)} / ${fmtSize(state.total_bytes)}`;
  }
}

function closeArchiveDialog() {
  archiveDialog.hidden = true;
}

function triggerArchiveDownload(jobId) {
  const url = `/api/jobs/${jobId}/download-all?t=${Date.now()}`;
  archiveDownloadFallback.href = url;
  archiveDownloadFallback.hidden = false;
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function startArchiveDownload(jobId, button) {
  if (button.disabled) return;
  const defaultText = button.textContent;
  button.disabled = true;
  button.textContent = "正在打包...";
  archiveTitle.textContent = "正在准备打包下载";
  archiveDownloadFallback.hidden = true;
  closeArchiveDialogBtn.hidden = true;
  setArchiveDialog({ progress: 0, message: "正在提交打包任务" });
  try {
    const startRes = await fetch(`/api/jobs/${jobId}/archive`, { method: "POST" });
    const startState = await startRes.json();
    if (!startRes.ok) throw new Error(startState.detail || "无法开始打包");
    let state = startState;
    while (state.status === "building") {
      setArchiveDialog(state);
      await new Promise(resolve => setTimeout(resolve, 700));
      const statusRes = await fetch(`/api/jobs/${jobId}/archive`);
      state = await statusRes.json();
      if (!statusRes.ok) throw new Error(state.detail || "读取打包进度失败");
    }
    if (state.status !== "ready") throw new Error(state.message || "打包失败");
    archiveTitle.textContent = "压缩包已准备完成";
    setArchiveDialog(state);
    archiveMessage.textContent = "压缩包已完成，已发起浏览器下载。如未自动开始，请点击下方链接。";
    closeArchiveDialogBtn.hidden = false;
    triggerArchiveDownload(jobId);
    setRecordsFeedback("压缩包已准备完成，正在下载", "success");
  } catch (error) {
    archiveTitle.textContent = "打包下载失败";
    archiveMessage.textContent = error.message || "请稍后重试";
    archiveProgressBar.style.width = "0%";
    archiveProgressText.textContent = "-";
    closeArchiveDialogBtn.hidden = false;
    setRecordsFeedback("打包下载失败，请重试", "error");
  } finally {
    button.disabled = false;
    button.textContent = defaultText;
  }
}

async function refreshRecords() {
  if (refreshBtn.disabled) return;
  refreshBtn.disabled = true;
  refreshBtn.textContent = "刷新中...";
  const success = await loadJobs();
  refreshBtn.disabled = false;
  refreshBtn.textContent = "刷新";
  showToast(success ? "处理记录已刷新" : "刷新失败，请稍后重试", success ? "success" : "error");
}

async function testWecomNotification() {
  if (testWecomBtn.disabled) return;
  testWecomBtn.disabled = true;
  const defaultText = testWecomBtn.textContent;
  testWecomBtn.textContent = "发送中...";
  try {
    const response = await fetch("/api/notifications/wecom/test", { method: "POST" });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      // Keep the fallback below.
    }
    if (!response.ok) throw new Error(payload.detail || "测试通知发送失败");
    showToast(payload.message || "测试通知已发送");
  } catch (error) {
    showToast(error.message || "测试通知发送失败", "error");
  } finally {
    testWecomBtn.disabled = false;
    testWecomBtn.textContent = defaultText;
  }
}

function renderCurrent(detail) {
  const job = detail && detail.job;
  const files = detail && detail.files ? detail.files : [];
  if (!job) {
    currentJobId = null;
    localStorage.removeItem("currentJobId");
    currentStatusText.textContent = "暂无正在处理的任务";
    currentProgressText.textContent = "0%";
    currentProgressBar.style.width = "0%";
    document.querySelector(".progress-ring").style.setProperty("--progress", "0%");
    currentJobMeta.textContent = "总任务：0　已处理：0/0　任务数：-";
    currentFiles.innerHTML = "";
    pauseJobBtn.disabled = true;
    resumeJobBtn.disabled = true;
    cancelJobBtn.disabled = true;
    setProcessingVisible(false);
    setControlsLocked(false);
    return;
  }
  currentJobId = job.id;
  localStorage.setItem("currentJobId", job.id);
  const terminalStatus = ["done", "error", "canceled", "cleaned"].includes(job.status);
  const progress = displayProgress(job.progress, terminalStatus);
  currentStatusText.textContent = `任务 ${job.id} · ${statusText(job.status)} · ${job.message || ""}`;
  currentProgressText.textContent = `${progress}%`;
  currentProgressBar.style.width = `${progress}%`;
  document.querySelector(".progress-ring").style.setProperty("--progress", `${progress}%`);
  currentJobMeta.textContent = `总任务：${job.total_count}　已处理：${job.done_count}/${job.total_count}　任务数：${job.worker_count}`;
  const tree = buildFileTree(files, file => file.original_name);
  currentFiles.innerHTML = renderTreeNodes(tree, {
    scope: `current:${job.id}`,
    getSize: file => file.size_bytes,
    isDone: file => file.status === "done",
    folderMeta: stats => `${stats.done}/${stats.count} 已完成 · ${fmtSize(stats.totalSize)}`,
    renderFile: (entry, depth) => {
      const file = entry.item;
      const runtime = file.status === "running"
        ? `<small class="current-file-runtime">${fmtSpeed(file.speed)} · ${file.encoder_threads || "-"} 线程</small>`
        : "";
      return `
        <div class="current-file tree-current-file" style="--tree-depth:${depth}">
          <strong title="${escapeHtml(file.original_name)}">${escapeHtml(entry.name)}</strong>
          <span class="current-file-state status-${file.status}">${statusText(file.status)}${runtime}</span>
          <span>${displayProgress(file.progress, file.status === "done")}%</span>
        </div>
      `;
    },
  });
  const running = job.status === "queued" || job.status === "running";
  const paused = job.status === "paused";
  const cancelable = running || paused;
  pauseJobBtn.disabled = !running;
  resumeJobBtn.disabled = !paused;
  cancelJobBtn.disabled = !cancelable;
  setProcessingVisible(cancelable);
  setControlsLocked(cancelable);
  if (!cancelable && ["done", "error", "canceled"].includes(job.status)) {
    currentJobId = null;
    localStorage.removeItem("currentJobId");
    if (selectedFiles.length) void clearSelectedVideoList();
    setProcessingVisible(false);
    setControlsLocked(false);
    if (job.status !== "canceled") showPage("records");
  }
}

async function loadCurrentJob() {
  let detail = null;
  try {
    if (currentJobId) {
      const res = await fetch(`/api/jobs/${currentJobId}`);
      if (res.ok) detail = await res.json();
    }
    if (!detail || !detail.job) {
      const res = await fetch("/api/current-job");
      detail = await res.json();
    }
  } catch {
    return;
  }
  renderCurrent(detail);
}

function setUploadProgress(percent) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  uploadProgress.hidden = false;
  uploadProgressBar.style.width = `${value}%`;
  uploadProgressText.textContent = `${value}%`;
}

const uploadSessionStorageKey = "videoProcessorResumableUpload";

function uploadFingerprint() {
  return JSON.stringify(selectedFiles.map(file => ({
    path: relativePathFor(file),
    size: file.size,
    lastModified: file.lastModified,
  })));
}

function chunkBytes(fileSize, chunkIndex, chunkSize) {
  const start = chunkIndex * chunkSize;
  return Math.max(0, Math.min(chunkSize, fileSize - start));
}

function receivedUploadBytes(files, sessionFiles, chunkSize) {
  return files.reduce((total, file, index) => {
    const sessionFile = sessionFiles[index];
    const received = new Set(sessionFile?.received_chunks || []);
    return total + [...received].reduce(
      (sum, chunkIndex) => sum + chunkBytes(file.size, Number(chunkIndex), chunkSize),
      0,
    );
  }, 0);
}

async function readJsonResponse(response, fallback) {
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    // Keep the caller's user-facing fallback when a proxy returned non-JSON content.
  }
  if (!response.ok) throw new Error(payload.detail || fallback);
  return payload;
}

async function initOrResumeUpload() {
  const fingerprint = uploadFingerprint();
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(uploadSessionStorageKey) || "null");
  } catch {
    localStorage.removeItem(uploadSessionStorageKey);
  }
  if (saved?.fingerprint === fingerprint && saved?.id) {
    const response = await fetch(`/api/uploads/${saved.id}`);
    if (response.ok) return { fingerprint, session: await response.json() };
    localStorage.removeItem(uploadSessionStorageKey);
  }
  const response = await fetch("/api/uploads/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      files: selectedFiles.map(file => ({
        path: relativePathFor(file),
        name: file.name,
        size: file.size,
      })),
    }),
  });
  const session = await readJsonResponse(response, "无法创建上传会话");
  localStorage.setItem(uploadSessionStorageKey, JSON.stringify({ id: session.id, fingerprint }));
  return { fingerprint, session };
}

async function uploadSelectedFilesResumable() {
  const { session } = await initOrResumeUpload();
  const chunkSize = Number(session.chunk_size) || 8 * 1024 * 1024;
  const totalBytes = selectedFiles.reduce((total, file) => total + file.size, 0);
  let uploadedBytes = receivedUploadBytes(selectedFiles, session.files, chunkSize);
  setUploadProgress(totalBytes ? uploadedBytes / totalBytes * 100 : 0);

  for (let fileIndex = 0; fileIndex < selectedFiles.length; fileIndex += 1) {
    const file = selectedFiles[fileIndex];
    const sessionFile = session.files[fileIndex];
    const received = new Set(sessionFile.received_chunks || []);
    for (let chunkIndex = 0; chunkIndex < sessionFile.chunk_count; chunkIndex += 1) {
      if (received.has(chunkIndex)) continue;
      const start = chunkIndex * chunkSize;
      const chunk = file.slice(start, Math.min(file.size, start + chunkSize));
      const response = await fetch(
        `/api/uploads/${session.id}/chunks/${fileIndex}/${chunkIndex}`,
        { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: chunk },
      );
      await readJsonResponse(response, "上传分块失败，请检查网络后重试");
      uploadedBytes += chunk.size;
      setUploadProgress(totalBytes ? uploadedBytes / totalBytes * 100 : 100);
    }
  }
  return session.id;
}

async function createJobWithProgress(data) {
  const sessionId = await uploadSelectedFilesResumable();
  data.delete("videos");
  data.delete("video_paths");
  const response = await fetch(`/api/uploads/${sessionId}/complete`, { method: "POST", body: data });
  const created = await readJsonResponse(response, "上传完成后创建任务失败");
  localStorage.removeItem(uploadSessionStorageKey);
  return created;
}

videoInput.addEventListener("change", async () => {
  if (controlsLocked) return;
  addVideoFiles(Array.from(videoInput.files || []));
  await renderSelectedVideos();
});

folderInput.addEventListener("change", async () => {
  if (controlsLocked) return;
  addVideoFiles(Array.from(folderInput.files || []));
  folderInput.value = "";
  await renderSelectedVideos();
});

function enableVideoDropTarget(target) {
  for (const eventName of ["dragenter", "dragover"]) {
    target.addEventListener(eventName, (event) => {
      event.preventDefault();
      if (!controlsLocked) target.classList.add("is-dragover");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    target.addEventListener(eventName, () => target.classList.remove("is-dragover"));
  }
  target.addEventListener("drop", async (event) => {
    event.preventDefault();
    if (controlsLocked) return;
    addVideoFiles(Array.from(event.dataTransfer.files || []));
    await renderSelectedVideos();
  });
}

enableVideoDropTarget(videoDropZone);
enableVideoDropTarget(selectedVideosEl);

selectedVideosEl.addEventListener("click", async (event) => {
  if (controlsLocked) return;
  const treeToggle = event.target.closest(".tree-toggle");
  if (treeToggle) {
    toggleTreeFolder(treeToggle.dataset.treeKey);
    await renderSelectedVideos();
    return;
  }
  const folderButton = event.target.closest(".remove-folder");
  if (folderButton) {
    const prefix = `${folderButton.dataset.folderPrefix}/`;
    selectedFiles = selectedFiles.filter(file => !relativePathFor(file).startsWith(prefix));
    syncFileInput();
    await renderSelectedVideos();
    return;
  }
  const previewButton = event.target.closest(".video-thumb-button");
  if (previewButton) {
    openVideoPreview(Number(previewButton.dataset.previewIndex));
    return;
  }
  const button = event.target.closest(".remove-video");
  if (!button) return;
  const index = Number(button.dataset.index);
  selectedFiles.splice(index, 1);
  syncFileInput();
  await renderSelectedVideos();
});

currentFiles.addEventListener("click", (event) => {
  const treeToggle = event.target.closest(".tree-toggle");
  if (!treeToggle) return;
  toggleTreeFolder(treeToggle.dataset.treeKey);
  void loadCurrentJob();
});

clearVideosBtn.addEventListener("click", async () => {
  if (controlsLocked) return;
  await clearSelectedVideoList();
});

fixedWatermarkInput.addEventListener("change", () => {
  if (controlsLocked) return;
  if (fixedWatermarkUrl.startsWith("blob:")) URL.revokeObjectURL(fixedWatermarkUrl);
  const file = fixedWatermarkInput.files && fixedWatermarkInput.files[0];
  fixedWatermarkUrl = file ? URL.createObjectURL(file) : "/assets/rt.png";
  fixedWatermarkName.textContent = file ? file.name : "默认：rt.png";
  updateWatermarkPreview();
});

dynamicWatermarkInput.addEventListener("change", () => {
  if (controlsLocked) return;
  if (dynamicWatermarkUrl.startsWith("blob:")) URL.revokeObjectURL(dynamicWatermarkUrl);
  const file = dynamicWatermarkInput.files && dynamicWatermarkInput.files[0];
  dynamicWatermarkUrl = file ? URL.createObjectURL(file) : "/assets/dt.png";
  dynamicWatermarkName.textContent = file ? file.name : "默认：dt.png";
  updateWatermarkPreview();
});

fixedWatermarkEnabled.addEventListener("change", updateWatermarkPreview);
dynamicWatermarkEnabled.addEventListener("change", updateWatermarkPreview);
fixedPreset.addEventListener("change", updateWatermarkPreview);
fixedWatermarkSize.addEventListener("input", () => {
  if (controlsLocked) return;
  fixedWatermarkSizeValue.textContent = `${fixedWatermarkSize.value}%`;
  updateWatermarkPreview();
});
dynamicWatermarkSize.addEventListener("input", () => {
  if (controlsLocked) return;
  dynamicWatermarkSizeValue.textContent = `${dynamicWatermarkSize.value}%`;
  updateWatermarkPreview();
});
customX.addEventListener("input", () => {
  if (controlsLocked) return;
  fixedPreset.value = "custom";
  updateWatermarkPreview();
});
customY.addEventListener("input", () => {
  if (controlsLocked) return;
  fixedPreset.value = "custom";
  updateWatermarkPreview();
});
previewStage.addEventListener("click", (event) => {
  if (!controlsLocked && fixedWatermarkEnabled.checked) setCustomFromStagePoint(event.clientX, event.clientY);
});
previewWatermark.addEventListener("pointerdown", (event) => {
  if (controlsLocked) return;
  draggingWatermark = true;
  previewWatermark.classList.add("dragging");
  previewWatermark.setPointerCapture(event.pointerId);
  event.preventDefault();
});
previewWatermark.addEventListener("pointermove", (event) => {
  if (draggingWatermark) setCustomFromStagePoint(event.clientX, event.clientY);
});
previewWatermark.addEventListener("pointerup", () => {
  draggingWatermark = false;
  previewWatermark.classList.remove("dragging");
});
window.addEventListener("resize", updateWatermarkPreview);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (controlsLocked) return;
  updatePositionField();
  if (!selectedFiles.length) {
    alert("请先选择视频文件");
    return;
  }
  const submit = form.querySelector("button[type=submit]");
  submit.textContent = "正在上传...";
  setUploadProgress(0);
  try {
    const data = new FormData(form);
    data.delete("videos");
    data.delete("video_paths");
    for (const file of selectedFiles) {
      data.append("videos", file, file.name);
      data.append("video_paths", relativePathFor(file));
    }
    data.delete("fixed_watermark_preset");
    data.set("fixed_watermark_pos", fixedWatermarkPos.value);
    for (const box of form.querySelectorAll("input[type=checkbox]")) {
      data.set(box.name, box.checked ? "true" : "false");
    }
    setControlsLocked(true);
    const created = await createJobWithProgress(data);
    currentJobId = created.id;
    localStorage.setItem("currentJobId", currentJobId);
    await loadCurrentJob();
    requestAnimationFrame(() => requestAnimationFrame(scrollToProcessingProgress));
  } catch (error) {
    setProcessingVisible(false);
    setControlsLocked(false);
    alert(error.message);
  } finally {
    submit.textContent = "开始制作";
    submit.disabled = false;
    setTimeout(() => { uploadProgress.hidden = true; }, 1000);
  }
});

refreshBtn.addEventListener("click", refreshRecords);
testWecomBtn.addEventListener("click", testWecomNotification);
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = loginForm.querySelector("button[type=submit]");
  submit.disabled = true;
  submit.textContent = "登录中...";
  loginError.textContent = "";
  try {
    const response = await nativeFetch("/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: loginUser.value.trim(),
        password: loginPassword.value,
      }),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      // Keep the standard error below.
    }
    if (!response.ok) {
      throw new Error(payload.detail || "登录失败，请检查账号密码");
    }
    hideLoginDialog();
    showToast("登录成功");
    await loadConfig();
    await loadCurrentJob();
    if (recordsPage.classList.contains("active")) {
      recordsSignature = "";
      await loadJobs();
    }
  } catch (error) {
    loginError.textContent = error.message || "登录失败，请检查账号密码";
  } finally {
    submit.disabled = false;
    submit.textContent = "登录";
  }
});
recordFiltersForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  recordsPageNumber = 1;
  recordsSignature = "";
  await loadJobs();
});
clearRecordFiltersBtn.addEventListener("click", async () => {
  recordSearch.value = "";
  recordStatus.value = "";
  recordDateFrom.value = "";
  recordDateTo.value = "";
  recordsPageNumber = 1;
  recordsSignature = "";
  await loadJobs();
});
recordsPagination.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-record-page]");
  if (!button || button.disabled) return;
  recordsPageNumber = Number(button.dataset.recordPage) || 1;
  recordsSignature = "";
  await loadJobs();
});
processTab.addEventListener("click", () => showPage("process"));
recordsTab.addEventListener("click", () => showPage("records"));
closeArchiveDialogBtn.addEventListener("click", closeArchiveDialog);

function toggleJobCard(card) {
  const jobId = card.dataset.jobId;
  const toggle = card.querySelector(".drawer-toggle");
  if (!jobId || !toggle) return;
  if (expandedJobs.has(jobId)) {
    expandedJobs.delete(jobId);
    card.classList.remove("expanded");
    toggle.setAttribute("aria-expanded", "false");
  } else {
    expandedJobs.add(jobId);
    card.classList.add("expanded");
    toggle.setAttribute("aria-expanded", "true");
  }
  saveExpandedJobs();
}

jobsEl.addEventListener("click", async (event) => {
  const treeToggle = event.target.closest(".tree-toggle");
  if (treeToggle) {
    toggleTreeFolder(treeToggle.dataset.treeKey);
    recordsSignature = "";
    await loadJobs();
    return;
  }
  const toggle = event.target.closest(".drawer-toggle");
  if (toggle) {
    const card = toggle.closest(".job");
    if (card) toggleJobCard(card);
    return;
  }

  const downloadButton = event.target.closest(".download-all");
  if (downloadButton) {
    await startArchiveDownload(downloadButton.dataset.jobId, downloadButton);
    return;
  }

  const errorButton = event.target.closest(".show-error-detail");
  if (errorButton) {
    showErrorDetail(recordErrorDetails.get(errorButton.dataset.errorKey));
    return;
  }

  const deleteButton = event.target.closest(".delete-record");
  if (deleteButton) {
    const jobId = deleteButton.dataset.jobId;
    const ok = await showConfirm({
      title: "删除处理记录",
      message: "删除后，服务器上对应的上传视频、成品视频和临时文件也会一起删除，无法恢复。",
      okText: "删除",
      cancelText: "取消"
    });
    if (!ok) return;
    deleteButton.disabled = true;
    const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
    if (!res.ok) {
      deleteButton.disabled = false;
      alert("删除失败，请稍后重试");
      return;
    }
    expandedJobs.delete(jobId);
    saveExpandedJobs();
    if (currentJobId === jobId) {
      currentJobId = null;
      localStorage.removeItem("currentJobId");
      await loadCurrentJob();
    }
    recordsSignature = "";
    await loadJobs();
    return;
  }

  const button = event.target.closest(".resume-record");
  if (button) {
    const jobId = button.dataset.jobId;
    button.disabled = true;
    try {
      await postJobAction(`/api/jobs/${jobId}/resume`);
    } catch (error) {
      button.disabled = false;
      alert(error.message);
      return;
    }
    currentJobId = jobId;
    localStorage.setItem("currentJobId", currentJobId);
    setControlsLocked(true);
    showPage("process");
    await loadCurrentJob();
    return;
  }

  const jobSurface = event.target.closest(".job-summary, .job > .bar");
  if (!jobSurface || event.target.closest(".job-actions")) return;
  const card = jobSurface.closest(".job");
  if (card) toggleJobCard(card);
});
pauseJobBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  pauseJobBtn.disabled = true;
  try {
    await postJobAction(`/api/jobs/${currentJobId}/pause`);
  } catch (error) {
    alert(error.message);
  }
  await loadCurrentJob();
});
resumeJobBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  resumeJobBtn.disabled = true;
  try {
    await postJobAction(`/api/jobs/${currentJobId}/resume`);
    setControlsLocked(true);
  } catch (error) {
    alert(error.message);
  }
  await loadCurrentJob();
});
cancelJobBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  const ok = await showConfirm({
    title: "取消当前任务",
    message: "取消后会删除本批上传视频、已生成成品、临时文件和处理记录，当前任务不能继续。",
    okText: "取消任务",
    cancelText: "继续制作"
  });
  if (!ok) return;
  cancelJobBtn.disabled = true;
  pauseJobBtn.disabled = true;
  resumeJobBtn.disabled = true;
  try {
    await postJobAction(`/api/jobs/${currentJobId}/cancel`);
    currentJobId = null;
    localStorage.removeItem("currentJobId");
    await clearSelectedVideoList();
  } catch (error) {
    alert(error.message);
  }
  await loadCurrentJob();
  if (recordsPage.classList.contains("active")) await loadJobs();
});

confirmCancelBtn.addEventListener("click", () => closeConfirm(false));
confirmOkBtn.addEventListener("click", () => closeConfirm(true));
confirmDialog.addEventListener("click", (event) => {
  if (event.target === confirmDialog) closeConfirm(false);
});
closeVideoPreviewBtn.addEventListener("click", closeVideoPreview);
videoPreviewDialog.addEventListener("click", (event) => {
  if (event.target === videoPreviewDialog) closeVideoPreview();
});
closeErrorDialogBtn.addEventListener("click", closeErrorDialog);
errorDialog.addEventListener("click", (event) => {
  if (event.target === errorDialog) closeErrorDialog();
});
window.addEventListener("keydown", (event) => {
  if (!confirmDialog.hidden && event.key === "Escape") closeConfirm(false);
  if (!videoPreviewDialog.hidden && event.key === "Escape") closeVideoPreview();
  if (!errorDialog.hidden && event.key === "Escape") closeErrorDialog();
});
for (const eventName of ["click", "input", "change", "keydown", "submit"]) {
  form.addEventListener(eventName, (event) => {
    if (!controlsLocked) return;
    if (event.target.closest("#processingOverlay") || event.target.closest(".form-lock-banner")) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
}

async function boot() {
  await loadConfig();
  await renderSelectedVideos();
  await loadCurrentJob();
  await loadSystemStatus();
  setInterval(loadCurrentJob, 1500);
  setInterval(loadSystemStatus, 3000);
  setInterval(() => {
    if (recordsPage.classList.contains("active")) loadJobs();
  }, 3000);
}

boot();
