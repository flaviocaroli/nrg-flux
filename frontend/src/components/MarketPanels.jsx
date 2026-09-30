import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { C, Chart, axisBase, tooltipBase } from './Chart'

/*
 * MarketPanels — the multi-market view.
 *
 * The forecast API has always been per-area. This view now shows all four
 * validated markets at once (IT · FR · DE · CH), each as a country block with
 * its load forecast and price forecast side by side — no selector, nothing
 * hidden a click away. A block that has no model still renders, and tells you
 * the exact command to train it.
 */

// The markets we train and publish today. Order is deliberate: IT first
// (reference market), then FR/DE/CH which got the 730-day treatment.
const SHOWN = ['IT', 'FR', 'DE', 'CH']

function loadChart(loadFc, loadAct) {
  if (!loadFc && !loadAct?.length) return null
  const band = (loadFc?.forecast || []).map((p) => [p.ts_utc, p.mw_p10, p.mw_p90])
  return {
    backgroundColor: 'transparent',
    tooltip: { ...tooltipBase, valueFormatter: (v) => (v != null ? `${Math.round(v).toLocaleString()} MW` : '—') },
    legend: { textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 10 }, top: 0,
      icon: 'roundRect', itemWidth: 12, itemHeight: 3, data: ['Actual', 'Forecast p50'] },
    grid: { left: 56, right: 12, top: 28, bottom: 24 },
    xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
    yAxis: { type: 'value', name: 'MW', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' }, ...axisBase, scale: true },
    series: [
      { name: 'p10 lo', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
        stack: 'b', data: band.map(([t, lo]) => [t, lo]), tooltip: { show: false } },
      { name: 'p10–p90', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
        stack: 'b', areaStyle: { color: 'rgba(31,73,224,0.10)' },
        data: band.map(([t, lo, hi]) => [t, hi - lo]), tooltip: { show: false } },
      { name: 'Actual', type: 'line', showSymbol: false, lineStyle: { width: 2, color: C.chalk },
        itemStyle: { color: C.chalk }, data: (loadAct || []).map((p) => [p.ts_utc, p.value]) },
      { name: 'Forecast p50', type: 'line', showSymbol: false, lineStyle: { width: 2.2, color: C.teal },
        itemStyle: { color: C.teal }, data: (loadFc?.forecast || []).map((p) => [p.ts_utc, p.mw_p50]) },
    ],
  }
}

function priceChart(priceFc, priceAct) {
  if (!priceFc && !priceAct?.length) return null
  const band = (priceFc?.forecast || []).map((p) => [p.ts_utc, p.eur_p10, p.eur_p90])
  return {
    backgroundColor: 'transparent',
    tooltip: { ...tooltipBase, valueFormatter: (v) => (v != null ? `${v.toFixed(2)} €/MWh` : '—') },
    legend: { textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 10 }, top: 0,
      icon: 'roundRect', itemWidth: 12, itemHeight: 3, data: ['Actual', 'Forecast p50'] },
    grid: { left: 50, right: 12, top: 28, bottom: 24 },
    xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
    yAxis: { type: 'value', name: '€/MWh', nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' }, ...axisBase, scale: true },
    series: [
      { name: 'p10 lo', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
        stack: 'pb', data: band.map(([t, lo]) => [t, lo]), tooltip: { show: false } },
      { name: 'p10–p90', type: 'line', showSymbol: false, silent: true, lineStyle: { width: 0 },
        stack: 'pb', areaStyle: { color: 'rgba(215,126,0,0.10)' },
        data: band.map(([t, lo, hi]) => [t, hi - lo]), tooltip: { show: false } },
      { name: 'Actual', type: 'line', showSymbol: false, smooth: 0.12,
        lineStyle: { width: 2, color: C.chalk }, itemStyle: { color: C.chalk },
        data: (priceAct || []).map((p) => [p.ts_utc, p.value]) },
      { name: 'Forecast p50', type: 'line', showSymbol: false, smooth: 0.12,
        lineStyle: { width: 2.2, color: C.amber }, itemStyle: { color: C.amber },
        data: (priceFc?.forecast || []).map((p) => [p.ts_utc, p.eur_p50]) },
    ],
  }
}

function CountryBlock({ market }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    let live = true
    setBusy(true)
    const zone = market.zones[0].eic
    Promise.allSettled([
      market.has_load_forecast ? api.forecast(168, market.national_eic) : Promise.reject('none'),
      market.has_price_forecast ? api.priceForecast(48, zone) : Promise.reject('none'),
      api.loadActual(market.national_eic),
      api.pricesFor(zone),
    ]).then(([lf, pf, la, pr]) => {
      if (!live) return
      setData({
        loadFc: lf.status === 'fulfilled' ? lf.value : null,
        priceFc: pf.status === 'fulfilled' ? pf.value : null,
        loadAct: la.status === 'fulfilled' ? la.value.series : [],
        priceAct: pr.status === 'fulfilled' ? pr.value.series : [],
      })
      setBusy(false)
    })
    return () => { live = false }
  }, [market])

  const lo = useMemo(() => (data ? loadChart(data.loadFc, data.loadAct) : null), [data])
  const po = useMemo(() => (data ? priceChart(data.priceFc, data.priceAct) : null), [data])
  const zoneLabel = market.zones[0].label

  return (
    <div className="country-block">
      <div className="country-head">
        <span className="cc">{market.country}</span>
        <span className="cname">{market.name}</span>
        {busy && <span className="tag">loading…</span>}
      </div>
      <div className="grid grid-even">
        <div className="panel">
          <h2>Load forecast
            {data?.loadFc && <span className="tag">{data.loadFc.training_source === 'synthetic'
              ? '⚠ synthetic' : `trained on ${data.loadFc.training_source || 'n/a'}`}</span>}
          </h2>
          {data && !data.loadFc && (
            <div className="train-hint">
              Demand forecast is temporarily unavailable. Showing actual load where available.
            </div>
          )}
          {lo ? <Chart option={lo} /> : !busy && (
            <div className="empty-note">
              No load data is currently available for {market.name}.
            </div>
          )}
        </div>

        <div className="panel">
          <h2>Price forecast · {zoneLabel}
          </h2>
          {data && !data.priceFc && (
            <div className="train-hint">
              Price forecast is temporarily unavailable. Showing day-ahead prices where available.
            </div>
          )}
          {po ? <Chart option={po} /> : !busy && (
            <div className="empty-note">
              No price data is currently available for {market.name}.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function MarketPanels() {
  const [markets, setMarkets] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.markets().then((m) => setMarkets(m.markets)).catch((e) => setErr(String(e)))
  }, [])

  if (err) return <div className="panel"><h2>Markets</h2>
    <div className="empty-note">Can't load markets: {err}</div></div>
  if (!markets) return <div className="panel"><h2>Markets</h2>
    <div className="empty-note">loading market catalog …</div></div>

  const shown = SHOWN
    .map((cc) => markets.find((m) => m.country === cc))
    .filter(Boolean)

  return (
    <div className="country-stack">
      {shown.map((m) => <CountryBlock key={m.country} market={m} />)}
    </div>
  )
}
