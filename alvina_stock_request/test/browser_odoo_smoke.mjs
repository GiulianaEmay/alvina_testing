/** Run after browser_fixture.py, against a local disposable Odoo test server.
 * ALVINA_BROWSER_FIXTURE=/tmp/alvina-browser-fixture.json ODOO_URL=http://127.0.0.1:8082
 * CHROMIUM=/usr/bin/chromium ODOO_LOGIN=admin ODOO_PASSWORD=admin node tests/browser_odoo_smoke.mjs
 * Only a pending request is reviewed. No delivery or production change occurs.
 */
import assert from "node:assert/strict";
import { readFile, mkdtemp, rm, stat } from "node:fs/promises";
import { spawn } from "node:child_process";
const fixturePath =
  process.env.ALVINA_BROWSER_FIXTURE || "/tmp/alvina-browser-fixture.json";
assert.equal(
  (await stat(fixturePath)).mode & 0o077,
  0,
  "Fixture must be private (chmod 600)",
);
const fixture = JSON.parse(await readFile(fixturePath, "utf8"));
assert(
  fixture.db === "alvina_code128_clean" ||
    fixture.db?.startsWith("alvina_browser_"),
  "Disposable test database required",
);
const baseUrl = new URL(process.env.ODOO_URL || "http://127.0.0.1:8082");
assert(
  ["127.0.0.1", "localhost", "[::1]"].includes(baseUrl.hostname),
  "Only local Odoo test servers are permitted",
);
assert(
  !baseUrl.username && !baseUrl.password,
  "Do not embed credentials in the URL",
);
const login = process.env.ODOO_LOGIN || "admin",
  password = process.env.ODOO_PASSWORD || "admin";
