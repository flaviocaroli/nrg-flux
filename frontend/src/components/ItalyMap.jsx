import { useState } from 'react'
import { C } from './Chart'

/*
 * ItalyMap — a geographic view of the six Italian bidding zones with the
 * mainland/Sicily/Sardinia laid out roughly as they sit on the map, and
 * animated import arrows from neighbouring countries.
 *
 * This is a stylised single-path map (not a full GeoJSON) so it stays tiny,
 * theme-able and dependency-free. Zones are coloured by price (a heat scale
 * from teal = cheap to alarm = expensive) and each shows its €/MWh + MW load.
 * The toggle in App swaps this with the busbar ZoneSchematic.
 */

// stylised zone polygons (viewBox 0 0 440 560). Deliberately simplified.
const ZONES = {
  NORD: { path: 'M120,70 L300,60 L340,120 L300,150 L150,150 L110,110 Z', cx: 220, cy: 108, label: 'NORD' },
  CNOR: { path: 'M150,150 L300,150 L290,215 L210,235 L160,205 Z', cx: 228, cy: 188, label: 'CNOR' },
  CSUD: { path: 'M210,235 L290,215 L320,285 L285,330 L225,315 L200,270 Z', cx: 258, cy: 278, label: 'CSUD' },
  SUD:  { path: 'M225,315 L285,330 L330,395 L300,450 L250,440 L230,380 Z', cx: 275, cy: 388, label: 'SUD' },
  SICI: { path: 'M250,470 L315,470 L330,510 L285,530 L245,505 Z', cx: 288, cy: 498, label: 'SICI' },
  SARD: { path: 'M60,280 L110,275 L120,345 L80,375 L48,340 Z', cx: 84, cy: 325, label: 'SARD' },
}

// import arrows from neighbours -> the zone they land in
const IMPORTS = [
  { from: 'FR', x: 55, y: 60, to: 'NORD', border: 'FR → IT-North' },
  { from: 'CH', x: 200, y: 22, to: 'NORD', border: 'CH → IT-North' },
  { from: 'AT', x: 330, y: 40, to: 'NORD', border: 'AT → IT-North' },
  { from: 'SI', x: 375, y: 95, to: 'NORD', border: 'SI → IT-North' },
  { from: 'GR', x: 375, y: 405, to: 'SUD', border: 'GR → IT-South' },
  { from: 'ME', x: 360, y: 300, to: 'CSUD', border: 'ME → IT-Centre-South' },
]

function priceColor(p, lo, hi) {
  if (p == null || hi === lo) return C.slate
  const t = Math.max(0, Math.min(1, (p - lo) / (hi - lo)))
  // teal -> amber -> alarm
  const stops = [[52, 217, 195], [255, 180, 84], [255, 90, 102]]
  const seg = t < 0.5 ? [stops[0], stops[1]] : [stops[1], stops[2]]
  const k = t < 0.5 ? t * 2 : (t - 0.5) * 2
  const c = seg[0].map((a, i) => Math.round(a + (seg[1][i] - a) * k))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

export default function ItalyMap({ dash }) {
  const [hover, setHover] = useState(null)
  const prices = dash?.prices || {}
  const flowByBorder = Object.fromEntries((dash?.flows_now || []).map((f) => [f.border, f.mw]))

  const latest = (z) => { const s = prices[z]; return s?.length ? s[s.length - 1][1] : null }
  const vals = Object.keys(ZONES).map(latest).filter((v) => v != null)
  const lo = Math.min(...vals), hi = Math.max(...vals)
  const totalImport = Object.values(flowByBorder).reduce((a, b) => a + b, 0)

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox="0 0 440 560" className="italy-map" role="img"
        aria-label="Geographic map of Italian bidding zones with live cross-border imports">
        <defs>
          <marker id="arrow" markerWidth="9" markerHeight="9" refX="6" refY="3"
            orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill={C.teal} /></marker>
        </defs>

        {/* import arrows first (under zones) */}
        {IMPORTS.map((im) => {
          const mw = flowByBorder[im.border] || 0
          const z = ZONES[im.to]
          const w = Math.max(1.5, Math.min(6, mw / 600))
          return (
            <g key={im.from} opacity={mw > 0 ? 0.95 : 0.3}>
              <line className="import-flow" x1={im.x} y1={im.y} x2={z.cx} y2={z.cy}
                stroke={C.teal} strokeWidth={w} markerEnd="url(#arrow)" />
              <circle cx={im.x} cy={im.y} r="15" fill={C.panel} stroke={C.hairline} />
              <text x={im.x} y={im.y + 4} fill={C.chalk} fontSize="11" fontWeight="600"
                textAnchor="middle" fontFamily="IBM Plex Mono">{im.from}</text>
              {mw > 0 && (
                <text x={(im.x + z.cx) / 2} y={(im.y + z.cy) / 2 - 5} fill={C.teal}
                  fontSize="10" textAnchor="middle" fontFamily="IBM Plex Mono">
                  {Math.round(mw)}
                </text>
              )}
            </g>
          )
        })}

        {/* zones */}
        {Object.entries(ZONES).map(([z, cfg]) => {
          const p = latest(z)
          const fill = priceColor(p, lo, hi)
          const on = hover === z
          return (
            <g key={z} onMouseEnter={() => setHover(z)} onMouseLeave={() => setHover(null)}
              style={{ cursor: 'pointer' }}>
              <path d={cfg.path} fill={fill} fillOpacity={on ? 0.85 : 0.62}
                stroke={on ? C.chalk : C.hairline} strokeWidth={on ? 2 : 1.2} />
              <text x={cfg.cx} y={cfg.cy - 2} fill="#0d1424" fontSize="12" fontWeight="700"
                textAnchor="middle" fontFamily="Space Grotesk">{cfg.label}</text>
              <text x={cfg.cx} y={cfg.cy + 12} fill="#0d1424" fontSize="10"
                textAnchor="middle" fontFamily="IBM Plex Mono">
                {p != null ? `${p.toFixed(0)}€` : '—'}
              </text>
            </g>
          )
        })}

        <text x="16" y="548" fill={C.slateDim} fontSize="10" fontFamily="IBM Plex Mono">
          zones by day-ahead price · imports total {Math.round(totalImport)} MW
        </text>
      </svg>

      {/* price legend */}
      <div className="map-legend">
        <span>{lo.toFixed(0)}€</span>
        <div className="map-scale" />
        <span>{hi.toFixed(0)}€</span>
      </div>

      {hover && (
        <div className="map-tip">
          <b>{ZONES[hover].label}</b>
          <div>{latest(hover)?.toFixed(2)} €/MWh</div>
        </div>
      )}
    </div>
  )
}
