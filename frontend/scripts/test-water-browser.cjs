// Authenticated Smart Water flow in an isolated Edge/Chrome CDP profile.
// API :8000, Vite :5173, CDP :9230 by default. Creates/deletes one fixture account.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const PYTHON = path.join(ROOT, 'backend/venv/Scripts/python.exe');
const FIXTURE = path.join(ROOT, 'backend/tests/ecomove_browser_session.py');
const option = (name, fallback) => process.argv.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) || fallback;
const FRONTEND = option('frontend', 'http://127.0.0.1:5173').replace(/\/$/, '');
const API = option('api', 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '');
const CDP_PORT = option('cdp-port', '9230');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const byTestId = name => `[data-testid="${name}"]`;
let actor, socket, send, evaluate;

async function until(expression, message, timeout = 45000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await delay(200);
  }
  throw new Error(message);
}

async function click(selector) {
  const bounds = await evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) throw new Error('Control unavailable');
    element.scrollIntoView({ block: 'center' });
    const rect = element.getBoundingClientRect();
    return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
  })()`);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', ...bounds, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', ...bounds, button: 'left', clickCount: 1 });
}

async function field(form, label, value) {
  await evaluate(`(() => {
    const parent = document.querySelector(${JSON.stringify(byTestId(form))});
    const target = [...parent.querySelectorAll('label')].find(item => item.textContent.trim().startsWith(${JSON.stringify(label)}));
    const element = target?.querySelector('input,select,textarea');
    if (!element) throw new Error('Field unavailable');
    const prototype = element instanceof HTMLSelectElement ? HTMLSelectElement.prototype : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, ${JSON.stringify(String(value))});
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
}

async function submit(form) {
  await evaluate(`document.querySelector(${JSON.stringify(byTestId(form))}).requestSubmit()`);
}

async function api(pathname) {
  return evaluate(`(async () => {
    const session = JSON.parse(localStorage.getItem('ecopulse.auth.session') || '{}');
    const response = await fetch(${JSON.stringify(API + pathname)}, { headers: { Authorization: 'Bearer ' + session.access_token } });
    if (!response.ok) throw new Error('Fixture API read failed');
    return response.json();
  })()`);
}

async function screenshot(filename) {
  const result = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  fs.mkdirSync(path.join(ROOT, 'diagnostics'), { recursive: true });
  fs.writeFileSync(path.join(ROOT, 'diagnostics', filename), Buffer.from(result.data, 'base64'));
}

