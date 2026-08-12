import { useState } from 'react'
import { api } from './useGameSocket.js'

const DIFFICULTIES = [
  { id: 'principiante', label: 'Principiante', mult: '×1' },
  { id: 'intermedio', label: 'Intermedio', mult: '×1.5' },
  { id: 'avanzado', label: 'Avanzado', mult: '×2' },
  { id: 'maximo', label: 'Máximo', mult: '×3' },
]

export default function StartScreen({ onStarted }) {
  const [name, setName] = useState('')
  const [difficulty, setDifficulty] = useState('intermedio')
  const [busy, setBusy] = useState(false)
  const valid = name.trim().split(/\s+/).length >= 2 // nombre y apellido

  const start = async () => {
    if (!valid || busy) return
    setBusy(true)
    try {
      await api('/api/game/new', {
        human_color: 'white',
        player_name: name.trim(),
        difficulty,
      })
      onStarted?.()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay">
      <div className="start-card">
        <h2>¿Te animás a jugarle al robot?</h2>
        <label htmlFor="player-name">Nombre y apellido</label>
        <input
          id="player-name"
          autoFocus
          value={name}
          maxLength={40}
          placeholder="Ej: Ana García"
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && start()}
        />
        {!valid && name.length > 0 && (
          <p className="hint">Escribí nombre y apellido para entrar al ranking</p>
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
        <button className="play-button" disabled={!valid || busy} onClick={start}>
          ▶ Jugar
        </button>
      </div>
    </div>
  )
}
