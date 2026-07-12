import { C } from './Chart'

/*
 * Signature element: a grid-operator style single-line schematic of the
 * Italian bidding zones. Zones are busbars on a vertical spine; foreign
 * interconnectors arrive from the sides with animated flow dashes whose
 * width scales with the live import level.
 */

const NODES = {
  NORD: { x: 210, y: 60 }, CNOR: { x: 210, y: 150 }, CSUD: { x: 210, y: 240 },
  SUD: { x: 210, y: 330 }, SICI: { x: 330, y: 400 }, SARD: { x: 80, y: 245 },
}
const INTERNAL = [['NORD', 'CNOR'], ['CNOR', 'CSUD'], ['CSUD', 'SUD'], ['SUD', 'SICI'], ['SARD', 'CSUD']]
const FOREIGN = {
  'FR → IT-North': { from: { x: 80, y: 30 }, to: 'NORD', label: 'FR' },
  'CH → IT-North': { from: { x: 210, y: 8 }, to: 'NORD', label: 'CH' },
  'AT → IT-North': { from: { x: 340, y: 30 }, to: 'NORD', label: 'AT' },
  'SI → IT-North': { from: { x: 372, y: 78 }, to: 'NORD', label: 'SI' },
  'GR → IT-South': { from: { x: 372, y: 330 }, to: 'SUD', label: 'GR' },
  'ME → IT-Centre-South': { from: { x: 372, y: 240 }, to: 'CSUD', label: 'ME' },
}

export default function ZoneSchematic({ flows, prices }) {
  const latestPrice = (z) => {
    const s = prices?.[z]
    return s?.length ? s[s.length - 1][1] : null
  }
  const flowByBorder = Object.fromEntries((flows || []).map((f) => [f.border, f.mw]))
  const totalImport = (flows || []).reduce((a, f) => a + f.mw, 0)

  return (
    <svg viewBox="0 0 420 460" className="schematic" role="img"
      aria-label="Schematic of Italian bidding zones with live border imports">
      {/* internal spine */}
      {INTERNAL.map(([a, b]) => (
        <line key={a + b} x1={NODES[a].x} y1={NODES[a].y} x2={NODES[b].x} y2={NODES[b].y}
          stroke={C.hairline} strokeWidth="2" />
      ))}
      {/* foreign interconnectors */}
      {Object.entries(FOREIGN).map(([border, cfg]) => {
        const mw = flowByBorder[border] || 0
        const w = Math.max(1.2, Math.min(5, mw / 700))
        const n = NODES[cfg.to]
        return (
          <g key={border}>
            <line className="flowline" x1={cfg.from.x} y1={cfg.from.y} x2={n.x} y2={n.y}
              stroke={C.teal} strokeWidth={w} opacity="0.85" />
            <text x={cfg.from.x} y={cfg.from.y - 7} fill={C.slate} fontSize="11"
              textAnchor="middle">{cfg.label}</text>
            <text x={(cfg.from.x + n.x) / 2} y={(cfg.from.y + n.y) / 2 - 6}
              fill={C.teal} fontSize="10" textAnchor="middle" opacity="0.9">
              {mw ? `${Math.round(mw)}` : ''}
            </text>
          </g>
        )
      })}
      {/* zone busbars */}
      {Object.entries(NODES).map(([z, p]) => {
        const price = latestPrice(z)
        return (
          <g key={z}>
            <rect x={p.x - 46} y={p.y - 17} width="92" height="34" rx="7"
              fill={C.panel} stroke={C.hairline} />
            <rect x={p.x - 46} y={p.y - 17} width="4" height="34" rx="2" fill={C.amber} />
            <text x={p.x - 34} y={p.y - 3} fill={C.chalk} fontSize="12" fontWeight="600">{z}</text>
            <text x={p.x - 34} y={p.y + 11} fill={C.amber} fontSize="10.5">
              {price != null ? `${price.toFixed(1)} €` : '—'}
            </text>
          </g>
        )
      })}
      <text x="14" y="446" fill={C.slateDim} fontSize="10">
        border imports, MW · line width ∝ flow · total {Math.round(totalImport)} MW
      </text>
    </svg>
  )
}
