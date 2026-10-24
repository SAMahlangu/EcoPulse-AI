// Uses the project's existing TypeScript compiler and Node's built-in runner; no new dependencies.
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');
const ts = require('typescript');
const filename = path.join(__dirname, '../src/components/ecomove/simulation.ts');
const compiled = ts.transpileModule(readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }, fileName: filename,
});
const moduleObject = { exports: {} };
vm.runInNewContext(compiled.outputText, { module: moduleObject, exports: moduleObject.exports, require });
const { createRouteTrack, createVehicleSimulation, sampleVehicle, distanceMeters, positionOnTrack } = moduleObject.exports;

const stop = (id, longitude, sequence) => ({
  id, name: id, longitude, latitude: 0, municipality_id: 'demo-municipality', stop_type: 'pickup',
  is_demo: true, is_interchange: false, stop_sequence: sequence, estimated_arrival_offset_minutes: null,
});
const route = (changes = {}) => ({
  id: 'road-east', name: 'Demo road east', origin: 'Start', destination: 'End', municipality_id: 'demo-municipality',
  route_geometry: { type: 'LineString', coordinates: [[0, 0], [0.005, 0], [0.01, 0]] },
  direction: 'outbound', description: 'Test fixture only', color: '#16846a',
  estimated_duration_minutes: null, estimated_fare: null, is_demo: true, data_source: 'test',
  simulation_mode: 'once', stops: [stop('middle', 0.005, 0), stop('end', 0.01, 1)], ...changes,
});
const vehicle = (changes = {}) => ({
  id: 'sim-1', vehicle_code: 'SIM-001', route_id: 'road-east', simulation_speed: 36,
  initial_progress: 0, is_active: true, position_source: 'simulation', ...changes,
});
function setup(routeChanges = {}, vehicleChanges = {}, pause = 5) {
  const track = createRouteTrack(route(routeChanges));
  assert.ok(track);
  const simulation = createVehicleSimulation(vehicle(vehicleChanges), track, pause);
  assert.ok(simulation);
  return { track, simulation };
}

test('physical speed follows geometry in metres, with heading and simulated ETA', () => {
  const { simulation } = setup();
  const start = sampleVehicle(simulation, 0);
  const tenSeconds = sampleVehicle(simulation, 10);
  assert.ok(Math.abs(distanceMeters([start.longitude, start.latitude], [tenSeconds.longitude, tenSeconds.latitude]) - 100) < 0.01);
  assert.equal(tenSeconds.status, 'moving');
  assert.equal(tenSeconds.speed_kmh, 36);
  assert.ok(Math.abs(tenSeconds.bearing - 90) < 0.001);
  assert.equal(tenSeconds.next_stop.id, 'middle');
  assert.ok(tenSeconds.eta_minutes > 0);
  assert.equal(tenSeconds.position_source, 'simulation');
});

test('a configured stop pauses for five active seconds then continues forward', () => {
  const { track, simulation } = setup();
  const arrival = track.length / 2 / 10;
  const waiting = sampleVehicle(simulation, arrival + 4.9);
  assert.equal(waiting.status, 'at_stop');
  assert.equal(waiting.next_stop.id, 'middle');
  assert.equal(waiting.speed_kmh, 0);
  assert.equal(waiting.longitude, 0.005);
  const leaving = sampleVehicle(simulation, arrival + 5.1);
  assert.equal(leaving.status, 'moving');
  assert.ok(leaving.longitude > waiting.longitude);
  assert.equal(leaving.next_stop.id, 'end');
});

test('frame rate and a long frame do not change the sampled route position', () => {
  const { simulation } = setup();
  let sixtyFps = 0;
  for (let index = 0; index < 90 * 60; index += 1) sixtyFps += 1 / 60;
  const fine = sampleVehicle(simulation, sixtyFps);
  const coarse = sampleVehicle(simulation, 90);
  assert.ok(Math.abs(fine.longitude - coarse.longitude) < 1e-10);
  assert.equal(fine.status, coarse.status);
});

