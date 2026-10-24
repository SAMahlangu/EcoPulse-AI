import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  Bus,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Flag,
  Footprints,
  Info,
  LocateFixed,
  MapPin,
  Navigation,
  Route,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import EcoMoveMap, { type MapBounds } from '../components/ecomove/EcoMoveMap';
import { getJson, postJson } from '../services/api';
import { useAuth } from '../services/auth';
import type {
  Coordinates,
  JourneyOption,
  JourneySearchResponse,
  NearbyTaxiRoute,
  TaxiPosition,
  TaxiRoute,
  TaxiStop,
  TaxiVehicle,
  TransportDisruption,
} from '../types/ecomove';
import './ecomove.css';

type Page<T> = { items: T[]; total: number; limit: number; offset: number };
type Place = Coordinates & { name: string; admin1?: string; country?: string };
type Municipality = { id: string; name: string };
const pointLabel = (point: Coordinates | null) =>
  point ? `${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)}` : 'No point selected';
const distance = (meters: number) =>
  meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;

interface LocationSearchProps {
  label: string;
  value: string;
  onSelect: (place: Place) => void;
  icon: 'pickup' | 'destination';
}

function LocationSearch({ label, value, onSelect, icon }: LocationSearchProps) {
  const [term, setTerm] = useState('');
  const [debounced, setDebounced] = useState('');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(term.trim()), 350);
    return () => clearTimeout(timer);
  }, [term]);

  const results = useQuery({
    queryKey: ['climate-locations', debounced],
    queryFn: () => getJson<{ items: Place[] }>(`/climate/locations?search=${encodeURIComponent(debounced)}`),
    enabled: open && debounced.length >= 2,
    staleTime: 3_600_000,
    retry: 1,
  });

  return (
    <div className="em-place-field">
      <label htmlFor={`em-${icon}-search`}>{label}</label>
      <div className="em-search-input">
        <span className={`em-point-dot ${icon}`} />
        <input
          id={`em-${icon}-search`}
          autoComplete="off"
          placeholder={value || `Search ${icon === 'pickup' ? 'a pickup area' : 'your destination'}`}
          value={term}
          onFocus={() => setOpen(true)}
          onChange={event => {
            setTerm(event.target.value);
            setOpen(true);
          }}
        />
        <Search size={15} />
      </div>
      {value && (
        <small className="em-selected-coordinate">
          <Check size={11} />{value}
        </small>
      )}
      {open && debounced.length >= 2 && (
        <div className="em-location-options" role="listbox" aria-label={`${label} search results`}>
          <button
            type="button"
            className="em-search-dismiss"
            onClick={() => setOpen(false)}
            aria-label="Close place search"
          >
            <X size={13} />
          </button>
          {results.isFetching ? (
            <p>Searching locations…</p>
          ) : results.isError ? (
            <p>Place search is unavailable. You can pin a point on the map.</p>
          ) : results.data?.items.length ? (
            results.data.items.slice(0, 6).map((place, index) => (
              <button
                type="button"
                key={`${place.name}-${index}`}
                onClick={() => {
                  onSelect(place);
                  setTerm('');
                  setOpen(false);
                }}
              >
                <MapPin size={14} />
                <span>
                  <b>{place.name}</b>
                  <small>{place.admin1 || place.country || 'South Africa'}</small>
                </span>
              </button>
            ))
          ) : (
            <p>No places found. Try a nearby suburb or pin the map.</p>
          )}
        </div>
      )}
    </div>
  );
}

interface RouteCardProps {
  route: TaxiRoute | NearbyTaxiRoute;
  selected: boolean;
  onSelect: () => void;
}

function RouteCard({ route, selected, onSelect }: RouteCardProps) {
  const nearby = route as NearbyTaxiRoute;
  const routeColor = route.color || '#168560';

  return (
    <button className={`em-route-card ${selected ? 'selected' : ''}`} onClick={onSelect}>
      <div className="em-route-card-top">
        <span className="em-route-icon" style={{ color: routeColor, backgroundColor: `${routeColor}14` }}>
          <Bus size={18} />
        </span>
        <span className="em-route-card-heading">
          <b>{route.name}</b>
          <small>{route.is_demo ? 'DEMO ROUTE' : 'CONFIGURED ROUTE'}</small>
        </span>
        <ArrowRight size={15} />
      </div>
      <div className="em-route-direction">
        <span>{route.origin}</span><ArrowRight size={12} /><span>{route.destination}</span>
      </div>
      {nearby.nearest_pickup_stop && (
        <p className="em-boarding">
          <MapPin size={12} />
          <span>Board at <b>{nearby.nearest_pickup_stop.name}</b></span>
        </p>
      )}
      <div className="em-route-meta">
        {nearby.walking_distance_meters != null && (
          <span><Footprints size={12} />{distance(nearby.walking_distance_meters)} pickup estimate</span>
        )}
        {route.estimated_duration_minutes != null && (
          <span><Clock3 size={12} />{route.estimated_duration_minutes} min estimate</span>
        )}
        {nearby.simulated_vehicle_count != null && (
          <span><Bus size={12} />{nearby.simulated_vehicle_count} simulated</span>
        )}
      </div>
      <span className="em-route-view">View route &amp; stops <ArrowRight size={12} /></span>
    </button>
  );
}

