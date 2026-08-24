import { useEffect, useRef, useState } from 'react'

// Cara del robot + globo de diálogo. El backend decide qué dice (status.speech);
// acá se muestra en grande y, si no hay parlante en la Pi (local_playback),
// se reproducen el efecto y la voz en el navegador del kiosk.

// Cejas: ángulo izq/der en grados (positivo = el extremo interior baja → enojo).
// eyes: apertura (1 normal, 0 cerrados, >1 abiertos de par en par).
const FACES = {
  smug: { brows: [-10, 8], eyes: 1, look: [2, 0], mouth: 'smirk' },
  gloating: { brows: [-14, -14], eyes: 1.1, look: [0, 0], mouth: 'grin' },
  bored: { brows: [0, 0], eyes: 0.45, look: [3, 2], mouth: 'flat' },
  sleepy: { brows: [4, 4], eyes: 0, look: [0, 0], mouth: 'small' },
  shocked: { brows: [-18, -18], eyes: 1.4, look: [0, -1], mouth: 'o' },
  angry: { brows: [20, 20], eyes: 0.8, look: [0, 0], mouth: 'frown' },
  sad: { brows: [-16, -16], eyes: 0.9, look: [-2, 3], mouth: 'frown' },
  sneaky: { brows: [12, -4], eyes: 0.5, look: [-4, 0], mouth: 'smirk' },
  thinking: { brows: [-6, 6], eyes: 1, look: [-4, -4], mouth: 'flat' },
}

function Eye({ cx, cy, face }) {
  const r = 11 * (face.eyes > 1 ? face.eyes : 1)
  const lid = Math.max(0, 1 - face.eyes) // fracción tapada desde arriba
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill="#fff" />
      <circle cx={cx + face.look[0]} cy={cy + face.look[1]} r={5.5} fill="#14141a" />
      <circle cx={cx + face.look[0] + 2} cy={cy + face.look[1] - 2} r={1.6} fill="#fff" />
      {lid > 0 && (
        <rect x={cx - r - 1} y={cy - r - 1} width={2 * r + 2} height={(2 * r + 2) * lid} fill="#2a2a36" />
      )}
      {face.eyes === 0 && (
        <path d={`M${cx - r} ${cy} Q${cx} ${cy + 6} ${cx + r} ${cy}`} stroke="#fff" strokeWidth="2.5" fill="none" />
      )}
      <rect className="blink" x={cx - r - 1} y={cy - r - 1} width={2 * r + 2} height={2 * r + 2} fill="#2a2a36" />
    </g>
  )
}

function Mouth({ kind, speaking }) {
  if (speaking) return <ellipse className="talking" cx="60" cy="89" rx="10" ry="7" fill="#14141a" />
  const stroke = { stroke: '#fff', strokeWidth: 3, fill: 'none', strokeLinecap: 'round' }
  switch (kind) {
    case 'grin':
      return <path d="M40 84 Q60 108 80 84 Z" fill="#14141a" stroke="#fff" strokeWidth="2" />
    case 'o':
      return <ellipse cx="60" cy="90" rx="7" ry="9" fill="#14141a" stroke="#fff" strokeWidth="2" />
    case 'small':
      return <circle cx="60" cy="90" r="3" fill="#14141a" />
    case 'frown':
      return <path d="M44 95 Q60 82 76 95" {...stroke} />
    case 'flat':
      return <path d="M48 90 L72 90" {...stroke} />
    case 'smirk':
    default:
      return <path d="M44 88 Q60 98 78 84" {...stroke} />
  }
}

export function RobotFace({ mood, speaking }) {
  const face = FACES[mood] || FACES.smug
  return (
    <svg className={`robot-face mood-${mood}`} viewBox="0 0 120 120" aria-hidden="true">
      <line x1="60" y1="4" x2="60" y2="16" stroke="#4f9cf9" strokeWidth="3" />
      <circle cx="60" cy="4" r="3.5" fill="#4f9cf9" className="antenna" />
      <rect x="10" y="16" width="100" height="94" rx="22" fill="#2a2a36" stroke="#4f9cf9" strokeWidth="3" />
      <line x1="24" y1="36" x2="46" y2="36" stroke="#fff" strokeWidth="4" strokeLinecap="round"
        transform={`rotate(${face.brows[0]} 35 36)`} />
      <line x1="74" y1="36" x2="96" y2="36" stroke="#fff" strokeWidth="4" strokeLinecap="round"
        transform={`rotate(${-face.brows[1]} 85 36)`} />
      <Eye cx={38} cy={58} face={face} />
      <Eye cx={82} cy={58} face={face} />
      <Mouth kind={face.mouth} speaking={speaking} />
      {mood === 'sleepy' && !speaking && (
        <text className="zzz" x="96" y="30" fill="#e8d44d" fontSize="14" fontWeight="700">z z z</text>
      )}
    </svg>
  )
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Resuelve cuando termina (true) o si el navegador no dejó reproducir (false).
function playUrl(url, volume, holder) {
  return new Promise((resolve) => {
    const audio = new Audio(url)
    audio.volume = volume
    holder.current = audio
    audio.onended = () => resolve(true)
    audio.onerror = () => resolve(false)
    audio.play().catch(() => resolve(false))
  })
}

export default function RobotVoice({ speech, idleMood }) {
  const [current, setCurrent] = useState(null)
  const [speaking, setSpeaking] = useState(false)
  const seqRef = useRef(null)
  const runRef = useRef(0)
  const audioRef = useRef(null)
  const hideRef = useRef(null)
  const last = speech?.last

  useEffect(() => {
    if (!last) return
    // Al conectar no se repite lo último que dijo antes de cargar la página.
    if (seqRef.current === null) {
      seqRef.current = last.seq
      return
    }
    if (last.seq === seqRef.current) return
    seqRef.current = last.seq
    const token = ++runRef.current
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    clearTimeout(hideRef.current)
    setCurrent(last)
    setSpeaking(true)
    const estimate = 1200 + last.text.length * 75
    ;(async () => {
      if (speech.local_playback) {
        await wait(estimate)
      } else {
        let played = false
        if (last.sfx_url) await playUrl(last.sfx_url, 0.7, audioRef)
        if (runRef.current !== token) return
        if (last.audio_url) played = await playUrl(last.audio_url, 1, audioRef)
        if (!played) await wait(estimate)
      }
      if (runRef.current !== token) return
      setSpeaking(false)
      hideRef.current = setTimeout(() => setCurrent(null), 2500)
    })()
  }, [last?.seq])

  if (!speech || speech.level === 0) return null
  const mood = current ? current.mood : idleMood || 'smug'
  return (
    <div className={'robot-corner' + (current ? ' talking' : '')}>
      {current && (
        <div className="speech-bubble" key={current.seq}>
          {current.text}
        </div>
      )}
      <RobotFace mood={mood} speaking={speaking} />
    </div>
  )
}
