import type { TaxiPosition, TaxiRoute, TaxiStop, TaxiVehicle } from '../../types/ecomove';

type LngLat = [number, number];
interface TrackStop { stop: TaxiStop; distance: number }
export interface RouteTrack {
  route: TaxiRoute;
  coordinates: LngLat[];
  distances: number[];
  length: number;
  stops: TrackStop[];
  loop: boolean;
}
interface Phase {
  start: number;
  end: number;
  from: number;
  to: number;
  stop: TaxiStop | null;
}
interface Timeline { phases: Phase[]; duration: number }
export interface VehicleSimulation {
  vehicle: TaxiVehicle;
  track: RouteTrack;
  initial: Timeline;
  cycle: Timeline;
  startDistance: number;
}

const EARTH_METERS = 6_371_008.8;
const RAD = Math.PI / 180;
const EPSILON = 0.01;

export function distanceMeters(a: LngLat, b: LngLat): number {
  const latitudeDelta = (b[1] - a[1]) * RAD;
  const longitudeDelta = (b[0] - a[0]) * RAD;
  const h = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.sin(longitudeDelta / 2) ** 2;
  return 2 * EARTH_METERS * Math.asin(Math.sqrt(Math.min(1, Math.max(0, h))));
}

function bearing(a: LngLat, b: LngLat): number {
  const longitudeDelta = (b[0] - a[0]) * RAD;
  const y = Math.sin(longitudeDelta) * Math.cos(b[1] * RAD);
  const x = Math.cos(a[1] * RAD) * Math.sin(b[1] * RAD)
    - Math.sin(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.cos(longitudeDelta);
  return (Math.atan2(y, x) / RAD + 360) % 360;
}

function validCoordinate(point: LngLat): boolean {
  return Array.isArray(point) && point.length === 2 && Number.isFinite(point[0])
    && Number.isFinite(point[1]) && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90;
}

/** Project stops onto the supplied road geometry; never create an alternate road path. */
function projectStop(coordinates: LngLat[], distances: number[], stop: TaxiStop): number {
  let closestDistance = Infinity;
  let alongRoute = 0;
  const scale = Math.cos(stop.latitude * RAD);
  for (let index = 1; index < coordinates.length; index += 1) {
    const a = coordinates[index - 1];
    const b = coordinates[index];
    const x = (b[0] - a[0]) * scale;
    const y = b[1] - a[1];
    const lengthSquared = x * x + y * y;
    const fraction = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1,
      (((stop.longitude - a[0]) * scale) * x + (stop.latitude - a[1]) * y) / lengthSquared));
    const projected: LngLat = [a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])];
    const distance = distanceMeters([stop.longitude, stop.latitude], projected);
    if (distance < closestDistance) {
      closestDistance = distance;
      alongRoute = distances[index - 1] + fraction * (distances[index] - distances[index - 1]);
    }
  }
  return alongRoute;
}

export function createRouteTrack(route: TaxiRoute): RouteTrack | null {
  const supplied = route.route_geometry?.coordinates;
  if (route.route_geometry?.type !== 'LineString' || !Array.isArray(supplied)
    || supplied.length < 2 || !supplied.every(validCoordinate)) return null;
  const coordinates: LngLat[] = [];
  supplied.forEach(point => {
    if (!coordinates.length || distanceMeters(coordinates[coordinates.length - 1], point) > EPSILON) {
      coordinates.push(point);
    }
  });
  if (coordinates.length < 2) return null;
  const distances = [0];
  for (let index = 1; index < coordinates.length; index += 1) {
    distances.push(distances[index - 1] + distanceMeters(coordinates[index - 1], coordinates[index]));
  }
  const length = distances[distances.length - 1];
  const usedStops = new Set<string>();
  const stops: TrackStop[] = [];
  [...route.stops].sort((a, b) => a.stop_sequence - b.stop_sequence).forEach(stop => {
    if (usedStops.has(stop.id) || !validCoordinate([stop.longitude, stop.latitude])) return;
    usedStops.add(stop.id);
    const distance = projectStop(coordinates, distances, stop);
    // Inconsistent stop sequence must not send vehicles back along a one-way geometry.
    if (stops.length && distance + EPSILON < stops[stops.length - 1].distance) return;
    stops.push({ stop, distance });
  });
  return {
    route, coordinates, distances, length, stops,
    // Only explicitly closed configurations may wrap; open paths always end at their destination.
    loop: route.simulation_mode === 'loop'
      && distanceMeters(coordinates[0], coordinates[coordinates.length - 1]) <= EPSILON,
  };
}

