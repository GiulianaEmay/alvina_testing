/**
 * Browser regression checks for the local decoder and camera lifecycle.
 * Run with Node >= 22, Chromium, and the Python environment used by Odoo:
 * ODOO_PYTHON=/path/to/python CHROMIUM=/usr/bin/chromium node tests/browser_reader.mjs
 * No database, server credentials, RPC, or real camera are required.
 */
import assert from "node:assert/strict";
import { readFile, mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync, spawn } from "node:child_process";

const addon = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const temp = await mkdtemp(resolve(tmpdir(), "alvina-browser-"));
const python = process.env.ODOO_PYTHON || "python3";
const chromium = process.env.CHROMIUM || "chromium";
const token = "0000012345678901234567890123456789012345";
const legacy =
  "alvina-stock-request:00000000-0000-4000-8000-000000000001:Test_only_legacy_secret_not_a_live_token_12";
const currentQr = `alvina-stock-request:00000000-0000-4000-8000-000000000001:${token}`;
execFileSync(python, [
  "-c",
  `
from pathlib import Path
from reportlab.graphics.barcode import createBarcodeDrawing
from PIL import Image
import qrcode
out = Path(${JSON.stringify(temp)})
payload = ${JSON.stringify(token)}
createBarcodeDrawing('Code128', value=payload, width=900, height=180,
                   quiet=True, humanReadable=True).save(formats=['png'], outDir=str(out), fnRoot='barcode')
qrcode.make(payload).save(out / 'qr.png')
qrcode.make(${JSON.stringify(legacy)}).save(out / 'legacy.png')
qrcode.make(${JSON.stringify(currentQr)}).save(out / 'current.png')
for extension, format in [('jpg', 'JPEG'), ('webp', 'WEBP')]:
    Image.open(out / 'barcode.png').convert('RGB').save(out / ('barcode.' + extension), format=format)
Image.new('RGB', (400, 200), 'white').save(out / 'blank.png')
`,
]);

const modules = {};
modules["/reader.js"] = (
  await readFile(resolve(addon, "static/src/js/code_reader.js"), "utf8")
)
  .replace(
    /import \{ loadJS \} from "@web\/core\/assets";/,
    'import {loadJS} from "/support.js";',
  )
  .replace(
    /import \{ _t \} from "@web\/core\/l10n\/translation";/,
    'import {_t} from "/support.js";',
  );
