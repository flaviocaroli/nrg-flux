import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { C, Chart, axisBase, tooltipBase } from './Chart'
import InfoTip from './InfoTip'

const COLORS = {
  actual: C.chalk,
  lgbm_domestic: '#1F49E0',
  lgbm_eu_1: '#6A3FC0',
  lgbm_eu_2: '#8753B6',
  lgbm_eu_3: '#188A57',
  lgbm_eu_4: '#0B7460',
  lgbm_eu_5: C.teal,
  sarimax: '#6A3FC0',
  dlm: C.ok,
  tso_retrieved: C.amber,
  naive_weekly: C.slateDim,
}

const DASHES = {
  tso_retrieved: 'dashed',
  naive_weekly: 'dotted',
}

const fmt = (value, digits = 2) => value == null ? '—' : Number(value).toFixed(digits)
const signed = (value, digits = 4) => value == null ? '—' : `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(digits)}`
const fmtDate = (iso) => new Date(iso).toLocaleString('en-GB', {
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
  timeZone: 'Europe/Rome',
})

function Status({ value }) {
  return <span className={`arena-status ${String(value).toLowerCase().replaceAll(' ', '-')}`}>{value}</span>
}

export default function ForecastBenchmarkPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [visible, setVisible] = useState(null)

  useEffect(() => {
    let live = true
    api.forecastBenchmark()
      .then((result) => {
        if (!live) return
        setData(result)
        const best = result.best_nrg_model_id || 'lgbm_domestic'
        setVisible(new Set([best, 'tso_retrieved', 'naive_weekly']))
      })
      .catch((reason) => { if (live) setError(String(reason)) })
    return () => { live = false }
  }, [])

  const latestFold = data?.folds?.at(-1)
  const modelsById = useMemo(() => Object.fromEntries(
    (data?.models || []).map((model) => [model.model_id, model]),
  ), [data])

  const chartIds = [
    'lgbm_domestic', 'lgbm_eu_1', 'lgbm_eu_2', 'lgbm_eu_3', 'lgbm_eu_4', 'lgbm_eu_5',
    'sarimax', 'dlm', 'tso_retrieved', 'naive_weekly',
  ]
    .filter((id) => modelsById[id]?.sample_count > 0)

  const option = useMemo(() => {
    if (!latestFold || !visible) return null
    const rows = latestFold.series || []
    const selected = chartIds.filter((id) => visible.has(id))
    const series = [{
      name: 'Actual load', type: 'line', showSymbol: false,
      lineStyle: { width: 3, color: COLORS.actual }, itemStyle: { color: COLORS.actual },
      data: rows.map((row) => [row.ts_utc, row.actual_mw]),
    }]
    selected.forEach((id) => {
      series.push({
        name: modelsById[id]?.label || id, type: 'line', showSymbol: false,
        lineStyle: { width: id === data.best_nrg_model_id ? 2.8 : 1.8,
          color: COLORS[id], type: DASHES[id] || 'solid' },
        itemStyle: { color: COLORS[id] },
        data: rows.map((row) => [row.ts_utc, row.predictions[id]]),
      })
    })
    return {
      backgroundColor: 'transparent',
      tooltip: { ...tooltipBase, valueFormatter: (v) => `${Math.round(v).toLocaleString()} MW` },
      legend: { top: 0, type: 'scroll', textStyle: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 10 } },
      grid: { left: 62, right: 18, top: 46, bottom: 30 },
      xAxis: { type: 'time', ...axisBase, splitLine: { show: false } },
      yAxis: { type: 'value', name: 'MW', scale: true, ...axisBase,
        nameTextStyle: { color: C.slateDim, fontFamily: 'IBM Plex Mono' } },
      series,
    }
  }, [data, latestFold, modelsById, visible])

  if (error) return (
    <section className="panel arena-shell">
      <h2>Forecast Arena <span className="tag">saved evidence only</span><InfoTip label="About Forecast Arena">A saved, reproducible comparison of Italian 24-hour load forecasts. It never computes headline accuracy in your browser.</InfoTip></h2>
      <div className="empty-note">
        Benchmark evidence has not been generated yet. Market and profile-cost workflows remain available.
      </div>
    </section>
  )
  if (!data || !visible) return <section className="panel arena-shell"><div className="empty-note">loading saved benchmark evidence …</div></section>

  const best = modelsById[data.best_nrg_model_id]
  const gate = data.europe_acceptance
  const evidence = data.tso_comparison
  const ablations = data.models.filter((model) => model.model_id.startsWith('lgbm_'))
  const toggle = (id) => setVisible((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })

  return (
    <section className="panel arena-shell">
      <div className="arena-head">
        <div>
          <h2>Forecast Arena <span className="tag">Italy · next 24 hours · {data.protocol.rolling_origins} rolling origins</span><InfoTip label="About Forecast Arena">Black is actual load. Toggle models to compare the same saved target hours. LightGBM variants use Italian calendar, lagged load and lagged temperature; European variants add selected neighbouring load and import-direction physical-flow history. TSO is retrieved/final revision data, so it remains preliminary.</InfoTip></h2>
          <p>{data.question}</p>
        </div>
        <span className={`arena-verdict ${gate.accepted ? 'accepted' : 'withheld'}`}>
          {gate.accepted ? 'EU SIGNAL ACCEPTED' : 'EU CLAIM WITHHELD'}
        </span>
      </div>

      <div className="arena-kpis">
        <div><span>Best NRG model</span><b>{best?.label || 'No validated result'}</b></div>
        <div><span>WAPE</span><b>{best ? `${fmt(best.wape, 2)}%` : '—'}</b></div>
        <div><span>Skill vs naive</span><b>{best?.skill_vs_naive_pct == null ? '—' : `${fmt(best.skill_vs_naive_pct, 1)}%`}</b></div>
        <div><span>EU-5 gain vs domestic</span><b>{gate.relative_wape_gain_pct == null ? '—' : `${fmt(gate.relative_wape_gain_pct, 1)}%`}</b></div>
      </div>

      {evidence && (
        <div className="arena-evidence">
          <div className="arena-evidence-head">
            <strong>How strong is the TSO comparison?</strong>
            <span>{evidence.status} · {evidence.forecast_days} FORECAST DAYS</span>
            <InfoTip label="About comparison uncertainty">The confidence interval is calculated in the saved backend artifact by resampling complete forecast days, preserving the within-day dependence of hourly errors. A range crossing zero means the current sample cannot establish which forecast is reliably better.</InfoTip>
          </div>
          <div className="arena-evidence-grid">
            <div><span>TSO daily wins</span><b>{evidence.winner_counts?.tso_retrieved ?? '—'}/{evidence.forecast_days}</b></div>
            <div><span>EU-3 daily wins</span><b>{evidence.winner_counts?.lgbm_eu_3 ?? '—'}/{evidence.forecast_days}</b></div>
            <div><span>EU-4 daily wins</span><b>{evidence.winner_counts?.lgbm_eu_4 ?? '—'}/{evidence.forecast_days}</b></div>
            <div><span>Best NRG − TSO WAPE</span><b>{signed(evidence.observed_wape_difference_points)} pp</b></div>
            <div><span>95% day-block interval</span><b>{signed(evidence.day_block_bootstrap_95_ci_points?.[0])} to {signed(evidence.day_block_bootstrap_95_ci_points?.[1])}</b></div>
            <div><span>P(best NRG beats TSO)</span><b>{fmt(evidence.bootstrap_probability_best_nrg_better_pct, 0)}%</b></div>
          </div>
          <div className="arena-evidence-conclusion">
            <strong>Defensible conclusion: </strong>{evidence.conclusion}
            <small>{evidence.caveat}</small>
          </div>
        </div>
      )}

      <div className="arena-controls" aria-label="Forecast series visibility">
        <span className="arena-actual"><i style={{ background: COLORS.actual }} />Actual load</span>
        {chartIds.map((id) => (
          <button key={id} type="button" className={visible.has(id) ? 'on' : ''} onClick={() => toggle(id)}>
            <i style={{ background: COLORS[id] }} />{modelsById[id].label}
          </button>
        ))}
      </div>

      {option && <Chart option={option} className="chart arena-chart" />}
      <div className="arena-window">
        Latest evaluation fold: {fmtDate(latestFold.target_start_utc)}–{fmtDate(latestFold.target_end_utc)} ·
        common timestamps {latestFold.common_sample_count}/24
      </div>

      <div className="arena-table-wrap">
        <table className="arena-table">
          <thead><tr><th>Forecast</th><th>Status</th><th>WAPE</th><th>MAE</th><th>RMSE</th><th>Peak MAE</th><th>Skill</th><th>n</th></tr></thead>
          <tbody>
            {data.models.map((model) => (
              <tr key={model.model_id}>
                <td>{model.label}{model.model_id === data.best_nrg_model_id && <small> best NRG</small>}</td>
                <td><Status value={model.status} /></td>
                <td>{fmt(model.wape, 2)}%</td><td>{fmt(model.mae_mw, 0)} MW</td>
                <td>{fmt(model.rmse_mw, 0)} MW</td><td>{fmt(model.peak_mae_mw, 0)} MW</td>
                <td>{model.skill_vs_naive_pct == null ? '—' : `${fmt(model.skill_vs_naive_pct, 1)}%`}</td>
                <td>{model.sample_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="arena-ablation">
        <strong>European feature ablation</strong>
        {ablations.map((model) => <span key={model.model_id}>{model.label.replace('LightGBM · ', '')}<b>{fmt(model.wape, 2)}%</b></span>)}
      </div>

      <div className="arena-notes">
        <span>TSO is labelled PRELIMINARY because historical issue revisions are not preserved.</span>
        <span>SARIMAX remains PRELIMINARY pending a wider convergence and tuning audit.</span>
        {gate.best_exploratory_variant && <span>Best exploratory EU variant: {modelsById[gate.best_exploratory_variant]?.label} ({fmt(gate.best_exploratory_wape, 2)}% WAPE). This is not a production-promotion claim.</span>}
        <span>No observed target-hour weather. No missing prices or flows filled with zero.</span>
        <span>Generated {new Date(data.generated_at_utc).toUTCString().replace('GMT', 'UTC')} · artifact v{data.benchmark_version}</span>
      </div>
    </section>
  )
}
