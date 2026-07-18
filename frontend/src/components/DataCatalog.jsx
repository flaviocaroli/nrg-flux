import { useState } from 'react'
import { C } from './Chart'

/*
 * "What you're downloading" — the panel that turns an abstract API into
 * something a buyer can see and touch. Pick a country and every dataset —
 * load, prices, TSO forecast, weather, our forecasts — resolves to that
 * market's real endpoints, with live row counts and one-click CSV export.
 *
 * Germany's two-code trap lives here: load / TSO / weather key off the
 * control area (…A83F); day-ahead prices and the price forecast key off the
 * DE-LU bidding zone (…A82H). The COUNTRIES map encodes that per market so a
 * download never silently hits the wrong EIC.
 */

const API = import.meta.env.VITE_API_URL || ''
const KEY = import.meta.env.VITE_API_KEY || 'demo-key'

// Per-market EICs. `load` drives load/TSO/weather/load-forecast; `price`
// drives day-ahead prices + price forecast; `imp` is a representative import
// border for the cross-border flow example.
const COUNTRIES = {
  IT: { name: 'Italy',       load: '10YIT-GRTN-----B', price: '10Y1001A1001A73I', imp: ['10YFR-RTE------C', '10Y1001A1001A73I'] },
  FR: { name: 'France',      load: '10YFR-RTE------C', price: '10YFR-RTE------C', imp: ['10Y1001A1001A82H', '10YFR-RTE------C'] },
  DE: { name: 'Germany',     load: '10Y1001A1001A83F', price: '10Y1001A1001A82H', imp: ['10YFR-RTE------C', '10Y1001A1001A82H'] },
  CH: { name: 'Switzerland', load: '10YCH-SWISSGRIDZ', price: '10YCH-SWISSGRIDZ', imp: ['10YFR-RTE------C', '10YCH-SWISSGRIDZ'] },
}

function datasetsFor(cc) {
  const c = COUNTRIES[cc]
  const [impFrom, impTo] = c.imp
  return [
    { id: 'prices', name: 'Day-ahead prices', path: `/v1/prices/dayahead?area=${c.price}`,
      key: 'series', unit: '€/MWh', src: 'ENTSO-E A44', cadence: 'daily ~12:45 CET', color: C.amber },
    { id: 'load', name: 'Actual load', path: `/v1/load/actual?area=${c.load}`,
      key: 'series', unit: 'MW', src: 'ENTSO-E A65', cadence: 'hourly', color: C.chalk },
    { id: 'tso', name: 'TSO load forecast', path: `/v1/load/forecast/tso?area=${c.load}`,
      key: 'series', unit: 'MW', src: 'ENTSO-E A65/A01', cadence: 'daily', color: C.slate },
    { id: 'weather', name: 'Weather (ERA5 temp)', path: `/v1/weather/history?area=${c.load}`,
      key: 'series', unit: '°C', src: 'ERA5 (Copernicus/Open-Meteo)', cadence: 'hourly history', color: C.ok },
    { id: 'fcload', name: 'Load forecast (ours)', path: `/v1/forecast/load?horizon=168&area=${c.load}`,
      key: 'forecast', unit: 'MW p10/p50/p90', src: 'NRG-Flux ML', cadence: 'after each run', color: C.teal },
    { id: 'fcprice', name: 'Price forecast (ours)', path: `/v1/forecast/price?horizon=48&area=${c.price}`,
      key: 'forecast', unit: '€/MWh p10/p50/p90', src: 'NRG-Flux ML', cadence: 'after each run', color: C.teal },
    { id: 'explain', name: 'Forecast drivers (SHAP)', path: `/v1/forecast/explain?area=${c.load}`,
      key: 'points', unit: 'MW per driver', src: 'NRG-Flux ML', cadence: 'per forecast', color: C.teal },
    { id: 'flows', name: 'Cross-border flows', path: `/v1/flows/physical?from=${impFrom}&to=${impTo}`,
      key: 'series', unit: 'MW', src: 'ENTSO-E A11', cadence: 'hourly', color: C.ok },
    { id: 'outages', name: 'Outages', path: `/v1/outages?area=${c.load}`,
      key: 'series', unit: 'MW unavailable', src: 'ENTSO-E A80/A78', cadence: 'on publication', color: C.alarm },
  ]
}