modules["/support.js"] = `
export const _t = (message, ...args) => args.reduce((text, value) => text.replace('%s', value), message);
export const loadJS = (src) => new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src; script.onload = resolve; script.onerror = reject;
    document.head.append(script);
});`;
modules["/tests.js"] = `
import {CameraSession, decodeImageFile, validateImageFile, MAX_IMAGE_BYTES} from '/reader.js';
const checks = [];
function assert(value, message) { if (!value) { throw Error(message); } }
async function check(name, body) { try { await body(); checks.push(name); } catch (error) { throw Error(name + ': ' + error.message); } }
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
const tick = () => new Promise(r => setTimeout(r));
const file = async (name, type) => new File([await (await fetch('/fixtures/' + name)).blob()], name, {type});
const mockStream = () => { const track = {stops: 0, stop() { this.stops++; }}; return {track, getTracks: () => [track]}; };
const video = () => ({pause() {}, srcObject: null});
const result = text => ({getText: () => text});

export async function run() {
    for (const [name, type, expected] of [
        ['barcode.png', 'image/png', ${JSON.stringify(token)}],
        ['barcode.jpg', 'image/jpeg', ${JSON.stringify(token)}],
        ['barcode.webp', 'image/webp', ${JSON.stringify(token)}],
        ['qr.png', 'image/png', ${JSON.stringify(token)}],
        ['legacy.png', 'image/png', ${JSON.stringify(legacy)}],
        ['current.png', 'image/png', ${JSON.stringify(currentQr)}],
    ]) {
        await check('Real local decoder: ' + name, async () => {
            assert(await decodeImageFile(await file(name, type)) === expected, name + ' decoded incorrectly');
        });
    }
    await check('Blank image produces readable error', async () => {
        let message = ''; try { await decodeImageFile(await file('blank.png', 'image/png')); }
        catch (error) { message = error.message; }
        assert(message.includes('No se encontró'), 'Unreadable image should not silently succeed');
    });
    await check('MIME/extension/signature/size rejected locally', async () => {
        const valid = await file('qr.png', 'image/png');
        const bad = [
            new File(['<svg/>'], 'code.svg', {type: 'image/svg+xml'}),
            new File([valid], 'code.jpg', {type: 'image/png'}),
            new File(['not a PNG'], 'code.png', {type: 'image/png'}),
            new File([new Uint8Array(MAX_IMAGE_BYTES + 1)], 'large.png', {type: 'image/png'}),
            new File([], 'empty.png', {type: 'image/png'}),
        ];
        for (const image of bad) {
            let failed = false;
            try { await validateImageFile(image); } catch { failed = true; }
            assert(failed, 'Invalid image accepted: ' + image.name);
        }
    });
    await check('Close during permission prompt releases the eventual stream', async () => {
        const request = deferred(), stream = mockStream(); let decoded = 0;
        const session = new CameraSession({video: video(), mediaDevices: {getUserMedia: () => request.promise},
            readerFactory: async () => ({decodeFromStream() {decoded++;}}), onResult() {}, onError() {}});
        const started = session.start(); await tick(); session.close(); request.resolve(stream); await started;
        assert(stream.track.stops > 0 && decoded === 0, 'Pending getUserMedia leaked after close');
    });
    await check('Close while loading decoder never requests camera permission', async () => {
        const decoder = deferred(); let requests = 0;
        const session = new CameraSession({video: video(), mediaDevices: {getUserMedia() { requests++; }},
            readerFactory: () => decoder.promise, onResult() {}, onError() {}});
        const started = session.start(); session.close(); decoder.resolve({}); await started;
        assert(requests === 0, 'Closing reader should cancel pending startup before getUserMedia');
    });
    await check('Close during decoder startup stops eventual scanner controls', async () => {
        const decoder = deferred(), stream = mockStream(); let stopped = 0;
        const session = new CameraSession({video: video(), mediaDevices: {getUserMedia: async () => stream},
            readerFactory: async () => ({decodeFromStream: () => decoder.promise}), onResult() {}, onError() {}});
        const started = session.start(); await tick(); session.close(); decoder.resolve({stop() {stopped++;}}); await started;
        assert(stream.track.stops > 0 && stopped > 0, 'Pending decoder leaked after close');
    });
    await check('Result before startup resolves is delivered once and releases camera', async () => {
        const stream = mockStream(); let stopped = 0; const texts = [], controls = {stop() {stopped++;}};
        const session = new CameraSession({video: video(), mediaDevices: {getUserMedia: async () => stream},
            readerFactory: async () => ({async decodeFromStream(stream, video, callback) {
                callback(result('recognized'), undefined, controls);
                callback(result('duplicate'), undefined, controls); return controls;
            }}), onResult: text => texts.push(text), onError: error => {throw error;}});
        await session.start();
        assert(texts.length === 1 && texts[0] === 'recognized' && stopped > 0 && stream.track.stops > 0,
            'Early result duplicated or leaked camera');
    });
    await check('Switching camera stops old tracks and uses exact selected device', async () => {
        const streams = [], constraints = []; let stopped = 0;
        const session = new CameraSession({video: video(), mediaDevices: {async getUserMedia(value) {
            constraints.push(value); const stream = mockStream(); streams.push(stream); return stream;
        }}, readerFactory: async () => ({decodeFromStream: async () => ({stop() {stopped++;}})}), onResult() {}, onError() {}});
        await session.start(); await session.start('camera-2');
        assert(streams[0].track.stops > 0 && constraints[1].video.deviceId.exact === 'camera-2', 'Camera selection failed');
        session.close(); assert(streams[1].track.stops > 0 && stopped >= 2, 'Selected camera leaked after close');
    });
    await check('Permission denied gives error and does not start scanning', async () => {
        let decoded = 0, error;
        const session = new CameraSession({video: video(), mediaDevices: {async getUserMedia() {
            throw new DOMException('denied', 'NotAllowedError');
        }}, readerFactory: async () => ({decodeFromStream() {decoded++;}}), onResult() {}, onError: value => error = value});
        assert(await session.start() === false && decoded === 0 && error?.name === 'NotAllowedError', 'Denial handled incorrectly');
    });
    return {passed: checks.length, checks};
}
`;

