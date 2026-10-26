import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import L, { type TileErrorEvent, type TileEvent } from 'leaflet';
import { TileLayer, useMap } from 'react-leaflet';

type TileStatus = 'switching' | 'slow' | 'error';

// The primary source is OSM Standard; the second is an independent OSM renderer.
// Keep the provider list small so a normal map view never fans out to every server.
const TILE_SOURCES = [
  {
    name: 'OpenStreetMap',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  },
  {
    name: 'OpenStreetMap.de',
    url: 'https://tile.openstreetmap.de/{z}/{x}/{y}.png',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &middot; tiles <a href="https://www.openstreetmap.de/">OpenStreetMap.de</a>',
  },
];

/** Shared resilient basemap for every Leaflet map in EcoPulse. */
export default function EcoMoveTileLayer() {
  const map = useMap();
  const [sourceIndex, setSourceIndex] = useState(0);
  const [generation, setGeneration] = useState(0);
  const [status, setStatus] = useState<TileStatus | null>(null);
  const activeSource = useRef(0);
  const pending = useRef(new Set<HTMLElement>());
  const loaded = useRef(new Set<HTMLElement>());
  const failed = useRef(new Set<HTMLElement>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notice = useRef<HTMLDivElement>(null);

  const clearTimer = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const switchSource = useCallback((failedSource: number) => {
    if (activeSource.current !== failedSource) return;
    clearTimer();
    pending.current.clear();
    loaded.current.clear();
    failed.current.clear();
    const next = failedSource + 1;
    if (next < TILE_SOURCES.length) {
      activeSource.current = next;
      setStatus('switching');
      setSourceIndex(next);
      setGeneration(value => value + 1);
    } else {
      setStatus('error');
    }
  }, [clearTimer]);

  const handlers = useMemo(() => ({
    loading: () => {
      clearTimer();
      const currentSource = sourceIndex;
      timer.current = setTimeout(() => {
        if (activeSource.current !== currentSource) return;
        if (loaded.current.size === 0 || pending.current.size > 0) switchSource(currentSource);
        else setStatus(previous => previous ?? 'slow');
      }, 6_000);
    },
    tileloadstart: (event: TileEvent) => {
      if (activeSource.current !== sourceIndex) return;
      pending.current.add(event.tile);
    },
    tileerror: (event: TileErrorEvent) => {
      if (activeSource.current !== sourceIndex) return;
      pending.current.delete(event.tile);
      failed.current.add(event.tile);
      // Switch after a small cluster of failures, including partly rendered maps.
      if (failed.current.size >= 3) switchSource(sourceIndex);
    },
    tileload: (event: TileEvent) => {
      if (activeSource.current !== sourceIndex) return;
      pending.current.delete(event.tile);
      loaded.current.add(event.tile);
      failed.current.delete(event.tile);
      setStatus(null);
    },
    tileunload: (event: TileEvent) => {
      if (activeSource.current !== sourceIndex) return;
      pending.current.delete(event.tile);
      loaded.current.delete(event.tile);
      failed.current.delete(event.tile);
    },
    load: () => {
      if (activeSource.current !== sourceIndex) return;
      clearTimer();
      if (loaded.current.size === 0) switchSource(sourceIndex);
      else setStatus(null);
    },
  }), [clearTimer, sourceIndex, switchSource]);

  useEffect(() => () => clearTimer(), [clearTimer]);
  useEffect(() => {
    if (notice.current) {
      L.DomEvent.disableClickPropagation(notice.current);
      L.DomEvent.disableScrollPropagation(notice.current);
    }
  }, [status]);

  const retry = () => {
    clearTimer();
    activeSource.current = 0;
    pending.current.clear();
    loaded.current.clear();
    failed.current.clear();
    setSourceIndex(0);
    setGeneration(value => value + 1);
    setStatus('switching');
  };
  const centre = map.getCenter();
  const openMapUrl = `https://www.openstreetmap.org/#map=${map.getZoom()}/${centre.lat.toFixed(5)}/${centre.lng.toFixed(5)}`;
  const source = TILE_SOURCES[sourceIndex];

  return <>
    <TileLayer key={`${sourceIndex}-${generation}`}
      attribution={source.attribution}
      url={source.url}
      updateWhenIdle updateWhenZooming={false} keepBuffer={1}
      eventHandlers={handlers}/>
    {status && <div ref={notice} role="status" aria-live="polite" data-testid="ecomove-tile-status"
      style={{ position: 'absolute', left: 12, bottom: 88, right: 54, maxWidth: 365, zIndex: 700,
        display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderRadius: 8,
        background: '#fff8e8', border: '1px solid #e6cb87', color: '#684b17',
        boxShadow: '0 2px 12px #30250e18', fontSize: 11, lineHeight: 1.45 }}>
      <span><b style={{ display: 'block' }}>{status === 'error' ? 'Street map unavailable' : status === 'switching' ? 'Switching map source' : 'Street map loading slowly'}</b>
        {status === 'error' ? 'Map tiles could not load from either map source.' : status === 'switching' ? `Checking ${source.name}...` : `Waiting for ${source.name} tiles...`}</span>
      {status === 'error' && <a href={openMapUrl} target="_blank" rel="noreferrer" style={{ color: '#684b17', whiteSpace: 'nowrap' }}>Open full map</a>}
      <button type="button" onClick={retry} style={{ marginLeft: 'auto', padding: '10px 8px', minHeight: 44,
        border: '1px solid #ceac62', background: '#fff', borderRadius: 6, color: '#684b17',
        fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>Retry map</button>
    </div>}
  </>;
}