function createTimeline(track: RouteTrack, from: number, metersPerSecond: number, pause: number): Timeline {
  const phases: Phase[] = [];
  let distance = from;
  let time = 0;
  const add = (to: number, stop: TaxiStop | null, duration: number) => {
    if (duration > 0) {
      phases.push({ start: time, end: time + duration, from: distance, to, stop });
      time += duration;
    }
    distance = to;
  };
  track.stops.forEach(item => {
    if (item.distance + EPSILON < from) return;
    const destination = Math.max(distance, item.distance);
    add(destination, null, (destination - distance) / metersPerSecond);
    add(destination, item.stop, pause);
  });
  add(track.length, null, (track.length - distance) / metersPerSecond);
  return { phases, duration: time };
}

export function createVehicleSimulation(
  vehicle: TaxiVehicle, track: RouteTrack, stopPauseSeconds = 5,
): VehicleSimulation | null {
  if (!vehicle.is_active || vehicle.position_source !== 'simulation'
    || vehicle.route_id !== track.route.id || !Number.isFinite(vehicle.simulation_speed)
    || vehicle.simulation_speed <= 0 || !Number.isFinite(vehicle.initial_progress)) return null;
  const progress = Math.min(1, Math.max(0, vehicle.initial_progress));
  const startDistance = progress * track.length;
  const speed = vehicle.simulation_speed / 3.6;
  const pause = Number.isFinite(stopPauseSeconds) ? Math.max(0, stopPauseSeconds) : 5;
  return {
    vehicle, track, startDistance,
    initial: startDistance >= track.length ? { phases: [], duration: 0 }
      : createTimeline(track, startDistance, speed, pause),
    cycle: createTimeline(track, 0, speed, pause),
  };
}

export function positionOnTrack(track: RouteTrack, distance: number): {
  latitude: number; longitude: number; bearing: number;
} {
  const along = Math.max(0, Math.min(track.length, distance));
  let low = 1;
  let high = track.distances.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (track.distances[middle] < along) low = middle + 1;
    else high = middle;
  }
  const a = track.coordinates[low - 1];
  const b = track.coordinates[low];
  const fraction = (along - track.distances[low - 1]) / (track.distances[low] - track.distances[low - 1]);
  return {
    latitude: a[1] + (b[1] - a[1]) * fraction,
    longitude: a[0] + (b[0] - a[0]) * fraction,
    bearing: bearing(a, b),
  };
}

/** A pure function of active elapsed time, independent of render rate and wall clock. */
export function sampleVehicle(simulation: VehicleSimulation, elapsedSeconds: number): TaxiPosition {
  const { vehicle, track } = simulation;
  let elapsed = Number.isFinite(elapsedSeconds) ? Math.max(0, elapsedSeconds) : 0;
  let timeline = simulation.initial;
  if (elapsed >= timeline.duration && track.loop && simulation.cycle.duration > 0) {
    elapsed = (elapsed - timeline.duration) % simulation.cycle.duration;
    timeline = simulation.cycle;
  }
  const finished = elapsed >= timeline.duration;
  let distance = track.length;
  let phase: Phase | undefined;
  if (!finished && timeline.phases.length) {
    let low = 0;
    let high = timeline.phases.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (timeline.phases[middle].end <= elapsed) low = middle + 1;
      else high = middle;
    }
    phase = timeline.phases[low];
    distance = phase.from + (phase.to - phase.from) * (elapsed - phase.start) / (phase.end - phase.start);
  }
  const next = finished ? undefined : track.stops.find(item => item.distance > distance + EPSILON);
  const stoppedAt = phase?.stop ?? null;
  return {
    vehicle_id: vehicle.id,
    vehicle_code: vehicle.vehicle_code,
    route_id: track.route.id,
    route_name: track.route.name,
    ...positionOnTrack(track, distance),
    speed_kmh: !finished && !stoppedAt ? vehicle.simulation_speed : 0,
    status: finished ? 'finished' : stoppedAt ? 'at_stop' : 'moving',
    next_stop: stoppedAt ?? next?.stop ?? null,
    eta_minutes: finished ? null : stoppedAt ? 0 : next
      ? (next.distance - distance) / (vehicle.simulation_speed / 3.6) / 60 : null,
    progress: distance / track.length,
    position_source: 'simulation',
  };
}