const requests = [];
const server = createServer(async (request, response) => {
  requests.push({ method: request.method, path: request.url });
  try {
    let body, contentType;
    if (request.url === "/") {
      body = "<!doctype html><title>Alvina browser reader tests</title>";
      contentType = "text/html";
    } else if (modules[request.url]) {
      body = modules[request.url];
      contentType = "text/javascript";
    } else if (/^\/fixtures\/[a-z]+\.(png|jpg|webp)$/.test(request.url)) {
      body = await readFile(resolve(temp, request.url.split("/").at(-1)));
      contentType = "application/octet-stream";
    } else if (
      request.url ===
      "/alvina_stock_request/static/lib/zxing_browser/zxing-browser.min.js"
    ) {
      body = await readFile(
        resolve(addon, "static/lib/zxing_browser/zxing-browser.min.js"),
      );
      contentType = "text/javascript";
    } else {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "Content-Type": contentType }).end(body);
  } catch (error) {
    response.writeHead(500).end(error.message);
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}/`;
const child = spawn(
  chromium,
  [
    "--headless",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-default-apps",
    "--no-first-run",
    `--user-data-dir=${temp}/chromium`,
    "--remote-debugging-port=0",
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let socket;
try {
  const wsUrl = await new Promise((resolve, reject) => {
    let log = "";
    const timer = setTimeout(
      () => reject(Error("Chromium startup timed out")),
      15000,
    );
    child.once("error", reject);
    child.stderr.on("data", (data) => {
      log += data;
      const match = /DevTools listening on (ws:\/\/[^\s]+)/.exec(log);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
  });
  socket = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 0;
  const pending = new Map();
  const browserRequests = [];
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data),
      callback = pending.get(message.id);
    if (message.method === "Network.requestWillBeSent") {
      browserRequests.push(message.params.request);
    }
    if (callback) {
      pending.delete(message.id);
      message.error
        ? callback.reject(Error(JSON.stringify(message.error)))
        : callback.resolve(message.result);
    }
  });
  const call = (method, params = {}, sessionId) =>
    new Promise((resolve, reject) => {
      const id = ++nextId;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params, sessionId }));
    });
  const { targetId } = await call("Target.createTarget", { url });
  const { sessionId } = await call("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  await call("Runtime.enable", {}, sessionId);
  await call("Network.enable", {}, sessionId);
  const result = await call(
    "Runtime.evaluate",
    {
      expression: "import('/tests.js').then(t => t.run())",
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId,
  );
  assert(!result.exceptionDetails, JSON.stringify(result.exceptionDetails));
  assert.equal(result.result.value.passed, 14);
  assert(
    requests.every((request) => request.method === "GET"),
    "Images must not be uploaded",
  );
  assert(
    browserRequests.every((request) => request.method === "GET" &&
      new URL(request.url).origin === new URL(url).origin),
    "Decoder must not contact external services or send images",
  );
  assert(
    requests.some((request) => request.path === "/alvina_stock_request/static/lib/zxing_browser/zxing-browser.min.js"),
    "The pinned local vendor bundle must actually be loaded",
  );
  console.log(
    JSON.stringify(
      { ...result.result.value, allRequestsGet: true, localVendorOnly: true },
      null,
      2,
    ),
  );
} finally {
  socket?.close();
  child.kill("SIGTERM");
  await new Promise((resolve) => child.once("exit", resolve));
  await new Promise((resolve) => server.close(resolve));
  await rm(temp, { recursive: true, force: true });
}
