export interface Coordinates {
  latitude: number;
  longitude: number;
}

export type VehiclePositionSource = 'simulation' | 'live_gps';

export interface TaxiStop extends Coordinates {
  id: string;
  name: string;
  municipality_id: string;
  stop_type: 'rank' | 'pickup' | 'dropoff' | 'interchange';
  is_demo: boolean;
  is_interchange: boolean;
}

export interface TaxiRoute {
  id: string;
  name: string;
  origin: string;
  destination: string;
  municipality_id: string;
  route_geometry: { type: 'LineString'; coordinates: [number, number][] };
  direction: string;
  description: string;
  color: string;
  estimated_duration_minutes: number | null;
  estimated_fare: number | null;
  is_demo: boolean;
  data_source: string;
  simulation_mode: 'once' | 'loop';
  stops: (TaxiStop & {
    stop_sequence: number;
    estimated_arrival_offset_minutes: number | null;
  })[];
}

export interface NearbyTaxiRoute extends TaxiRoute {
  nearest_pickup_stop: TaxiStop | null;
  walking_distance_meters: number;
  simulated_vehicle_count: number;
  distance_meters: number;
}

export interface TaxiVehicle {
  id: string;
  vehicle_code: string;
  route_id: string;
  /** Configured demonstration speed in kilometres per hour. */
  simulation_speed: number;
  initial_progress: number;
  is_active: boolean;
  position_source: 'simulation';
}

export interface TaxiPosition extends Coordinates {
  vehicle_id: string;
  vehicle_code: string;
  route_id: string;
  route_name: string;
  bearing: number;
  speed_kmh: number;
  status: 'moving' | 'at_stop' | 'finished';
  next_stop: TaxiStop | null;
  /** Simulation time to the next configured stop, not a real arrival estimate. */
  eta_minutes: number | null;
  progress: number;
  position_source: 'simulation';
}

export interface JourneyOption {
  route_id: string;
  route_name: string;
  transfers: 0 | 1;
  legs: {
    route_id: string;
    route_name: string;
    pickup_stop: TaxiStop;
    destination_stop: TaxiStop;
  }[];
  pickup_stop: TaxiStop;
  destination_stop: TaxiStop;
  walking_distance_meters: number;
  estimated_duration_minutes: number | null;
  fare: number | null;
  data_source: string;
  is_demo: boolean;
}

export type JourneyResult = JourneyOption;

export interface JourneySearchResponse {
  pickup: Coordinates;
  destination: Coordinates;
  routes: JourneyOption[];
}

export interface TransportDisruption extends Coordinates {
  id: string;
  category: string;
  title: string;
  location_text: string;
  created_at: string;
  status: string;
  route_ids: string[];
  distance_meters: number;
}

export interface EcoMoveCollection<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  source?: string;
  message?: string;
}
