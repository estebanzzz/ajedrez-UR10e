import { useEffect, useMemo, useRef, useState } from 'react'
import Board from './Board.jsx'
import EvalBar from './EvalBar.jsx'
import GameOver from './GameOver.jsx'
import OperatorPanel from './OperatorPanel.jsx'
import Ranking from './Ranking.jsx'
import RobotVoice from './RobotVoice.jsx'
import SideNav from './SideNav.jsx'
import StartScreen from './StartScreen.jsx'
import arbyteLogo from './assets/arbyte-logo.png'
import { api, useSocket } from './useGameSocket.js'

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

function bannerFor(status) {
  if (!status) return { text: 'Conectando…', tone: 'neutral' }
  const name = status.player_name ? `, ${status.player_name.split(' ')[0]}` : ''
  const selfPlay = status.mode === 'self_play'
  switch (status.phase) {
    case 'idle':
      return { text: '¿Jugamos?', tone: 'neutral' }
    case 'human_turn': {
      if (status.detector_phase === 'in_progress')
        return { text: 'Jugada en curso…', tone: 'active' }
      if (status.detector_phase === 'complete')
        return { text: 'Pulsá el botón para confirmar tu jugada', tone: 'active' }
      const check = status.in_check ? ' — ¡Jaque!' : ''
      return { text: `Tu turno${name}${check}`, tone: 'human' }
    }
    case 'human_error':
      return { text: status.last_error || 'Jugada no válida — restaurá las piezas', tone: 'error' }
    case 'human_choice':
      return { text: 'No vi qué pieza comiste — tocá tu jugada', tone: 'active' }
    case 'paused':
      return { text: '⏸ Partida en pausa — reloj detenido', tone: 'neutral' }
    case 'robot_turn': {
      if (!selfPlay) return { text: 'Pensando…', tone: 'robot' }
      const side = status.turn === 'white' ? 'blancas' : 'negras'
      const check = status.in_check ? ' — ¡Jaque!' : ''
      return { text: `🤖 Robot contra sí mismo · juegan las ${side}${check}`, tone: 'robot' }
    }
    case 'resync':
      // En la demo el operador necesita saber qué falta (p. ej. posición inicial).
      return {
        text: selfPlay && status.last_error
          ? status.last_error
          : 'Ajustando el tablero — un momento…',
        tone: 'error',
      }
    case 'game_over':
      return { text: selfPlay ? 'Fin de la demostración' : 'Fin de partida', tone: 'neutral' }
    default:
      return { text: '', tone: 'neutral' }
  }
}

// Cara del robot cuando no está hablando, según la fase de la partida.
function idleMoodFor(status) {
  switch (status?.phase) {
    case 'robot_turn':
      return 'thinking'
    case 'human_turn':
      return status.detector_phase === 'in_progress' ? 'sneaky' : 'smug'
    case 'human_choice':
      return 'shocked'
    case 'paused':
      return 'bored'
    case 'human_error':
    case 'resync':
      return 'angry'
    case 'game_over': {
      const winner = status.outcome?.winner
      if (!winner) return 'bored'
      return winner === status.human_color ? 'sad' : 'gloating'
    }
    default:
      return 'bored'
  }
}

// Reloj del jugador (corre solo en su turno; el backend manda remaining_s).
function GameClock({ clock }) {
  if (!clock) return null
  const minutes = Math.floor(clock.remaining_s / 60)
  const seconds = String(clock.remaining_s % 60).padStart(2, '0')
  const urgency =
    clock.remaining_s <= 30 ? ' danger' : clock.remaining_s <= 60 ? ' warn' : ''
  return (
    <div className={'game-clock' + urgency + (clock.running ? '' : ' paused')}>
      ⏱ {minutes}:{seconds}
    </div>
  )
}

// Vista de depuración: lo que ve el clasificador de la cámara (stream MJPEG).
function CameraDebug() {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState(false)
  return (
    <div className="camera-debug">
      {open &&
        (error ? (
          <div className="camera-error">Cámara no disponible</div>
        ) : (
          <img
            src="/api/vision/stream"
            alt="vista de la cámara"
            onError={() => setError(true)}
          />
        ))}
      <button
        onClick={() => {
          setOpen((o) => !o)
          setError(false)
        }}
      >
        {open ? '✕ Ocultar cámara' : '📷 Cámara'}
      </button>
    </div>
  )
}

