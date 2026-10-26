import { useEffect, useMemo, useRef, useState } from 'react';
import L, { type TileErrorEvent, type TileEvent } from 'leaflet';
import { Pane, TileLayer, useMap } from 'react-leaflet';

type Provider = 'openstreetmap' | 'openfreemap';
type MapStatus = 'slow' | 'backup' | 'error' | null;

const OPENSTREETMAP_TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const OPENFREEMAP_STYLE = 'https://tiles.openfreemap.org/styles/liberty';
const OPENFREEMAP_ATTRIBUTION = '<a href="https://openfreemap.org/">OpenFreeMap</a> · <a href="https://www.openmaptiles.org/">© OpenMapTiles</a> · Data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/** Keep third-party basemap failures distinct from EcoMove route/API errors. */
export default function EcoMoveTileLayer() {
  const [provider, setProvider] = useState<Provider>('openstreetmap');
  const [fallbackRequested, setFallbackRequested] = useState(false);
  const [secondaryFailed, setSecondaryFailed] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [status, setStatus] = useState<MapStatus>(null);
  const failed = useRef(new Set<HTMLElement>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notice = useRef<HTMLDivElement>(null);

  const clearTimer = useMemo(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const requestBackup = useMemo(() => () => {
    clearTimer();
    setSecondaryFailed(false);
    setFallbackRequested(true);
  }, [clearTimer]);

  const retry = useMemo(() => () => {
    clearTimer();
    failed.current.clear();
    setSecondaryFailed(false);
    setFallbackRequested(false);
    setProvider('openstreetmap');
    setStatus(null);
    // Recreate only the basemap; local route, vehicle, and pickup state stays in place.
    setGeneration(previous => previous + 1);
  }, [clearTimer]);

  const handlers = useMemo(() => ({
    loading: () => {
      clearTimer();
      timer.current = setTimeout(() => {
        setStatus(previous => previous ?? 'slow');
        setFallbackRequested(true);
      }, 12_000);
    },
    tileerror: (event: TileErrorEvent) => {
      failed.current.add(event.tile);
      setStatus('error');
      requestBackup();
    },
    tileload: (event: TileEvent) => {
      failed.current.delete(event.tile);
      if (failed.current.size === 0) {
        clearTimer();
        setFallbackRequested(false);
        setStatus(null);
      }
    },
    tileunload: (event: TileEvent) => {
      failed.current.delete(event.tile);
      if (failed.current.size === 0) {
        clearTimer();
        setFallbackRequested(false);
        setStatus(null);
      }
    },
    load: () => {
      clearTimer();
      if (failed.current.size === 0) {
        setFallbackRequested(false);
        setStatus(null);
      }
    },
  }), [clearTimer, requestBackup]);

  useEffect(() => {
    if (provider === 'openstreetmap' && !fallbackRequested) {
      // A blocked request can hang without producing a tileerror; don't leave a blank map waiting forever.
      timer.current = setTimeout(() => {
        setStatus(previous => previous ?? 'slow');
        setFallbackRequested(true);
      }, 12_000);
    } else if (fallbackRequested && provider === 'openstreetmap') {
      // Give the independent vector service time to load before declaring both sources unavailable.
      timer.current = setTimeout(() => setStatus('error'), 20_000);
    }
    return clearTimer;
  }, [provider, fallbackRequested, generation, clearTimer]);

  useEffect(() => () => { clearTimer(); }, [clearTimer]);

  useEffect(() => {
    if (notice.current) {
      L.DomEvent.disableClickPropagation(notice.current);
      L.DomEvent.disableScrollPropagation(notice.current);
    }
  }, [status]);

  useEffect(() => {
    if (status !== 'error') return;
    const retryWhenOnline = () => retry();
    window.addEventListener('online', retryWhenOnline);
    const interval = setInterval(retry, 30_000);
    return () => {
      window.removeEventListener('online', retryWhenOnline);
      clearInterval(interval);
    };
  }, [status, retry]);

  const backupLoaded = useMemo(() => () => {
    clearTimer();
    setProvider('openfreemap');
    setFallbackRequested(false);
    setStatus('backup');
  }, [clearTimer]);
  const backupFailed = useMemo(() => () => {
    setSecondaryFailed(true);
    setProvider('openstreetmap');
    setFallbackRequested(false);
    setStatus('error');
  }, []);

  return <>
    {provider === 'openstreetmap' && <TileLayer key={generation}
      attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      url={OPENSTREETMAP_TILES}
      opacity={status ? 0 : 1}
      updateWhenIdle updateWhenZooming={false} keepBuffer={1}
      eventHandlers={handlers} />}
    {status && <div ref={notice} role="status" aria-live="polite" data-testid="ecomove-tile-status"
      style={{ position: 'absolute', left: 12, bottom: 88, right: 54, maxWidth: 345, zIndex: 700,
        display: 'flex', alignItems: 'center', gap: 10, padding: '9px 11px', borderRadius: 8,
        background: status === 'backup' ? '#eef7f1' : '#fff8e8',
        border: `1px solid ${status === 'backup' ? '#a8cfb7' : '#e6cb87'}`,
        color: status === 'backup' ? '#245b3a' : '#684b17',
        boxShadow: '0 2px 12px #30250e18', fontSize: 11, lineHeight: 1.45 }}>
      <span>
        <b style={{ display: 'block' }}>{status === 'backup'
          ? 'Using backup street map'
          : status === 'error'
            ? 'Street map unavailable'
            : 'Street map loading slowly'}</b>
        {status === 'backup'
          ? 'The primary map did not respond. Your routes and reports are still shown.'
          : status === 'error' && secondaryFailed
            ? 'Both map services are unavailable. Routes and markers remain usable on the map grid; retrying automatically.'
            : status === 'error'
              ? 'OpenStreetMap tiles could not load. Switching to the backup map.'
              : 'Street map tiles are taking longer than expected. Switching to the backup map.'}
      </span>
      <button type="button" onClick={retry} style={{ marginLeft: 'auto', padding: '10px 8px', minHeight: 44,
        border: '1px solid #ceac62', background: '#fff', borderRadius: 6, color: '#684b17',
        fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>
        {status === 'backup' ? 'Retry primary' : 'Retry map'}
      </button>
    </div>}
    <Pane name="ecomove-map-grid" style={{
      zIndex: 150,
      pointerEvents: 'none',
      backgroundColor: '#e8eee8',
      backgroundImage: 'linear-gradient(#cbd8cd66 1px, transparent 1px), linear-gradient(90deg, #cbd8cd66 1px, transparent 1px)',
      backgroundSize: '28px 28px',
    }} />
    {(provider === 'openfreemap' || (fallbackRequested && !secondaryFailed)) && <OpenFreeMapLayer
      key={generation}
      onLoad={backupLoaded}
      onError={backupFailed} />}
  </>;
}

function OpenFreeMapLayer({ onLoad, onError }: { onLoad: () => void; onError: () => void }) {
  const map = useMap();

  useEffect(() => {
    let disposed = false;
    let removeLayer: (() => void) | undefined;
    let timeout: ReturnType<typeof setTimeout> | undefined;

    void Promise.all([
      import('maplibre-gl'),
      import('@maplibre/maplibre-gl-leaflet'),
      import('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'),
      import('maplibre-gl/dist/maplibre-gl.css'),
    ]).then(([maplibre, adapter, workerUrl]) => {
      if (disposed) return;
      maplibre.setWorkerUrl(workerUrl.default);

      const layerOptions = {
        style: OPENFREEMAP_STYLE,
        pane: 'tilePane',
        attributionControl: false as const,
        maxZoom: 18,
      };
      const layer = adapter.maplibreGL(layerOptions).addTo(map);
      map.attributionControl?.addAttribution(OPENFREEMAP_ATTRIBUTION);
      removeLayer = () => {
        map.attributionControl?.removeAttribution(OPENFREEMAP_ATTRIBUTION);
        if (map.hasLayer(layer)) map.removeLayer(layer);
      };

      let ready = false;
      let errorCount = 0;
      let failedProvider = false;
      const vectorMap = layer.getMaplibreMap();
      const handleError = () => {
        errorCount += 1;
        if (errorCount >= 3 && !failedProvider && !disposed) {
          failedProvider = true;
          onError();
        }
      };
      vectorMap.on('error', handleError);
      vectorMap.once('load', () => {
        ready = true;
        errorCount = 0;
        if (!disposed) onLoad();
      });
      timeout = setTimeout(() => {
        if (!ready && !disposed) onError();
      }, 19_000);
    }).catch(() => {
      if (!disposed) onError();
    });

    return () => {
      disposed = true;
      if (timeout) clearTimeout(timeout);
      removeLayer?.();
    };
  }, [map, onError, onLoad]);

  return null;
}
