const BASE = import.meta.env.VITE_API_URL || ''

async function get(path) {
  const r = await fetch(`${BASE}${path}`, { headers: { 'X-Api-Key': import.meta.env.VITE_API_KEY || 'demo-key' } })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export const api = {
  dashboard: () => get('/v1/dashboard/italy'),
  markets: () => get('/v1/markets'),
  forecast: (h = 168, area) =>
    get(`/v1/forecast/load?horizon=${h}${area ? `&area=${encodeURIComponent(area)}` : ''}`),
  priceForecast: (h = 48, area) =>
    get(`/v1/forecast/price?horizon=${h}${area ? `&area=${encodeURIComponent(area)}` : ''}`),
  explain: (area) => get(`/v1/forecast/explain${area ? `?area=${encodeURIComponent(area)}` : ''}`),
  loadActual: (area, hours = 96) =>
    get(`/v1/load/actual?area=${encodeURIComponent(area)}`),
  pricesFor: (area, start, end) => {
    const params = new URLSearchParams({ area })
    if (start) params.set('start', start)
    if (end) params.set('end', end)
    return get(`/v1/prices/dayahead?${params.toString()}`)
  },
  weather: (area) => get(`/v1/weather/history?area=${encodeURIComponent(area)}`),
  tsoForecast: (area = '10YIT-GRTN-----B') =>
    get(`/v1/load/forecast/tso?area=${encodeURIComponent(area)}`),
  forecastBenchmark: (area = '10YIT-GRTN-----B') =>
    get(`/v1/forecast/benchmark?area=${encodeURIComponent(area)}`),
  status: () => get('/v1/status'),
}
