import { useEffect, useRef } from 'react';
import L from 'leaflet';
import { useMap } from 'react-leaflet';
import type { TaxiPosition, TaxiRoute, TaxiVehicle } from '../../types/ecomove';
import { createRouteTrack, createVehicleSimulation, sampleVehicle } from './simulation';
import type { VehicleSimulation } from './simulation';

export interface TaxiSimulationControllerProps {
  routes: TaxiRoute[];
  vehicles: TaxiVehicle[];
  selectedVehicleId?: string | null;
  onVehicleSelect?: (position: TaxiPosition) => void;
  /** Selected details update once per active second, plus a final completion snapshot. */
  onVehicleUpdate?: (position: TaxiPosition) => void;
  onVisibleCountChange?: (count: number) => void;
  enabled?: boolean;
  stopPauseSeconds?: number;
  relevantRouteIds?: string[];
}

interface RenderedVehicle {
  key: string;
  simulation: VehicleSimulation;
  elapsed: number;
  position: TaxiPosition;
  sampledAt: number;
  marker?: L.Marker;
  visible: boolean;
}

function vehicleIcon(color: string): L.DivIcon {
  // Only a validated colour enters static marker HTML; server names remain text/title attributes.
  const safeColor = /^#[\da-f]{3}(?:[\da-f]{3})?$/i.test(color) ? color : '#177968';
  const taxiMarkup = [
    '<span class="ecomove-taxi-heading" style="display:block;width:34px;height:42px;transform-origin:50% 50%;filter:drop-shadow(0 2px 3px #0005)">',
    '<svg aria-hidden="true" width="34" height="42" viewBox="0 0 34 42">',
    `<path d="M17 0l5 6H12z" fill="${safeColor}"/>`,
    `<rect x="6" y="7" width="22" height="31" rx="6" fill="white" stroke="${safeColor}" stroke-width="3"/>`,
    `<rect x="10" y="11" width="14" height="7" rx="2" fill="${safeColor}"/>`,
    `<path d="M9 22h16M9 28h16" stroke="${safeColor}" stroke-width="2"/>`,
    `<rect x="3" y="14" width="4" height="7" rx="1" fill="${safeColor}"/>`,
    `<rect x="27" y="14" width="4" height="7" rx="1" fill="${safeColor}"/>`,
    `<circle cx="11" cy="34" r="1.5" fill="${safeColor}"/>`,
    `<circle cx="23" cy="34" r="1.5" fill="${safeColor}"/>`,
    '</svg></span>',
  ].join('');

  return L.divIcon({
    className: 'ecomove-taxi-marker',
    iconSize: [34, 42],
    iconAnchor: [17, 21],
    html: taxiMarkup,
  });
}

