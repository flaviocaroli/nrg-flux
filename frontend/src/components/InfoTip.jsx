import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

const EDGE = 12
const MAX_WIDTH = 310

export default function InfoTip({ label, children }) {
  const id = useId()
  const buttonRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState(null)

  const place = useCallback(() => {
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(MAX_WIDTH, window.innerWidth - EDGE * 2)
    const left = Math.min(
      Math.max(EDGE, rect.left + rect.width / 2 - width / 2),
      window.innerWidth - width - EDGE,
    )
    const placeAbove = window.innerHeight - rect.bottom < 190 && rect.top > 190
    setPosition({
      left,
      width,
      top: placeAbove ? undefined : rect.bottom + 8,
      bottom: placeAbove ? window.innerHeight - rect.top + 8 : undefined,
    })
  }, [])

  useEffect(() => {
    if (!open) return undefined
    place()
    const reposition = () => place()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, place])

  return (
    <span className="info-tip"
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>
      <button ref={buttonRef} className="info-tip-button" type="button"
        aria-label={label} aria-describedby={open ? id : undefined}>
        i
      </button>
      {open && position && createPortal(
        <span id={id} className="info-tip-popover" role="tooltip" style={position}>
          {children}
        </span>, document.body,
      )}
    </span>
  )
}