test('once routes stop at the destination forever, including destination dwell', () => {
  const { track, simulation } = setup();
  const finish = sampleVehicle(simulation, track.length / 10 + 10.1);
  assert.equal(finish.status, 'finished');
  assert.equal(finish.longitude, 0.01);
  assert.equal(finish.progress, 1);
  assert.equal(finish.next_stop, null);
  assert.equal(finish.eta_minutes, null);
  assert.equal(sampleVehicle(simulation, 1e9).longitude, 0.01);
});

test('vehicles have independent initial progress and skip already passed stops', () => {
  const { track, simulation: first } = setup();
  const second = createVehicleSimulation(vehicle({ id: 'sim-2', initial_progress: 0.75 }), track);
  assert.ok(second);
  const firstPosition = sampleVehicle(first, 0);
  const secondPosition = sampleVehicle(second, 0);
  assert.equal(firstPosition.vehicle_id, 'sim-1');
  assert.equal(secondPosition.vehicle_id, 'sim-2');
  assert.ok(secondPosition.progress > firstPosition.progress);
  assert.equal(secondPosition.next_stop.id, 'end');
  assert.equal(sampleVehicle(second, 3).status, 'moving');
});

test('loop configuration cannot teleport an open road back to its origin', () => {
  const { track, simulation } = setup({ simulation_mode: 'loop' });
  assert.equal(track.loop, false);
  assert.equal(sampleVehicle(simulation, 1e6).status, 'finished');
});

test('explicitly closed loops wrap forward while once mode never wraps', () => {
  const geometry = { type: 'LineString', coordinates: [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0]] };
  const { track, simulation } = setup({ simulation_mode: 'loop', route_geometry: geometry, stops: [] });
  const duration = track.length / 10;
  assert.equal(track.loop, true);
  const firstLap = sampleVehicle(simulation, 2);
  const secondLap = sampleVehicle(simulation, duration + 2);
  assert.ok(Math.abs(firstLap.latitude - secondLap.latitude) < 1e-10);
  assert.ok(Math.abs(firstLap.longitude - secondLap.longitude) < 1e-10);
  assert.equal(secondLap.status, 'moving');
  const once = setup({ route_geometry: geometry, stops: [] });
  assert.equal(sampleVehicle(once.simulation, 1e6).status, 'finished');
});

test('a closed loop dwells at its single configured origin stop on each forward lap', () => {
  const { track, simulation } = setup({
    simulation_mode: 'loop', stops: [stop('origin', 0, 0)],
    route_geometry: { type: 'LineString', coordinates: [[0, 0], [0.001, 0], [0.001, 0.001], [0, 0]] },
  });
  const cycleSeconds = track.length / 10 + 5;
  const returned = sampleVehicle(simulation, cycleSeconds + 2);
  assert.equal(returned.status, 'at_stop');
  assert.equal(returned.next_stop.id, 'origin');
  assert.equal(returned.progress, 0);
  assert.equal(sampleVehicle(simulation, cycleSeconds + 5.1).status, 'moving');
});

test('heading follows turns and consecutive duplicate road coordinates are safe', () => {
  const { track } = setup({
    route_geometry: { type: 'LineString', coordinates: [[0, 0], [0, 0], [0.01, 0], [0.01, 0.01]] }, stops: [],
  });
  assert.equal(track.coordinates.length, 3);
  const north = positionOnTrack(track, track.length * 0.75);
  assert.equal(north.longitude, 0.01);
  assert.ok(Math.abs(north.bearing) < 0.001);
  assert.ok(north.latitude > 0);
});

test('invalid geometry, inactive vehicles and unsupported position sources never animate', () => {
  assert.equal(createRouteTrack(route({ route_geometry: { type: 'LineString', coordinates: [[0, 0], [999, 0]] } })), null);
  assert.equal(createRouteTrack(route({ route_geometry: { type: 'LineString', coordinates: [[0, 0], [0, 0]] } })), null);
  const { track } = setup();
  for (const changes of [{ simulation_speed: 0 }, { simulation_speed: NaN }, { is_active: false }, { position_source: 'live_gps' }, { initial_progress: NaN }, { route_id: 'other' }]) {
    assert.equal(createVehicleSimulation(vehicle(changes), track), null);
  }
});

