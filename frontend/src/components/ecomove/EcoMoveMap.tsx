import { useEffect, useMemo, useRef, useState } from 'react';
import L, { type LeafletEvent, type LatLngTuple } from 'leaflet';
import {
  Circle,
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  Tooltip,
  useMap,
  useMapEvents,
} from 'react-leaflet';
import TaxiSimulationController from './TaxiSimulationController';
import EcoMoveTileLayer from './EcoMoveTileLayer';
import type { Coordinates, TaxiPosition, TaxiRoute, TaxiStop, TaxiVehicle, TransportDisruption } from '../../types/ecomove';
import 'leaflet/dist/leaflet.css';

export type MapBounds = [number, number, number, number];
type SelectionMode = 'pickup' | 'destination' | null;
type Props = {
  routes: TaxiRoute[];
  stops: TaxiStop[];
  vehicles: TaxiVehicle[];
  disruptions: TransportDisruption[];
  pickup: Coordinates | null;
  destination: Coordinates | null;
  radius: number;
  selectionMode: SelectionMode;
  matchingRouteIds?: string[];
  initialRoutes: TaxiRoute[];
  initialRoutesLoaded: boolean;
  onInitialViewReady: () => void;
  selectedJourneyRouteIds?: string[];
  selectedRouteId: string | null;
  selectedVehicleId: string | null;
  onPickup: (point: Coordinates) => void;
  onDestination: (point: Coordinates) => void;
  onRouteSelect: (route: TaxiRoute) => void;
  onVehicleSelect: (position: TaxiPosition) => void;
  onVehicleUpdate: (position: TaxiPosition) => void;
  onBoundsChange: (bounds: MapBounds) => void;
};

const pinIcon = (kind: 'pickup' | 'destination') => L.divIcon({
  className: `ecomove-location-marker ${kind}`,
  html: `<span>${kind === 'pickup' ? 'P' : 'D'}</span>`,
  iconSize: [32, 40],
  iconAnchor: [16, 39],
});

const pickupIcon = pinIcon('pickup');
const destinationIcon = pinIcon('destination');
const coordinates = (point: Coordinates): LatLngTuple => [point.latitude, point.longitude];

type MapBehaviorProps = Pick<
  Props,
  | 'pickup'
  | 'destination'
  | 'routes'
  | 'selectedRouteId'
  | 'selectedJourneyRouteIds'
  | 'selectionMode'
  | 'initialRoutes'
  | 'initialRoutesLoaded'
  | 'onInitialViewReady'
  | 'onPickup'
  | 'onDestination'
  | 'onBoundsChange'
>;

function MapBehavior({
  pickup,
  destination,
  routes,
  selectedRouteId,
  selectedJourneyRouteIds,
  selectionMode,
  initialRoutes,
  initialRoutesLoaded,
  onInitialViewReady,
  onPickup,
  onDestination,
  onBoundsChange,
}: MapBehaviorProps) {
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const initialized = useRef(false);
  const callback = useRef(onBoundsChange);
  callback.current = onBoundsChange;
  const map = useMapEvents({
    click: event => {
      const point = { latitude: event.latlng.lat, longitude: event.latlng.lng };
      if (selectionMode === 'pickup') onPickup(point);
      if (selectionMode === 'destination') onDestination(point);
    },
    moveend: () => {
      // The viewport changes continuously during a drag; query only after movement settles.
      clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        const bounds = map.getBounds();
        callback.current([
          bounds.getWest(),
          bounds.getSouth(),
          bounds.getEast(),
          bounds.getNorth(),
        ]);
      }, 450);
    },
  });
  useEffect(() => {
    const bounds = map.getBounds();
    callback.current([
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    ]);
    return () => { clearTimeout(timer.current); };
  }, [map]);
  useEffect(() => {
    if (!initialRoutesLoaded || initialized.current) return;
    initialized.current = true;
    const points = initialRoutes.flatMap(route =>
      route.route_geometry.coordinates.map(([longitude, latitude]) => [latitude, longitude] as LatLngTuple));
    if (points.length) map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 13, animate: false });
    const bounds = map.getBounds();
    callback.current([
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    ]);
    onInitialViewReady();
  }, [map, initialRoutes, initialRoutesLoaded, onInitialViewReady]);
  useEffect(() => {
    map.getContainer().classList.toggle('ecomove-picking', selectionMode !== null);
    return () => { map.getContainer().classList.remove('ecomove-picking'); };
  }, [map, selectionMode]);
  const pickupKey = pickup ? `${pickup.latitude},${pickup.longitude}` : '';
  const destinationKey = destination ? `${destination.latitude},${destination.longitude}` : '';
  useEffect(() => {
    if (pickup) map.flyTo(coordinates(pickup), 14, { duration: .65 });
  }, [map, pickupKey]);
  useEffect(() => {
    if (destination) map.flyTo(coordinates(destination), 14, { duration: .65 });
  }, [map, destinationKey]);
  const selectedIds = selectedJourneyRouteIds?.length
    ? selectedJourneyRouteIds
    : selectedRouteId ? [selectedRouteId] : [];
  const selectionKey = selectedIds.join(',');
  const geometryKey = routes
    .filter(route => selectedIds.includes(route.id))
    .map(route => `${route.id}:${route.route_geometry.coordinates.length}`)
    .join(',');
  useEffect(() => {
    const points = routes
      .filter(route => selectedIds.includes(route.id))
      .flatMap(route => route.route_geometry.coordinates.map(
        ([longitude, latitude]) => [latitude, longitude] as LatLngTuple,
      ));
    if (points.length) map.fitBounds(L.latLngBounds(points), { padding: [55, 55], maxZoom: 14 });
  }, [map, selectionKey, geometryKey]);
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map]);
  return null;
}