function JourneyCard({ journey, onSelect }: { journey: JourneyOption; onSelect: () => void }) {
  return (
    <button className="em-journey-card" onClick={onSelect}>
      <div>
        <span className="em-journey-tag">
          {journey.transfers ? '1 configured transfer' : 'Direct route'}
        </span>
        <ArrowRight size={14} />
      </div>
      <b>{journey.route_name}</b>
      <p>
        <MapPin size={12} />{journey.pickup_stop.name}
        <ArrowRight size={12} />{journey.destination_stop.name}
      </p>
      {journey.legs.length > 1 && <small>Transfer at {journey.legs[0].destination_stop.name}</small>}
      <div className="em-route-meta">
        <span>
          <Footprints size={12} />
          {distance(journey.walking_distance_meters)} estimated total walk (straight-line)
        </span>
        {journey.estimated_duration_minutes != null && (
          <span><Clock3 size={12} />{journey.estimated_duration_minutes} min estimate</span>
        )}
      </div>
      <small>
        {journey.is_demo
          ? 'Demonstration journey · confirm boarding locally'
          : 'Based on configured route data'}
        {journey.fare != null
          ? ` · R${journey.fare.toFixed(2)} estimated fare`
          : ' · fare unconfirmed'}
      </small>
    </button>
  );
}

interface JourneyDetailsProps {
  journey: JourneyOption;
  routes: TaxiRoute[];
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  disruptions: TransportDisruption[];
  onClose: () => void;
}

function JourneyDetails({
  journey,
  routes,
  loading,
  failed,
  onRetry,
  disruptions,
  onClose,
}: JourneyDetailsProps) {
  const routeIds = journey.legs.map(leg => leg.route_id);
  const hazards = disruptions.filter(incident => incident.route_ids.some(id => routeIds.includes(id)));
  return (
    <div className="em-detail-card em-journey-details" aria-label="Selected configured journey details">
      <button className="em-detail-close" aria-label="Close journey details" onClick={onClose}>
        <X size={17} />
      </button>
      <span className="em-detail-eyebrow">
        <Route size={13} />{journey.is_demo ? 'DEMONSTRATION JOURNEY' : 'CONFIGURED JOURNEY'}
      </span>
      <h2>Your journey to {journey.destination_stop.name}</h2>
      <p className="em-detail-direction">
        <Navigation size={13} />
        {journey.transfers ? 'Two routes · one configured interchange' : 'One direct configured route'}
      </p>
      {loading && (
        <p className="em-detail-description" role="status">Loading complete route geometries…</p>
      )}
      {failed && (
        <div className="em-route-hazard" role="alert">
          <span>
            Some route geometry could not load.{' '}
            <button onClick={onRetry}>Retry route details</button>
          </span>
        </div>
      )}
      {hazards.length > 0 && (
        <div className="em-route-hazard">
          <AlertTriangle size={15} />
          <span>
            Reported road hazards near this journey ({hazards.length}). Reports do not confirm a closure.
          </span>
        </div>
      )}
      {journey.legs.map((leg, index) => (
        <div className="em-journey-leg" key={`${leg.route_id}-${index}`}>
          <span>LEG {index + 1}</span>
          <h3>{leg.route_name}</h3>
          <p>
            Towards {routes.find(route => route.id === leg.route_id)?.destination ?? leg.destination_stop.name}
          </p>
          <ol>
            <li>
              <MapPin size={14} />
              <div>
                <small>{index === 0 ? 'BOARD AT' : 'BOARD NEXT ROUTE AT'}</small>
                <b>{leg.pickup_stop.name}</b>
              </div>
            </li>
            <li>
              <Flag size={14} />
              <div>
                <small>{index < journey.legs.length - 1 ? 'TRANSFER AT' : 'DROP OFF AT'}</small>
                <b>{leg.destination_stop.name}</b>
              </div>
            </li>
          </ol>
          {index < journey.legs.length - 1 && (
            <div className="em-configured-transfer">
              <ArrowDown size={13} />
              Change to {journey.legs[index + 1].route_name} at this configured interchange.
            </div>
          )}
        </div>
      ))}
      <div className="em-journey-walk">
        <Footprints size={14} />
        <span>
          <b>{distance(journey.walking_distance_meters)} estimated total walk</b>
          <small>
            Pickup + destination distances are straight-line estimates; pedestrian routes have not been checked.
          </small>
        </span>
      </div>
      <p className="em-detail-source">
        Source: {journey.data_source}.{' '}
        {journey.is_demo
          ? 'Illustrative routes and boarding points. Confirm locally.'
          : 'Based on configured transport data.'}{' '}
        {journey.fare == null ? 'Fare unconfirmed.' : `Estimated fare R${journey.fare.toFixed(2)}.`}
      </p>
    </div>
  );
}

