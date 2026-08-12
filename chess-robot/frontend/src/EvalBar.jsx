// Barra de evaluación vertical: proporción blanca según centipawns
// (escala logística) o barra llena si hay mate forzado.
export default function EvalBar({ evaluation }) {
  let whiteShare = 0.5
  let label = '0.00'
  if (evaluation) {
    if (evaluation.mate !== null && evaluation.mate !== undefined) {
      whiteShare = evaluation.mate > 0 ? 1 : 0
      label = `M${Math.abs(evaluation.mate)}`
    } else if (evaluation.cp !== null && evaluation.cp !== undefined) {
      whiteShare = 1 / (1 + Math.exp(-evaluation.cp / 400))
      label = (evaluation.cp / 100).toFixed(2)
    }
  }
  return (
    <div className="eval-bar" title="Evaluación">
      <div className="eval-black" style={{ height: `${(1 - whiteShare) * 100}%` }} />
      <span className="eval-label">{label}</span>
    </div>
  )
}
