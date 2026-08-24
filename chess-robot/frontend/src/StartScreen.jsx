import { useState } from 'react'
import { api } from './useGameSocket.js'

const DIFFICULTIES = [
  { id: 'principiante', label: 'Principiante', mult: '×1' },
  { id: 'intermedio', label: 'Intermedio', mult: '×1.5' },
  { id: 'avanzado', label: 'Avanzado', mult: '×2' },
  { id: 'maximo', label: 'Máximo', mult: '×3' },
]

// Reloj del jugador: corre solo en su turno; a 0, gana el robot.
const TIMES = [
  { minutes: 3, label: '3 min' },
  { minutes: 5, label: '5 min' },
  { minutes: 10, label: '10 min' },
  { minutes: 0, label: 'Sin reloj' },
]

export default function StartScreen({ onStarted, onClose, speech }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [difficulty, setDifficulty] = useState('intermedio')
  const [timeMinutes, setTimeMinutes] = useState(5)
  const [personality, setPersonality] = useState(null)
  const [busy, setBusy] = useState(false)
  // Personalidades/voces del robot (las define el backend).
  const personalities = speech?.personalities || []
  const voice = personality ?? speech?.personality
  const nameValid = name.trim().split(/\s+/).length >= 2 // nombre y apellido
  const emailValid = /^\S+@\S+\.\S+$/.test(email.trim())
  const valid = nameValid && emailValid

  const start = async () => {
    if (!valid || busy) return
    setBusy(true)
    try {
      await api('/api/game/new', {
        mode: 'human',
        human_color: 'white',
        player_name: name.trim(),
        player_email: email.trim(),
        difficulty,
        time_minutes: timeMinutes,
        ...(voice ? { personality: voice } : {}),
      })
      onStarted?.()
    } finally {
      setBusy(false)
    }
  }

  // Modo demo: el robot juega contra sí mismo (no requiere nombre ni entra
  // al ranking). Útil para atraer público cuando no hay nadie jugando.
  const startSelfPlay = async () => {
    if (busy) return
    setBusy(true)
    try {
      await api('/api/game/new', {
        mode: 'self_play',
        difficulty,
        ...(voice ? { personality: voice } : {}),
      })
      onStarted?.()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="start-card" onClick={(e) => e.stopPropagation()}>
        {onClose && (
          <button className="card-close" aria-label="Cerrar" onClick={onClose}>
            ✕
          </button>
        )}
        <h2>¿Te animás a jugarle al robot?</h2>
        <label htmlFor="player-name">Nombre y apellido</label>
        <input
          id="player-name"
          autoFocus
          value={name}
          maxLength={40}
          placeholder="Ej: Ana García"
          onChange={(e) => setName(e.target.value)}
        />
        {!nameValid && name.length > 0 && (
          <p className="hint">Escribí nombre y apellido para entrar al ranking</p>
        )}
        <label htmlFor="player-email">Email</label>
        <input
          id="player-email"
          type="email"
          value={email}
          maxLength={80}
          placeholder="Ej: ana@mail.com"
          onChange={(e) => setEmail(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && start()}
        />
        <p className="hint">
          Solo para avisarte si ganás el premio del día. No se muestra en
          pantalla.
        </p>
        {!emailValid && email.length > 0 && (
          <p className="hint">Escribí un email válido</p>
        )}
        <label>Dificultad</label>
        <div className="difficulty-row">
          {DIFFICULTIES.map((d) => (
            <button
              key={d.id}
              className={difficulty === d.id ? 'selected' : ''}
              onClick={() => setDifficulty(d.id)}
            >
              {d.label} <small>{d.mult}</small>
            </button>
          ))}
        </div>
        <p className="hint">
          A mayor dificultad, más puntos: se premia ganar, resistir jugadas y
          capturar piezas.
        </p>
        {personalities.length > 1 && (
          <>
            <label>Voz del robot</label>
            <div className="difficulty-row personality-row">
              {personalities.map((p) => (
                <button
                  key={p.id}
                  className={voice === p.id ? 'selected' : ''}
                  onClick={() => setPersonality(p.id)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </>
        )}
        <label>Tu tiempo (corre solo en tu turno)</label>
        <div className="difficulty-row time-row">
          {TIMES.map((t) => (
            <button
              key={t.minutes}
              className={timeMinutes === t.minutes ? 'selected' : ''}
              onClick={() => setTimeMinutes(t.minutes)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {timeMinutes > 0 && (
          <p className="hint">Si tu reloj llega a cero, gana el robot.</p>
        )}
        <button className="play-button" disabled={!valid || busy} onClick={start}>
          ▶ Jugar
        </button>
        <div className="or-divider">
          <span>o</span>
        </div>
        <button className="demo-button" disabled={busy} onClick={startSelfPlay}>
          🤖 Ver al robot jugar contra sí mismo
        </button>
        <p className="hint">
          Demostración: el robot mueve las blancas y las negras con la
          dificultad elegida. No suma al ranking.
        </p>
      </div>
    </div>
  )
}