function StopLayer({
  stops,
  selectedRouteId,
  routes,
  onDestination,
}: Pick<Props, 'stops' | 'selectedRouteId' | 'routes' | 'onDestination'>) {
  const map = useMap();
  const [zoom, setZoom] = useState(map.getZoom());
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) });
  const selectedStops = new Set(routes.find(route => route.id === selectedRouteId)?.stops.map(stop => stop.id));
  // Keep the base map readable at low zoom while retaining every selected-route stop.
  const visibleStops = useMemo(
    () => stops
      .filter(stop =>
        zoom >= 12 || stop.stop_type === 'rank' || stop.is_interchange || selectedStops.has(stop.id))
      .slice(0, 120),
    [stops, zoom, selectedRouteId],
  );

  return (
    <>
      {visibleStops.map(stop => (
        <CircleMarker
          key={stop.id}
          center={[stop.latitude, stop.longitude]}
          radius={stop.stop_type === 'rank' ? 7 : 5}
          pathOptions={{
            color: '#fff',
            weight: 2,
            fillColor: stop.is_interchange
              ? '#7759b5'
              : stop.stop_type === 'rank' ? '#163f2d' : '#4e8f78',
            fillOpacity: 1,
          }}
        >
          <Tooltip>{stop.name}{stop.is_demo ? ' · demo point' : ''}</Tooltip>
          <Popup>
            <div className="ecomove-map-popup">
              <span>{stop.is_demo ? 'DEMONSTRATION POINT' : 'CONFIGURED STOP'}</span>
              <h3>{stop.name}</h3>
              <p>
                {stop.stop_type.replace('_', ' ')}
                {stop.is_interchange ? ' · configured interchange' : ''}
              </p>
              {stop.is_demo && (
                <p>Illustrative boarding point; confirm local boarding arrangements.</p>
              )}
              {stop.stop_type !== 'pickup' && (
                <button onClick={() => onDestination({
                  latitude: stop.latitude,
                  longitude: stop.longitude,
                })}>
                  Use as destination
                </button>
              )}
            </div>
          </Popup>
        </CircleMarker>
      ))}
    </>
  );
}

