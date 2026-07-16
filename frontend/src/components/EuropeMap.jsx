import { useState } from 'react'
import { C } from './Chart'

/*
 * EuropeMap — the multi-market view. Italy sits at the centre with its six
 * bidding zones; the surrounding markets (FR, DE-LU, CH, AT, SI, ES, GB, NL,
 * BE, GR) are drawn as neighbouring blocks, each coloured by its day-ahead
 * price when we have it and dimmed when we don't.
 *
 * Stylised geography, not GeoJSON: it stays dependency-free, themeable and
 * legible at panel size. Shapes are recognisable rather than survey-accurate —
 * the point is that a trader instantly sees WHERE the spread is.
 *
 * Props:
 *   dash    — the /v1/dashboard/italy payload (prices + flows_now)
 *   markets — optional { CC: {price, load} } for non-Italian markets
 */

// --- Italian bidding zones (centre) ---
const IT_ZONES = {
  NORD: { path: 'M300,225 L410,218 L438,258 L410,282 L320,283 L292,252 Z', cx: 360, cy: 250 },
  CNOR: { path: 'M320,283 L410,282 L404,325 L352,338 L326,318 Z', cx: 364, cy: 308 },
  CSUD: { path: 'M352,338 L404,325 L424,372 L400,400 L364,390 L346,360 Z', cx: 384, cy: 362 },
  SUD:  { path: 'M364,390 L400,400 L430,442 L410,478 L378,472 L366,432 Z', cx: 393, cy: 434 },
  SICI: { path: 'M366,492 L410,492 L420,518 L390,532 L362,516 Z', cx: 390, cy: 510 },
  SARD: { path: 'M252,362 L286,358 L292,406 L266,426 L244,400 Z', cx: 268, cy: 392 },
}

// --- neighbouring markets ---
const COUNTRIES = {
  FR: { path: 'M150,215 L268,205 L288,268 L262,330 L182,338 L140,282 Z', cx: 212, cy: 272, name: 'France' },
  DE: { path: 'M300,105 L400,98 L420,170 L392,212 L306,218 L288,150 Z', cx: 352, cy: 158, name: 'Germany-Lux' },
  CH: { path: 'M282,205 L344,200 L352,238 L296,244 L272,228 Z', cx: 312, cy: 222, name: 'Switzerland' },
  AT: { path: 'M400,180 L482,172 L492,212 L418,222 L396,206 Z', cx: 444, cy: 198, name: 'Austria' },
  SI: { path: 'M456,222 L508,218 L516,250 L462,254 Z', cx: 486, cy: 236, name: 'Slovenia' },
  NL: { path: 'M268,60 L340,54 L348,96 L282,102 Z', cx: 308, cy: 78, name: 'Netherlands' },
  BE: { path: 'M226,102 L292,96 L300,138 L238,144 Z', cx: 264, cy: 120, name: 'Belgium' },
  GB: { path: 'M120,60 L196,52 L212,120 L176,168 L124,150 L104,100 Z', cx: 158, cy: 108, name: 'Great Britain' },
  ES: { path: 'M78,340 L188,332 L206,398 L160,442 L86,432 L58,384 Z', cx: 132, cy: 388, name: 'Spain' },
  GR: { path: 'M498,410 L556,404 L568,452 L516,470 L490,438 Z', cx: 528, cy: 436, name: 'Greece' },
}

// interconnector arrows: [fromKey, toKey, borderLabel]
const LINKS = [
  ['FR', 'NORD', 'FR → IT-North'], ['CH', 'NORD', 'CH → IT-North'],
  ['AT', 'NORD', 'AT → IT-North'], ['SI', 'NORD', 'SI → IT-North'],
  ['GR', 'SUD', 'GR → IT-South'], ['DE', 'CH', 'DE → CH'],
  ['FR', 'ES', 'FR → ES'], ['GB', 'FR', 'GB → FR'],
  ['NL', 'DE', 'NL → DE'], ['BE', 'FR', 'BE → FR'],
]

