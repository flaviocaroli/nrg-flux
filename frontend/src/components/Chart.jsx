import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

/*
 * Design tokens — "NRG-Flux Europe", daylight control room.
 * Cool paper ground, white instrument cards, ink numerals.
 *   voltage cobalt -> demand / our forecasts
 *   auction amber  -> markets / prices
 *   alarm red      -> outages / warnings
 * Token NAMES are kept from the previous theme so every component
 * keeps working: `harbor` is now the paper ground, `chalk` the ink.
 */
export const C = {
  harbor: '#EDF0F2', panel: '#FFFFFF', hairline: '#D7DDE6',
  chalk: '#0E1B2C', slate: '#54657D', slateDim: '#8C99AB',
  amber: '#D77E00', teal: '#1F49E0', alarm: '#CC3D4E', ok: '#188A57',
  zones: ['#D77E00', '#B8447E', '#1F49E0', '#6A3FC0', '#188A57', '#8A6D1B'],
}

export const axisBase = {
  axisLine: { lineStyle: { color: C.hairline } },
  axisLabel: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(215,221,230,0.7)' } },
  axisTick: { show: false },
}

export const tooltipBase = {
  trigger: 'axis',
  backgroundColor: '#FFFFFF',
  borderColor: C.hairline,
  textStyle: { color: C.chalk, fontFamily: 'IBM Plex Mono', fontSize: 12 },
  axisPointer: { type: 'line', lineStyle: { color: C.slateDim } },
  extraCssText: 'box-shadow: 0 6px 24px rgba(14,27,44,.12); border-radius: 8px;',
}

export function Chart({ option, className = 'chart' }) {
  const ref = useRef(null)
  const chartRef = useRef(null)

  useEffect(() => {
    chartRef.current = echarts.init(ref.current)
    const onResize = () => chartRef.current?.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chartRef.current?.dispose() }
  }, [])

  useEffect(() => { if (option) chartRef.current?.setOption(option, true) }, [option])

  return <div ref={ref} className={className} />
}
