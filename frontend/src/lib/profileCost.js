export const PROFILE_HEADERS = ['timestamp_utc', 'energy_kwh']
export const ALLOWED_INTERVALS = [15, 30, 60]

const MAX_FILE_BYTES = 1024 * 1024
const MAX_ROWS = 3000
const MAX_DAYS = 31
const ISO_WITH_TIMEZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,3})?)?(?:Z|[+-]\d{2}:\d{2})$/
const NUMBER = /^(?:\d+(?:\.\d*)?|\.\d+)$/

const issue = (row, message) => ({ row, message })

export function parseProfileCsv(text, intervalMinutes, fileSizeBytes = new Blob([text]).size) {
  const errors = []
  const interval = Number(intervalMinutes)

  if (!ALLOWED_INTERVALS.includes(interval)) {
    return { rows: [], errors: [issue('file', 'Choose a 15, 30, or 60 minute interval.')], intervalMinutes: interval }
  }
  if (fileSizeBytes > MAX_FILE_BYTES) {
    return { rows: [], errors: [issue('file', 'CSV is larger than 1 MB. Use a smaller profile for this demo.')], intervalMinutes: interval }
  }
  if (typeof text !== 'string' || !text.trim()) {
    return { rows: [], errors: [issue('file', 'Choose a non-empty CSV file.')], intervalMinutes: interval }
  }

  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/)
  const header = (lines.shift() || '').split(',').map((value) => value.trim())
  if (header.length !== PROFILE_HEADERS.length || header.some((value, index) => value !== PROFILE_HEADERS[index])) {
    return {
      rows: [],
      errors: [issue(1, 'Header must be exactly: timestamp_utc,energy_kwh')],
      intervalMinutes: interval,
    }
  }

  const rows = []
  const seen = new Set()
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim()
    const rowNumber = index + 2
    if (!line) continue
    if (rows.length >= MAX_ROWS) {
      errors.push(issue(rowNumber, `This demo accepts at most ${MAX_ROWS.toLocaleString()} data rows.`))
      break
    }

    const cells = line.split(',')
    if (cells.length !== 2) {
      errors.push(issue(rowNumber, 'Expected exactly two comma-separated values.'))
      continue
    }
    const timestampText = cells[0].trim()
    const energyText = cells[1].trim()
    if (!ISO_WITH_TIMEZONE.test(timestampText)) {
      errors.push(issue(rowNumber, 'timestamp_utc must be ISO 8601 with Z or a numeric timezone offset.'))
      continue
    }
    const startMs = Date.parse(timestampText)
    if (!Number.isFinite(startMs)) {
      errors.push(issue(rowNumber, 'timestamp_utc is not a valid date and time.'))
      continue
    }
    if (seen.has(startMs)) {
      errors.push(issue(rowNumber, 'Duplicate timestamp_utc. Each interval may appear once.'))
      continue
    }
    if (!NUMBER.test(energyText)) {
      errors.push(issue(rowNumber, 'energy_kwh must be a non-negative number using a decimal point.'))
      continue
    }
    const energyKwh = Number(energyText)
    if (!Number.isFinite(energyKwh) || energyKwh < 0) {
      errors.push(issue(rowNumber, 'energy_kwh must be a finite non-negative number.'))
      continue
    }
    seen.add(startMs)
    rows.push({ rowNumber, startMs, endMs: startMs + interval * 60 * 1000, timestampUtc: new Date(startMs).toISOString(), energyKwh })
  }

  if (!rows.length && !errors.length) errors.push(issue('file', 'CSV contains no data rows.'))
  for (let index = 1; index < rows.length; index += 1) {
    const previous = rows[index - 1]
    const current = rows[index]
    const expected = previous.startMs + interval * 60 * 1000
    if (current.startMs !== expected) {
      errors.push(issue(current.rowNumber, `Expected the next interval to start at ${new Date(expected).toISOString()}.`))
    }
  }
  if (rows.length > 1 && rows.at(-1).startMs - rows[0].startMs > MAX_DAYS * 24 * 60 * 60 * 1000) {
    errors.push(issue('file', `This demo accepts at most ${MAX_DAYS} days of consecutive data.`))
  }

  if (errors.length) return { rows: [], errors: errors.slice(0, 30), intervalMinutes: interval }
  return {
    rows,
    errors: [],
    intervalMinutes: interval,
    startUtc: rows[0].timestampUtc,
    endUtc: new Date(rows.at(-1).endMs).toISOString(),
    totalEnergyKwh: rows.reduce((sum, row) => sum + row.energyKwh, 0),
  }
}

