const BASE = import.meta.env.VITE_API_URL || ''

async function get(path) {
  const r = await fetch(`${BASE}${path}`, { headers: { 'X-Api-Key': import.meta.env.VITE_API_KEY || 'demo-key' } })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export const api = {
  dashboard: () => get('/v1/dashboard/italy'),
  forecast: (h = 168) => get(`/v1/forecast/load?horizon=${h}`),
  explain: () => get('/v1/forecast/explain'),
  tsoForecast: () => get('/v1/load/forecast/tso?area=10YIT-GRTN-----B'),
  status: () => get('/v1/status'),
}
