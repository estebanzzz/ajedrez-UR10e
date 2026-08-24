import { useState } from 'react'
import { api, useSocket } from './useGameSocket.js'

// Diagnóstico de sensores en vivo (mismo stream que la página /diagnostics).
function SensorMap() {
  const { message } = useSocket('/ws/sensors')
  const occupied = new Set(message ? message.squares : [])
  const files = 'abcdefgh'
  const cells = []
  for (let rank = 8; rank >= 1; rank--)
    for (let c = 0; c < 8; c++) {
      const name = files[c] + rank
      cells.push(
        <div
          key={name}
          className={'mini-square' + (occupied.has(name) ? ' on' : '')}
          title={name}
        />,
      )
    }
  return <div className="sensor-map">{cells}</div>
}

export default function OperatorPanel({ status, onClose }) {
  const [busy, setBusy] = useState(false)

  const call = async (path, body) => {
    setBusy(true)
    try {
      await api(path, body)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="operator-panel">
      <div className="operator-header">
        <h2>Panel de operador</h2>
        <button onClick={onClose}>Cerrar</button>
      </div>
      <div className="operator-grid">
        <section>
          <h3>Voz del robot</h3>
          <div className="difficulty">
            {(status?.speech?.personalities || []).map((p) => (
              <button
                key={p.id}
                disabled={busy}
                className={status?.speech?.personality === p.id ? 'selected' : ''}
                onClick={() => call('/api/speech/personality', { personality: p.id })}
              >
                Voz: {p.label}
              </button>
            ))}
          </div>
          <div className="difficulty">
            {[
              [0, 'Mudo'],
              [1, 'Comentarista (solo la partida)'],
              [2, 'Provocador (distrae y se burla)'],
            ].map(([level, label]) => (
              <button
                key={level}
                disabled={busy}
                className={status?.speech?.level === level ? 'selected' : ''}
                onClick={() => call('/api/speech/level', { level })}
              >
                {label}
              </button>
            ))}
          </div>
          <button disabled={busy} onClick={() => call('/api/speech/test', { event: 'filler' })}>
            Probar voz
          </button>
          <button disabled={busy} onClick={() => call('/api/speech/test', { event: 'robot_wins' })}>
            Probar voz + efecto
          </button>
          <small className="hint">
            {status?.speech?.local_playback
              ? 'Audio por el parlante del backend.'
              : 'Audio por el navegador del kiosk.'}
          </small>
        </section>
        <section>
          <h3>Partida</h3>
          <button disabled={busy} onClick={() => call('/api/game/new', { human_color: 'white' })}>
            Nueva partida (humano blancas)
          </button>
          <button disabled={busy} onClick={() => call('/api/game/new', { human_color: 'black' })}>
            Nueva partida (humano negras)
          </button>
          <button disabled={busy} onClick={() => call('/api/game/new', { mode: 'self_play' })}>
            Demo: robot contra sí mismo
          </button>
          <button disabled={busy} onClick={() => call('/api/game/stop', {})}>
            Detener partida
          </button>
          <button disabled={busy} onClick={() => call('/api/game/confirm', {})}>
            Confirmar jugada (botón)
          </button>
          <button disabled={busy} onClick={() => call('/api/game/resync-check', {})}>
            Verificar resync
          </button>
        </section>
        <section>
          <h3>Sensores en vivo</h3>
          <SensorMap />
          {status?.mismatched_squares?.length > 0 && (
            <p className="conflict-list">
              En conflicto: {status.mismatched_squares.join(', ')}
            </p>
          )}
        </section>
        <section>
          <h3>Estado</h3>
          <pre className="status-dump">{JSON.stringify(status, null, 2)}</pre>
        </section>
      </div>
    </div>
  )
}
