// Browser checks with fixture-only auth/API/geolocation in an isolated CDP tab.
// Start the frontend, then a headless Edge/Chrome profile with --remote-debugging-port=9234.
// No real account, profile writes, provider calls, or credentials are used.
const assert = require('node:assert/strict');

const option = (name, fallback) => process.argv.find(value => value.startsWith(`--${name}=`))?.slice(name.length + 3) || fallback;
const FRONTEND = option('frontend', 'http://localhost:5173').replace(/\/$/, '');
const CDP = `http://127.0.0.1:${option('cdp-port', '9234')}`;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket, send, evaluate, targetId;

async function until(expression, message, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await delay(100);
  }
  throw new Error(message);
}

async function state() {
  return evaluate(`(() => {
    const field = (name, selector) => [...document.querySelectorAll('.onboarding-panel > label')]
      .find(label => label.textContent.trim().startsWith(name))?.querySelector(selector)?.value ?? null;
    return {
      province: field('Province', 'select'), municipality: field('Municipality', 'select'),
      community: field('Community (optional)', 'select'), suburb: field('Community or suburb', 'input'),
      message: document.querySelector('.onboarding-location-status')?.textContent ?? '',
      busy: document.querySelector('.onboarding-location button')?.disabled,
      attributions: [...document.querySelectorAll('.onboarding-location small')].map(item => item.textContent),
      lookups: window.__onboardingFixture.lookups,
    };
  })()`);
}

async function scenario(name, result, { denied = false, failed = false } = {}) {
  await send('Page.navigate', { url: `${FRONTEND}/resident/onboarding` });
  await until("Boolean(document.querySelector('.onboarding-location button'))", 'Community setup did not render');
  await evaluate(`Object.assign(window.__onboardingFixture, ${JSON.stringify({ result, denied, failed })})`);
  await evaluate("document.querySelector('.onboarding-location button').click()");
  await until("document.querySelector('.onboarding-location-status') && !document.querySelector('.onboarding-location button').disabled", `${name} did not finish`);
  // Wait for dependent options to render rather than assuming a fixed delay.
  // resolveCurrentPosition awaits its optional community request before clearing busy.
  if (result?.province?.id) {
    await until(`(() => {
      const labels = [...document.querySelectorAll('.onboarding-panel > label')];
      const province = labels.find(item => item.textContent.trim().startsWith('Province'))?.querySelector('select');
      const municipality = labels.find(item => item.textContent.trim().startsWith('Municipality'))?.querySelector('select');
      return province?.value === ${JSON.stringify(result.province.id)} && municipality && !municipality.disabled
        && municipality.value === ${JSON.stringify(result.municipality?.id ?? '')};
    })()`, `${name} did not populate the dependent municipality options`);
  }
  return state();
}