test('out-of-order configured stops never reverse travel and progress is clamped', () => {
  const { track, simulation } = setup({ stops: [stop('later', 0.008, 0), stop('earlier', 0.002, 1)] }, { initial_progress: -1 });
  assert.equal(track.stops.length, 1);
  assert.equal(sampleVehicle(simulation, -1).progress, 0);
  assert.equal(sampleVehicle(simulation, 1).next_stop.id, 'later');
  const arrived = createVehicleSimulation(vehicle({ initial_progress: 2 }), track);
  assert.equal(sampleVehicle(arrived, 0).status, 'finished');
});

function mountController(props) {
  const cleanups = [];
  const frames = new Map();
  const documentListeners = new Map();
  const mapListeners = new Map();
  const layersOnMap = new Set();
  const markers = [];
  let nextFrame = 0;
  const fakeDocument = { hidden: false,
    addEventListener: (name, callback) => documentListeners.set(name, callback),
    removeEventListener: name => documentListeners.delete(name),
  };
  const bounds = { pad() { return this; }, contains: () => true };
  const map = {
    getBounds: () => bounds, getZoom: () => 12,
    on: (names, callback) => names.split(' ').forEach(name => mapListeners.set(name, callback)),
    off: names => names.split(' ').forEach(name => mapListeners.delete(name)),
  };
  const leaflet = {
    divIcon: options => options,
    layerGroup: () => {
      const group = {
        layers: new Set(),
        addTo() { layersOnMap.add(this); return this; },
        addLayer(marker) { this.layers.add(marker); return this; },
        removeLayer(marker) { this.layers.delete(marker); return this; },
        clearLayers() { this.layers.clear(); },
        remove() { layersOnMap.delete(this); },
      };
      return group;
    },
    marker: (latlng, options) => {
      const element = { style: {}, querySelector: () => ({ style: {} }) };
      const marker = {
        latlng, options, handlers: {},
        on(name, callback) { this.handlers[name] = callback; return this; },
        off() { this.handlers = {}; },
        setLatLng(point) { this.latlng = point; return this; },
        getElement: () => element,
        setZIndexOffset() { return this; },
      };
      markers.push(marker);
      return marker;
    },
  };
  const controllerFilename = path.join(__dirname, '../src/components/ecomove/TaxiSimulationController.tsx');
  const controllerCompiled = ts.transpileModule(readFileSync(controllerFilename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX }, fileName: controllerFilename,
  });
  const controllerModule = { exports: {} };
  const mockRequire = name => {
    if (name === 'react') return {
      useRef: initial => ({ current: initial }),
      useEffect: callback => { const cleanup = callback(); if (cleanup) cleanups.push(cleanup); },
    };
    if (name === 'react-leaflet') return { useMap: () => map };
    if (name === 'leaflet') return { default: leaflet };
    if (name === './simulation') return moduleObject.exports;
    throw new Error(`Unexpected controller dependency: ${name}`);
  };
  vm.runInNewContext(controllerCompiled.outputText, {
    module: controllerModule, exports: controllerModule.exports, require: mockRequire,
    document: fakeDocument,
    requestAnimationFrame: callback => { const id = ++nextFrame; frames.set(id, callback); return id; },
    cancelAnimationFrame: id => frames.delete(id),
  });
  controllerModule.exports.default(props);
  return {
    frames, markers, documentListeners, mapListeners, layersOnMap,
    frame(timestamp) { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(timestamp)); },
    hidden(value) { fakeDocument.hidden = value; documentListeners.get('visibilitychange')?.(); },
    unmount() { [...cleanups].reverse().forEach(cleanup => cleanup()); },
  };
}