function toCSV(rows) {
  if (!rows?.length) return ''
  const flat = rows.map((r) => {
    const o = {}
    const walk = (obj, prefix = '') => {
      for (const [k, v] of Object.entries(obj || {})) {
        if (v && typeof v === 'object' && !Array.isArray(v)) walk(v, `${prefix}${k}.`)
        else o[`${prefix}${k}`] = Array.isArray(v) ? JSON.stringify(v) : v
      }
    }
    walk(r)
    return o
  })
  const cols = [...new Set(flat.flatMap(Object.keys))]
  const esc = (v) => (v == null ? '' : `"${String(v).replace(/"/g, '""')}"`)
  return [cols.join(','), ...flat.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n')
}

export default function DataCatalog() {
  const [cc, setCc] = useState('IT')
  const [busy, setBusy] = useState(null)
  const [counts, setCounts] = useState({})
  const [err, setErr] = useState({})

  const datasets = datasetsFor(cc)

  // Row counts + errors are keyed by country so switching markets doesn't show
  // stale numbers from the previous one.
  const k = (id) => `${cc}:${id}`

  async function fetchSet(ds) {
    const r = await fetch(`${API}${ds.path}`, { headers: { 'X-Api-Key': KEY } })
    if (!r.ok) throw new Error(`${r.status}`)
    const j = await r.json()
    return j[ds.key] ?? j
  }

  async function preview(ds) {
    setBusy(k(ds.id)); setErr((e) => ({ ...e, [k(ds.id)]: null }))
    try {
      const rows = await fetchSet(ds)
      setCounts((c) => ({ ...c, [k(ds.id)]: Array.isArray(rows) ? rows.length : 1 }))
    } catch (e) {
      setErr((x) => ({ ...x, [k(ds.id)]: String(e.message) }))
    } finally { setBusy(null) }
  }

  async function download(ds) {
    setBusy(k(ds.id)); setErr((e) => ({ ...e, [k(ds.id)]: null }))
    try {
      const rows = await fetchSet(ds)
      const csv = toCSV(Array.isArray(rows) ? rows : [rows])
      const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url; a.download = `nrgflux_${cc}_${ds.id}.csv`; a.click()
      URL.revokeObjectURL(url)
      setCounts((c) => ({ ...c, [k(ds.id)]: Array.isArray(rows) ? rows.length : 1 }))
    } catch (e) {
      setErr((x) => ({ ...x, [k(ds.id)]: String(e.message) }))
    } finally { setBusy(null) }
  }

  return (
    <div className="catalog">
      <div className="catalog-country">
        <span className="market-label">market</span>
        {Object.entries(COUNTRIES).map(([code, c]) => (
          <button key={code} onClick={() => setCc(code)}
            className={`market-chip ${cc === code ? 'on' : ''}`} title={c.name}>
            {code}
          </button>
        ))}
        <span className="market-meta">{COUNTRIES[cc].name} · every dataset below is this market</span>
      </div>

      <div className="catalog-head">
        <span>Dataset</span><span>Source</span><span>Units</span>
        <span>Updates</span><span>Rows</span><span></span>
      </div>
      {datasets.map((ds) => (
        <div className="catalog-row" key={ds.id}>
          <span className="ds-name"><i style={{ background: ds.color }} />{ds.name}</span>
          <span className="ds-src">{ds.src}</span>
          <span className="ds-unit">{ds.unit}</span>
          <span className="ds-cad">{ds.cadence}</span>
          <span className="ds-count">
            {err[k(ds.id)] ? <em style={{ color: C.alarm }}>{err[k(ds.id)]}</em>
              : counts[k(ds.id)] != null ? counts[k(ds.id)].toLocaleString() : '—'}
          </span>
          <span className="ds-actions">
            <button onClick={() => preview(ds)} disabled={busy === k(ds.id)}>
              {busy === k(ds.id) ? '…' : 'check'}
            </button>
            <button className="dl" onClick={() => download(ds)} disabled={busy === k(ds.id)}>
              CSV ↓
            </button>
          </span>
        </div>
      ))}
      <div className="catalog-foot">
        Every row ships with UTC + market-day timestamps, units, lineage (source ·
        document type · parser version) and quality flags. Same data via API, Excel
        (Power Query) or these CSV exports — for IT, FR, DE and CH.
      </div>
    </div>
  )
}