function normalizePrices(series) {
  const prices = (series || []).map((point, index) => ({
    index,
    startMs: Date.parse(point.ts_utc),
    value: Number(point.value),
    unit: point.unit,
  })).sort((a, b) => a.startMs - b.startMs)

  if (prices.length < 2) throw new Error('At least two market-price points are required to establish price intervals.')
  if (prices.some((point) => !Number.isFinite(point.startMs) || !Number.isFinite(point.value) || point.unit !== 'EUR/MWh')) {
    throw new Error('Market-price response contains an invalid timestamp, value, or unit.')
  }

  const differences = prices.slice(1).map((point, index) => point.startMs - prices[index].startMs)
  const resolutionMs = Math.min(...differences)
  if (!Number.isFinite(resolutionMs) || resolutionMs <= 0) throw new Error('Market-price timestamps are not strictly increasing.')
  return prices.map((point, index) => ({
    ...point,
    // A large gap is missing price coverage, not a reason to extend the prior price.
    endMs: index < prices.length - 1
      ? Math.min(prices[index + 1].startMs, point.startMs + resolutionMs)
      : point.startMs + resolutionMs,
  }))
}

export function calculateSpotBenchmark(profile, priceSeries) {
  const prices = normalizePrices(priceSeries)
  const unmatched = []
  const costRows = []

  for (const row of profile.rows) {
    const price = prices.find((candidate) => row.startMs >= candidate.startMs && row.endMs <= candidate.endMs)
    if (!price) {
      unmatched.push(row)
      continue
    }
    const energyMwh = row.energyKwh / 1000
    costRows.push({ ...row, priceEurMwh: price.value, energyMwh, costEur: energyMwh * price.value })
  }

  const coveragePct = profile.rows.length ? (costRows.length / profile.rows.length) * 100 : 0
  if (unmatched.length) {
    return {
      ok: false,
      coveragePct,
      unmatched,
      message: `${unmatched.length} consumption interval(s) cannot be matched to verified Italy NORD prices. No total cost is shown.`,
    }
  }

  const totalEnergyKwh = costRows.reduce((sum, row) => sum + row.energyKwh, 0)
  const totalCostEur = costRows.reduce((sum, row) => sum + row.costEur, 0)
  return {
    ok: true,
    coveragePct,
    rows: costRows,
    totalEnergyKwh,
    totalCostEur,
    weightedPriceEurMwh: totalEnergyKwh > 0 ? totalCostEur / (totalEnergyKwh / 1000) : null,
    highestCostRows: [...costRows].sort((a, b) => b.costEur - a.costEur).slice(0, 5),
  }
}

export function makeTemplateCsv() {
  return [
    'timestamp_utc,energy_kwh',
    '2026-01-15T08:00:00Z,100',
    '2026-01-15T09:00:00Z,200',
  ].join('\n')
}

export function makeSampleCsv(priceSeries) {
  const prices = normalizePrices(priceSeries)
  const rows = prices.slice(0, 24)
  if (rows.length < 2) throw new Error('Not enough Italy NORD prices are available for the example.')
  const intervalMinutes = (rows[0].endMs - rows[0].startMs) / 60000
  if (!ALLOWED_INTERVALS.includes(intervalMinutes)) {
    throw new Error(`Italy NORD example uses an unsupported ${intervalMinutes}-minute market interval.`)
  }
  return {
    intervalMinutes,
    csv: [
      'timestamp_utc,energy_kwh',
      ...rows.map((price, index) => `${new Date(price.startMs).toISOString()},${80 + (index % 6) * 25}`),
    ].join('\n'),
  }
}

export function downloadText(filename, text) {
  const anchor = document.createElement('a')
  anchor.href = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }))
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(anchor.href)
}
