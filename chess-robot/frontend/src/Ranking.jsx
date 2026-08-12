import { useEffect, useState } from 'react'

const RESULT_ICONS = { win: '🏆', draw: '½', loss: '' }

export default function Ranking({ refreshKey }) {
  const [ranking, setRanking] = useState({ today: [], alltime: [] })

  useEffect(() => {
    let cancelled = false
    fetch('/api/ranking?limit=10')
      .then((r) => r.json())
      .then((data) => { if (!cancelled) setRanking(data) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [refreshKey])

  const best = ranking.alltime[0]

  return (
    <aside className="ranking">
      <section>
        <h3>🏅 Ranking de hoy</h3>
        {ranking.today.length === 0 && (
          <p className="ranking-empty">Todavía no hay partidas hoy.<br />¡Sé el primero!</p>
        )}
        <ol>
          {ranking.today.map((row, i) => (
            <li key={i} className={i === 0 ? 'leader' : ''}>
              <span className="rank-pos">{i + 1}</span>
              <span className="rank-name">{row.name} {RESULT_ICONS[row.result]}</span>
              <span className="rank-score">{row.score}</span>
            </li>
          ))}
        </ol>
        {ranking.today.length > 0 && (
          <p className="prize-note">El 1° de hoy gana el premio del día 🎁</p>
        )}
      </section>
      {best && (
        <section className="alltime">
          <h3>Récord de la feria</h3>
          <p className="record">
            {best.name} — <strong>{best.score}</strong> pts
          </p>
        </section>
      )}
    </aside>
  )
}
