export default function InfoTip({ label, children }) {
  return (
    <span className="info-tip">
      <button className="info-tip-button" type="button" aria-label={label}>
        i
      </button>
      <span className="info-tip-popover" role="tooltip">
        {children}
      </span>
    </span>
  )
}
