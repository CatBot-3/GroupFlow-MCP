// Screenshot pages of the running demo with an installed Chrome or Edge, no extra packages.
//
//   node scripts/shoot.mjs <chrome-path> <out.png> <url> [width] [height] [wait-ms]
//
// It starts the browser headless with a throwaway profile, opens the URL over the DevTools protocol
// (Node 22+ has WebSocket built in), waits for the page to load its data, and saves a PNG.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [chrome, out, url, width = '1600', height = '1200', waitMs = '4000'] = process.argv.slice(2);
if (!chrome || !out || !url) {
  console.error('usage: node scripts/shoot.mjs <chrome-path> <out.png> <url> [width] [height] [wait-ms]');
  process.exit(2);
}

const profile = mkdtempSync(join(tmpdir(), 'gf-shot-'));
const port = 9300 + Math.floor(Math.random() * 400);
const browser = spawn(chrome, [
  '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
  '--disable-gpu', '--hide-scrollbars', `--window-size=${width},${height}`,
  ...(process.getuid?.() === 0 ? ['--no-sandbox'] : []), 'about:blank',
], { stdio: 'ignore' });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      const page = list.find((t) => t.type === 'page');
      if (page) return page.webSocketDebuggerUrl;
    } catch { /* not up yet */ }
    await sleep(200);
  }
  throw new Error('The browser did not start.');
}

try {
  const ws = new WebSocket(await target());
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0;
  const pending = new Map();
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  };
  const send = (method, params = {}) => new Promise((r) => { const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params })); });
  await send('Emulation.setDeviceMetricsOverride', { width: Number(width), height: Number(height), deviceScaleFactor: 1, mobile: false });
  await send('Page.enable');
  await send('Page.navigate', { url });
  await sleep(Number(waitMs));
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  if (!shot.result) throw new Error(JSON.stringify(shot.error));
  writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  console.log(`saved ${out}`);
  ws.close();
} finally {
  browser.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* the browser may still hold a file */ }
}
