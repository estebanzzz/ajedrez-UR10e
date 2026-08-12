const RESULT_TEXT = {
  win: '¡GANASTE! 🎉',
  draw: 'Tablas — ¡nada mal contra el robot!',
  loss: 'Ganó el robot — ¡buena partida!',
}

export default function GameOver({ status, onPlayAgain }) {
  const last = status.last_game
  if (!last) return null
  return (
    <div className="overlay">
      <div className="start-card gameover-card">
        <h2>{RESULT_TEXT[last.result]}</h2>
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