async function run() {
  const runtimeErrors = [];
  const incidentWrites = [];
  let injection;
  try {
    const created = spawnSync(PYTHON, [FIXTURE, '--create'], { cwd: ROOT, encoding: 'utf8', timeout: 60000, windowsHide: true });
    assert.equal(created.status, 0, 'Temporary browser account could not be created');
    const ephemeral = JSON.parse(created.stdout);
    actor = ephemeral.id;
    const targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
    const target = targets.find(item => item.type === 'page');
    assert.ok(target, 'Isolated browser CDP page unavailable');
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    let sequence = 0;
    const pending = new Map();
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`CDP ${method} timed out`)); }, 60000);
      pending.set(id, { resolve: result => { clearTimeout(timeout); resolve(result); }, reject: error => { clearTimeout(timeout); reject(error); } });
      socket.send(JSON.stringify({ id, method, params }));
    });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.id) {
        const call = pending.get(message.id);
        if (call) { pending.delete(message.id); message.error ? call.reject(new Error(message.error.message)) : call.resolve(message.result); }
      }
      if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.text);
      if (message.method === 'Network.requestWillBeSent') {
        const request = message.params.request;
        if (request.method !== 'GET' && /\/api\/v1\/(?:incidents|infrastructure\/reports)(?:\/|$)/.test(request.url)) incidentWrites.push(request.method);
      }
    });
    evaluate = async expression => {
      const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (response.exceptionDetails) throw new Error('Browser evaluation failed');
      return response.result.value;
    };
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Network.enable');
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
    injection = await send('Page.addScriptToEvaluateOnNewDocument', {
      source: `if (location.origin === ${JSON.stringify(FRONTEND)}) localStorage.setItem('ecopulse.auth.session', ${JSON.stringify(JSON.stringify(ephemeral.session))});`,
    });
    await send('Page.navigate', { url: `${FRONTEND}/resident/water` });
    await until("document.querySelector('[data-testid=water-simulation-form]')", 'Water management simulation did not load');
    assert.equal(await evaluate("document.querySelectorAll('a[href=\"/resident/water\"]').length"), 1, 'Water navigation was duplicated');
    assert.equal(await evaluate("document.querySelectorAll('.water-tabs').length"), 0, 'Water Management still shows the removed tabs');
    assert.equal(await evaluate("document.querySelectorAll('[data-testid=water-daily-form], .reading-form').length"), 0, 'Actual and meter entry forms should not appear on the demonstration page');
    const actualBefore = await api('/consumption');
    const notificationsBefore = await api('/notifications');
    console.log('PASS: Water Management displays only the simulation demo, with no daily or meter entry tabs.');

    await field('water-simulation-form', 'Simulation Duration', 30);
    await field('water-simulation-form', 'Daily Water Target', 600);
    await click(byTestId('water-sim-generate'));
    await until("document.querySelectorAll('[data-testid=water-day-select] option').length>1", '30 simulated days did not render');
    assert.equal(await evaluate("document.querySelector('[aria-label=\"Consumption date range\"]').value"), 'month', 'The simulation graph did not switch to this month');
    assert.ok(await evaluate("document.querySelector('[data-testid=water-chart]').dataset.chartStart.endsWith('-01')"), 'The monthly simulation graph did not start on the first of the month');
    await until("document.querySelectorAll('[data-testid=water-chart] .recharts-line-curve').length>=3", 'Simulation chart lines did not render');
    assert.ok(await evaluate("document.body.textContent.includes('SIMULATED DATA')"), 'Simulation labelling missing');
    const nextDate = await evaluate("(() => { const date = new Date(document.querySelector('[data-testid=water-chart]').dataset.chartEnd + 'T12:00:00'); date.setDate(date.getDate() + 1); return date.toISOString().slice(0, 10); })()");
    await click(byTestId('water-sim-next'));
    await until(`document.querySelector('[data-testid=water-chart]')?.dataset.chartEnd===${JSON.stringify(nextDate)}`, 'Simulate Next Day did not add a date');
    await click(byTestId('water-sim-leak'));
    await until("document.body.textContent.includes('Possible leak') || document.body.textContent.includes('Potential Water Leak') || document.body.textContent.includes('possible_leak')", 'Possible leak alert did not render');
    console.log('PASS: 30-day simulation, next day, interactive graph and triggered leak alert.');

    await click(byTestId('water-sim-analyse'));
    await until("document.querySelector('[data-testid=water-analysis] .wi-insight-copy')?.textContent.length>40", 'Gemini or rules analysis did not render', 120000);
    await until("document.querySelector('[data-testid=water-explanation-form]')", 'Consumption explanation form missing');
    await field('water-explanation-form', 'Tell EcoPulse', 'We had visitors and did additional laundry.');
    await submit('water-explanation-form');
    await until("document.querySelector('[data-testid=water-explanation-form]')?.textContent.includes('saved') || document.querySelector('[data-testid=water-explanation-form]')?.textContent.includes('updated')", 'Explanation and contextual assessment did not save', 120000);
    await field('water-explanation-form', 'Tell EcoPulse', 'Visitors have left; we will monitor the meter and check visible taps.');
    await submit('water-explanation-form');
    await until("!document.querySelector('[data-testid=water-explanation-form] button')?.disabled", 'Updated explanation analysis did not complete', 120000);
    console.log('PASS: backend AI/rules analysis and editable household explanations.');

    await screenshot('water-desktop.png');
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await delay(600);
    assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Water page overflows at 390px');
    assert.ok(await evaluate("document.querySelector('[data-testid=water-chart]').getBoundingClientRect().width>=280"), 'Mobile graph is too narrow');
    assert.ok(await evaluate("document.querySelector('[data-testid=water-chart] .recharts-surface')?.getBoundingClientRect().height>150"), 'Mobile graph did not render');
    await screenshot('water-mobile.png');
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
    console.log('PASS: desktop/mobile chart rendering and no horizontal overflow.');

    await click(byTestId('water-sim-reset'));
    await until("document.querySelectorAll('[data-testid=water-day-select] option').length<=1", 'Simulation reset did not clear demo records');
    assert.deepEqual(await api('/consumption'), actualBefore, 'Simulation changed actual consumption');
    assert.deepEqual(await api('/notifications'), notificationsBefore, 'Simulation created real notifications');
    console.log('PASS: simulation reset preserves actual consumption and real notification history.');

    await until("document.querySelector('[data-testid=water-sim-generate]')", 'Simulation controls disappeared after reset');
    await click(byTestId('water-sim-generate'));
    await until("document.querySelectorAll('[data-testid=water-day-select] option').length>1", 'Fresh simulation did not load');
    await click(byTestId('water-sim-leak'));
    await until("document.querySelector('[data-testid=water-investigate]')", 'Existing incident handoff unavailable');
    await click(byTestId('water-investigate'));
    await until("location.pathname.includes('/resident/report-incident')", 'Leak investigation did not open existing incident workflow');
    assert.ok(await evaluate("document.body.textContent.toLowerCase().includes('simulat')"), 'Simulated incident handoff did not retain a demo warning');
    assert.deepEqual(incidentWrites, [], 'Browser flow unexpectedly submitted a real incident');
    assert.deepEqual(runtimeErrors, [], 'Browser raised runtime exceptions');
    console.log('PASS: simulated leak-report handoff; no incident submitted and no browser runtime errors.');
  } catch (error) {
    console.error('Smart Water browser verification failed:', error.message);
    if (evaluate) {
      const status = await evaluate("JSON.stringify({path:location.pathname,errors:[...document.querySelectorAll('.household-water [role=alert]')].map(item=>item.textContent).slice(0,3)})").catch(() => 'unavailable');
      console.error('Water page status:', status);
    }
    process.exitCode = 1;
  } finally {
    if (send && injection) await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: injection.identifier }).catch(() => {});
    if (evaluate) await evaluate("localStorage.removeItem('ecopulse.auth.session')").catch(() => {});
    if (send) await send('Page.navigate', { url: 'about:blank' }).catch(() => {});
    socket?.close();
    if (actor) {
      const deleted = spawnSync(PYTHON, [FIXTURE, '--delete', actor], { cwd: ROOT, encoding: 'utf8', timeout: 30000, windowsHide: true });
      if (deleted.status !== 0) { console.error('Temporary browser account cleanup failed; inspect fixture accounts.'); process.exitCode = 1; }
      else console.log('PASS: temporary browser account and its private water records deleted.');
    }
  }
}

run();
