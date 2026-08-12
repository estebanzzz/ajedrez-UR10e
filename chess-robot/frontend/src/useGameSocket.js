import { useEffect, useRef, useState } from 'react'

// Conexión WebSocket con reconexión automática (el kiosk corre días enteros).
export function useSocket(path) {
  const [message, setMessage] = useState(null)
  const [connected, setConnected] = useState(false)
  const retryRef = useRef(null)

  useEffect(() => {
    let ws = null
    let closed = false

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss://' : 'ws://'
      ws = new WebSocket(proto + window.location.host + path)
      ws.onopen = () => setConnected(true)
      ws.onmessage = (event) => setMessage(JSON.parse(event.data))
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retryRef.current = setTimeout(connect, 1000)
      }
      ws.onerror = () => ws.close()
    }
    connect()

    return () => {
      closed = true
      clearTimeout(retryRef.current)
      if (ws) ws.close()
    }
  }, [path])

  return { message, connected }
}

export async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return response.json()
}
