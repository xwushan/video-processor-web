(() => {
  const transientStatuses = new Set([408, 429, 502, 503, 504]);
  const abortError = () => new DOMException("请求已取消", "AbortError");

  function waitForRetry(delay, signal) {
    return new Promise((resolve, reject) => {
      if (signal?.aborted) { reject(abortError()); return; }
      const cancel = () => {
        clearTimeout(timer);
        signal.removeEventListener("abort", cancel);
        reject(abortError());
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener("abort", cancel);
        resolve();
      }, delay);
      signal?.addEventListener("abort", cancel, { once: true });
    });
  }

  async function requestJson(url, init = {}, options = {}) {
    const { fallback = "请求失败", timeoutMs = 30000, retryDelayMs = 1000, onRetry } = options;
    // Only retry requests that cannot create another task or trial.
    const safeToRetry = ["GET", "HEAD", "PUT"].includes((init.method || "GET").toUpperCase());
    const retries = safeToRetry ? (options.retries ?? 0) : 0;
    const parentSignal = init.signal;
    for (let attempt = 0; ; attempt += 1) {
      if (parentSignal?.aborted) throw abortError();
      const controller = new AbortController();
      const cancel = () => controller.abort();
      parentSignal?.addEventListener("abort", cancel, { once: true });
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      let retryError;
      try {
        const response = await fetch(url, { ...init, signal: controller.signal });
        let payload;
        try {
          payload = await response.json();
        } catch (error) {
          if (parentSignal?.aborted) throw abortError();
          if (response.ok) {
            const incomplete = new Error("服务器响应不完整");
            incomplete.retryable = true;
            throw incomplete;
          }
          payload = {};
        }
        if (!response.ok) {
          const error = new Error(payload.detail || (response.status === 401 ? "登录已失效，请重新登录" : fallback));
          error.status = response.status;
          error.retryable = transientStatuses.has(response.status);
          throw error;
        }
        return payload;
      } catch (error) {
        if (parentSignal?.aborted) throw abortError();
        if (!timedOut && !(error instanceof TypeError) && !error.retryable) throw error;
        retryError = error;
      } finally {
        clearTimeout(timer);
        parentSignal?.removeEventListener("abort", cancel);
      }
      if (attempt >= retries) {
        const error = new Error("无法连接服务器或响应中断，请确认服务已运行且网络畅通。");
        error.networkFailure = true;
        error.cause = retryError;
        throw error;
      }
      onRetry?.(attempt + 1, retries);
      await waitForRetry(retryDelayMs * 2 ** attempt, parentSignal);
    }
  }

  window.VideoProcessorNetwork = { requestJson };
})();