async function run() {
  try {
    const target = await (await fetch(`${CDP}/json/new?about:blank`, { method: 'PUT' })).json();
    assert.ok(target.webSocketDebuggerUrl, 'Isolated browser CDP tab unavailable');
    targetId = target.id;
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', reject, { once: true });
    });
    let sequence = 0;
    const pending = new Map();
    send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP ${method} timed out`)); }, 20000);
      pending.set(id, { resolve: value => { clearTimeout(timer); resolve(value); }, reject: error => { clearTimeout(timer); reject(error); } });
      socket.send(JSON.stringify({ id, method, params }));
    });
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
      const item = pending.get(message.id);
      if (item) {
        pending.delete(message.id);
        message.error ? item.reject(new Error(message.error.message)) : item.resolve(message.result);
      }
    });
    evaluate = async expression => {
      const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? 'Browser evaluation failed');
      return result.result.value;
    };
    await send('Page.enable');
    await send('Page.addScriptToEvaluateOnNewDocument', {
      source: `(() => {
        if (location.origin !== ${JSON.stringify(FRONTEND)}) return;
        const gp = '00000000-0000-0000-0000-000000000001';
        const jhb = '00000000-0000-0000-0000-000000000002';
        const community = '00000000-0000-0000-0000-000000000003';
        window.__onboardingFixture = { result: null, denied: false, failed: false, lookups: 0 };
        localStorage.setItem('ecopulse.auth.session', JSON.stringify({ access_token: 'fixture-only', refresh_token: 'fixture-only', expires_at: Math.floor(Date.now()/1000)+3600, user: { id: 'fixture-resident', email: 'fixture@example.test' } }));
        const originalFetch = window.fetch.bind(window);
        window.fetch = async (input, init = {}) => {
          const url = new URL(typeof input === 'string' ? input : input.url, location.href);
          if (!url.pathname.startsWith('/api/v1/')) return originalFetch(input, init);
          if ((init.method || 'GET') !== 'GET') throw new Error('Fixture forbids account/profile writes');
          const path = url.pathname.slice('/api/v1'.length);
          let data;
          if (path === '/auth/me') data = { id: 'fixture-resident', email: 'fixture@example.test', full_name: 'Location Check', role: 'resident', municipality_id: null, onboarding_completed_at: null };
          else if (path === '/resident/onboarding') data = { profile: null };
          else if (path === '/locations/provinces') data = { items: [{ id: gp, name: 'Gauteng', code: 'GP' }] };
          else if (path === '/locations/municipalities') data = { items: [{ id: jhb, name: 'City of Johannesburg', province_id: gp, province: 'Gauteng', code: 'JHB', municipality_type: 'metropolitan', category: 'A', parent_municipality_id: null, parent_municipality: null, is_onboarded: false }], total: 1, limit: 100, offset: 0 };
          else if (path === '/locations/communities') data = { items: [{ id: community, municipality_id: jhb, name: 'Soweto' }], total: 1, limit: 100, offset: 0 };
          else if (path === '/locations/reverse') {
            window.__onboardingFixture.lookups++;
            if (window.__onboardingFixture.failed) throw new Error('Fixture provider unavailable');
            data = window.__onboardingFixture.result;
          } else throw new Error('Unexpected fixture API path: ' + path);
          return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
        };
        Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
          getCurrentPosition(success, failure) {
            setTimeout(() => window.__onboardingFixture.denied
              ? failure({ code: 1 })
              : success({ coords: { latitude: -26.2678, longitude: 27.8585, accuracy: 25 } }), 0);
          }
        } });
      })();`,
    });

    const province = { id: '00000000-0000-0000-0000-000000000001', name: 'Gauteng' };
    const municipality = { id: '00000000-0000-0000-0000-000000000002', name: 'City of Johannesburg', province: 'Gauteng' };
    const resolved = { province, municipality, locality: 'Soweto', source: 'OpenStreetMap Nominatim', boundary_source: 'Municipal Demarcation Board 2026', country_code: 'za' };
    let value = await scenario('Complete location', resolved);
    assert.equal(value.province, province.id);
    assert.equal(value.municipality, municipality.id);
    assert.equal(value.community, '00000000-0000-0000-0000-000000000003');
    assert.equal(value.suburb, 'Soweto');
    assert.match(value.message, /Soweto.*City of Johannesburg.*Gauteng/);
    assert.ok(value.attributions.some(text => text.includes('OpenStreetMap')));
    assert.ok(value.attributions.some(text => text.includes('Municipal Demarcation Board')));
    console.log('PASS: actual rendered province, municipality and listed suburb stay selected after autofill');

    value = await scenario('Boundary-only location', { ...resolved, locality: null, source: 'Municipal Demarcation Board 2026' });
    assert.equal(value.province, province.id);
    assert.equal(value.municipality, municipality.id);
    assert.equal(value.suburb, '');
    assert.match(value.message, /City of Johannesburg.*Gauteng/);
    console.log('PASS: boundary fallback fills municipality and leaves unavailable suburb editable');

    value = await scenario('Province-only location', { ...resolved, municipality: null, locality: null });
    assert.equal(value.province, province.id);
    assert.equal(value.municipality, '');
    assert.match(value.message, /Gauteng.*could not confirm the municipality/);
    console.log('PASS: a known province survives when municipality needs manual selection');

    const unresolved = { province: null, municipality: null, locality: null, source: null, boundary_source: null, country_code: null };
    value = await scenario('Unresolved location', unresolved);
    assert.equal(value.province, '');
    assert.equal(value.municipality, '');
    assert.match(value.message, /could not confirm/i);
    assert.doesNotMatch(value.message, /outside South Africa/);
    console.log('PASS: unavailable location data gives manual guidance without claiming the resident is abroad');

    value = await scenario('Foreign location', { ...unresolved, country_code: 'us' });
    assert.match(value.message, /outside South Africa/);
    console.log('PASS: explicit foreign-country response gives appropriate guidance');

    value = await scenario('Location permission denied', null, { denied: true });
    assert.match(value.message, /permission was denied/);
    assert.equal(value.lookups, 0);
    console.log('PASS: denied geolocation permission leaves the manual form usable');

    value = await scenario('Lookup request failure', null, { failed: true });
    assert.match(value.message, /could not look up/i);
    assert.equal(value.busy, false);
    console.log('PASS: failed lookup stops loading and restores manual entry');
  } finally {
    if (targetId) await fetch(`${CDP}/json/close/${targetId}`).catch(() => undefined);
    socket?.close();
  }
}

run().catch(error => { console.error('Onboarding browser check failed:', error.message); process.exitCode = 1; });