function MoveHistory({ sans }) {
  const rows = []
  for (let i = 0; i < sans.length; i += 2)
    rows.push({ n: i / 2 + 1, white: sans[i], black: sans[i + 1] })
  return (
    <div className="history">
      <table>
        <tbody>
          {rows.map((row) => (
            <tr key={row.n}>
              <td className="move-number">{row.n}.</td>
              <td>{row.white}</td>
              <td>{row.black || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function App() {
  const { message: status, connected } = useSocket('/ws/game')
  const [operatorOpen, setOperatorOpen] = useState(false)
  const [startRequested, setStartRequested] = useState(false)
  const [rankingKey, setRankingKey] = useState(0)
  const tapsRef = useRef({ count: 0, last: 0 })
  const prevPhaseRef = useRef(null)

  // Refrescar el ranking cuando termina una partida.
  useEffect(() => {
    const phase = status?.phase
    if (phase === 'game_over' && prevPhaseRef.current !== 'game_over')
      setRankingKey((k) => k + 1)
    prevPhaseRef.current = phase
  }, [status?.phase])

  // Panel de operador oculto: 5 toques rápidos sobre el logo del encabezado.
  const handleTitleTap = () => {
    const now = Date.now()
    const taps = tapsRef.current
    taps.count = now - taps.last < 800 ? taps.count + 1 : 1
    taps.last = now
    if (taps.count >= 5) {
      taps.count = 0
      setOperatorOpen(true)
    }
  }

  const banner = useMemo(() => bannerFor(status), [status])
  const phase = status?.phase
  const selfPlay = status?.mode === 'self_play'
  const showStart = phase === 'idle' || startRequested
  const showGameOver = phase === 'game_over' && !startRequested

  // Botón de confirmación en pantalla (mientras no exista el botón físico).
  // Habilitado durante todo el turno humano; se resalta cuando el detector
  // continuo ya reconoce una jugada completa.
  const [confirmBusy, setConfirmBusy] = useState(false)
  const moveReady = status?.detector_phase === 'complete'
  const showConfirm = phase === 'human_turn' || phase === 'human_error'
  const confirmMove = async () => {
    setConfirmBusy(true)
    try {
      await api('/api/game/confirm', {})
    } finally {
      setConfirmBusy(false)
    }
  }

  // Demo robot vs robot: botón para cortarla y volver a la pantalla de inicio.
  const showStop = selfPlay && (phase === 'robot_turn' || phase === 'resync')
  const [stopBusy, setStopBusy] = useState(false)
  const stopGame = async () => {
    setStopBusy(true)
    try {
      await api('/api/game/stop', {})
    } finally {
      setStopBusy(false)
    }
  }

  // Captura ambigua: la cámara no vio qué pieza se comió; el jugador elige.
  const pendingChoices = status?.pending_choices || []
  const showChoice = phase === 'human_choice' && pendingChoices.length > 0
  const [choiceBusy, setChoiceBusy] = useState(false)
  const chooseMove = async (uci) => {
    setChoiceBusy(true)
    try {
      await api('/api/game/choose', { move: uci })
    } finally {
      setChoiceBusy(false)
    }
  }

  // Pausa de emergencia (dudas con una jugada): detiene el reloj y bloquea
  // las confirmaciones hasta reanudar. Solo en las fases del humano.
  const showPause =
    !selfPlay && ['human_turn', 'human_error', 'human_choice'].includes(phase)
  const paused = phase === 'paused'
  const [pauseBusy, setPauseBusy] = useState(false)
  const pauseGame = async () => {
    setPauseBusy(true)
    try {
      await api('/api/game/pause', {})
    } finally {
      setPauseBusy(false)
    }
  }
  const resumeGame = async () => {
    setPauseBusy(true)
    try {
      await api('/api/game/resume', {})
    } finally {
      setPauseBusy(false)
    }
  }

  // Abandonar la partida en curso (con confirmación: es una pantalla pública
  // y un toque accidental no puede matarle la partida a nadie).
  const showResign =
    !selfPlay &&
    ['human_turn', 'human_error', 'human_choice', 'robot_turn', 'resync', 'paused'].includes(phase)
  const [resignConfirm, setResignConfirm] = useState(false)
  const [resignBusy, setResignBusy] = useState(false)
  const resign = async () => {
    setResignBusy(true)
    try {
      await api('/api/game/resign', {})
      setResignConfirm(false)
    } finally {
      setResignBusy(false)
    }
  }

  return (
    <div className="app">
      <SideNav active="/ui" />

      <header>
        {/* El logo hereda el gesto oculto que antes vivía en el título. */}
        <img
          className="brand-logo"
          src={arbyteLogo}
          alt="Arbyte"
          onClick={handleTitleTap}
        />
        {!connected && <span className="disconnected">sin conexión</span>}
      </header>

      <div className={`banner ${banner.tone}`}>
        <GameClock clock={status?.clock} />
        <span>{banner.text}</span>
        {showConfirm && (
          <button
            className={'confirm-move' + (moveReady ? ' ready' : ' armed')}
            disabled={confirmBusy}
            onClick={confirmMove}
          >
            ✓ Confirmar jugada
          </button>
        )}
        {showStop && (
          <button className="stop-demo" disabled={stopBusy} onClick={stopGame}>
            ■ Detener demo
          </button>
        )}
        {showPause && (
          <button className="pause-button" disabled={pauseBusy} onClick={pauseGame}>
            ⏸ Pausa
          </button>
        )}
        {showResign && (
          <button className="resign-button" onClick={() => setResignConfirm(true)}>
            🏳 Terminar
          </button>
        )}
      </div>

      <main>
        <Ranking refreshKey={rankingKey} />
        <EvalBar evaluation={status?.evaluation} />
        <Board
          fen={status?.fen || START_FEN}
          lastMove={status?.last_move}
          mismatched={phase === 'human_error' || phase === 'resync'
            ? status?.mismatched_squares
            : []}
        />
        <aside>
          <section>
            <h3>Jugadas</h3>
            <MoveHistory sans={status?.san_history || []} />
          </section>
        </aside>
      </main>

      <CameraDebug />
      <RobotVoice speech={status?.speech} idleMood={idleMoodFor(status)} />

      {paused && (
        <div className="overlay">
          <div className="start-card pause-card">
            <h2>⏸ Partida en pausa</h2>
            <p className="hint">
              El reloj está detenido. Resuelvan la duda con tranquilidad y
              toquen Reanudar para seguir.
            </p>
            <button className="play-button" disabled={pauseBusy} onClick={resumeGame}>
              ▶ Reanudar
            </button>
            <button className="resign-button" onClick={() => setResignConfirm(true)}>
              🏳 Terminar la partida
            </button>
          </div>
        </div>
      )}
      {showChoice && (
        <div className="overlay">
          <div className="start-card choice-card">
            <h2>¿Cuál fue tu jugada?</h2>
            <p className="hint">
              La cámara no llegó a ver qué pieza capturaste. Tocá la jugada
              que hiciste y seguimos.
            </p>
            {pendingChoices.map((choice) => (
              <button
                key={choice.uci}
                className="play-button choice-button"
                disabled={choiceBusy}
                onClick={() => chooseMove(choice.uci)}
              >
                {choice.san}
              </button>
            ))}
          </div>
        </div>
      )}
      {showResign && resignConfirm && (
        <div className="overlay" onClick={() => setResignConfirm(false)}>
          <div className="start-card resign-card" onClick={(e) => e.stopPropagation()}>
            <h2>¿Terminar la partida?</h2>
            <p className="hint">
              Cuenta como abandono: gana el robot, pero tus jugadas y el
              material capturado suman puntos igual.
            </p>
            <button className="play-button" onClick={() => setResignConfirm(false)}>
              Seguir jugando
            </button>
            <button className="resign-confirm" disabled={resignBusy} onClick={resign}>
              🏳 Sí, abandonar
            </button>
          </div>
        </div>
      )}
      {showStart && status && (
        <StartScreen onStarted={() => setStartRequested(false)} speech={status?.speech} />
      )}
      {showGameOver && (
        <GameOver status={status} onPlayAgain={() => setStartRequested(true)} />
      )}
      {operatorOpen && (
        <OperatorPanel status={status} onClose={() => setOperatorOpen(false)} />
      )}
    </div>
  )
}
