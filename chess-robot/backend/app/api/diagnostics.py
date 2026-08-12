"""Página de diagnóstico visual de sensores (Fase 2).

HTML autocontenido servido por el backend: muestra el mapa de ocupación en
vivo vía WebSocket. En modo mock permite togglear casillas con click para
simular piezas.
"""

DIAGNOSTICS_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Diagnóstico de sensores — Tablero</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1e1e24; color: #eee;
         display: flex; flex-direction: column; align-items: center; gap: 1rem;
         padding: 1.5rem; }
  h1 { font-size: 1.2rem; margin: 0; }
  #board { display: grid; grid-template-columns: repeat(8, 56px);
           grid-template-rows: repeat(8, 56px); border: 3px solid #555; }
  .sq { display: flex; align-items: center; justify-content: center;
        cursor: pointer; user-select: none; font-size: .7rem; color: #0008; }
  .light { background: #d8cfc0; } .dark { background: #7a8a63; }
  .sq .dot { width: 34px; height: 34px; border-radius: 50%; background: transparent;
             transition: background .1s; }
  .sq.occupied .dot { background: #e33; box-shadow: 0 0 10px #e33a; }
  #info { font-size: .9rem; color: #aaa; }
  #status.ok { color: #6c6; } #status.bad { color: #e66; }
</style>
</head>
<body>
<h1>Diagnóstico de sensores del tablero</h1>
<div id="info">WebSocket: <span id="status" class="bad">conectando…</span>
  — casillas ocupadas: <span id="count">0</span>
  — <span id="hint"></span></div>
<div id="board"></div>
<script>
const board = document.getElementById('board');
const files = 'abcdefgh';
const cells = {};
for (let rank = 8; rank >= 1; rank--) {
  for (let f = 0; f < 8; f++) {
    const name = files[f] + rank;
    const div = document.createElement('div');
    div.className = 'sq ' + ((rank + f) % 2 ? 'light' : 'dark');
    div.title = name;
    div.innerHTML = '<div class="dot"></div>';
    div.onclick = () => fetch('/api/mock/toggle/' + name, {method: 'POST'});
    board.appendChild(div);
    cells[name] = div;
  }
}
fetch('/api/status').then(r => r.json()).then(s => {
  document.getElementById('hint').textContent = s.mock
    ? 'modo mock: click para simular piezas' : 'driver: ' + s.driver;
});
function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://')
                           + location.host + '/ws/sensors');
  const status = document.getElementById('status');
  ws.onopen = () => { status.textContent = 'conectado'; status.className = 'ok'; };
  ws.onclose = () => { status.textContent = 'desconectado'; status.className = 'bad';
                       setTimeout(connect, 1000); };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    const occupied = new Set(msg.squares);
    for (const [name, div] of Object.entries(cells))
      div.classList.toggle('occupied', occupied.has(name));
    document.getElementById('count').textContent = msg.squares.length;
  };
}
connect();
</script>
</body>
</html>
"""
