import { useEffect, useMemo, useState } from 'react'
import { api } from './lib/api'
import { C, Chart, axisBase, tooltipBase } from './components/Chart'
import ZoneSchematic from './components/ZoneSchematic'

const fmtHour = (iso) =>
  new Date(iso).toLocaleString('en-GB', { weekday: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Rome' })
const fmtDay = (iso) =>
  new Date(iso).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'Europe/Rome' })

export default function App() {
  const [dash, setDash] = useState(null)
  const [fc, setFc] = useState(null)
  const [expl, setExpl] = useState(null)
  const [tso, setTso] = useState(null)
  const [err, setErr] = useState(null)
  const [explHour, setExplHour] = useState(19)

  useEffect(() => {
    const load = () =>
      Promise.all([api.dashboard(), api.forecast(168), api.explain(), api.tsoForecast()])
        .then(([d, f, e, t]) => { setDash(d); setFc(f); setExpl(e); setTso(t) })
        .catch((e) => setErr(String(e)))
    load()
    const id = setInterval(load, 120000)
    return () => clearInterval(id)
  }, [])

  const kpis = useMemo(() => {
    if (!dash || !fc) return null
    const lastByZone = Object.entries(dash.prices).map(([z, s]) => [z, s.at(-1)?.[1] ?? 0])
    const pun = lastByZone.reduce((a, [, v]) => a + v, 0) / lastByZone.length
    const hi = lastByZone.reduce((a, b) => (b[1] > a[1] ? b : a))
    const lo = lastByZone.reduce((a, b) => (b[1] < a[1] ? b : a))
    const next24 = fc.forecast.slice(0, 24)
    const peak = next24.reduce((a, b) => (b.mw_p50 > a.mw_p50 ? b : a), next24[0])
    const outMw = dash.outages.reduce((a, o) => a + o.mw, 0)
    const loadNow = dash.load.at(-1)?.[1] ?? 0
    return { pun, spread: hi[1] - lo[1], hiZone: hi[0], loZone: lo[0], peak, outMw, nOut: dash.outages.length, loadNow }
  }, [dash, fc])

  const priceOption = useMemo(() => {
    if (!dash) return null
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, valueFormatter: (v) => `${v?.toFixed?.(2)} €/MWh` },
      legend: { textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 11 }, top: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 3 },
      grid: { left: 48, right: 16, top: 34, bottom: 28 },
      xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
      yAxis: { type: 'value', name: '€/MWh', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' }, ...axisBase, scale: true },
      series: Object.entries(dash.prices).map(([z, s], i) => ({
        name: z, type: 'line', showSymbol: false, smooth: 0.15,
        lineStyle: { width: z === 'NORD' ? 2.4 : 1.4, color: C.zones[i % C.zones.length] },
        itemStyle: { color: C.zones[i % C.zones.length] },
        emphasis: { focus: 'series' },
        data: s.map(([t, v]) => [t, v]),
      })),
    }
  }, [dash])

  const loadOption = useMemo(() => {
    if (!dash || !fc) return null
    const band = fc.forecast.map((p) => [p.ts_utc, p.mw_p10, p.mw_p90])
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, valueFormatter: (v) => (v != null ? `${Math.round(v).toLocaleString()} MW` : '—') },
      legend: { textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 11 }, top: 0, icon: 'roundRect', itemWidth: 14, itemHeight: 3, data: ['Actual load', 'NRG-Flux p50', 'TSO day-ahead'] },
      grid: { left: 58, right: 16, top: 34, bottom: 28 },
      xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
      yAxis: { type: 'value', name: 'MW', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' }, ...axisBase, scale: true },
      series: [
        { name: 'p10–p90 band', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 }, stack: 'band', data: band.map(([t, lo]) => [t, lo]) },
        { name: 'p10–p90', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 }, stack: 'band', areaStyle: { color: 'rgba(52,217,195,0.14)' }, data: band.map(([t, lo, hi]) => [t, hi - lo]) },
        { name: 'Actual load', type: 'line', showSymbol: false, lineStyle: { width: 2.2, color: C.chalk }, itemStyle: { color: C.chalk }, data: dash.load },
        { name: 'NRG-Flux p50', type: 'line', showSymbol: false, lineStyle: { width: 2.2, color: C.teal }, itemStyle: { color: C.teal }, data: fc.forecast.map((p) => [p.ts_utc, p.mw_p50]) },
        { name: 'TSO day-ahead', type: 'line', showSymbol: false, lineStyle: { width: 1.4, color: C.amber, type: 'dashed' }, itemStyle: { color: C.amber }, data: (tso?.series || []).map((p) => [p.ts_utc, p.value]) },
      ],
    }
  }, [dash, fc, tso])

  const driverOption = useMemo(() => {
    if (!expl) return null
    const pt = expl.points[Math.min(explHour, expl.points.length - 1)]
    if (!pt) return null
    const rows = [...pt.explanation].slice(0, 7).reverse()
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, trigger: 'item', valueFormatter: (v) => `${v > 0 ? '+' : ''}${Math.round(v)} MW` },
      grid: { left: 150, right: 40, top: 8, bottom: 24 },
      xAxis: { type: 'value', ...axisBase, name: 'MW impact', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
      yAxis: { type: 'category', ...axisBase, splitLine: { show: false }, data: rows.map((d) => FRIENDLY[d.feature] || d.feature) },
      series: [{
        type: 'bar', barWidth: 14,
        itemStyle: { borderRadius: 3, color: (p) => (rows[p.dataIndex].impact_mw >= 0 ? C.alarm : C.teal) },
        label: { show: true, position: 'right', color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 10, formatter: (p) => `${p.value > 0 ? '+' : ''}${Math.round(p.value)}` },
        data: rows.map((d) => d.impact_mw),
      }],
      __ts: pt.ts_utc, __p50: pt.mw_p50,
    }
  }, [expl, explHour])

  const flowOption = useMemo(() => {
    if (!dash) return null
    const rows = [...dash.flows_now].sort((a, b) => a.mw - b.mw)
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, trigger: 'item', valueFormatter: (v) => `${Math.round(v)} MW` },
      grid: { left: 96, right: 52, top: 8, bottom: 24 },
      xAxis: { type: 'value', ...axisBase, name: 'MW', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
      yAxis: { type: 'category', ...axisBase, splitLine: { show: false }, data: rows.map((f) => f.border.split(' → ')[0]) },
      series: [{
        type: 'bar', barWidth: 15,
        itemStyle: { borderRadius: 3, color: C.teal, opacity: 0.9 },
        label: { show: true, position: 'right', color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 10, formatter: (p) => Math.round(p.value).toLocaleString() },
        data: rows.map((f) => Math.round(f.mw)),
      }],
    }
  }, [dash])

  if (err) return (
    <div className="error">
      <div>
        Italy Power Watch can't reach the NRG-Flux API.
        <code>{err}</code>
        <code>Start it with: cd backend && uvicorn app.main:app --port 8000</code>
      </div>
    </div>
  )
  if (!dash || !fc || !kpis) return <div className="loading">syncing with the grid …</div>

  const tickerItems = Object.entries(dash.prices).map(([z, s]) => {
    const last = s.at(-1)?.[1], prev = s.at(-25)?.[1] ?? last
    return { z, v: last, d: last - prev }
  })
  const bt = fc.backtest_summary || {}

  return (
    <div className="shell">
      <header className="masthead">
        <span className="brand">NRG<b>-FLUX</b></span>
        <h1>Italy Power Watch</h1>
        <div className="meta">
          <span>{new Date(dash.generated_at_utc).toUTCString().replace('GMT', 'UTC')}</span>
          <span className={`pill ${dash.demo_mode ? 'demo' : ''}`}>
            <span className="dot" />{dash.demo_mode ? 'DEMO DATA' : 'LIVE · ENTSO-E'}
          </span>
        </div>
      </header>

      <div className="ticker" aria-hidden="true">
        <div className="ticker-track">
          {[...tickerItems, ...tickerItems].map((t, i) => (
            <span className="tick" key={i}>
              <span className="z">{t.z}</span>
              <span className="v">{t.v?.toFixed(2)}</span>
              <span className="u">€/MWh</span>
              <span className={t.d >= 0 ? 'up' : 'down'}>{t.d >= 0 ? '▲' : '▼'}{Math.abs(t.d).toFixed(1)}</span>
            </span>
          ))}
        </div>
      </div>

      <section className="kpis">
        <div className="kpi amber">
          <div className="label">Avg zonal price · latest hour</div>
          <div className="value">{kpis.pun.toFixed(2)}<small>€/MWh</small></div>
          <div className="sub">proxy for PUN across 6 zones</div>
        </div>
        <div className="kpi teal">
          <div className="label">Tomorrow peak load · p50</div>
          <div className="value">{(kpis.peak.mw_p50 / 1000).toFixed(1)}<small>GW</small></div>
          <div className="sub">{fmtHour(kpis.peak.ts_utc)} CET · band {(kpis.peak.mw_p10 / 1000).toFixed(1)}–{(kpis.peak.mw_p90 / 1000).toFixed(1)} GW</div>
        </div>
        <div className="kpi">
          <div className="label">Max zonal spread</div>
          <div className="value">{kpis.spread.toFixed(2)}<small>€/MWh</small></div>
          <div className="sub">{kpis.hiZone} over {kpis.loZone}</div>
        </div>
        <div className="kpi alarm">
          <div className="label">Unavailable capacity</div>
          <div className="value">{(kpis.outMw / 1000).toFixed(2)}<small>GW</small></div>
          <div className="sub">{kpis.nOut} tracked outages</div>
        </div>
      </section>

      <div className="grid">
        <div className="panel">
          <h2>Day-ahead prices by zone <span className="tag">ENTSO-E A44 · 72h + D+1</span></h2>
          <Chart option={priceOption} />
        </div>
        <div className="panel">
          <h2>Zonal grid · live imports <span className="tag">physical flows, MW</span></h2>
          <ZoneSchematic flows={dash.flows_now} prices={dash.prices} />
        </div>
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Load · actual vs explainable forecast
            <span className="tag">D+1..D+7 · WAPE {bt.wape_model}% vs naive {bt.wape_naive_weekly}%</span>
          </h2>
          <Chart option={loadOption} className="chart tall" />
        </div>
        <div className="panel">
          <h2>Why demand moves <span className="tag">SHAP · MW per driver</span></h2>
          <div style={{ marginTop: 10, fontFamily: 'IBM Plex Mono', fontSize: 12, color: C.slate }}>
            {driverOption && <>
              {fmtDay(driverOption.__ts)} {fmtHour(driverOption.__ts).slice(-5)} CET ·
              p50 <b style={{ color: C.teal }}> {Math.round(driverOption.__p50).toLocaleString()} MW</b>
            </>}
            <input type="range" min="0" max="23" value={explHour}
              onChange={(e) => setExplHour(+e.target.value)}
              style={{ width: '100%', marginTop: 8, accentColor: C.teal }}
              aria-label="Forecast hour selector" />
          </div>
          <Chart option={driverOption} className="chart short" />
        </div>
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Cross-border imports · latest hour <span className="tag">A11 physical flows</span></h2>
          <Chart option={flowOption} className="chart short" />
        </div>
        <div className="panel" style={{ overflowX: 'auto' }}>
          <h2>Largest unavailabilities <span className="tag">generation + grid</span></h2>
          <table className="outages">
            <thead>
              <tr><th>Asset</th><th>Zone</th><th>Type</th><th style={{ textAlign: 'right' }}>MW</th></tr>
            </thead>
            <tbody>
              {dash.outages.slice(0, 7).map((o) => (
                <tr key={o.asset + o.start}>
                  <td className="asset">{o.asset}</td>
                  <td className="zone">{o.zone}</td>
                  <td><span className={`badge ${o.planned ? '' : 'forced'}`}>{o.planned ? 'planned' : 'forced'}</span></td>
                  <td className="mw">{Math.round(o.mw).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <footer className="foot">
        <span>Market data © ENTSO-E Transparency Platform</span>
        <span>Weather: MET Norway · NOAA GFS · DWD · ECMWF open data</span>
        <span>Forecast: LightGBM quantiles + TreeSHAP · model v{fc.model_version}</span>
        <a href="/docs" target="_blank" rel="noreferrer">API docs ↗</a>
        {dash.demo_mode && <span style={{ color: C.amber }}>Synthetic demo data — not for trading decisions</span>}
      </footer>
    </div>
  )
}

const FRIENDLY = {
  hour: 'hour of day', weekday: 'day of week', month: 'month',
  is_weekend: 'weekend', is_holiday: 'public holiday', is_dst: 'daylight saving',
  temp_c: 'temperature', hdd: 'heating degrees', cdd: 'cooling degrees',
  temp_anomaly: 'temperature anomaly',
  load_lag_24h: 'load yesterday, same hour', load_lag_168h: 'load last week, same hour',
  load_roll_24h_mean: '24h average load', load_roll_168h_mean: '7-day average load',
}
