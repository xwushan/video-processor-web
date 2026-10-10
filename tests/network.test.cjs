const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../web_app/static/network.js'), 'utf8');
function network(fetch) {
  const scope = { window: {}, fetch, setTimeout, clearTimeout, AbortController, DOMException, TypeError };
  vm.runInNewContext(source, scope);
  return scope.window.VideoProcessorNetwork;
}
const fast = { retries: 2, retryDelayMs: 1, timeoutMs: 1000 };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

test('lost chunk acknowledgement retries the same bytes without another committed chunk', async () => {
  const body = new Blob(['video-data']);
  const stored = new Map();
  const retries = [];
  let calls = 0;
  const api = network(async (url, init) => {
    calls += 1;
    assert.equal(init.body, body);
    if (!stored.has(url)) stored.set(url, await init.body.text());
    if (calls === 1) throw new TypeError('Failed to fetch');
    return json({ ok: true, already_received: true });
  });
  const result = await api.requestJson('/chunks/0/1', { method: 'PUT', body },
    { ...fast, onRetry: attempt => retries.push(attempt) });
  assert.equal(result.already_received, true);
  assert.equal(calls, 2);
  assert.equal(stored.size, 1);
  assert.deepEqual(retries, [1]);
});

test('retries are bounded and expose a useful connection error', async () => {
  let calls = 0;
  const api = network(async () => { calls += 1; throw new TypeError('Failed to fetch'); });
  await assert.rejects(api.requestJson('/uploads/session', {}, fast), error =>
    error.networkFailure && error.message.includes('服务器') && !error.message.includes('Failed to fetch'));
  assert.equal(calls, 3);
});

for (const status of [400, 401, 409, 413]) {
  test(`HTTP ${status} is surfaced without repeating the request`, async () => {
    let calls = 0;
    const api = network(async () => { calls += 1; return json({ detail: '保留服务端错误说明' }, status); });
    await assert.rejects(api.requestJson('/chunks/0/0', { method: 'PUT' }, fast), error =>
      error.status === status && error.message === '保留服务端错误说明');
    assert.equal(calls, 1);
  });
}

test('temporary gateway failures can reconnect', async () => {
  let calls = 0;
  const api = network(async () => ++calls === 1 ? json({}, 503) : json({ status: 'done' }));
  assert.equal((await api.requestJson('/estimate', {}, fast)).status, 'done');
  assert.equal(calls, 2);
});

test('POST is never retried even when retries were requested', async () => {
  let calls = 0;
  const api = network(async () => { calls += 1; throw new TypeError('reply lost'); });
  await assert.rejects(api.requestJson('/complete', { method: 'POST' }, fast));
  assert.equal(calls, 1);
});

test('cancel during retry delay prevents another upload', async () => {
  const controller = new AbortController();
  let calls = 0;
  const api = network(async () => { calls += 1; throw new TypeError('offline'); });
  await assert.rejects(api.requestJson('/chunks/0/0', { method: 'PUT', signal: controller.signal },
    { ...fast, onRetry: () => controller.abort() }), error => error.name === 'AbortError');
  assert.equal(calls, 1);
});

test('cancel aborts a request in flight', async () => {
  const controller = new AbortController();
  const api = network((_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true });
    controller.abort();
  }));
  await assert.rejects(api.requestJson('/chunks/0/0', { method: 'PUT', signal: controller.signal }, fast),
    error => error.name === 'AbortError');
});

test('timed out request is aborted and retried', async () => {
  let calls = 0;
  let aborted = false;
  const api = network((_url, init) => {
    if (++calls > 1) return Promise.resolve(json({ ok: true }));
    return new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => {
      aborted = true;
      reject(new DOMException('aborted', 'AbortError'));
    }, { once: true }));
  });
  assert.equal((await api.requestJson('/chunks/0/0', { method: 'PUT' }, { ...fast, timeoutMs: 10 })).ok, true);
  assert.equal(aborted, true);
  assert.equal(calls, 2);
});

test('partial JSON response retries a read safely', async () => {
  let calls = 0;
  const api = network(async () => ++calls === 1 ? new Response('{') : json({ progress: 60 }));
  assert.equal((await api.requestJson('/estimate', {}, fast)).progress, 60);
  assert.equal(calls, 2);
});
