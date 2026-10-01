import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { C, Chart, axisBase, tooltipBase } from './Chart'

const ORDER = ['IT', 'FR', 'DE', 'CH', 'AT', 'SI', 'GR', 'BE', 'NL', 'ES', 'GB']

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

const compact = (value) => new Intl.NumberFormat('en', {
  notation: 'compact', maximumFractionDigits: 1,
}).format(Number(value || 0))

function CountryBlock({ market }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(true)

  useEffect(() => {
    let live = true
    setBusy(true)
    setData(null)
    const zone = market.zones[0]?.eic
    const unavailable = () => Promise.reject(new Error('unavailable'))
    Promise.allSettled([
      market.has_load_forecast ? api.forecast(168, market.national_eic) : unavailable(),
      market.has_price_forecast && zone ? api.priceForecast(48, zone) : unavailable(),
      market.load_rows > 0 ? api.loadActual(market.national_eic) : unavailable(),
      market.price_rows > 0 && zone ? api.pricesFor(zone) : unavailable(),
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
  const zoneLabel = market.zones[0]?.label || 'not configured'
  const noGbPrice = market.country === 'GB' && market.price_rows === 0

  return (
    <div className="country-block">
      <div className="country-head">
        <span className="cc">{market.country}</span>
        <span className="cname">{market.name}</span>
        <span className="market-meta">{market.currency} · {zoneLabel}</span>
        {busy && <span className="tag">loading…</span>}
      </div>
      <div className="grid grid-even">
        <div className="panel">
          <h2>Load · actual and forecast
            {data?.loadFc && <span className="tag">D+1..D+7 · p10/p50/p90</span>}
          </h2>
          {data && !data.loadFc && (
            <div className="train-hint">Forecast is not published yet. Verified actual load remains visible.</div>
          )}
          {lo ? <Chart option={lo} /> : !busy && (
            <div className="empty-note">No load data is currently available for {market.name}.</div>
          )}
        </div>

        <div className="panel">
          <h2>Day-ahead price · actual and forecast
            {data?.priceFc && <span className="tag">48h · p10/p50/p90</span>}
          </h2>
          {data && !data.priceFc && !noGbPrice && (
            <div className="train-hint">Forecast is not published yet. Verified market prices remain visible.</div>
          )}
          {po ? <Chart option={po} /> : !busy && (
            <div className="empty-note">
              {noGbPrice
                ? 'GB day-ahead price ingestion is not enabled in this demonstration.'
                : `No price data is currently available for ${market.name}.`}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function MarketPanels({ selectedCountry = 'IT', onSelect }) {
  const [markets, setMarkets] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let live = true
    const refresh = () => api.markets()
      .then((response) => {
        if (!live) return
        setMarkets(response.markets)
        setErr(null)
      })
      .catch((error) => { if (live) setErr(String(error)) })
    refresh()
    const interval = setInterval(refresh, 60000)
    return () => { live = false; clearInterval(interval) }
  }, [])

  if (err) return <div className="panel"><h2>European markets</h2>
    <div className="empty-note">Market catalog is temporarily unavailable: {err}</div></div>
  if (!markets) return <div className="panel"><h2>European markets</h2>
    <div className="empty-note">loading market catalog …</div></div>

  const ordered = ORDER.map((cc) => markets.find((market) => market.country === cc)).filter(Boolean)
  const active = ordered.find((market) => market.country === selectedCountry) || ordered[0]
  const ready = ordered.filter((market) => market.ready).length
  const loadModels = ordered.filter((market) => market.has_load_forecast).length
  const priceModels = ordered.filter((market) => market.has_price_forecast).length

  const choose = (country) => {
    if (onSelect) onSelect(country)
  }

  return (
    <div className="country-stack">
      <div className="market-overview panel">
        <div className="market-overview-copy">
          <strong>Choose a market</strong>
          <span>One detailed market at a time keeps the dashboard fast and presentation-ready.</span>
        </div>
        <div className="market-stats" aria-label="European market coverage">
          <span><b>{ready}</b> data-ready</span>
          <span><b>{loadModels}</b> load forecasts</span>
          <span><b>{priceModels}</b> price forecasts</span>
        </div>
        <div className="market-bar" role="tablist" aria-label="European electricity market">
          {ordered.map((market) => {
            const activeMarket = market.country === active.country
            const modelReady = market.has_load_forecast &&
              (market.has_price_forecast || market.country === 'GB')
            return (
              <button key={market.country} type="button" role="tab"
                aria-selected={activeMarket} onClick={() => choose(market.country)}
                className={`market-chip ${activeMarket ? 'on' : ''}`}
                title={`${market.name}: ${compact(market.load_rows)} load rows · ${compact(market.price_rows)} price rows`}>
                <span className={`market-state ${modelReady ? 'model' : market.ready ? 'data' : 'partial'}`} />
                {market.country}
              </button>
            )
          })}
        </div>
        <div className="market-key">
          <span><i className="model" /> forecast published</span>
          <span><i className="data" /> market data ready</span>
          <span><i className="partial" /> partial coverage</span>
        </div>
      </div>

      {active && <CountryBlock key={active.country} market={active} />}
    </div>
  )
}
