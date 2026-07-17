import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { C, Chart, axisBase, tooltipBase } from './Chart'

/*
 * MarketPanels — the multi-market view.
 *
 * The forecast API has always been per-area; only the dashboard was hard-wired
 * to Italy. This component reads /v1/markets, lets you pick any market that has
 * been backfilled, and renders its load + price forecasts side by side.
 *
 * Markets that aren't ready are still listed but disabled, with the exact
 * command to make them ready — an empty panel should always tell you why.
 */

export default function MarketPanels() {
  const [markets, setMarkets] = useState(null)
  const [cc, setCc] = useState(null)
  const [data, setData] = useState({})
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.markets()
      .then((m) => {
        setMarkets(m.markets)
        const first = m.markets.find((x) => x.has_load_forecast || x.has_price_forecast)
          || m.markets.find((x) => x.ready)
        setCc(first?.country || m.markets[0]?.country)
      })
      .catch((e) => setErr(String(e)))
  }, [])

  const market = useMemo(
    () => markets?.find((m) => m.country === cc), [markets, cc])

  useEffect(() => {
    if (!market) return
    setBusy(true); setErr(null)
    const zone = market.zones[0].eic
    Promise.allSettled([
      market.has_load_forecast ? api.forecast(168, market.national_eic) : Promise.reject('none'),
      market.has_price_forecast ? api.priceForecast(48, zone) : Promise.reject('none'),
      api.loadActual(market.national_eic),
      api.pricesFor(zone),
    ]).then(([lf, pf, la, pr]) => {
      setData({
        loadFc: lf.status === 'fulfilled' ? lf.value : null,
        priceFc: pf.status === 'fulfilled' ? pf.value : null,
        loadAct: la.status === 'fulfilled' ? la.value.series : [],
        priceAct: pr.status === 'fulfilled' ? pr.value.series : [],
      })
      setBusy(false)
    })
  }, [market])

  const loadOption = useMemo(() => {
    const { loadFc, loadAct } = data
    if (!loadFc && !loadAct?.length) return null
    const band = (loadFc?.forecast || []).map((p) => [p.ts_utc, p.mw_p10, p.mw_p90])
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, valueFormatter: (v) => (v != null ? `${Math.round(v).toLocaleString()} MW` : '—') },
      legend: { textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 11 }, top: 0,
        icon: 'roundRect', itemWidth: 14, itemHeight: 3, data: ['Actual', 'Forecast p50'] },
      grid: { left: 58, right: 14, top: 32, bottom: 26 },
      xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
      yAxis: { type: 'value', name: 'MW', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' }, ...axisBase, scale: true },
      series: [
        { name: 'p10 lo', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
          stack: 'b', data: band.map(([t, lo]) => [t, lo]), tooltip: { show: false } },
        { name: 'p10–p90', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
          stack: 'b', areaStyle: { color: 'rgba(52,217,195,0.14)' },
          data: band.map(([t, lo, hi]) => [t, hi - lo]), tooltip: { show: false } },
        { name: 'Actual', type: 'line', showSymbol: false, lineStyle: { width: 2, color: C.chalk },
          itemStyle: { color: C.chalk }, data: (loadAct || []).map((p) => [p.ts_utc, p.value]) },
        { name: 'Forecast p50', type: 'line', showSymbol: false, lineStyle: { width: 2.2, color: C.teal },
          itemStyle: { color: C.teal }, data: (loadFc?.forecast || []).map((p) => [p.ts_utc, p.mw_p50]) },
      ],
    }
  }, [data])

  const priceOption = useMemo(() => {
    const { priceFc, priceAct } = data
    if (!priceFc && !priceAct?.length) return null
    const band = (priceFc?.forecast || []).map((p) => [p.ts_utc, p.eur_p10, p.eur_p90])
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, valueFormatter: (v) => (v != null ? `${v.toFixed(2)} €/MWh` : '—') },
      legend: { textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 11 }, top: 0,
        icon: 'roundRect', itemWidth: 14, itemHeight: 3, data: ['Actual', 'Forecast p50'] },
      grid: { left: 52, right: 14, top: 32, bottom: 26 },
      xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
      yAxis: { type: 'value', name: '€/MWh', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' }, ...axisBase, scale: true },
      series: [
        { name: 'p10 lo', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
          stack: 'pb', data: band.map(([t, lo]) => [t, lo]), tooltip: { show: false } },
        { name: 'p10–p90', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
          stack: 'pb', areaStyle: { color: 'rgba(255,180,84,0.13)' },
          data: band.map(([t, lo, hi]) => [t, hi - lo]), tooltip: { show: false } },
        { name: 'Actual', type: 'line', showSymbol: false, smooth: 0.12,
          lineStyle: { width: 2, color: C.chalk }, itemStyle: { color: C.chalk },
          data: (priceAct || []).map((p) => [p.ts_utc, p.value]) },
        { name: 'Forecast p50', type: 'line', showSymbol: false, smooth: 0.12,
          lineStyle: { width: 2.2, color: C.amber }, itemStyle: { color: C.amber },
          data: (priceFc?.forecast || []).map((p) => [p.ts_utc, p.eur_p50]) },
      ],
    }
  }, [data])

  if (err) return <div className="panel"><h2>Markets</h2>
    <div className="empty-note">Can't load markets: {err}</div></div>
  if (!markets) return <div className="panel"><h2>Markets</h2>
    <div className="empty-note">loading market catalog …</div></div>

  const badge = (fc) => fc && (
    <span className={`skill-badge ${fc.beats_naive_baseline ? 'good' : 'bad'}`}>
      {fc.beats_naive_baseline
        ? `beats naive${fc.skill_vs_best_naive_pct != null ? ` · +${fc.skill_vs_best_naive_pct}%` : ''}`
        : 'does not beat naive'}
    </span>
  )
  const cov = (fc) => fc?.coverage?.achieved_pct != null && (
    <span className="tag">coverage {fc.coverage.achieved_pct}% / {fc.coverage.target_pct}%</span>
  )

  return (
    <>
      <div className="market-bar">
        <span className="market-label">market</span>
        {markets.map((m) => (
          <button key={m.country} onClick={() => m.ready && setCc(m.country)}
            disabled={!m.ready}
            className={`market-chip ${cc === m.country ? 'on' : ''} ${m.ready ? '' : 'off'}`}
            title={m.ready ? m.name : `No data — run: python scripts/backfill_eu.py --markets ${m.country}`}>
            {m.country}
            {m.ready && (m.has_load_forecast || m.has_price_forecast) && <i className="dot" />}
          </button>
        ))}
        {market && (
          <span className="market-meta">
            {market.name} · {market.zones.length} zone{market.zones.length > 1 ? 's' : ''}
            {busy && ' · loading…'}
          </span>
        )}
      </div>

      <div className="grid">
        <div className="panel">
          <h2>Load forecast · {market?.name}
            {data.loadFc && <span className="tag">{data.loadFc.training_source === 'synthetic'
              ? '⚠ synthetic training data' : `trained on ${data.loadFc.training_source || 'n/a'}`}</span>}
            {cov(data.loadFc)}
            {badge(data.loadFc)}
          </h2>
          {!data.loadFc && (
            <div className="train-hint">
              No model trained for {market?.name} — showing actual load only.<br />
              <code>python3 scripts/train_forecast.py --area {market?.national_eic}</code>
            </div>
          )}
          {loadOption ? <Chart option={loadOption} /> : (
            <div className="empty-note">
              No load data for {market?.name}.<br />
              <code>python3 scripts/backfill_eu.py --markets {market?.country}</code>
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Price forecast · {market?.zones[0].label}
            {cov(data.priceFc)}
            {badge(data.priceFc)}
          </h2>
          {!data.priceFc && (
            <div className="train-hint">
              No price model for {market?.zones[0].label} — showing actual prices only.<br />
              <code>python3 scripts/train_price_forecast.py --area {market?.zones[0].eic}</code>
            </div>
          )}
          {priceOption ? <Chart option={priceOption} /> : (
            <div className="empty-note">
              No price data for {market?.name}.<br />
              <code>python3 scripts/backfill_eu.py --markets {market?.country}</code>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
