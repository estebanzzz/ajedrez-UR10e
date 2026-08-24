import { useEffect, useState } from 'react'

// Mismos destinos que el menú del backend (app/api/nav.py): las páginas de
// herramientas son HTML servido por FastAPI, así que se navega con href
// normales (recarga completa, no hay router).
const ITEMS = [
  { href: '/ui', icon: '♞', label: 'Partida', hint: 'UI de exposición' },
  { href: '/calibracion', icon: '📷', label: 'Visión', hint: 'Esquinas y entrenamiento' },
  { href: '/calibration', icon: '🦾', label: 'Robot', hint: 'Teach de puntos' },
  { href: '/', icon: '⬛', label: 'Sensores', hint: 'Diagnóstico del tablero' },
  { href: '/docs', icon: '⚙', label: 'API', hint: 'Documentación REST' },
]

// En el kiosk el menú arranca plegado (solo el botón ☰) para no distraer al
// público; queda desplegado si el operador lo dejó así en otra página.
export default function SideNav({ active = '/ui' }) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem('chessNavCollapsed') !== '0',
  )

  useEffect(() => {
    document.documentElement.classList.toggle('nav-collapsed', collapsed)
    localStorage.setItem('chessNavCollapsed', collapsed ? '1' : '0')
  }, [collapsed])

  return (
    <aside id="sidenav">
      <div className="nav-head">
        <button
          className="nav-toggle"
          title="Plegar/desplegar menú"
          onClick={() => setCollapsed((c) => !c)}
        >
          ☰
        </button>
        <span className="nav-title">Robot Ajedrecista</span>
      </div>
      <nav>
        {ITEMS.map((item) => (
          <a
            key={item.href}
            href={item.href}
            className={item.href === active ? 'active' : undefined}
            title={`${item.label} — ${item.hint}`}
          >
            <span className="nav-icon">{item.icon}</span>
            <span className="nav-label">
              <b>{item.label}</b>
              <span>{item.hint}</span>
            </span>
          </a>
        ))}
      </nav>
    </aside>
  )
}