test('controller pauses hidden tabs, throttles selected details and cleans every layer/listener/frame', () => {
  const updates = [];
  const selections = [];
  const mounted = mountController({
    routes: [route()], vehicles: [vehicle()], selectedVehicleId: 'sim-1',
    onVehicleUpdate: position => updates.push(position), onVehicleSelect: position => selections.push(position),
  });
  assert.equal(mounted.markers.length, 1);
  assert.equal(mounted.frames.size, 1);
  mounted.frame(0);
  mounted.frame(500);
  assert.equal(updates.length, 0);
  mounted.frame(1000);
  assert.equal(updates.length, 1);
  const beforeHidden = mounted.markers[0].latlng[1];
  mounted.markers[0].handlers.click();
  assert.equal(selections[0].vehicle_id, 'sim-1');
  mounted.hidden(true);
  assert.equal(mounted.frames.size, 0);
  mounted.hidden(false);
  mounted.frame(61_000);
  assert.equal(mounted.markers[0].latlng[1], beforeHidden);
  mounted.frame(62_000);
  assert.ok(mounted.markers[0].latlng[1] > beforeHidden);
  assert.equal(updates.length, 2);
  mounted.unmount();
  assert.equal(mounted.frames.size, 0);
  assert.equal(mounted.documentListeners.size, 0);
  assert.equal(mounted.mapListeners.size, 0);
  assert.equal(mounted.layersOnMap.size, 0);
  assert.equal(Object.keys(mounted.markers[0].handlers).length, 0);
});

test('controller deduplicates repeated vehicle IDs and excludes unrelated routes', () => {
  const mounted = mountController({
    routes: [route()], vehicles: [vehicle(), vehicle(), vehicle({ id: 'other', route_id: 'unrelated' })],
  });
  assert.equal(mounted.markers.length, 1);
  mounted.unmount();
  const filtered = mountController({ routes: [route()], vehicles: [vehicle()], relevantRouteIds: ['unrelated'] });
  assert.equal(filtered.markers.length, 0);
  assert.equal(filtered.frames.size, 0);
  filtered.unmount();
});

test('disabled simulation renders its static initial location without creating an animation loop', () => {
  const mounted = mountController({ routes: [route()], vehicles: [vehicle()], enabled: false });
  assert.equal(mounted.markers.length, 1);
  assert.equal(mounted.frames.size, 0);
  mounted.unmount();
});

test('the final selected-vehicle snapshot is emitted before the completed animation stops', () => {
  const updates = [];
  const mounted = mountController({
    routes: [route({ route_geometry: { type: 'LineString', coordinates: [[0, 0], [0.00001, 0]] }, stops: [] })],
    vehicles: [vehicle()], selectedVehicleId: 'sim-1', onVehicleUpdate: position => updates.push(position),
  });
  mounted.frame(0);
  mounted.frame(200);
  assert.equal(mounted.frames.size, 0);
  assert.equal(updates.length, 1);
  assert.equal(updates[0].status, 'finished');
  assert.equal(updates[0].speed_kmh, 0);
  mounted.unmount();
});

test('the supplied Pretoria road fixtures support every configured stop and twelve independent taxis', () => {
  const seed = JSON.parse(readFileSync(path.join(__dirname, '../../database/seeds/ecomove_pretoria_demo.json'), 'utf8'));
  const codes = new Set();
  let count = 0;
  for (const configured of seed.routes) {
    const configuredRoute = route({ ...configured, id: configured.key, simulation_mode: 'once',
      stops: configured.stop_keys.map((key, sequence) => ({
        ...stop(key, 0, sequence), ...seed.stops[key], id: key,
      })),
    });
    const track = createRouteTrack(configuredRoute);
    assert.ok(track, `Invalid road geometry: ${configured.key}`);
    assert.equal(track.stops.length, configuredRoute.stops.length, `Inconsistent stop direction: ${configured.key}`);
    assert.ok(track.length > 1000);
    for (let index = 0; index < 3; index += 1) {
      const code = `${configured.key}-${index}`;
      codes.add(code);
      const simulation = createVehicleSimulation(vehicle({
        id: code, vehicle_code: code, route_id: track.route.id, initial_progress: index * 0.25,
      }), track);
      assert.ok(simulation);
      const initial = sampleVehicle(simulation, 0);
      const later = sampleVehicle(simulation, 60);
      assert.ok(later.progress >= initial.progress);
      assert.equal(later.position_source, 'simulation');
      count += 1;
    }
  }
  assert.equal(count, 12);
  assert.equal(codes.size, 12);
});
