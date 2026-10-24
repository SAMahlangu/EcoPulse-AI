// Real Edge/CDP checks with a temporary Supabase account, deleted in finally.
// Requires API :8000, frontend :5173, isolated headless Edge CDP :9229.
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '../..');
const PYTHON = path.join(ROOT, 'backend/venv/Scripts/python.exe');
const fixtureScript = path.join(ROOT, 'backend/tests/ecomove_browser_session.py');
const fixture = JSON.parse(fs.readFileSync(path.join(ROOT, 'database/seeds/ecomove_pretoria_demo.json'), 'utf8'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let actor, socket, send, evaluate;

async function until(expression, message, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await delay(200);
  }
  throw new Error(message);
}

async function click(selector) {
  const rect = await evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) throw new Error('Element unavailable');
    element.scrollIntoView({ block: 'center' });
    const bounds = element.getBoundingClientRect();
    return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
  })()`);
  await mouse(rect.x, rect.y);
}

async function chooseConfiguredStop(name) {
  await evaluate(`(() => {
    const select = document.querySelector('[aria-label="Choose a configured destination"]');
    const option = [...select.options].find(candidate => candidate.text.includes(${JSON.stringify(name)}));
    if (!option) throw new Error(${JSON.stringify(`Configured ${name} missing`)});
    select.value = option.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  })()`);
}

async function saveScreenshot(filename) {
  const screenshot = await send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
  });
  fs.writeFileSync(path.join(ROOT, filename), Buffer.from(screenshot.data, 'base64'));
}

async function mouse(x, y) {
  await send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x, y, button: 'left', clickCount: 1,
  });
  await send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x, y, button: 'left', clickCount: 1,
  });
}

(async () => {
  const runtimeErrors = [];
  const journeyResponses = [];
  try {
    const creation = spawnSync(PYTHON, [fixtureScript, '--create'], {
      cwd: ROOT,
      encoding: 'utf8',
      timeout: 60000,
      windowsHide: true,
    });
    assert.equal(creation.status, 0, 'Could not provision the temporary real-auth browser account');
    const ephemeral = JSON.parse(creation.stdout);
    actor = ephemeral.id;
    const targets = await (await fetch('http://127.0.0.1:9229/json/list')).json();
    const target = targets.find(item => item.type === 'page');
    assert.ok(target, 'Isolated Edge test page unavailable');
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    let sequence = 0;
    const pending = new Map();
    socket.addEventListener('message', event => {
      const data = JSON.parse(event.data);
      if (data.id) {
        const call = pending.get(data.id);
        if (call) {
          pending.delete(data.id);
          data.error
            ? call.reject(new Error(data.error.message))
            : call.resolve(data.result);
        }
      }
      if (data.method === 'Runtime.exceptionThrown') runtimeErrors.push(data.params.exceptionDetails.text);
    });
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
    evaluate = async expression => {
      const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (response.exceptionDetails) throw new Error('Browser evaluation failed');
      return response.result.value;
    };
    await send('Page.enable');
    await send('Runtime.enable');
    await send('Network.enable');
    socket.addEventListener('message', event => {
      const data = JSON.parse(event.data);
      if (
        data.method === 'Network.responseReceived'
        && data.params.response.url.includes('/ecomove/journey/search')
      ) {
        journeyResponses.push({
          requestId: data.params.requestId,
          status: data.params.response.status,
        });
      }
    });
    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1050, deviceScaleFactor: 1, mobile: false });
    const geoRequestProbe = `if (location.port === '5173') {
      localStorage.setItem('ecopulse.auth.session', ${JSON.stringify(JSON.stringify(ephemeral.session))});
      window.__emGeoRequests = 0;
      const nativeGetCurrentPosition = navigator.geolocation.getCurrentPosition.bind(navigator.geolocation);
      navigator.geolocation.getCurrentPosition = (...args) => {
        window.__emGeoRequests += 1;
        return nativeGetCurrentPosition(...args);
      };
    }`;
    const injection = await send('Page.addScriptToEvaluateOnNewDocument', {
      source: geoRequestProbe,
    });
    await send('Page.navigate', { url: 'http://127.0.0.1:5173/resident/ecomove' });
    await until(
      "document.querySelector('.ecomove-leaflet') && document.querySelectorAll('.em-route-card').length===4 && document.querySelectorAll('.ecomove-taxi-marker').length>=8",
      'EcoMove map, four road routes and taxis did not render',
      60000,
    );
    assert.equal(await evaluate('window.__emGeoRequests'), 0, 'GPS requested without resident action');
    assert.ok(await evaluate("document.querySelector('.em-demo-banner').textContent.includes('Not live GPS tracking')"));
    await until("document.querySelectorAll('.leaflet-tile-loaded').length>0", 'Road basemap tiles did not load', 30000);
    console.log('PASS: authenticated resident page, four routes, 12 assignments, road tiles and explicit simulation labels; no automatic GPS request.');

    const firstPosition = await evaluate("document.querySelector('.ecomove-taxi-marker').style.transform");
    await delay(700);
    assert.notEqual(await evaluate("document.querySelector('.ecomove-taxi-marker').style.transform"), firstPosition, 'Taxi marker did not move');
    await click('.ecomove-taxi-marker');
    await until("document.querySelector('[aria-label=\"Simulated taxi details\"]')", 'Taxi click did not open details');
    assert.ok(await evaluate("document.querySelector('.em-vehicle-facts').textContent.includes('Simulated')"));
    await click('.em-detail-close');
    console.log('PASS: taxi markers move and open simulation details with configured route and stops.');

    await send('Browser.setPermission', { permission: { name: 'geolocation' }, setting: 'denied', origin: 'http://127.0.0.1:5173' });
    await click('.em-current-location');
    await until("document.body.textContent.includes('Location permission was denied')", 'Denied geolocation fallback missing');
    assert.ok(await evaluate("Boolean(document.querySelector('.em-pin-button'))"));
    await send('Browser.setPermission', { permission: { name: 'geolocation' }, setting: 'granted', origin: 'http://127.0.0.1:5173' });
    await send('Emulation.setGeolocationOverride', { latitude: fixture.stops.soshanguve.latitude, longitude: fixture.stops.soshanguve.longitude, accuracy: 10 });
    await click('.em-current-location');
    await until("document.querySelector('.ecomove-location-marker.pickup') && document.querySelector('.em-boarding')", 'GPS pickup or nearby configured routes missing');
    await delay(1000);
    console.log('PASS: denied GPS preserves manual selection; granted GPS centers pickup and finds a configured boarding point.');

    await click('.em-pin-button');
    const map = await evaluate("(()=>{const r=document.querySelector('.ecomove-leaflet').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()");
    await mouse(map.x + 10, map.y + 8);
    await until(
      "document.querySelector('.em-selected-coordinate')?.textContent.includes('Pickup location selected') && document.querySelector('.em-boarding')",
      'Manual pickup did not find nearby routes',
    );
    const marker = await evaluate("(()=>{const r=document.querySelector('.ecomove-location-marker.pickup').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()");
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: marker.x, y: marker.y });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: marker.x, y: marker.y, button: 'left', clickCount: 1 });
    for (let i = 1; i <= 5; i++) await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: marker.x + i * 3, y: marker.y + i, button: 'left', buttons: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: marker.x + 15, y: marker.y + 5, button: 'left', clickCount: 1 });
    await until("document.querySelector('.em-boarding')", 'Nearby results did not update after dragging pickup', 30000);
    console.log('PASS: map pickup selection and draggable marker keep nearby route results working.');

    await chooseConfiguredStop('Pretoria CBD');
    await click('.em-find-button');
    const journeyStatus = await evaluate(
      "document.querySelector('.em-empty')?.innerText || document.querySelector('.em-journey-card')?.innerText || 'pending'",
    );
    await until(
      "document.querySelector('.em-journey-card')?.textContent.includes('Direct route')",
      `Direct configured journey did not render: ${journeyStatus}`,
    );
    // Reload restores full route catalogue so the next destination is in the visible dataset.
    await send('Page.navigate', { url: 'http://127.0.0.1:5173/resident/ecomove' });
    await until("document.querySelectorAll('.em-route-card').length===4", 'Full route catalogue did not restore');
    await send('Emulation.setGeolocationOverride', { latitude: fixture.stops.soshanguve.latitude, longitude: fixture.stops.soshanguve.longitude, accuracy: 10 });
    await click('.em-current-location');
    await until("document.querySelector('.em-boarding')", 'Pickup unavailable for transfer test');
    await chooseConfiguredStop('Centurion');
    await click('.em-find-button');
    await until("document.querySelector('.em-journey-card')?.textContent.includes('1 configured transfer')", 'Single-transfer journey missing');
    await click('.em-journey-card');
    await until("document.querySelector('.em-detail-card')", 'Selected journey details missing');
    console.log('PASS: direct journey and explicit shared-interchange transfer render from live Supabase route data.');
    await saveScreenshot('diagnostics/ecomove-desktop.png');

    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await delay(700);
    assert.ok(await evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Mobile layout overflows horizontally');
    assert.ok(await evaluate("document.querySelector('.ecomove-leaflet').getBoundingClientRect().width>300"), 'Mobile map is too narrow');
    assert.ok(await evaluate("getComputedStyle(document.querySelector('.em-mobile-panel-toggle')).display!=='none'"));
    await click('.em-mobile-panel-toggle');
    await click('.em-mobile-panel-toggle');
    await saveScreenshot('diagnostics/ecomove-mobile.png');
    console.log('PASS: 390px mobile layout, usable map width, collapsible journey panel and no horizontal overflow.');

    await send('Emulation.setDeviceMetricsOverride', { width: 1500, height: 1050, deviceScaleFactor: 1, mobile: false });
    await evaluate("document.querySelector('a[href=\"/resident\"]').click()");
    await until("location.pathname==='/resident' && !document.querySelector('.ecomove-leaflet')", 'Resident navigation cleanup failed');
    await evaluate("document.querySelector('a[href=\"/resident/ecomove\"]').click()");
    await until("document.querySelectorAll('.ecomove-leaflet').length===1 && document.querySelectorAll('.ecomove-taxi-marker').length>=8", 'EcoMove failed to remount');
    assert.equal(await evaluate("document.querySelectorAll('.ecomove-taxi-marker').length"), 12, 'Duplicate/missing taxis after navigation');
    assert.deepEqual(runtimeErrors, [], 'Browser raised runtime errors');
    console.log('PASS: Resident dashboard navigation and EcoMove remount; no duplicate markers or browser runtime exceptions.');
    await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: injection.identifier });
  } catch (error) {
    console.error('EcoMove browser verification failed:', error.message);
    if (evaluate) {
      const status = await evaluate(`JSON.stringify({
        path: location.pathname,
        empty: document.querySelector('.em-empty')?.textContent,
        loading: document.querySelector('.em-loading')?.textContent,
      })`).catch(() => 'unavailable');
      console.error('Planner status:', status);
    }
    if (typeof journeyResponses !== 'undefined') for (const response of journeyResponses) {
      try { console.error('Journey API:', response.status, JSON.stringify(await send('Network.getResponseBody', { requestId: response.requestId }))); } catch {}
    }
    process.exitCode = 1;
  } finally {
    if (evaluate) await evaluate("localStorage.removeItem('ecopulse.auth.session')").catch(() => {});
    if (send) await send('Page.navigate', { url: 'about:blank' }).catch(() => {});
    socket?.close();
    if (actor) {
      const deletion = spawnSync(PYTHON, [fixtureScript, '--delete', actor], { cwd: ROOT, encoding: 'utf8', timeout: 30000, windowsHide: true });
      if (deletion.status !== 0) { console.error('Temporary account cleanup failed; inspect Supabase test accounts.'); process.exitCode = 1; }
      else console.log('PASS: temporary real-auth browser account deleted.');
    }
  }
})();
