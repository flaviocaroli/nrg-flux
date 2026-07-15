import { useState } from 'react'
import { C } from './Chart'

/*
 * "What you're downloading" — the panel that turns an abstract API into
 * something a buyer can see and touch. Each dataset shows its endpoint,
 * coverage, update cadence and live row count, plus a one-click CSV export
 * so a prospect can open real data in Excel during a demo.
 */

const API = import.meta.env.VITE_API_URL || ''
const KEY = import.meta.env.VITE_API_KEY || 'demo-key'

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

const DATASETS = [
  { id: 'prices', name: 'Day-ahead prices', path: '/v1/prices/dayahead?area=10Y1001A1001A73I',
    key: 'series', unit: '€/MWh', src: 'ENTSO-E A44', cadence: 'daily ~12:45 CET', color: C.amber },
  { id: 'load', name: 'Actual load', path: '/v1/load/actual?area=10YIT-GRTN-----B',
    key: 'series', unit: 'MW', src: 'ENTSO-E A65', cadence: 'hourly', color: C.chalk },
  { id: 'tso', name: 'TSO load forecast', path: '/v1/load/forecast/tso?area=10YIT-GRTN-----B',
    key: 'series', unit: 'MW', src: 'ENTSO-E A65/A01', cadence: 'daily', color: C.slate },
  { id: 'fcload', name: 'Load forecast (ours)', path: '/v1/forecast/load?horizon=168',
    key: 'forecast', unit: 'MW p10/p50/p90', src: 'NRG-Flux ML', cadence: 'after each run', color: C.teal },
  { id: 'fcprice', name: 'Price forecast (ours)', path: '/v1/forecast/price?horizon=48',
    key: 'forecast', unit: '€/MWh p10/p50/p90', src: 'NRG-Flux ML', cadence: 'after each run', color: C.teal },
  { id: 'explain', name: 'Forecast drivers (SHAP)', path: '/v1/forecast/explain',
    key: 'points', unit: 'MW per driver', src: 'NRG-Flux ML', cadence: 'per forecast', color: C.teal },
  { id: 'flows', name: 'Cross-border flows', path: '/v1/flows/physical?from=10YFR-RTE------C&to=10Y1001A1001A73I',
    key: 'series', unit: 'MW', src: 'ENTSO-E A11', cadence: 'hourly', color: C.ok },
  { id: 'outages', name: 'Outages', path: '/v1/outages',
    key: 'series', unit: 'MW unavailable', src: 'ENTSO-E A80/A78', cadence: 'on publication', color: C.alarm },
]

export default function DataCatalog() {
  const [busy, setBusy] = useState(null)
  const [counts, setCounts] = useState({})
  const [err, setErr] = useState({})

  async function fetchSet(ds) {
    const r = await fetch(`${API}${ds.path}`, { headers: { 'X-Api-Key': KEY } })
    if (!r.ok) throw new Error(`${r.status}`)
    const j = await r.json()
    return j[ds.key] ?? j
  }

  async function preview(ds) {
    setBusy(ds.id); setErr((e) => ({ ...e, [ds.id]: null }))
    try {
      const rows = await fetchSet(ds)
      setCounts((c) => ({ ...c, [ds.id]: Array.isArray(rows) ? rows.length : 1 }))
    } catch (e) {
      setErr((x) => ({ ...x, [ds.id]: String(e.message) }))
    } finally { setBusy(null) }
  }

  async function download(ds) {
    setBusy(ds.id); setErr((e) => ({ ...e, [ds.id]: null }))
    try {
      const rows = await fetchSet(ds)
      const csv = toCSV(Array.isArray(rows) ? rows : [rows])
      const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }))
      const a = document.createElement('a')
      a.href = url; a.download = `nrgflux_${ds.id}.csv`; a.click()
      URL.revokeObjectURL(url)
      setCounts((c) => ({ ...c, [ds.id]: Array.isArray(rows) ? rows.length : 1 }))
    } catch (e) {
      setErr((x) => ({ ...x, [ds.id]: String(e.message) }))
    } finally { setBusy(null) }
  }

  return (
    <div className="catalog">
      <div className="catalog-head">
        <span>Dataset</span><span>Source</span><span>Units</span>
        <span>Updates</span><span>Rows</span><span></span>
      </div>
      {DATASETS.map((ds) => (
        <div className="catalog-row" key={ds.id}>
          <span className="ds-name"><i style={{ background: ds.color }} />{ds.name}</span>
          <span className="ds-src">{ds.src}</span>
          <span className="ds-unit">{ds.unit}</span>
          <span className="ds-cad">{ds.cadence}</span>
          <span className="ds-count">
            {err[ds.id] ? <em style={{ color: C.alarm }}>{err[ds.id]}</em>
              : counts[ds.id] != null ? counts[ds.id].toLocaleString() : '—'}
          </span>
          <span className="ds-actions">
            <button onClick={() => preview(ds)} disabled={busy === ds.id}>
              {busy === ds.id ? '…' : 'check'}
            </button>
            <button className="dl" onClick={() => download(ds)} disabled={busy === ds.id}>
              CSV ↓
            </button>
          </span>
        </div>
      ))}
      <div className="catalog-foot">
        Every row ships with UTC + market-day timestamps, units, lineage (source ·
        document type · parser version) and quality flags. Same data via API, Excel
        (Power Query) or these CSV exports.
      </div>
    </div>
  )
}