export default function EcoMoveDashboard() {
  const { user } = useAuth();
  const [area, setArea] = useState<'resident' | 'pretoria'>('resident');
  const [pickup, setPickup] = useState<Coordinates | null>(null);
  const [destination, setDestination] = useState<Coordinates | null>(null);
  const [pickupName, setPickupName] = useState('');
  const [destinationName, setDestinationName] = useState('');
  const [radius, setRadius] = useState(1000);
  const [selectionMode, setSelectionMode] = useState<'pickup' | 'destination' | null>(null);
  const [geoError, setGeoError] = useState('');
  const [locating, setLocating] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);
  const [selectedRoute, setSelectedRoute] = useState<TaxiRoute | null>(null);
  const [selectedVehicle, setSelectedVehicle] = useState<TaxiPosition | null>(null);
  const [selectedJourney, setSelectedJourney] = useState<JourneyOption | null>(null);
  const [initialViewReady, setInitialViewReady] = useState(false);
  const [bounds, setBounds] = useState<MapBounds | null>(null);
  const [showHazards, setShowHazards] = useState(true);

  // Keep municipality lookup on the existing locations API and reuse the resident's saved area.
  const demoArea = useQuery({
    queryKey: ['ecomove', 'pretoria-municipality'],
    queryFn: () => getJson<Page<Municipality>>('/locations/municipalities?search=Tshwane&limit=100'),
    staleTime: 3_600_000,
  });
  const residentArea = useQuery({
    queryKey: ['ecomove', 'resident-municipality', user?.municipality_id],
    queryFn: () => getJson<Municipality>(`/locations/municipalities/${user?.municipality_id}`),
    enabled: Boolean(user?.municipality_id),
    staleTime: 3_600_000,
  });
  const pretoriaId = demoArea.data?.items.find(item => item.name.toLowerCase().includes('tshwane'))?.id;
  const municipalityId = area === 'pretoria' ? pretoriaId : user?.municipality_id;
  const queryScope = municipalityId ? `municipality_id=${encodeURIComponent(municipalityId)}` : '';
  const viewportScope = `${queryScope}${bounds ? `&bbox=${bounds.map(value => value.toFixed(5)).join(',')}` : ''}`;
  const catalog = useQuery({
    queryKey: ['ecomove', 'catalog', municipalityId],
    queryFn: () => getJson<Page<TaxiRoute>>(`/ecomove/routes?${queryScope}&limit=100`),
    enabled: Boolean(municipalityId),
    staleTime: 300_000,
    retry: 1,
  });

  // Wait for the map's initial fit before loading viewport-bound map layers.
  const routes = useQuery({
    queryKey: ['ecomove', 'routes', municipalityId, bounds],
    queryFn: () => getJson<Page<TaxiRoute>>(`/ecomove/routes?${viewportScope}&limit=100`),
    enabled: Boolean(municipalityId && initialViewReady && bounds),
    staleTime: 300_000,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === municipalityId ? previous : undefined,
    retry: 1,
  });
  const stops = useQuery({
    queryKey: ['ecomove', 'stops', municipalityId, bounds],
    queryFn: () => getJson<Page<TaxiStop>>(`/ecomove/stops?${viewportScope}&limit=100`),
    enabled: Boolean(municipalityId && initialViewReady && bounds),
    staleTime: 300_000,
    placeholderData: (previous, previousQuery) =>
      previousQuery?.queryKey[2] === municipalityId ? previous : undefined,
    retry: 1,
  });
  const vehicles = useQuery({
    queryKey: ['ecomove', 'vehicles', municipalityId],
    queryFn: () => getJson<Page<TaxiVehicle>>(`/ecomove/simulation/vehicles?${queryScope}&limit=100`),
    enabled: Boolean(municipalityId),
    staleTime: 300_000,
    retry: 1,
  });
  const disruptions = useQuery({
    queryKey: ['ecomove', 'disruptions', municipalityId, bounds],
    queryFn: () => getJson<Page<TransportDisruption>>(`/ecomove/disruptions?${viewportScope}&limit=100`),
    enabled: Boolean(municipalityId && initialViewReady && bounds) && showHazards,
    staleTime: 60_000,
    retry: 1,
  });
  const nearby = useQuery({
    queryKey: ['ecomove', 'nearby', municipalityId, pickup?.latitude, pickup?.longitude, radius],
    queryFn: () => getJson<Page<NearbyTaxiRoute>>(
      `/ecomove/routes/nearby?${queryScope}&latitude=${pickup!.latitude}`
      + `&longitude=${pickup!.longitude}&radius_meters=${radius}&limit=100`,
    ),
    enabled: Boolean(pickup && municipalityId),
    staleTime: 120_000,
    retry: 1,
  });
  const journey = useMutation({
    mutationFn: () => postJson<JourneySearchResponse>('/ecomove/journey/search', {
      pickup,
      destination,
      radius_meters: radius,
      municipality_id: municipalityId,
      limit: 20,
    }),
  });

  const journeyRouteIds = useMemo(() => selectedJourney ? [...new Set(selectedJourney.legs.map(leg => leg.route_id))] : [], [selectedJourney]);
  const journeyRoutes = useQuery({
    queryKey: ['ecomove', 'journey-routes', journeyRouteIds],
    queryFn: () => Promise.all(journeyRouteIds.map(id => getJson<TaxiRoute>(`/ecomove/routes/${id}`))),
    enabled: journeyRouteIds.length > 0,
    staleTime: 300_000,
    retry: 1,
  });
  const resetJourney = journey.reset;
  const selectPickup = useCallback((point: Coordinates) => {
    setPickup(point);
    setPickupName('Pickup location selected');
    setSelectedJourney(null);
    setSelectionMode(null);
    setGeoError('');
    resetJourney();
  }, [resetJourney]);
  const selectDestination = useCallback((point: Coordinates) => {
    setDestination(point);
    setDestinationName('Destination selected');
    setSelectedJourney(null);
    setSelectionMode(null);
    resetJourney();
  }, [resetJourney]);
  const changeArea = (next: 'resident' | 'pretoria') => {
    setArea(next);
    setInitialViewReady(false);
    setBounds(null);
    setSelectedJourney(null);
    setSelectedRoute(null);
    setSelectedVehicle(null);
    setPickup(null);
    setDestination(null);
    setPickupName('');
    setDestinationName('');
    journey.reset();
  };

  // Browser location is requested only from the resident's explicit button action.
  const useCurrentLocation = () => {
    if (!navigator.geolocation) {
      setGeoError('This browser does not support location. Pin your pickup on the map.');
      return;
    }
    setLocating(true);
    setGeoError('');
    navigator.geolocation.getCurrentPosition(
      position => {
        selectPickup({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
        });
        setPickupName('Your current location');
        setLocating(false);
      },
      error => {
        setLocating(false);
        setGeoError(error.code === 1
          ? 'Location permission was denied. Pin your pickup on the map instead.'
          : 'Your location could not be found. Pin your pickup on the map instead.');
      },
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 60000 },
    );
  };
  const selectRoute = useCallback((route: TaxiRoute) => {
    setSelectedJourney(null);
    setSelectedRoute(route);
    setSelectedVehicle(null);
    setPanelOpen(false);
  }, []);
  const mapRoutes = useMemo(() => {
    const result = new Map((routes.data?.items ?? catalog.data?.items ?? []).map(route => [route.id, route]));
    nearby.data?.items.forEach(route => result.set(route.id, route));
    if (selectedRoute) result.set(selectedRoute.id, selectedRoute);
    if (selectedJourney) journeyRoutes.data?.forEach(route => result.set(route.id, route));
    return Array.from(result.values());
  }, [routes.data, catalog.data, nearby.data, selectedRoute, selectedJourney, journeyRoutes.data]);
  const selectedVehicleRoute = mapRoutes.find(route => route.id === selectedVehicle?.route_id);
  const detailRoute = selectedVehicleRoute ?? selectedRoute;
  const displayRoutes = pickup ? nearby.data?.items ?? [] : routes.data?.items ?? catalog.data?.items ?? [];
  const routeLoading = pickup ? nearby.isFetching : catalog.isPending && Boolean(municipalityId);
  const routeError = pickup ? nearby.isError : catalog.isError || routes.isError;
  const activeRouteIds = useMemo(() => new Set(mapRoutes.map(route => route.id)), [mapRoutes]);
  const activeVehicles = useMemo(
    () => vehicles.data?.items.filter(vehicle => vehicle.is_active && activeRouteIds.has(vehicle.route_id)) ?? [],
    [vehicles.data, activeRouteIds],
  );
  const routeHazards = detailRoute ? disruptions.data?.items.filter(incident => incident.route_ids.includes(detailRoute.id)) ?? [] : [];
  const mapStops = useMemo(() => {
    const result = new Map((stops.data?.items ?? []).map(stop => [stop.id, stop]));
    mapRoutes.forEach(route => route.stops.forEach(stop => result.set(stop.id, stop)));
    return Array.from(result.values());
  }, [stops.data, mapRoutes]);
  const destinationStops = useMemo(() => {
    const result = new Map<string, TaxiStop>();
    catalog.data?.items.forEach(route => route.stops.forEach(stop => {
      if (stop.stop_type !== 'pickup') result.set(stop.id, stop);
    }));
    return Array.from(result.values()).sort((left, right) => left.name.localeCompare(right.name));
  }, [catalog.data]);
  const submitJourney = (event: FormEvent) => {
    event.preventDefault();
    if (pickup && destination && municipalityId) {
      setSelectionMode(null);
      setSelectedJourney(null);
      journey.mutate();
    }
  };
  const selectJourney = (option: JourneyOption) => {
    setSelectedJourney(option);
    setSelectedRoute(null);
    setSelectedVehicle(null);
    setPanelOpen(false);
  };
  const handleInitialViewReady = useCallback(() => setInitialViewReady(true), []);
  const areaName = area === 'pretoria'
    ? 'Pretoria demonstration area'
    : residentArea.data?.name ?? 'Your selected municipality';

  return (
    <div className="ecomove-page">
      <div className="em-heading">
        <div>
          <p className="em-eyebrow">
            <Bus size={14} /> ECOMOVE AI <span>SMART MINIBUS TRANSPORT</span>
          </p>
          <h1>Your next journey, mapped<span>.</span></h1>
          <p>Find your pickup point. Explore configured routes. Move with a little more clarity.</p>
        </div>
        <button className="em-current-location" disabled={locating} onClick={useCurrentLocation}>
          <LocateFixed size={16} />
          {locating ? 'Finding your location…' : 'Use My Current Location'}
        </button>
      </div>

      <div className="em-demo-banner" role="status">
        <span className="em-demo-dot" />
        <b>DEMO MODE — Taxi positions are simulated. Not live GPS tracking.</b>
        <span>Illustrative routes & boarding points</span>
      </div>

      <div className={`em-workspace ${panelOpen ? 'panel-expanded' : 'panel-collapsed'}`}>
        <aside className="em-sidebar" aria-label="Taxi journey planner">
          <button
            className="em-mobile-panel-toggle"
            onClick={() => setPanelOpen(!panelOpen)}
            aria-expanded={panelOpen}
          >
            <span className="em-panel-handle" />
            <span>
              <Bus size={17} />
              <b>Plan your journey</b>
              <small>{displayRoutes.length} configured routes</small>
              {panelOpen ? <ChevronDown size={18} /> : <ChevronUp size={18} />}
            </span>
          </button>

          <div className="em-sidebar-content">
            <div className="em-planner-title">
              <span><Navigation size={17} /></span>
              <div>
                <h2>Where are you heading?</h2>
                <p>A clearer way to connect your journey.</p>
              </div>
            </div>

            <div className="em-area-control">
              <label htmlFor="em-area"><MapPin size={12} /> Explore an area</label>
              <select
                id="em-area"
                value={area}
                onChange={event => changeArea(event.target.value as 'resident' | 'pretoria')}
              >
                <option value="resident">
                  {residentArea.data?.name ?? 'My selected municipality'}
                </option>
                <option value="pretoria" disabled={!pretoriaId}>
                  Pretoria demo coverage{demoArea.isFetching ? ' (loading)' : ''}
                </option>
              </select>
              {area === 'pretoria' && <small>Demonstration coverage selected intentionally.</small>}
              {demoArea.isError && (
                <small role="alert">
                  Demo coverage lookup unavailable.{' '}
                  <button onClick={() => demoArea.refetch()}>Retry</button>
                </small>
              )}
            </div>

            <form onSubmit={submitJourney} className="em-journey-form">
              <LocationSearch
                label="YOUR PICKUP"
                icon="pickup"
                value={pickup ? pickupName || pointLabel(pickup) : ''}
                onSelect={place => {
                  selectPickup(place);
                  setPickupName(place.name);
                }}
              />
              <button
                type="button"
                className={`em-pin-button ${selectionMode === 'pickup' ? 'picking' : ''}`}
                onClick={() => {
                  setSelectionMode(selectionMode === 'pickup' ? null : 'pickup');
                  setPanelOpen(false);
                }}
              >
                <MapPin size={14} />
                {pickup ? 'Change Pickup Location' : 'Pin My Pickup Location'}
                <ArrowRight size={13} />
              </button>
              <div className="em-planner-connector">
                <i /><ArrowDown size={12} /><i />
              </div>
              <LocationSearch
                label="YOUR DESTINATION"
                icon="destination"
                value={destination ? destinationName || pointLabel(destination) : ''}
                onSelect={place => {
                  selectDestination(place);
                  setDestinationName(place.name);
                }}
              />

              <div className="em-destination-options">
                <button
                  type="button"
                  className={selectionMode === 'destination' ? 'picking' : ''}
                  onClick={() => {
                    setSelectionMode(selectionMode === 'destination' ? null : 'destination');
                    setPanelOpen(false);
                  }}
                >
                  <Flag size={13} /> Pin on map
                </button>
                <select
                  aria-label="Choose a configured destination"
                  value=""
                  onChange={event => {
                    const stop = destinationStops.find(item => item.id === event.target.value);
                    if (stop) {
                      selectDestination({ latitude: stop.latitude, longitude: stop.longitude });
                      setDestinationName(stop.name);
                    }
                  }}
                >
                  <option value="">Choose a configured stop</option>
                  {destinationStops.map(stop => (
                    <option key={stop.id} value={stop.id}>
                      {stop.name}{stop.is_demo ? ' (demo)' : ''}
                    </option>
                  ))}
                </select>
              </div>

              <div className="em-radius">
                <label htmlFor="em-radius"><SlidersHorizontal size={12} /> Pickup search radius</label>
                <select
                  id="em-radius"
                  value={radius}
                  onChange={event => {
                    setRadius(Number(event.target.value));
                    setSelectedJourney(null);
                    journey.reset();
                  }}
                >
                  <option value={1000}>1 km</option>
                  <option value={2000}>2 km</option>
                  <option value={5000}>5 km</option>
                  <option value={10000}>10 km</option>
                </select>
              </div>
              <button
                className="em-find-button"
                disabled={!pickup || !destination || !municipalityId || journey.isPending}
              >
                <Search size={16} />
                {journey.isPending ? 'Finding configured journeys…' : 'Find Taxi Routes'}
                <ArrowRight size={15} />
              </button>
              <p className="em-privacy">
                <Info size={11} /> Your pickup stays in this session and is used to search routes.
              </p>
            </form>

            {geoError && (
              <div className="em-inline-notice" role="alert">
                <AlertTriangle size={16} /><p>{geoError}</p>
              </div>
            )}
            {!municipalityId && (
              <div className="em-inline-notice">
                <Info size={16} />
                <p>Choose a municipality in your profile or explore Pretoria demo coverage.</p>
              </div>
            )}

            <div className="em-results-heading">
              <div>
                <h2>
                  {journey.data
                    ? 'Your journey options'
                    : pickup ? 'Routes near your pickup' : 'Explore configured routes'}
                </h2>
                <p>
                  {journey.data
                    ? 'Only stored routes and interchanges are suggested.'
                    : pickup
                      ? 'Board at a configured pickup point or rank.'
                      : 'Select a pickup to find routes near you.'}
                </p>
              </div>
              <span>{journey.data?.routes.length ?? displayRoutes.length}</span>
            </div>

            {journey.isError && (
              <div className="em-empty" role="alert">
                <AlertTriangle size={22} />
                <b>Journey search unavailable</b>
                <p>{(journey.error as Error).message}</p>
                <button onClick={() => journey.mutate()}>Try again</button>
              </div>
            )}
            {journey.data ? (
              journey.data.routes.length ? (
                <div className="em-route-list">
                  {journey.data.routes.map((option, index) => (
                    <JourneyCard
                      key={`${option.route_id}-${index}`}
                      journey={option}
                      onSelect={() => selectJourney(option)}
                    />
                  ))}
                </div>
              ) : (
                <div className="em-empty">
                  <Route size={25} />
                  <b>No confirmed route information is available for this journey.</b>
                  <p>Try another configured stop, expand the radius, or change your pickup.</p>
                  <button
                    onClick={() => {
                      setRadius(Math.min(radius * 2, 10000));
                      journey.reset();
                    }}
                    disabled={radius >= 10000}
                  >
                    Expand search radius
                  </button>
                </div>
              )
            ) : routeLoading ? (
              <div className="em-loading" role="status"><span /><p>Loading configured routes…</p></div>
            ) : routeError ? (
              <div className="em-empty" role="alert">
                <AlertTriangle size={25} />
                <b>Route data is unavailable</b>
                <p>We could not load transport data from EcoPulse.</p>
                <button
                  onClick={() => pickup
                    ? nearby.refetch()
                    : catalog.isError ? catalog.refetch() : routes.refetch()}
                >
                  Retry routes
                </button>
              </div>
            ) : displayRoutes.length ? (
              <div className="em-route-list">
                {displayRoutes.map(route => (
                  <RouteCard
                    route={route}
                    key={route.id}
                    selected={detailRoute?.id === route.id}
                    onSelect={() => selectRoute(route)}
                  />
                ))}
              </div>
            ) : (
              <div className="em-empty">
                <Route size={27} />
                <b>
                  {pickup
                    ? 'No configured taxi routes found nearby.'
                    : 'No configured routes in this area.'}
                </b>
                <p>
                  {pickup
                    ? 'Expand your radius or change your pickup location.'
                    : 'Pan the map to explore coverage, or select Pretoria demo coverage.'}
                </p>
                {pickup && radius < 10000 && (
                  <button onClick={() => setRadius(Math.min(radius * 2, 10000))}>
                    Expand search to {Math.min(radius * 2, 10000) / 1000} km
                  </button>
                )}
                {area === 'resident' && pretoriaId && municipalityId !== pretoriaId && (
                  <button onClick={() => changeArea('pretoria')}>Explore Pretoria demo</button>
                )}
              </div>
            )}

            <div className="em-sidebar-footer">
              <Info size={13} />
              <p>
                Configured data supports these suggestions. Walking distances are straight-line estimates.
                Demo routes, stops, fares and timings are unverified; confirm locally.
              </p>
            </div>
          </div>
        </aside>

        {/* The map owns Leaflet layers; this page supplies selected state and API data. */}
        <section className="em-map-stage" aria-label="Interactive EcoMove taxi map">
          <EcoMoveMap
            key={`${area}:${municipalityId ?? 'unconfigured'}`}
            routes={mapRoutes}
            initialRoutes={catalog.data?.items ?? []}
            initialRoutesLoaded={catalog.isSuccess}
            onInitialViewReady={handleInitialViewReady}
            selectedJourneyRouteIds={journeyRouteIds}
            stops={mapStops}
            vehicles={activeVehicles}
            disruptions={showHazards ? disruptions.data?.items ?? [] : []}
            pickup={pickup}
            destination={destination}
            radius={radius}
            selectionMode={selectionMode}
            matchingRouteIds={pickup ? nearby.data?.items.map(route => route.id) ?? [] : undefined}
            selectedRouteId={detailRoute?.id ?? null}
            selectedVehicleId={selectedVehicle?.vehicle_id ?? null}
            onPickup={selectPickup}
            onDestination={selectDestination}
            onRouteSelect={selectRoute}
            onVehicleSelect={position => {
              setSelectedJourney(null);
              setPanelOpen(false);
              setSelectedVehicle(position);
              setSelectedRoute(mapRoutes.find(route => route.id === position.route_id) ?? null);
            }}
            onVehicleUpdate={position => setSelectedVehicle(current =>
              current?.vehicle_id === position.vehicle_id ? position : current)}
            onBoundsChange={setBounds}
          />
          <div className="em-map-heading">
            <span><i /> {areaName}</span>
            <small>CONFIGURED TRANSPORT NETWORK</small>
          </div>
          <div className="em-map-summary">
            <span><Bus size={16} /><b>{activeVehicles.length}</b><small>simulated vehicles</small></span>
            <span><Route size={16} /><b>{mapRoutes.length}</b><small>configured routes</small></span>
            <span><MapPin size={16} /><b>{mapStops.length}</b><small>mapped points</small></span>
          </div>
          {selectionMode && (
            <div className="em-pick-instruction" role="status">
              <MapPin size={17} />
              <span>Tap the map to set your {selectionMode}.</span>
              <button aria-label="Cancel map selection" onClick={() => setSelectionMode(null)}>
                <X size={16} />
              </button>
            </div>
          )}
          <div className="em-map-legend">
            <span><i className="em-legend-route" /> Taxi route</span>
            <span><i className="em-legend-stop" /> Configured point</span>
            <button aria-pressed={showHazards} onClick={() => setShowHazards(!showHazards)}>
              <i className={`em-legend-hazard ${showHazards ? '' : 'off'}`} />
              Road reports {showHazards ? <Check size={10} /> : null}
            </button>
          </div>
          {vehicles.isError && (
            <div className="em-map-notice" role="alert">
              Vehicle configuration unavailable. <button onClick={() => vehicles.refetch()}>Retry</button>
            </div>
          )}
          {disruptions.isError && showHazards && (
            <div className="em-map-notice hazards" role="alert">
              Road reports unavailable. <button onClick={() => disruptions.refetch()}>Retry</button>
            </div>
          )}
          {stops.isError && (
            <div className="em-map-notice stops" role="alert">
              Additional pickup points unavailable. <button onClick={() => stops.refetch()}>Retry</button>
            </div>
          )}

          {selectedJourney && (
            <JourneyDetails
              journey={selectedJourney}
              routes={journeyRoutes.data ?? []}
              loading={journeyRoutes.isPending}
              failed={journeyRoutes.isError}
              onRetry={() => journeyRoutes.refetch()}
              disruptions={disruptions.data?.items ?? []}
              onClose={() => setSelectedJourney(null)}
            />
          )}
          {detailRoute && !selectedJourney && (
            <div
              className="em-detail-card"
              aria-label={selectedVehicle ? 'Simulated taxi details' : 'Taxi route details'}
            >
              <button
                className="em-detail-close"
                aria-label="Close route details"
                onClick={() => {
                  setSelectedRoute(null);
                  setSelectedVehicle(null);
                }}
              >
                <X size={17} />
              </button>
              <span className="em-detail-eyebrow">
                <Bus size={13} />
                {selectedVehicle
                  ? `TAXI ${selectedVehicle.vehicle_code} · DEMO VEHICLE`
                  : detailRoute.is_demo ? 'DEMONSTRATION ROUTE' : 'CONFIGURED ROUTE'}
              </span>
              <h2>{detailRoute.name}</h2>
              <p className="em-detail-direction">
                <Navigation size={13} />Towards {detailRoute.destination}
              </p>
              {selectedVehicle && (
                <div className="em-vehicle-facts">
                  <div>
                    <span>Status</span>
                    <b>
                      {selectedVehicle.status === 'at_stop'
                        ? 'At configured stop'
                        : selectedVehicle.status === 'finished' ? 'Route complete' : 'Moving'}
                    </b>
                  </div>
                  <div><span>Position source</span><b>Simulated</b></div>
                  <div>
                    <span>Next configured stop</span>
                    <b>{selectedVehicle.next_stop?.name ?? 'Destination reached'}</b>
                  </div>
                  <div>
                    <span>Simulation estimate</span>
                    <b>
                      {selectedVehicle.eta_minutes != null
                        ? `${Math.ceil(selectedVehicle.eta_minutes)} min to next stop`
                        : '—'}
                    </b>
                  </div>
                  <div><span>Simulated speed</span><b>{Math.round(selectedVehicle.speed_kmh)} km/h</b></div>
                </div>
              )}
              {routeHazards.length > 0 && (
                <div className="em-route-hazard">
                  <AlertTriangle size={15} />
                  <span>
                    Reported road hazard near this taxi route ({routeHazards.length}).
                    This does not confirm a closure.
                  </span>
                </div>
              )}
              <p className="em-detail-description">
                {detailRoute.description || `${detailRoute.origin} towards ${detailRoute.destination}`}
              </p>
              <div className="em-detail-stops">
                <b>Configured stops <small>{detailRoute.stops.length}</small></b>
                <ol>
                  {detailRoute.stops.map((stop, index) => (
                    <li key={stop.id}>
                      <span style={{ background: detailRoute.color || '#168560' }}>{index + 1}</span>
                      <div>
                        {stop.name}
                        {stop.is_interchange && <small>Configured interchange</small>}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
              {selectedVehicle && (
                <button
                  className="em-view-full-route"
                  onClick={() => {
                    setSelectedVehicle(null);
                    setSelectedRoute(detailRoute);
                  }}
                >
                  View Full Route <ArrowRight size={14} />
                </button>
              )}
              <p className="em-detail-source">
                Source: {detailRoute.data_source}.{' '}
                {detailRoute.estimated_fare != null
                  ? `Estimated fare R${detailRoute.estimated_fare.toFixed(2)}.`
                  : 'Fare unconfirmed.'}
              </p>
            </div>
          )}
        </section>
      </div>

      <div className="em-page-footer">
        <span><Info size={12} /> Demo vehicles follow stored geometry. No driver locations are collected.</span>
        <span>Map data © OpenStreetMap contributors</span>
      </div>
    </div>
  );
}