const temp = await mkdtemp("/tmp/alvina-ui-");
const child = spawn(
  process.env.CHROMIUM || "chromium",
  [
    "--headless",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    `--user-data-dir=${temp}`,
    "--remote-debugging-port=0",
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
let socket;
const events = [];
try {
  const ws = await new Promise((res, rej) => {
    let log = "";
    const timer = setTimeout(() => rej(Error("start timeout")), 15000);
    child.stderr.on("data", (chunk) => {
      log += chunk;
      const match = /DevTools listening on (ws:\/\/\S+)/.exec(log);
      if (match) {
        clearTimeout(timer);
        res(match[1]);
      }
    });
  });
  socket = new WebSocket(ws);
  await new Promise((res) =>
    socket.addEventListener("open", res, { once: true }),
  );
  let id = 0;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) {
      pending.get(data.id)(data);
      pending.delete(data.id);
    } else events.push(data);
  });
  const call = (method, params = {}, sessionId) =>
    new Promise((res, rej) => {
      const next = ++id;
      const timer = setTimeout(() => {
        pending.delete(next);
        rej(Error("Browser command timed out: " + method));
      }, 30000);
      pending.set(
        next,
        (data) => (
          clearTimeout(timer),
          data.error
            ? rej(Error("Browser command failed: " + method))
            : res(data.result)
        ),
      );
      socket.send(JSON.stringify({ id: next, method, params, sessionId }));
    });
  const { targetId } = await call("Target.createTarget", {
    url: new URL("/web/login?db=" + encodeURIComponent(fixture.db), baseUrl)
      .href,
  });
  const { sessionId } = await call("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  await call("Runtime.enable", {}, sessionId);
  await call("Network.enable", {}, sessionId);
  await call("Page.enable", {}, sessionId);
  const evaluate = async (expression) => {
    const out = await call(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true },
      sessionId,
    );
    if (out.exceptionDetails)
      throw Error(out.exceptionDetails.text || "Browser evaluation failed");
    return out.result.value;
  };
  const wait = async (expression) => {
    const started = Date.now();
    while (Date.now() - started < 30000) {
      if (await evaluate(expression)) return;
      await new Promise((res) => setTimeout(res, 200));
    }
    throw Error("Timed out waiting for the browser condition");
  };
  await wait("document.readyState === 'complete'");
  assert(
    await evaluate(
      `fetch('/web/session/authenticate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jsonrpc:'2.0',method:'call',params:${JSON.stringify({ db: fixture.db, login, password })}})}).then(r=>r.json()).then(r=>Boolean(r.result?.uid))`,
    ),
  );
  await call(
    "Page.navigate",
    { url: new URL("/odoo/discuss?debug=assets", baseUrl).href },
    sessionId,
  );
  await wait("Boolean(window.odoo?.isReady)");
  // isReady precedes initial action loading. Wait for that action to mount so
  // the first wizard action does not race Odoo's own startup navigation.
  await wait("Boolean(odoo.__WOWL_DEBUG__.root.env.services.action.currentController) && Boolean(document.querySelector('.o-mail-Discuss'))");
  await evaluate(
    `window.alvinaComponents = () => { const items=[]; const visit=node=>{if(node.component)items.push(node.component);Object.values(node.children || {}).forEach(visit);};visit(odoo.__WOWL_DEBUG__.root.__owl__);return items; }; true`,
  );
  const action = `{type:'ir.actions.act_window',res_model:'alvina.stock.pickup.wizard',views:[[false,'form']],target:'new'}`;
  await evaluate(
    `odoo.__WOWL_DEBUG__.root.env.services.action.doAction(${action}).then(()=>true)`,
  );
  await wait(
    "Boolean(document.querySelector('.o_alvina_pickup_code input.o_input'))",
  );
  const codeField = await evaluate(
    "alvinaComponents().filter(c=>c.constructor.name==='PickupCodeField').length",
  );
  assert.equal(codeField, 1);
  const beforeEvents = events.length;
  await evaluate(
    `const input=document.querySelector('.o_alvina_pickup_code input.o_input');input.focus();input.value=${JSON.stringify(fixture.code128)};input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText'}));input.dispatchEvent(new Event('change',{bubbles:true}));input.blur();true`,
  );
  await wait(
    `alvinaComponents().find(c=>c.constructor.name==='PickupCodeField').props.record.data.code === ${JSON.stringify(fixture.code128)}`,
  );
  assert(
    await evaluate(
      "Array.from(document.querySelectorAll('.modal-footer button')).some(b=>b.textContent.includes('Revisar solicitud'))",
    ),
  );
  await evaluate(
    `alvinaComponents().find(c=>c.constructor.name==='PickupCodeField').setRecognizedCode(${JSON.stringify(fixture.code128)}).then(()=>true)`,
  );
  await wait(
    "document.querySelector('.o_alvina_pickup_code [role=status]')?.textContent.includes('Revisar solicitud')",
  );
  const forbidden = events
    .slice(beforeEvents)
    .filter(
      (e) =>
        e.method === "Network.requestWillBeSent" &&
        e.params.request.postData &&
        /"method"\s*:\s*"action_(scan|redeem)"/.test(e.params.request.postData),
    );
  assert.equal(
    forbidden.length,
    0,
    "Recognized code must not review or deliver automatically",
  );
  // Camera can be opened and dismissed even if hardware is absent/permission is pending.
  await evaluate(
    "alvinaComponents().find(c=>c.constructor.name==='PickupCodeField').openCamera();true",
  );
  await wait(
    "Boolean(document.querySelector('.o_alvina_camera_reader video'))",
  );
  await evaluate(
    "alvinaComponents().find(c=>c.constructor.name==='PickupCameraDialog').close();true",
  );
  await wait("!document.querySelector('.o_alvina_camera_reader')");
  await evaluate(
    "Array.from(document.querySelectorAll('.modal-footer button')).find(b=>b.textContent.includes('Revisar solicitud')).click();true",
  );
  await wait(
    "document.body.textContent.includes('Browser test Papel') && document.body.textContent.includes('Autorizar y entregar')",
  );
  const request = await evaluate(
    "alvinaComponents().find(c=>c.constructor.name==='PickupCodeField').props.record.data.request_id.id",
  );
  assert.equal(request, fixture.request_id);
  const status = await evaluate(
    `odoo.__WOWL_DEBUG__.root.env.services.orm.read('alvina.stock.request',[${fixture.request_id}],['state','picking_id']).then(r=>({state:r[0].state,picking:r[0].picking_id}))`,
  );
  assert.equal(status.state, "submitted");
  assert.equal(status.picking, false);
  await evaluate(
    "odoo.__WOWL_DEBUG__.root.env.services.action.doAction({type:'ir.actions.act_window_close'}).then(()=>true)",
  );
  await evaluate(
    `odoo.__WOWL_DEBUG__.root.env.services.action.doAction({type:'ir.actions.act_window',res_model:'alvina.stock.qr.wizard',res_id:${fixture.receipt_id},views:[[false,'form']],target:'new'}).then(()=>true)`,
  );
  await wait(
    "Boolean(document.querySelector('.o_alvina_code128_receipt img')?.naturalWidth)",
  );
  const receipt = await evaluate(
    "(()=>{const img=document.querySelector('.o_alvina_code128_receipt img');const box=document.querySelector('.o_alvina_code128_receipt');return {width:img.naturalWidth,height:img.naturalHeight,shownWidth:img.getBoundingClientRect().width,overflow:getComputedStyle(box).overflowX,products:document.body.textContent.includes('Browser test Papel')};})()",
  );
  assert.equal(receipt.width, 900);
  assert.equal(receipt.height, 180);
  assert.equal(receipt.shownWidth, 900);
  assert.equal(receipt.overflow, "auto");
  assert(receipt.products);
  const receiptImageUrl = await evaluate(
    "document.querySelector('.o_alvina_code128_receipt img').src",
  );
  await evaluate(
    "odoo.__WOWL_DEBUG__.root.env.services.action.doAction({type:'ir.actions.act_window_close'}).then(()=>true)",
  );
  await evaluate(
    `odoo.__WOWL_DEBUG__.root.env.services.action.doAction(${action}).then(()=>true)`,
  );
  await wait(
    "Boolean(document.querySelector('.o_alvina_pickup_code input[type=file]'))",
  );
  const imageEvents = events.length;
  await evaluate(
    `fetch(${JSON.stringify(receiptImageUrl)}).then(r=>r.blob()).then(blob=>{const transfer=new DataTransfer();transfer.items.add(new File([blob],'codigo.png',{type:'image/png'}));const input=document.querySelector('.o_alvina_pickup_code input[type=file]');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));return true;})`,
  );
  await wait(
    `alvinaComponents().find(c=>c.constructor.name==='PickupCodeField').props.record.data.code === ${JSON.stringify(fixture.code128)} && document.querySelector('.o_alvina_pickup_code [role=status]')?.textContent.includes('Revisar solicitud')`,
  );
  assert.equal(
    await evaluate(
      "Boolean(alvinaComponents().find(c=>c.constructor.name==='PickupCodeField').props.record.data.request_id)",
    ),
    false,
  );
  assert.equal(
    events
      .slice(imageEvents)
      .filter(
        (e) =>
          e.method === "Network.requestWillBeSent" &&
          e.params.request.postData &&
          /"method"\s*:\s*"action_(scan|redeem)"/.test(
            e.params.request.postData,
          ),
      ).length,
    0,
  );
  const errors = events
    .filter((e) => e.method === "Runtime.exceptionThrown")
    .map(
      (e) =>
        e.params.exceptionDetails.exception?.description ||
        e.params.exceptionDetails.text,
    );
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify(
      {
        passed: 9,
        checks: [
          "Real Odoo19 assets load and Owl widget mounts",
          "Standard CharField receives HID/manual-style input",
          "Recognized text updates only code and displays review status",
          "No automatic scan/redeem RPC",
          "Camera dialog opens and closes",
          "Explicit review shows products and links correct request",
          "Review preserves submitted state without transfer",
          "Receipt displays products and 900x180 Code128 with horizontal scroll",
          "Real file-input change decodes PNG locally and sets code without review RPC",
        ],
        receipt,
        runtimeErrors: errors,
      },
      null,
      2,
    ),
  );
} finally {
  socket?.close();
  child.kill("SIGTERM");
  await new Promise((res) => child.once("exit", res));
  await rm(temp, { recursive: true, force: true });
}
