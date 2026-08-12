import { useMemo, useRef, useState } from 'react'
import Board from './Board.jsx'
import EvalBar from './EvalBar.jsx'
import OperatorPanel from './OperatorPanel.jsx'
import { api, useSocket } from './useGameSocket.js'

const DIFFICULTIES = [
  { id: 'principiante', label: 'Principiante' },
  { id: 'intermedio', label: 'Intermedio' },
  { id: 'avanzado', label: 'Avanzado' },
  { id: 'maximo', label: 'Máximo' },
]

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'

function bannerFor(status) {
  if (!status) return { text: 'Conectando…', tone: 'neutral' }
  switch (status.phase) {
    case 'idle':
      return { text: '¿Jugamos? Pedile al operador una partida nueva', tone: 'neutral' }
    case 'human_turn': {
      if (status.detector_phase === 'in_progress')
        return { text: 'Jugada en curso…', tone: 'active' }
      if (status.detector_phase === 'complete')
        return { text: 'Pulsá el botón para confirmar tu jugada', tone: 'active' }
      const check = status.in_check ? ' — ¡Jaque!' : ''
      return { text: `Tu turno${check}`, tone: 'human' }
    }
    case 'human_error':
      return { text: status.last_error || 'Jugada no válida — restaurá las piezas', tone: 'error' }
    case 'robot_turn':
      return { text: 'Pensando…', tone: 'robot' }
    case 'resync':
      return { text: 'Ajustando el tablero — un momento…', tone: 'error' }
    case 'game_over': {
      const outcome = status.outcome
      if (!outcome) return { text: 'Fin de partida', tone: 'neutral' }
      if (outcome.winner === null) return { text: `Tablas (${outcome.termination})`, tone: 'neutral' }
      const humanWon = outcome.winner === status.human_color
      return {
        text: humanWon ? '¡Ganaste! Felicitaciones 🎉' : 'Gana el robot — ¡buena partida!',
        tone: humanWon ? 'human' : 'robot',
      }
    }
    default:
      return { text: '', tone: 'neutral' }
  }
}

function MoveHistory({ sans }) {
  const rows = []
  for (let i = 0; i < sans.length; i += 2)
    rows.push({ n: i / 2 + 1, white: sans[i], black: sans[i + 1] })
  const endRef = useRef(null)
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
      <div ref={endRef} />
    </div>
  )
}

export default function App() {
  const { message: status, connected } = useSocket('/ws/game')
  const [operatorOpen, setOperatorOpen] = useState(false)
  const [difficulty, setDifficulty] = useState('intermedio')
  const tapsRef = useRef({ count: 0, last: 0 })

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

  const setLevel = async (level) => {
    setDifficulty(level)
    await api('/api/game/difficulty', { level })
  }

  const banner = useMemo(() => bannerFor(status), [status])

  return (
    <div className="app">
      <header>
        <h1 onClick={handleTitleTap}>♞ Robot Ajedrecista</h1>
        {!connected && <span className="disconnected">sin conexión</span>}
      </header>

      <div className={`banner ${banner.tone}`}>{banner.text}</div>

      <main>
        <EvalBar evaluation={status?.evaluation} />
        <Board
          fen={status?.fen || START_FEN}
          lastMove={status?.last_move}
          mismatched={status?.phase === 'human_error' || status?.phase === 'resync'
            ? status?.mismatched_squares
            : []}
        />
        <aside>
          <section className="difficulty">
            <h3>Dificultad</h3>
            {DIFFICULTIES.map((d) => (
              <button
                key={d.id}
                className={difficulty === d.id ? 'selected' : ''}
                onClick={() => setLevel(d.id)}
              >
                {d.label}
              </button>
            ))}
          </section>
          <section>
            <h3>Jugadas</h3>
            <MoveHistory sans={status?.san_history || []} />
          </section>
        </aside>
      </main>

      {operatorOpen && (
        <OperatorPanel status={status} onClose={() => setOperatorOpen(false)} />
      )}
    </div>
  )
}
