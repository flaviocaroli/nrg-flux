import { useState } from 'react'
import { C } from './Chart'

/*
 * EuropeMap — real geography.
 *
 * Country outlines are simplified TRUE lat/lon boundaries, projected with an
 * equirectangular projection carrying a cos(lat) correction so shapes don't
 * smear at northern latitudes. Italy is split into its six bidding zones along
 * the actual regional groupings (NORD = Po valley + Alps + Liguria,
 * CNOR = Tuscany/Umbria/Marche, CSUD = Lazio/Abruzzo/Campania,
 * SUD = Molise/Puglia/Basilicata/Calabria, plus the two islands).
 *
 * Colour = day-ahead price heat. Dashed outline = market data unavailable.
 * Arrows = live physical flows.
 */

// ---- projection -----------------------------------------------------------
const LON_MIN = -10.5, LAT_MAX = 59.5, LAT_MIN = 35.0
const K = 23                                   // px per degree of latitude
const COS_MID = Math.cos((46 * Math.PI) / 180) // longitude compression
const VB_W = Math.round((27 - LON_MIN) * K * COS_MID)
const VB_H = Math.round((LAT_MAX - LAT_MIN) * K)

const px = ([lat, lon]) => [(lon - LON_MIN) * K * COS_MID, (LAT_MAX - lat) * K]
// Catmull-Rom -> cubic Bezier: rounds the simplified outlines so coastlines
// read as coastlines rather than polygons, without adding vertices.
const toPath = (co, tension = 0.5) => {
  const p = co.map(px)
  const n = p.length
  if (n < 3) return 'M' + p.map((q) => q.join(',')).join(' L') + ' Z'
  const at = (i) => p[(i + n) % n]
  let d = `M${at(0)[0].toFixed(1)},${at(0)[1].toFixed(1)}`
  for (let i = 0; i < n; i++) {
    const p0 = at(i - 1), p1 = at(i), p2 = at(i + 1), p3 = at(i + 2)
    const c1 = [p1[0] + ((p2[0] - p0[0]) / 6) * tension,
                p1[1] + ((p2[1] - p0[1]) / 6) * tension]
    const c2 = [p2[0] - ((p3[0] - p1[0]) / 6) * tension,
                p2[1] - ((p3[1] - p1[1]) / 6) * tension]
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ` +
         `${p2[0].toFixed(1)},${p2[1].toFixed(1)}`
  }
  return d + ' Z'
}
const centroid = (co) => {
  const p = co.map(px)
  return [p.reduce((a, q) => a + q[0], 0) / p.length,
          p.reduce((a, q) => a + q[1], 0) / p.length]
}

// ---- Italian bidding zones (lat, lon) ------------------------------------
const IT_ZONES = {
  NORD: [[46.5, 8.4], [46.0, 6.8], [44.9, 6.9], [44.1, 7.5], [43.8, 7.6], [44.4, 8.9],
         [44.3, 9.9], [44.2, 12.2], [44.9, 12.4], [45.6, 13.6], [46.5, 13.6], [46.7, 11.2]],
  CNOR: [[44.3, 9.9], [43.4, 10.3], [42.9, 10.7], [42.4, 11.6], [42.5, 12.9], [42.9, 13.9],
         [43.6, 13.6], [44.2, 12.4], [44.2, 12.2]],
  CSUD: [[42.4, 11.6], [41.4, 12.9], [40.8, 14.0], [40.6, 14.9], [40.9, 15.4], [41.5, 15.0],
         [42.0, 14.5], [42.9, 13.9], [42.5, 12.9]],
  SUD:  [[41.5, 15.0], [41.9, 15.9], [41.4, 16.9], [40.5, 18.4], [40.0, 18.0], [39.9, 16.6],
         [39.3, 17.1], [38.5, 16.6], [38.0, 15.9], [38.3, 15.6], [39.0, 16.0], [39.8, 15.7],
         [40.4, 15.0], [40.6, 14.9], [40.9, 15.4]],
  SICI: [[38.2, 12.4], [38.1, 13.4], [38.0, 15.1], [37.4, 15.1], [36.7, 15.1], [36.7, 14.5],
         [37.0, 12.8], [37.8, 12.4]],
  SARD: [[41.2, 9.2], [41.1, 9.6], [40.5, 9.8], [39.2, 9.6], [38.9, 8.8], [39.2, 8.4],
         [40.0, 8.4], [40.6, 8.2], [41.0, 8.3]],
}

// ---- neighbouring markets -------------------------------------------------
const COUNTRIES = {
  FR: { name: 'France', c: [[51.0, 2.5], [49.5, 0.1], [48.6, -1.5], [48.5, -4.7], [47.3, -2.2],
        [46.2, -1.2], [44.7, -1.2], [43.4, -1.8], [42.8, 0.7], [42.5, 3.2], [43.3, 4.8],
        [43.7, 7.4], [45.0, 6.9], [46.4, 6.1], [47.5, 7.6], [49.0, 8.2], [49.5, 6.4],
        [50.4, 4.2]] },
  DE: { name: 'Germany-Lux', c: [[54.8, 8.4], [54.4, 11.0], [54.2, 13.5], [53.9, 14.2],
        [52.0, 14.6], [50.9, 15.0], [50.2, 12.1], [48.8, 13.8], [47.7, 13.0], [47.5, 10.2],
        [47.6, 7.6], [49.0, 8.2], [49.5, 6.4], [50.8, 6.0], [51.9, 6.0], [53.5, 7.2]] },
  CH: { name: 'Switzerland', c: [[47.6, 7.6], [47.7, 8.6], [47.5, 9.6], [46.9, 10.5],
        [46.4, 9.9], [46.0, 8.9], [46.0, 8.4], [45.9, 7.0], [46.2, 6.1], [46.9, 6.4],
        [47.4, 7.0]] },
  AT: { name: 'Austria', c: [[48.6, 13.8], [48.8, 15.0], [48.7, 16.9], [47.7, 16.5],
        [46.7, 16.0], [46.4, 14.6], [46.5, 13.7], [46.8, 12.2], [47.0, 10.4], [47.5, 10.2],
        [47.7, 13.0]] },
  SI: { name: 'Slovenia', c: [[46.9, 13.7], [46.7, 16.6], [45.8, 16.0], [45.4, 15.3],
        [45.5, 13.6], [46.4, 13.6]] },
  NL: { name: 'Netherlands', c: [[53.4, 6.9], [53.2, 7.2], [52.4, 7.1], [51.9, 6.0],
        [51.2, 6.0], [51.3, 4.2], [51.8, 3.6], [52.6, 4.6], [53.2, 4.8]] },
  BE: { name: 'Belgium', c: [[51.5, 3.4], [51.4, 4.9], [51.0, 6.0], [50.2, 6.4], [49.5, 5.8],
        [49.6, 4.8], [50.4, 3.2], [51.1, 2.5]] },
  GB: { name: 'Great Britain', c: [[58.6, -3.1], [57.5, -1.8], [56.0, -2.5], [55.0, -1.4],
        [54.1, -0.2], [53.0, 0.3], [52.0, 1.7], [51.4, 1.4], [50.8, 0.3], [50.6, -1.9],
        [50.2, -3.6], [50.0, -5.7], [51.2, -4.2], [51.6, -3.2], [52.9, -4.8], [53.4, -3.1],
        [54.1, -3.2], [54.9, -3.6], [55.9, -5.0], [56.7, -5.8], [57.6, -5.9], [58.6, -5.0]] },
  ES: { name: 'Spain', c: [[43.4, -8.9], [43.6, -5.7], [43.4, -3.0], [43.3, -1.8], [42.5, 0.7],
        [42.4, 3.2], [41.0, 1.0], [39.5, -0.2], [38.0, -0.6], [36.7, -2.2], [36.0, -5.6],
        [37.2, -7.4], [38.7, -7.2], [39.7, -7.0], [41.0, -6.9], [41.9, -8.9]] },
  GR: { name: 'Greece', c: [[41.0, 20.5], [41.4, 22.9], [41.3, 26.1], [40.8, 25.9],
        [40.0, 24.0], [39.0, 23.5], [38.0, 24.0], [37.0, 23.2], [36.7, 22.5], [37.0, 21.4],
        [38.3, 21.1], [39.6, 20.2], [40.5, 20.4]] },
}

// geographic context only — not markets we serve
const CONTEXT = {
  PT: [[41.9, -8.9], [41.0, -6.9], [39.7, -7.0], [38.7, -7.2], [37.2, -7.4], [37.0, -8.9],
       [39.4, -9.4], [41.1, -8.7]],
  IE: [[55.2, -7.3], [54.3, -5.5], [53.3, -6.0], [52.2, -6.4], [51.5, -9.5], [52.9, -9.9],
       [54.3, -8.8]],
}

const LINKS = [
  ['FR', 'NORD', 'FR → IT-North'], ['CH', 'NORD', 'CH → IT-North'],
  ['AT', 'NORD', 'AT → IT-North'], ['SI', 'NORD', 'SI → IT-North'],
  ['GR', 'SUD', 'GR → IT-South'], ['DE', 'CH', 'DE → CH'],
  ['FR', 'ES', 'FR → ES'], ['GB', 'FR', 'GB → FR'],
  ['NL', 'DE', 'NL → DE'], ['BE', 'FR', 'BE → FR'], ['FR', 'DE', 'FR → DE'],
]

function heat(p, lo, hi) {
  if (p == null || !isFinite(p)) return null
  const t = hi === lo ? 0.5 : Math.max(0, Math.min(1, (p - lo) / (hi - lo)))
  const stops = [[31, 73, 224], [215, 126, 0], [204, 61, 78]]
  const seg = t < 0.5 ? [stops[0], stops[1]] : [stops[1], stops[2]]
  const k = t < 0.5 ? t * 2 : (t - 0.5) * 2
  const c = seg[0].map((a, i) => Math.round(a + (seg[1][i] - a) * k))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

export default function EuropeMap({ dash, markets = {}, selectedCountry = 'IT', onSelect }) {
  const [hover, setHover] = useState(null)
  const prices = dash?.prices || {}
  const flows = Object.fromEntries(
    (dash?.flows_eu || dash?.flows_now || []).map((f) => [f.border, f.mw]))

  const itPrice = (z) => { const s = prices[z]; return s?.length ? s[s.length - 1][1] : null }
  const ccPrice = (cc) => markets?.[cc]?.price ?? null
  const priceOf = (k) => (IT_ZONES[k] ? itPrice(k) : ccPrice(k))
  const nameOf = (k) => (IT_ZONES[k] ? `IT · ${k}` : COUNTRIES[k]?.name || k)

  const all = [...Object.keys(IT_ZONES), ...Object.keys(COUNTRIES)]
    .map(priceOf).filter((v) => v != null && isFinite(v))
  const lo = all.length ? Math.min(...all) : 0
  const hi = all.length ? Math.max(...all) : 1

  const cent = (k) => (IT_ZONES[k] ? centroid(IT_ZONES[k])
    : COUNTRIES[k] ? centroid(COUNTRIES[k].c) : null)

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${VB_W} ${VB_H}`} className="eu-map" role="img"
        aria-label="European power markets by day-ahead price with interconnector flows">
        <defs>
          <marker id="euarrow" markerWidth="7" markerHeight="7" refX="5" refY="2.5" orient="auto">
            <path d="M0,0 L5,2.5 L0,5 Z" fill={C.teal} />
          </marker>
        </defs>

        {Object.entries(CONTEXT).map(([k, c]) => (
          <path key={k} d={toPath(c)} fill="#DFE5ED" stroke={C.hairline}
            strokeWidth="0.6" opacity="0.5" />
        ))}

        {LINKS.map(([a, b, label]) => {
          const A = cent(a), B = cent(b)
          if (!A || !B) return null
          const mw = flows[label] || 0
          return (
            <g key={label} opacity={mw ? 0.95 : 0.16}>
              <line className={mw ? 'eu-flow' : ''} x1={A[0]} y1={A[1]} x2={B[0]} y2={B[1]}
                stroke={C.teal} strokeWidth={mw ? Math.max(1.2, Math.min(4.5, mw / 800)) : 0.7}
                markerEnd={mw ? 'url(#euarrow)' : undefined} />
              {mw > 0 && (
                <text x={(A[0] + B[0]) / 2} y={(A[1] + B[1]) / 2 - 3} fill={C.teal}
                  fontSize="8.5" textAnchor="middle" fontFamily="IBM Plex Mono">
                  {Math.round(mw)}
                </text>
              )}
            </g>
          )
        })}

        {Object.entries(COUNTRIES).map(([cc, cfg]) => {
          const p = ccPrice(cc)
          const f = heat(p, lo, hi)
          const on = hover === cc
          const selected = selectedCountry === cc
          const [cx, cy] = centroid(cfg.c)
          return (
            <g key={cc} onMouseEnter={() => setHover(cc)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(cc)} role="button" tabIndex="0"
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelect?.(cc) }}
              aria-label={`Open ${cfg.name} market`} style={{ cursor: 'pointer' }}>
              <path d={toPath(cfg.c)} fill={f || '#EFF2F6'}
                fillOpacity={f ? (on ? 0.88 : 0.6) : 0.5}
                stroke={selected ? C.teal : on ? C.chalk : C.hairline}
                strokeWidth={selected ? 2.6 : on ? 1.6 : 0.9}
                strokeDasharray={f ? '0' : '3 2.5'} />
              <text x={cx} y={cy} fill={f ? '#FFFFFF' : C.slate} fontSize="10"
                fontWeight="700" textAnchor="middle" fontFamily="Space Grotesk">{cc}</text>
              {p != null && (
                <text x={cx} y={cy + 11} fill="#FFFFFF" fontSize="8.5" textAnchor="middle"
                  fontFamily="IBM Plex Mono">{p.toFixed(0)}€</text>
              )}
            </g>
          )
        })}

        {Object.entries(IT_ZONES).map(([z, co]) => {
          const p = itPrice(z)
          const f = heat(p, lo, hi)
          const on = hover === z
          const selected = selectedCountry === 'IT'
          const [cx, cy] = centroid(co)
          return (
            <g key={z} onMouseEnter={() => setHover(z)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.('IT')} role="button" tabIndex="0"
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onSelect?.('IT') }}
              aria-label={`Open Italy ${z} market`} style={{ cursor: 'pointer' }}>
              <path d={toPath(co)} fill={f || '#EFF2F6'} fillOpacity={on ? 0.92 : 0.78}
                stroke={selected ? C.teal : on ? C.chalk : C.amber}
                strokeWidth={selected ? 1.8 : on ? 1.6 : 0.9} />
              <text x={cx} y={cy - 1} fill="#FFFFFF" fontSize="7.5" fontWeight="700"
                textAnchor="middle" fontFamily="Space Grotesk">{z}</text>
              {p != null && (
                <text x={cx} y={cy + 7.5} fill="#FFFFFF" fontSize="6.5" textAnchor="middle"
                  fontFamily="IBM Plex Mono">{p.toFixed(0)}</text>
              )}
            </g>
          )
        })}

        <text x="6" y={VB_H - 6} fill={C.slateDim} fontSize="8.5" fontFamily="IBM Plex Mono">
          click a market to explore · dashed = price unavailable · arrows = physical flows (MW)
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
          <div>{priceOf(hover) != null
            ? `${priceOf(hover).toFixed(2)} €/MWh`
            : 'price data unavailable'}</div>
          {markets?.[hover]?.load_mw != null && (
            <div style={{ color: C.slate }}>{markets[hover].load_mw.toLocaleString()} MW</div>
          )}
        </div>
      )}
    </div>
  )
}
