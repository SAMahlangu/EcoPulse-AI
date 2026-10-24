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
      if (name === 'react-leaflet') return { TileLayer: 'tile-layer' };
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

test('tile failures show an accessible source warning and successful retry clears it', () => {
  const app = mount();
  const failedTile = {};
  assert.equal(app.notice(), null);
  app.handlers().tileerror({ tile: failedTile });
  app.render();
  assert.equal(app.notice().props.role, 'status');
  assert.equal(app.notice().props['aria-live'], 'polite');
  assert.match(JSON.stringify(app.notice()), /Street map unavailable/);
  assert.match(JSON.stringify(app.notice()), /OpenStreetMap tiles could not load/);
  app.handlers().tileload({ tile: failedTile });
  app.render();
  assert.equal(app.notice(), null);
  app.unmount();
});

test('partial tile failures remain visible until failed tiles recover or leave the viewport', () => {
  const app = mount();
  const failedTile = {};
  app.handlers().tileerror({ tile: failedTile });
  app.handlers().tileload({ tile: {} });
  app.render();
  assert.ok(app.notice());
  app.handlers().tileunload({ tile: failedTile });
  app.render();
  assert.equal(app.notice(), null);
  app.unmount();
});

test('retry replaces only the tilelayer and retains source attribution', () => {
  const app = mount();
  app.handlers().tileerror({ tile: {} });
  app.render();
  const oldKey = app.tile().key;
  const button = app.notice().props.children[1];
  assert.equal(button.props.type, 'button');
  button.props.onClick();
  app.render();
  assert.notEqual(app.tile().key, oldKey);
  assert.equal(app.notice(), null);
  assert.match(app.tile().props.attribution, /openstreetmap.org\/copyright/);
  assert.match(app.tile().props.url, /tile.openstreetmap.org/);
  app.unmount();
});

test('slow pending requests are distinguished from source errors and timers clean up', () => {
  const app = mount();
  app.handlers().loading();
  assert.equal(app.timers.size, 1);
  const pending = [...app.timers.values()][0];
  assert.equal(pending.delay, 12_000);
  pending.callback();
  app.render();
  assert.match(JSON.stringify(app.notice()), /Street map loading slowly/);
  app.handlers().tileload({ tile: {} });
  app.render();
  assert.equal(app.notice(), null);
  app.handlers().loading();
  app.unmount();
  assert.equal(app.timers.size, 0);
});
