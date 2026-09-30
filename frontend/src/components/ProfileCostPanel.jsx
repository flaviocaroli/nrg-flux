import { useState } from 'react'
import { api } from '../lib/api'
import {
  ALLOWED_INTERVALS,
  calculateSpotBenchmark,
  downloadText,
  makeSampleCsv,
  makeTemplateCsv,
  parseProfileCsv,
} from '../lib/profileCost'

const ITALY_NORD_EIC = '10Y1001A1001A73I'
const currency = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 })
const number = new Intl.NumberFormat('en-IE', { maximumFractionDigits: 2 })

const fmtUtc = (timestamp) => new Date(timestamp).toLocaleString('en-GB', {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short',
})

export default function ProfileCostPanel() {
  const [intervalMinutes, setIntervalMinutes] = useState(60)
  const [profile, setProfile] = useState(null)
  const [fileName, setFileName] = useState('')
  const [errors, setErrors] = useState([])
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  const acceptProfile = (text, name, bytes) => {
    const parsed = parseProfileCsv(text, intervalMinutes, bytes)
    setErrors(parsed.errors)
    setResult(null)
    setNotice('')
    setFileName(name)
    setProfile(parsed.errors.length ? null : parsed)
    return parsed
  }

  const onFile = async (event) => {
    const file = event.target.files?.[0]
    if (!file) return
    acceptProfile(await file.text(), file.name, file.size)
  }

  const priceProfile = async (candidate, prices = null) => {
    if (!candidate || candidate.errors.length) return
    setBusy(true)
    setResult(null)
    setNotice('')
    try {
      const response = prices || await api.pricesFor(
        ITALY_NORD_EIC,
        new Date(Date.parse(candidate.startUtc) - 60 * 60 * 1000).toISOString(),
        new Date(Date.parse(candidate.endUtc) + 60 * 60 * 1000).toISOString(),
      )
      const nextResult = calculateSpotBenchmark(candidate, response.series)
      setResult(nextResult)
      if (nextResult.ok) setNotice(`Priced ${candidate.rows.length} intervals using Italy NORD day-ahead prices.`)
    } catch (error) {
      setErrors([{ row: 'market', message: `Could not retrieve usable Italy NORD prices: ${error.message}` }])
    } finally {
      setBusy(false)
    }
  }

  const loadExample = async () => {
    setBusy(true)
    setErrors([])
    setNotice('')
    setResult(null)
    try {
      const prices = await api.pricesFor(ITALY_NORD_EIC)
      const sample = makeSampleCsv(prices.series)
      const candidate = parseProfileCsv(sample.csv, sample.intervalMinutes)
      setIntervalMinutes(sample.intervalMinutes)
      setProfile(candidate)
      setFileName('Built-in Italy NORD example')
      await priceProfile(candidate, prices)
    } catch (error) {
      setErrors([{ row: 'market', message: `Could not create the example: ${error.message}` }])
      setBusy(false)
    }
  }

  return (
    <section className="profile-cost panel" aria-labelledby="profile-cost-title">
      <div className="profile-head">
        <div>
          <h2 id="profile-cost-title">Consumption profile → spot-energy benchmark <span className="tag">Italy NORD · browser-only CSV</span></h2>
          <p>Upload a profile with interval-start timestamps. Your file stays in this browser and is not stored by NRG-Flux.</p>
        </div>
        <button className="pc-button secondary" type="button" onClick={() => downloadText('nrg-flux-profile-template.csv', makeTemplateCsv())}>Download CSV template</button>
      </div>

      <div className="profile-controls">
        <label>
          <span>Interval duration</span>
          <select value={intervalMinutes} onChange={(event) => { setIntervalMinutes(Number(event.target.value)); setProfile(null); setResult(null); setErrors([]); setFileName('') }}>
            {ALLOWED_INTERVALS.map((minutes) => <option key={minutes} value={minutes}>{minutes} minutes</option>)}
          </select>
        </label>
        <label className="file-input">
          <span>Consumption CSV</span>
          <input type="file" accept=".csv,text/csv" onChange={onFile} />
        </label>
        <button className="pc-button" type="button" onClick={loadExample} disabled={busy}>Use built-in example</button>
        <button className="pc-button" type="button" onClick={() => priceProfile(profile)} disabled={!profile || busy}>{busy ? 'Calculating…' : 'Calculate benchmark'}</button>
      </div>

      {fileName && <div className="profile-file">Selected: <b>{fileName}</b>{profile && ` · ${profile.rows.length} intervals · ${number.format(profile.totalEnergyKwh)} kWh`}</div>}
      {errors.length > 0 && (
        <div className="profile-errors" role="alert">
          <b>Fix the CSV before calculating:</b>
          <ul>{errors.map((error, index) => <li key={`${error.row}-${index}`}>Row {error.row}: {error.message}</li>)}</ul>
        </div>
      )}
      {notice && <div className="profile-note">{notice}</div>}
      {result && !result.ok && <div className="profile-errors" role="alert">{result.message}</div>}

      {result?.ok && (
        <div className="profile-report" id="profile-cost-report">
          <div className="profile-kpis">
            <div><span>Total energy</span><b>{number.format(result.totalEnergyKwh)} <small>kWh</small></b></div>
            <div><span>Spot-energy benchmark</span><b>{currency.format(result.totalCostEur)}</b></div>
            <div><span>Consumption-weighted price</span><b>{result.weightedPriceEurMwh == null ? '—' : `${number.format(result.weightedPriceEurMwh)} €/MWh`}</b></div>
            <div><span>Price coverage</span><b>{number.format(result.coveragePct)}%</b></div>
          </div>
          <div className="profile-report-head">
            <div><b>Report basis</b><br />Italy NORD · ENTSO-E day-ahead prices · {fmtUtc(profile.startUtc)} to {fmtUtc(profile.endUtc)}</div>
            <button className="pc-button secondary no-print" type="button" onClick={() => window.print()}>Print / save report</button>
          </div>
          <p className="profile-disclaimer">Spot-energy benchmark — not a final electricity bill. It excludes supplier margin, network charges, taxes, balancing, and contract-specific terms.</p>
          <h3>Highest cost intervals</h3>
          <div className="profile-table-wrap">
            <table className="profile-table">
              <thead><tr><th>Interval start (UTC)</th><th>Energy</th><th>Price</th><th>Cost</th></tr></thead>
              <tbody>{result.highestCostRows.map((row) => <tr key={row.timestampUtc}><td>{fmtUtc(row.timestampUtc)}</td><td>{number.format(row.energyKwh)} kWh</td><td>{number.format(row.priceEurMwh)} €/MWh</td><td>{currency.format(row.costEur)}</td></tr>)}</tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  )
}