export default function EcoMoveMap(props: Props) {
  const {
    routes,
    stops,
    vehicles,
    disruptions,
    pickup,
    destination,
    radius,
    selectedRouteId,
    selectedVehicleId,
  } = props;
  const drag = (event: LeafletEvent, callback: Props['onPickup']) => {
    const point = (event.target as L.Marker).getLatLng();
    callback({ latitude: point.lat, longitude: point.lng });
  };
  const highlighted = props.selectedJourneyRouteIds?.length
    ? props.selectedJourneyRouteIds
    : selectedRouteId ? [selectedRouteId] : [];

  return (
    <MapContainer
      center={[-28.5, 24]}
      zoom={5}
      minZoom={5}
      maxZoom={18}
      className="ecomove-leaflet"
      scrollWheelZoom
      zoomControl={false}
    >
      <EcoMoveTileLayer />
      <MapBehavior {...props} />
      {routes.map(route => {
        const isHighlighted = highlighted.includes(route.id);
        const isMatch = props.matchingRouteIds?.includes(route.id);
        const weight = isHighlighted ? 7 : isMatch ? 5 : 4;
        const opacity = highlighted.length
          ? isHighlighted ? 0.95 : 0.25
          : props.matchingRouteIds ? isMatch ? 0.9 : 0.2 : 0.8;

        return (
          <Polyline
            key={route.id}
            positions={route.route_geometry.coordinates.map(
              ([longitude, latitude]) => [latitude, longitude] as LatLngTuple,
            )}
            pathOptions={{
              color: route.color || '#168b65',
              weight,
              opacity,
              lineCap: 'round',
            }}
            eventHandlers={{ click: () => props.onRouteSelect(route) }}
          >
            <Tooltip sticky>
              {route.name} · {route.is_demo ? 'demonstration route' : 'configured route'}
            </Tooltip>
          </Polyline>
        );
      })}
      {pickup && (
        <>
          <Circle
            center={coordinates(pickup)}
            radius={radius}
            pathOptions={{
              color: '#178760',
              fillColor: '#3cbf92',
              fillOpacity: 0.055,
              weight: 1,
              dashArray: '5 6',
            }}
          />
          <Marker
            position={coordinates(pickup)}
            icon={pickupIcon}
            draggable
            eventHandlers={{ dragend: event => drag(event, props.onPickup) }}
          >
            <Popup>
              <b>Your selected pickup</b>
              <p>Drag this pin to change location. Board only at a configured stop.</p>
            </Popup>
          </Marker>
        </>
      )}
      {destination && (
        <Marker
          position={coordinates(destination)}
          icon={destinationIcon}
          draggable
          eventHandlers={{ dragend: event => drag(event, props.onDestination) }}
        >
          <Popup>
            <b>Your selected destination</b>
            <p>Drag this pin to adjust your destination.</p>
          </Popup>
        </Marker>
      )}
      <StopLayer
        stops={stops}
        routes={routes}
        selectedRouteId={selectedRouteId}
        onDestination={props.onDestination}
      />
      {disruptions.slice(0, 100).map(incident => (
        <CircleMarker
          key={incident.id}
          center={[incident.latitude, incident.longitude]}
          radius={8}
          pathOptions={{ color: '#fff', weight: 2, fillColor: '#d88937', fillOpacity: 1 }}
        >
          <Tooltip>Reported road hazard · {incident.category.replace(/_/g, ' ')}</Tooltip>
          <Popup>
            <div className="ecomove-map-popup hazard">
              <span>RESIDENT INFRASTRUCTURE REPORT</span>
              <h3>{incident.title}</h3>
              <p>
                {incident.category.replace(/_/g, ' ')} · {incident.status.replace(/_/g, ' ')}
              </p>
              <p>{incident.location_text || 'Mapped report location'}</p>
              <p>
                {new Date(incident.created_at).toLocaleString('en-ZA')}
                {incident.distance_meters != null
                  ? ` · ${Math.round(incident.distance_meters)} m from route`
                  : ''}
              </p>
              <b>A nearby report does not confirm a route closure.</b>
            </div>
          </Popup>
        </CircleMarker>
      ))}
      <TaxiSimulationController
        routes={routes}
        vehicles={vehicles}
        selectedVehicleId={selectedVehicleId}
        onVehicleSelect={props.onVehicleSelect}
        onVehicleUpdate={props.onVehicleUpdate}
      />
      <MapControls />
    </MapContainer>
  );
}

function MapControls() {
  const map = useMap();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) {
      L.DomEvent.disableClickPropagation(ref.current);
      L.DomEvent.disableScrollPropagation(ref.current);
    }
  }, []);

  return (
    <div className="ecomove-zoom" ref={ref}>
      <button aria-label="Zoom in" onClick={() => map.zoomIn()}>+</button>
      <button aria-label="Zoom out" onClick={() => map.zoomOut()}>−</button>
    </div>
  );
}
