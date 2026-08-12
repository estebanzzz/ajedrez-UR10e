import { useEffect, useMemo, useRef, useState } from 'react'
import Board from './Board.jsx'
import EvalBar from './EvalBar.jsx'
import GameOver from './GameOver.jsx'
import OperatorPanel from './OperatorPanel.jsx'
import Ranking from './Ranking.jsx'
import StartScreen from './StartScreen.jsx'
import { useSocket } from './useGameSocket.js'

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

function bannerFor(status) {
  if (!status) return { text: 'Conectando…', tone: 'neutral' }
  const name = status.player_name ? `, ${status.player_name.split(' ')[0]}` : ''
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
    case 'robot_turn':
      return { text: 'Pensando…', tone: 'robot' }
    case 'resync':
      return { text: 'Ajustando el tablero — un momento…', tone: 'error' }
    case 'game_over':
      return { text: 'Fin de partida', tone: 'neutral' }
    default:
      return { text: '', tone: 'neutral' }
  }
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

  // Panel de operador oculto: 5 toques rápidos sobre el título.
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
  const showStart = phase === 'idle' || startRequested
  const showGameOver = phase === 'game_over' && !startRequested

  return (
    <div className="app">
      <header>
        <h1 onClick={handleTitleTap}>♞ Robot Ajedrecista</h1>
        {!connected && <span className="disconnected">sin conexión</span>}
      </header>

      <div className={`banner ${banner.tone}`}>{banner.text}</div>

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

      {showStart && status && (
        <StartScreen onStarted={() => setStartRequested(false)} />
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
