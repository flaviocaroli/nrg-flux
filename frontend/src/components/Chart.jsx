import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

export const C = {
  harbor: '#0d1424', panel: '#131d33', hairline: '#26334f',
  chalk: '#e9eef8', slate: '#8da0c4', slateDim: '#5a6b8f',
  amber: '#ffb454', teal: '#34d9c3', alarm: '#ff5a66', ok: '#58d18a',
  zones: ['#ffb454', '#f2789f', '#8ecbff', '#b7a5ff', '#7ee0a3', '#ffd97a'],
}

export const axisBase = {
  axisLine: { lineStyle: { color: C.hairline } },
  axisLabel: { color: C.slate, fontFamily: 'IBM Plex Mono', fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(38,51,79,0.5)' } },
  axisTick: { show: false },
}

export const tooltipBase = {
  trigger: 'axis',
  backgroundColor: '#182543',
  borderColor: C.hairline,
  textStyle: { color: C.chalk, fontFamily: 'IBM Plex Mono', fontSize: 12 },
  axisPointer: { type: 'line', lineStyle: { color: C.slateDim } },
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
