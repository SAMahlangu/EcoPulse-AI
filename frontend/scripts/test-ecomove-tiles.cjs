const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const ts = require('typescript');

function mount() {
  const slots = [];
  const timers = new Map();
  let cursor = 0;
  let sequence = 0;
  const equalDeps = (a, b) => a && b && a.length === b.length && a.every((value, index) => value === b[index]);
  const react = {
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: initial };
      return [slots[index].value, value => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }];
    },
    useMemo(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !equalDeps(slots[index].deps, deps)) slots[index] = { value: callback(), deps };
      return slots[index].value;
    },
    useCallback(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !equalDeps(slots[index].deps, deps)) slots[index] = { value: callback, deps };
      return slots[index].value;
    },
    useEffect(callback, deps) {
      const index = cursor++;
      if (!slots[index] || !equalDeps(slots[index].deps, deps)) {
        slots[index]?.cleanup?.();
        slots[index] = { cleanup: callback(), deps };
      }
    },
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const filename = path.join(__dirname, '../src/components/ecomove/EcoMoveTileLayer.tsx');
  const compiled = ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX }, fileName: filename,
  });
  const compiledModule = { exports: {} };
  vm.runInNewContext(compiled.outputText, {
    module: compiledModule, exports: compiledModule.exports,
    require(name) {
      if (name === 'react') return react;
      if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
      if (name === 'leaflet') return { default: { DomEvent: { disableClickPropagation() {}, disableScrollPropagation() {} } } };
      if (name === 'react-leaflet') return {
        TileLayer: 'tile-layer',
        useMap: () => ({ getCenter: () => ({ lat: -25.75, lng: 28.2 }), getZoom: () => 12 }),
      };
      throw new Error(`Unexpected dependency ${name}`);
    },
    setTimeout(callback, delay) { const id = ++sequence; timers.set(id, { callback, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  let tree;
  const render = () => { cursor = 0; tree = compiledModule.exports.default(); return tree; };
  render();
  return {
    timers, render,
    handlers: () => tree.props.children[0].props.eventHandlers,
    notice: () => tree.props.children[1],
    tile: () => tree.props.children[0],
    unmount: () => slots.forEach(slot => slot.cleanup?.()),
  };
}

function failSource(app) {
  for (let index = 0; index < 3; index += 1) app.handlers().tileerror({ tile: {} });
  app.render();
}

test('failed map sources show an accessible warning, external map and recovery', () => {
  const app = mount();
  assert.equal(app.notice(), null);
  failSource(app);
  assert.match(app.tile().props.url, /tile.openstreetmap.de/);
  assert.match(JSON.stringify(app.notice()), /Switching map source/);
  failSource(app);
  assert.equal(app.notice().props.role, 'status');
  assert.equal(app.notice().props['aria-live'], 'polite');
  assert.match(JSON.stringify(app.notice()), /Street map unavailable/);
  assert.match(JSON.stringify(app.notice()), /either map source/);
  const external = app.notice().props.children.find(child => child?.type === 'a');
  assert.equal(external.props.href, 'https://www.openstreetmap.org/#map=12/-25.75000/28.20000');
  app.handlers().tileload({ tile: {} });
  app.render();
  assert.equal(app.notice(), null);
  app.unmount();
});

test('isolated failed tiles can recover or unload without replacing a working source', () => {
  const app = mount();
  const originalKey = app.tile().key;
  const failedTile = {}, recoveredTile = {};
  app.handlers().tileerror({ tile: failedTile });
  app.handlers().tileerror({ tile: recoveredTile });
  app.handlers().tileload({ tile: recoveredTile });
  app.handlers().tileunload({ tile: failedTile });
  app.handlers().tileerror({ tile: {} });
  app.handlers().tileerror({ tile: {} });
  app.render();
  assert.equal(app.tile().key, originalKey);
  assert.equal(app.notice(), null);
  app.unmount();
});

test('retry returns to the primary tile source and retains attribution', () => {
  const app = mount();
  failSource(app);
  failSource(app);
  const oldKey = app.tile().key;
  const button = app.notice().props.children.find(child => child?.type === 'button');
  assert.equal(button.props.type, 'button');
  button.props.onClick();
  app.render();
  assert.notEqual(app.tile().key, oldKey);
  assert.match(JSON.stringify(app.notice()), /Switching map source/);
  assert.match(app.tile().props.attribution, /openstreetmap.org\/copyright/);
  assert.match(app.tile().props.url, /tile.openstreetmap.org/);
  app.unmount();
});

test('a stalled primary source falls back, then reports failure if the backup also stalls', () => {
  const app = mount();
  app.handlers().loading();
  assert.equal(app.timers.size, 1);
  const pending = [...app.timers.values()][0];
  assert.equal(pending.delay, 6_000);
  pending.callback();
  app.render();
  assert.match(JSON.stringify(app.notice()), /Switching map source/);
  assert.match(app.tile().props.url, /tile.openstreetmap.de/);
  app.handlers().loading();
  [...app.timers.values()][0].callback();
  app.render();
  assert.match(JSON.stringify(app.notice()), /Street map unavailable/);
  app.handlers().tileload({ tile: {} });
  app.handlers().load();
  app.render();
  assert.equal(app.notice(), null);
  app.handlers().loading();
  app.unmount();
  assert.equal(app.timers.size, 0);
});

test('late events from a failed source cannot clear the backup-source warning', () => {
  const app = mount();
  const oldHandlers = app.handlers();
  failSource(app);
  oldHandlers.tileload({ tile: {} });
  oldHandlers.load();
  app.render();
  assert.match(JSON.stringify(app.notice()), /Switching map source/);
  assert.match(app.tile().props.url, /tile.openstreetmap.de/);
  app.unmount();
});