/** Imperative Leaflet layers keep animation outside React's render cycle. */
export default function TaxiSimulationController(props: TaxiSimulationControllerProps) {
  const map = useMap();
  const callbacks = useRef(props);
  callbacks.current = props;
  const clocks = useRef(new Map<string, number>());
  const redraw = useRef<(() => void) | null>(null);
  const {
    routes,
    vehicles,
    enabled = true,
    stopPauseSeconds = 5,
    relevantRouteIds,
  } = props;
  const routeFilter = relevantRouteIds?.slice().sort().join(',') ?? '';

  useEffect(() => {
    const allowed = relevantRouteIds ? new Set(relevantRouteIds) : null;
    // Track geometry is prepared once per route, then reused by every animation frame.
    const tracks = new Map(
      routes
        .filter(route => !allowed || allowed.has(route.id))
        .map(route => [route.id, createRouteTrack(route)]),
    );
    const seen = new Set<string>();
    const entries: RenderedVehicle[] = [];
    vehicles.forEach(vehicle => {
      // A vehicle is one entity even if an erroneous API page repeats its assignment.
      if (seen.has(vehicle.id)) return;
      seen.add(vehicle.id);
      const track = tracks.get(vehicle.route_id);
      if (!track) return;
      const simulation = createVehicleSimulation(vehicle, track, stopPauseSeconds);
      if (!simulation) return;
      const key = `${vehicle.route_id}:${vehicle.id}`;
      const elapsed = clocks.current.get(key) ?? 0;
      entries.push({
        key,
        simulation,
        elapsed,
        position: sampleVehicle(simulation, elapsed),
        sampledAt: -Infinity,
        visible: false,
      });
    });
    const layers = L.layerGroup().addTo(map);
    let frame: number | null = null;
    let lastTimestamp: number | null = null;
    let activeTime = 0;
    let lastDetails = 0;
    let lastDetailsPosition: TaxiPosition | null = null;
    let visibleCount = -1;
    let disposed = false;

    const draw = (force = false) => {
      if (disposed) return;
      const bounds = map.getBounds().pad(0.15);
      // Demo datasets are small; a defensive cap also prevents future dense imports crowding the map.
      const markerLimit = map.getZoom() < 9 ? 35 : 150;
      let count = 0;
      entries.forEach(entry => {
        if (
          force
          || entry.visible
          || callbacks.current.selectedVehicleId === entry.position.vehicle_id
          || activeTime - entry.sampledAt >= 500
        ) {
          entry.position = sampleVehicle(entry.simulation, entry.elapsed);
          entry.sampledAt = activeTime;
        }
        const position = entry.position;
        const show = bounds.contains([position.latitude, position.longitude]) && count < markerLimit;
        if (!show) {
          if (entry.visible && entry.marker) layers.removeLayer(entry.marker);
          entry.visible = false;
          return;
        }
        count += 1;
        if (!entry.marker) {
          entry.marker = L.marker([position.latitude, position.longitude], {
            icon: vehicleIcon(entry.simulation.track.route.color),
            title: `${position.vehicle_code} · Simulated taxi · ${position.route_name}`,
            alt: `Demo taxi ${position.vehicle_code}; select for simulated vehicle details`,
            keyboard: true,
            zIndexOffset: 400,
          });
          entry.marker.on('click', () => {
            entry.position = sampleVehicle(entry.simulation, entry.elapsed);
            callbacks.current.onVehicleSelect?.(entry.position);
          });
        }
        if (!entry.visible) layers.addLayer(entry.marker);
        entry.visible = true;
        entry.marker.setLatLng([position.latitude, position.longitude]);
        const element = entry.marker.getElement();
        const heading = element?.querySelector<HTMLElement>('.ecomove-taxi-heading');
        if (heading) heading.style.transform = `rotate(${position.bearing}deg)`;
        if (element) {
          const selected = callbacks.current.selectedVehicleId === position.vehicle_id;
          element.style.filter = selected ? 'drop-shadow(0 0 5px #f5bb45)' : '';
          entry.marker.setZIndexOffset(selected ? 600 : 400);
        }
      });
      if (count !== visibleCount) {
        visibleCount = count;
        callbacks.current.onVisibleCountChange?.(count);
      }
      const selected = entries.find(
        entry => entry.position.vehicle_id === callbacks.current.selectedVehicleId,
      );
      const completed = selected?.position.status === 'finished'
        && (
          lastDetailsPosition?.status !== 'finished'
          || lastDetailsPosition.vehicle_id !== selected.position.vehicle_id
        );
      if (activeTime - lastDetails >= 1_000 || completed) {
        if (selected) {
          callbacks.current.onVehicleUpdate?.(selected.position);
          lastDetailsPosition = selected.position;
        }
        lastDetails = activeTime;
      }
    };

    const tick = (timestamp: number) => {
      frame = null;
      if (disposed || document.hidden || !enabled) return;
      const delta = lastTimestamp === null ? 0 : Math.max(0, timestamp - lastTimestamp);
      lastTimestamp = timestamp;
      activeTime += delta;
      entries.forEach(entry => {
        entry.elapsed += delta / 1_000;
      });
      draw();
      if (entries.some(entry => entry.position.status !== 'finished')) {
        frame = requestAnimationFrame(tick);
      }
    };
    const resume = () => {
      if (
        !disposed
        && enabled
        && !document.hidden
        && frame === null
        && entries.some(entry => entry.position.status !== 'finished')
      ) {
        frame = requestAnimationFrame(tick);
      }
    };
    const visibilityChanged = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      // Background time is excluded, so returning to a hidden tab never teleports a taxi.
      lastTimestamp = null;
      resume();
    };
    const viewportChanged = () => {
      draw(true);
      resume();
    };
    redraw.current = viewportChanged;
    map.on('moveend zoomend', viewportChanged);
    document.addEventListener('visibilitychange', visibilityChanged);
    draw(true);
    resume();

    return () => {
      disposed = true;
      if (frame !== null) cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', visibilityChanged);
      map.off('moveend zoomend', viewportChanged);
      entries.forEach(entry => {
        clocks.current.delete(entry.key);
        clocks.current.set(entry.key, entry.elapsed);
        entry.marker?.off();
      });
      // Retain recent route clocks across viewport changes, with a bounded session cache.
      while (clocks.current.size > 500) {
        clocks.current.delete(clocks.current.keys().next().value!);
      }
      redraw.current = null;
      layers.clearLayers();
      layers.remove();
    };
    // The route ID signature is stable when callers construct the filter during rendering.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, routes, vehicles, enabled, stopPauseSeconds, routeFilter]);

  useEffect(() => {
    redraw.current?.();
  }, [props.selectedVehicleId]);

  return null;
}
