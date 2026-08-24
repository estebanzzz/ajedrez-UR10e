const RESULT_TEXT = {
  win: '¡GANASTE! 🎉',
  draw: 'Tablas — ¡nada mal contra el robot!',
  loss: 'Ganó el robot — ¡buena partida!',
}

const TERMINATION_TEXT = {
  CHECKMATE: 'Jaque mate',
  STALEMATE: 'Ahogado',
  INSUFFICIENT_MATERIAL: 'Material insuficiente',
  THREEFOLD_REPETITION: 'Triple repetición',
  FIVEFOLD_REPETITION: 'Repetición',
  FIFTY_MOVES: 'Regla de las 50 jugadas',
  SEVENTYFIVE_MOVES: 'Regla de las 75 jugadas',
  RESIGNATION: 'Abandono',
  TIMEOUT: 'Tiempo agotado',
}

// Fin de la demo robot vs robot: no hay puntaje ni ranking, solo el resultado.
function SelfPlayOver({ status, onPlayAgain }) {
  const outcome = status.outcome
  const winner =
    outcome?.winner === 'white'
      ? 'Ganaron las blancas'
      : outcome?.winner === 'black'
        ? 'Ganaron las negras'
        : 'Tablas'
  const how = outcome ? TERMINATION_TEXT[outcome.termination] || outcome.termination : ''
  const moves = Math.ceil((status.san_history?.length || 0) / 2)
  return (
    <div className="overlay">
      <div className="start-card gameover-card">
        <h2>🤖 Fin de la demostración</h2>
        <div className="score-big">{winner}</div>
        <div className="score-breakdown">
          {how && <span>{how}</span>}
          <span>{moves} jugadas</span>
          <span>resultado: {outcome?.result}</span>
        </div>
        <p className="hint">¿Te animás a jugarle vos?</p>
        <button className="play-button" onClick={onPlayAgain}>
          Volver al inicio
        </button>
      </div>
    </div>
  )
}

export default function GameOver({ status, onPlayAgain }) {
  if (status.mode === 'self_play')
    return <SelfPlayOver status={status} onPlayAgain={onPlayAgain} />
  const last = status.last_game
  if (!last) return null
  return (
    <div className="overlay">
      <div className="start-card gameover-card">
        <h2>
          {status.outcome?.termination === 'RESIGNATION'
            ? 'Partida abandonada — ganó el robot'
            : status.outcome?.termination === 'TIMEOUT'
              ? '⏱ Se acabó el tiempo — ganó el robot'
              : RESULT_TEXT[last.result]}
        </h2>
        <p className="gameover-name">{last.player_name || 'Anónimo'}</p>
        <div className="score-big">{last.score} pts</div>
        <div className="score-breakdown">
          <span>{last.moves} jugadas</span>
          <span>{last.material} pts de material capturado</span>
          <span>dificultad: {last.difficulty}</span>
        </div>
        <button className="play-button" onClick={onPlayAgain}>
          Jugar de nuevo
        </button>
      </div>
    </div>
  )
}
