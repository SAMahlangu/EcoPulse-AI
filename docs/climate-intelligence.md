# Climate Intelligence implementation

The Climate Intelligence page is the current resident portal page in scope. It uses Open-Meteo for modelled forecast data, geocoding and historical ERA5 archive data. If Open-Meteo is rate-limited or unavailable, current and forecast conditions fall back to MET Norway Locationforecast; responses identify the actual source. Historical records are daily archive values from Open-Meteo. The map displays the selected location's returned weather marker and coordinate-bearing community reports only. OpenStreetMap provides map tiles.

The resident dashboard uses a selected community centroid or municipality-boundary centroid when available. If reference municipalities have no geometry, it resolves an approximate populated-place coordinate through Open-Meteo geocoding and requires both municipality and province matches before showing local weather. This is an approximate municipal-area forecast, not a street-level observation.

## Endpoints

- `GET /api/v1/climate/current` — current modelled conditions.
- `GET /api/v1/climate/forecast` — current, hourly (up to 7 days) and daily forecast (7 days).
- `GET /api/v1/climate/history` — daily, monthly or annual archive series.
- `GET /api/v1/climate/analytics` — archive series, 1991–2020 matched reference anomalies and summary measures.
- `GET /api/v1/climate/risks` — four transparent forecast screening indicators.
- `GET /api/v1/climate/locations` — South African location search.
- `GET /api/v1/climate/map` — selected point and coordinate-bearing incident reports.
- `GET /api/v1/climate/advisories` — EcoPulse-generated advisory records; the official warning feed reports unavailable.
- `POST /api/v1/climate/advisor` — rule-based answers from retrieved forecast data.
- `GET/PUT /api/v1/climate/preferences` — reports unavailable until authenticated resident storage exists.

Weather cache durations are configurable through `OPEN_METEO_CURRENT_CACHE_SECONDS`, `OPEN_METEO_FORECAST_CACHE_SECONDS`, and `OPEN_METEO_HISTORY_CACHE_SECONDS`. Weather provider requests use bounded timeouts and retries.

## Risk interpretation

The heat bands (35/40/45°C maximum temperature), heavy-rain bands (25/50/100 mm/day), strong-wind bands (50/72/90 km/h), and rainfall deficit bands (-25/-50/-75% against matched 1991–2020 windows) are operational screening thresholds. They are not official warnings, formal drought classifications, flood probabilities, or the SAWS heatwave definition. SAWS defines heatwave in relation to the hottest month's normal maximum temperature and persistence; this deployment does not have the location-specific normals needed to apply that definition. Rainfall amount by itself does not estimate flood probability.

The dryness indicator is unavailable if the archive does not provide at least ten complete comparison years or the matched reference cannot be calculated. Analytics leave anomalies empty when reference coverage is insufficient. Official weather alerts are not integrated; the page links residents to SAWS and labels local screening separately.

The map API uses the development SQLite incident store and a small geographic bounding box. Browser location is optional and only sent after the resident selects the location action. Preferences are not persisted because authentication and resident-scoped storage are not configured. Migration `database/migrations/002_climate_intelligence.sql` is a schema proposal and has not been applied.

## Sources

- [Open-Meteo forecast API](https://open-meteo.com/en/docs/ecmwf-api)
- [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api)
- [MET Norway Locationforecast API](https://api.met.no/weatherapi/locationforecast/2.0/documentation)
- [SAWS weather questions: heatwave definition](https://testing.weathersa.co.za/home/weatherques)
- [SAWS WeatherSMART: Gauteng rainfall thresholds](https://www.weathersa.co.za/Documents/Corporate/WeatherSmart_News_202210_03042025083216.pdf)
- [SAWS WeatherSMART: wind thresholds](https://www.weathersa.co.za/Documents/Corporate/WeatherSmart_News_201608_03042025081249.pdf)
