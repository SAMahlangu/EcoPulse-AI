// Authenticated Smart Energy flow in an isolated Edge/Chrome CDP profile.
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
  const energyWrites = [];
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
        if (request.method !== 'GET' && /\/api\/v1\/energy\//.test(request.url)) {
          energyWrites.push({ requestId: message.params.requestId, method: request.method, path: new URL(request.url).pathname, status: null });
        }
      }
      if (message.method === 'Network.responseReceived' && /\/api\/v1\/energy\//.test(message.params.response.url)) {
        const entry = energyWrites.find(item => item.requestId === message.params.requestId);
        if (entry) entry.status = message.params.response.status;
      }
      if (message.method === 'Network.loadingFailed') {
        const entry = energyWrites.find(item => item.requestId === message.params.requestId);
        if (entry) entry.error = message.params.errorText;
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
    await send('Emulation.setTimezoneOverride', { timezoneId: 'Africa/Johannesburg' });
    await send('Page.navigate', { url: `${FRONTEND}/resident/energy` });
    await until("document.querySelector('[data-testid=energy-daily-form]')", 'Existing Energy management page did not load');
    assert.equal(await evaluate("document.querySelectorAll('a[href=\"/resident/energy\"]').length"), 1, 'Energy navigation was duplicated');
    assert.ok(await evaluate("document.body.textContent.includes('Community Electricity Status')"), 'Community electricity panel was lost');
    assert.ok(await evaluate("document.querySelectorAll('.ei-stat').length===6"), 'Six integrated KPI cards did not render');
    console.log('PASS: existing energy route, navigation, community panel, actual controls and six KPI cards.');

    await evaluate("document.querySelector('.ei-settings').open=true");
    await field('energy-settings-form', 'Tariff', '2.75');
    await submit('energy-settings-form');
    await until("document.querySelector('.ei-settings summary').textContent.includes('15')", 'Household settings did not load');
    await until("!document.querySelector('[data-testid=energy-settings-form] button').disabled", 'Settings save did not finish');
    const actualBefore = await api('/energy/consumption');
    const notificationsBefore = await api('/notifications');
    const appliancesBefore = await api('/energy/appliances');
    if (process.argv.includes('--actual-only')) {
      await field('energy-daily-form', 'Energy consumed', '22');
      console.log('Manual-entry field before submit:', JSON.stringify(await evaluate(`(() => ({date:document.querySelector('[data-testid="energy-daily-form"] input[type=date]').value, kwh:document.querySelector('[data-testid="energy-daily-form"] input[type=number]').value}))()`)));
      await submit('energy-daily-form');
      await until("document.querySelector('[data-testid=energy-daily-form] [role=status]')?.textContent.includes('Daily consumption saved')", 'Actual daily entry did not save', 120000);
      const actualAfter = await api('/energy/consumption');
      console.log('Browser actual-reading count:', actualBefore.total, '=>', actualAfter.total, 'days:', JSON.stringify((actualAfter.items || []).map(row => ({ date: row.date, kwh: row.kwh }))));
      console.log('Energy write statuses:', JSON.stringify(energyWrites));
      assert.equal(actualAfter.total, actualBefore.total + 1, 'Actual consumption did not persist');
      await until("document.querySelector('.ei-stat')?.textContent.includes('22')", 'Actual consumption KPI did not refresh');
    } else {
    await click(byTestId('energy-simulation-tab'));
    await until("document.querySelector('[data-testid=energy-simulation-form]')", 'Energy simulation controls missing');
    await field('energy-simulation-form', 'Simulation Duration', '30');
    await field('energy-simulation-form', 'Daily Electricity Target', '15');
    await submit('energy-simulation-form');
    await until("document.querySelector('[data-testid=energy-chart]')", 'Simulation graph did not load');
    if (await evaluate(`Boolean(document.querySelector(${JSON.stringify(byTestId('energy-sim-skip'))}))`)) await click(byTestId('energy-sim-skip'));
    await until("document.querySelectorAll('[data-testid=energy-day-select] option').length>=31", 'Thirty simulated days did not render');
    await until("document.querySelectorAll('[data-testid=energy-chart] .recharts-line-curve').length>=3", 'History chart lines did not render');
    assert.ok(await evaluate("document.body.textContent.includes('DEMO MODE') && document.body.textContent.includes('SIMULATED DATA')"), 'Simulation label missing');
    assert.ok(await evaluate("document.body.textContent.includes('Assumed demo tariff') && /2[,.]75/.test(document.body.textContent)"), 'Assumed demo tariff and simulated cost model were not shown');
    const beforeSpike = await evaluate("document.querySelector('[data-testid=energy-chart]').dataset.chartEnd");
    await click(byTestId('energy-sim-geyser'));
    await until(`document.querySelector('[data-testid=energy-chart]')?.dataset.chartEnd!==${JSON.stringify(beforeSpike)}`, 'Geyser overuse did not advance chart');
    await until("document.body.textContent.includes('Critical Consumption')", 'Geyser simulation did not show critical status');
    assert.ok(await evaluate("document.body.textContent.includes('Unusual')"), 'Historical spike screening missing');
    console.log('PASS: thirty-day generation, graph playback, geyser spike, target status and anomaly alerts.');

    await click(byTestId('energy-sim-analyse'));
    await until("document.querySelector('[data-testid=energy-analysis] .ei-insight-copy')?.textContent.length>40", 'AI or rule advice did not render', 120000);
    await field('energy-explanation-form', 'Tell EcoPulse', 'We used the geyser for longer than usual because we had visitors.');
    await evaluate("[...document.querySelectorAll('[data-testid=energy-explanation-form] label')].find(item=>item.textContent==='Geyser usage')?.querySelector('input').click()");
    await submit('energy-explanation-form');
    await until("document.querySelector('[data-testid=energy-explanation-form]')?.textContent.includes('saved')", 'Household explanation did not save', 120000);
    console.log('PASS: saved Gemini/rule explanation, contextual household explanation and qualified recommendations.');

    await until("document.querySelector('[data-testid=energy-appliance-form]')", 'Appliance modeller missing');
    await field('energy-appliance-form', 'Power rating', '2000');
    await field('energy-appliance-form', 'Operating hours', '5');
    await submit('energy-appliance-form');
    await until("document.querySelector('#energy-appliances')?.textContent.includes('10') && !document.querySelector('[data-testid=energy-appliance-form] button[type=submit]')?.disabled", 'Appliance model did not render');
    assert.deepEqual(await api('/energy/appliances'), appliancesBefore, 'Simulated appliance changed actual inventory');
    await screenshot('energy-desktop.png');
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await delay(500);
    assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Energy page overflows at 390px');
    assert.ok(await evaluate("document.querySelector('[data-testid=energy-chart] .recharts-surface')?.getBoundingClientRect().height>150"), 'Mobile graph failed to render');
    await screenshot('energy-mobile.png');
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1100, deviceScaleFactor: 1, mobile: false });
    console.log('PASS: appliance modelling isolation, desktop/mobile chart rendering and no horizontal overflow.');

    await click(byTestId('energy-sim-reset'));
    await until("!document.querySelector('[data-testid=energy-day-select]') || document.querySelectorAll('[data-testid=energy-day-select] option').length<=1", 'Reset retained simulated days');
    assert.deepEqual(await api('/energy/consumption'), actualBefore, 'Simulation altered actual consumption');
    assert.deepEqual(await api('/notifications'), notificationsBefore, 'Simulation sent actual customer notifications');
    assert.deepEqual(await api('/energy/appliances'), appliancesBefore, 'Reset altered actual appliance records');
    await click(byTestId('energy-actual-tab'));
    await until("document.querySelector('[data-testid=energy-daily-form]')", 'Actual daily entry failed after reset');
    await field('energy-daily-form', 'Energy consumed', '22');
    await submit('energy-daily-form');
    await until("document.querySelector('[data-testid=energy-daily-form] [role=status]')?.textContent.includes('Daily consumption saved')", 'Actual daily entry did not save', 120000);
    const actualAfter = await api('/energy/consumption');
    console.log('Browser actual-reading count:', actualBefore.total, '=>', actualAfter.total, 'days:', JSON.stringify((actualAfter.items || []).map(row => ({ date: row.date, kwh: row.kwh }))));
    console.log('Energy write statuses:', JSON.stringify(energyWrites));
    assert.equal(actualAfter.total, actualBefore.total + 1, 'Actual consumption did not persist');
    await until("document.querySelector('.ei-stat')?.textContent.includes('22')", 'Actual consumption KPI did not refresh');
    }
    assert.deepEqual(incidentWrites, [], 'Energy demonstration submitted an infrastructure incident');
    assert.deepEqual(runtimeErrors, [], 'Browser raised runtime exceptions');
    console.log(process.argv.includes('--actual-only')
      ? 'PASS: actual daily persistence, KPI refresh and no runtime errors.'
      : 'PASS: reset preserves real records/notifications, actual daily persistence and no runtime errors.');
  } catch (error) {
    console.error('Smart Energy browser verification failed:', error.message);
    if (evaluate) {
      console.error('Energy write statuses:', JSON.stringify(energyWrites));
      const status = await evaluate("JSON.stringify({path:location.pathname,errors:[...document.querySelectorAll('.household-energy [role=alert]')].map(item=>item.textContent).slice(0,3)})").catch(() => 'unavailable');
      console.error('Energy page status:', status);
      await screenshot('energy-browser-failure.png').catch(() => {});
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
      else console.log('PASS: temporary browser account and its private energy records deleted.');
    }
  }
}

run();