function heat(p, lo, hi) {
  if (p == null || !isFinite(p) || hi === lo) return null
  const t = Math.max(0, Math.min(1, (p - lo) / (hi - lo)))
  const stops = [[52, 217, 195], [255, 180, 84], [255, 90, 102]]
  const seg = t < 0.5 ? [stops[0], stops[1]] : [stops[1], stops[2]]
  const k = t < 0.5 ? t * 2 : (t - 0.5) * 2
  const c = seg[0].map((a, i) => Math.round(a + (seg[1][i] - a) * k))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

export default function EuropeMap({ dash, markets = {} }) {
  const [hover, setHover] = useState(null)
  const prices = dash?.prices || {}
  const flows = Object.fromEntries((dash?.flows_now || []).map((f) => [f.border, f.mw]))

  const itPrice = (z) => { const s = prices[z]; return s?.length ? s[s.length - 1][1] : null }
  const ccPrice = (cc) => markets?.[cc]?.price ?? null

  const all = [...Object.keys(IT_ZONES).map(itPrice), ...Object.keys(COUNTRIES).map(ccPrice)]
    .filter((v) => v != null && isFinite(v))
  const lo = all.length ? Math.min(...all) : 0
  const hi = all.length ? Math.max(...all) : 1

  const centre = (k) => IT_ZONES[k] || COUNTRIES[k]
  const priceOf = (k) => (IT_ZONES[k] ? itPrice(k) : ccPrice(k))
  const nameOf = (k) => (IT_ZONES[k] ? `IT · ${k}` : COUNTRIES[k].name)

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox="0 0 600 560" className="eu-map" role="img"
        aria-label="European power markets by day-ahead price with interconnector flows">
        <defs>
          <marker id="euarrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6 Z" fill={C.teal} />
          </marker>
        </defs>

        {/* interconnectors under the shapes */}
        {LINKS.map(([a, b, label]) => {
          const A = centre(a), B = centre(b)
          if (!A || !B) return null
          const mw = flows[label] || 0
          const w = mw ? Math.max(1.4, Math.min(5, mw / 700)) : 1
          return (
            <g key={label} opacity={mw ? 0.9 : 0.25}>
              <line className="eu-flow" x1={A.cx} y1={A.cy} x2={B.cx} y2={B.cy}
                stroke={C.teal} strokeWidth={w} markerEnd="url(#euarrow)" />
              {mw > 0 && (
                <text x={(A.cx + B.cx) / 2} y={(A.cy + B.cy) / 2 - 4} fill={C.teal}
                  fontSize="9" textAnchor="middle" fontFamily="IBM Plex Mono">
                  {Math.round(mw)}
                </text>
              )}
            </g>
          )
        })}

        {/* neighbouring markets */}
        {Object.entries(COUNTRIES).map(([cc, cfg]) => {
          const p = ccPrice(cc)
          const f = heat(p, lo, hi)
          const on = hover === cc
          return (
            <g key={cc} onMouseEnter={() => setHover(cc)} onMouseLeave={() => setHover(null)}
              style={{ cursor: 'pointer' }}>
              <path d={cfg.path} fill={f || C.panelRaised || '#182543'}
                fillOpacity={f ? (on ? 0.82 : 0.55) : 0.5}
                stroke={on ? C.chalk : C.hairline} strokeWidth={on ? 1.8 : 1}
                strokeDasharray={f ? '0' : '4 3'} />
              <text x={cfg.cx} y={cfg.cy} fill={f ? '#0d1424' : C.slate} fontSize="11"
                fontWeight="700" textAnchor="middle" fontFamily="Space Grotesk">{cc}</text>
              <text x={cfg.cx} y={cfg.cy + 13} fill={f ? '#0d1424' : C.slateDim} fontSize="9"
                textAnchor="middle" fontFamily="IBM Plex Mono">
                {p != null ? `${p.toFixed(0)}€` : 'no data'}
              </text>
            </g>
          )
        })}

        {/* Italy — the beachhead, drawn last so it sits on top */}
        {Object.entries(IT_ZONES).map(([z, cfg]) => {
          const p = itPrice(z)
          const f = heat(p, lo, hi)
          const on = hover === z
          return (
            <g key={z} onMouseEnter={() => setHover(z)} onMouseLeave={() => setHover(null)}
              style={{ cursor: 'pointer' }}>
              <path d={cfg.path} fill={f || '#182543'} fillOpacity={on ? 0.9 : 0.72}
                stroke={on ? C.chalk : C.amber} strokeWidth={on ? 2 : 1.1} />
              <text x={cfg.cx} y={cfg.cy - 1} fill="#0d1424" fontSize="10" fontWeight="700"
                textAnchor="middle" fontFamily="Space Grotesk">{z}</text>
              <text x={cfg.cx} y={cfg.cy + 11} fill="#0d1424" fontSize="8.5"
                textAnchor="middle" fontFamily="IBM Plex Mono">
                {p != null ? `${p.toFixed(0)}€` : '—'}
              </text>
            </g>
          )
        })}

        <text x="14" y="548" fill={C.slateDim} fontSize="9.5" fontFamily="IBM Plex Mono">
          day-ahead price heat · dashed = market not yet backfilled · arrows = physical flows (MW)
        </text>
      </svg>

      <div className="map-legend">
        <span>{isFinite(lo) ? lo.toFixed(0) : '–'}€</span>
        <div className="map-scale" />
        <span>{isFinite(hi) ? hi.toFixed(0) : '–'}€</span>
      </div>

      {hover && (
        <div className="map-tip">
          <b>{nameOf(hover)}</b>
          <div>{priceOf(hover) != null ? `${priceOf(hover).toFixed(2)} €/MWh` : 'not backfilled'}</div>
        </div>
      )}
    </div>
  )
}
