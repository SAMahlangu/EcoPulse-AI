import { useEffect, useMemo, useRef, useState } from 'react';
import L, { type TileErrorEvent, type TileEvent } from 'leaflet';
import { TileLayer } from 'react-leaflet';

/** Keep third-party basemap failures distinct from EcoMove route/API errors. */
export default function EcoMoveTileLayer() {
  const [generation, setGeneration] = useState(0);
  const [status, setStatus] = useState<'error' | 'slow' | null>(null);
  const failed = useRef(new Set<HTMLElement>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notice = useRef<HTMLDivElement>(null);
  const clearTimer = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  const handlers = useMemo(() => ({
    loading: () => {
      clearTimer();
      timer.current = setTimeout(() => setStatus(previous => previous ?? 'slow'), 12_000);
    },
    tileerror: (event: TileErrorEvent) => {
      failed.current.add(event.tile);
      clearTimer();
      setStatus('error');
    },
    tileload: (event: TileEvent) => {
      failed.current.delete(event.tile);
      clearTimer();
      if (failed.current.size === 0) setStatus(null);
    },
    tileunload: (event: TileEvent) => {
      failed.current.delete(event.tile);
      if (failed.current.size === 0) setStatus(null);
    },
    load: () => {
      clearTimer();
      setStatus(failed.current.size ? 'error' : null);
    },
  }), []);
  useEffect(() => () => { clearTimer(); }, []);
  useEffect(() => {
    if (notice.current) {
      L.DomEvent.disableClickPropagation(notice.current);
      L.DomEvent.disableScrollPropagation(notice.current);
    }
  }, [status]);
  const retry = () => {
    clearTimer();
    failed.current.clear();
    setStatus(null);
    // Recreate only the basemap layer; local route/vehicle/pickup state stays in place.
    setGeneration(previous => previous + 1);
  };
  return <>
    <TileLayer key={generation}
      attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      updateWhenIdle updateWhenZooming={false} keepBuffer={1}
      eventHandlers={handlers}/>
    {status && <div ref={notice} role="status" aria-live="polite" data-testid="ecomove-tile-status"
      style={{ position: 'absolute', left: 12, bottom: 88, right: 54, maxWidth: 345, zIndex: 700,
        display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderRadius: 8,
        background: '#fff8e8', border: '1px solid #e6cb87', color: '#684b17',
        boxShadow: '0 2px 12px #30250e18', fontSize: 11, lineHeight: 1.45 }}>
      <span><b style={{ display: 'block' }}>{status === 'error' ? 'Street map unavailable' : 'Street map loading slowly'}</b>
        {status === 'error' ? 'OpenStreetMap tiles could not load.' : 'OpenStreetMap is taking longer than expected to load.'}</span>
      <button type="button" onClick={retry} style={{ marginLeft: 'auto', padding: '10px 8px', minHeight: 44,
        border: '1px solid #ceac62', background: '#fff', borderRadius: 6, color: '#684b17',
        fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>Retry map</button>
    </div>}
  </>;
}
