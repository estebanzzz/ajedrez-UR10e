"""Página del asistente de calibración del robot (teach por freedrive).

HTML autocontenido servido en ``/calibration``: estado del robot en vivo,
control de freedrive, captura guiada de puntos y prueba de posicionamiento.
"""

CALIBRATION_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calibración del robot — Ajedrez</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1e1e24; color: #eee;
         max-width: 720px; margin: 0 auto; padding: 1.5rem; }
  h1 { font-size: 1.2rem; } h2 { font-size: 1rem; margin: 1.2rem 0 .5rem; }
  section { background: #26262e; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
  button { background: #3a6ea5; color: #fff; border: 0; border-radius: 6px;
           padding: .55rem 1rem; font-size: .95rem; cursor: pointer; margin: .15rem; }
  button:disabled { background: #444; color: #888; cursor: default; }
  button.warn { background: #a3592a; } button.ok { background: #2f7d4f; }
  input, select { background: #1a1a20; color: #eee; border: 1px solid #555;
                  border-radius: 6px; padding: .45rem; font-size: .95rem; }
  .mono { font-family: ui-monospace, monospace; }
  .ok-text { color: #6c6; } .bad-text { color: #e66; } .dim { color: #999; }
  ul#steps { list-style: none; padding: 0; margin: .5rem 0; }
  ul#steps li { padding: .4rem .6rem; border-left: 3px solid #444; margin: .2rem 0; }
  ul#steps li.done { border-color: #2f7d4f; color: #9c9; }
  ul#steps li.current { border-color: #e6b23a; background: #2e2a20; }
  #instruction { background: #2e2a20; border: 1px solid #e6b23a55; padding: .8rem;
                 border-radius: 6px; margin: .5rem 0; }
  #warnings { color: #e6b23a; } #error { color: #e66; min-height: 1.2em; }
  .row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
</style>
</head>
<body>
<h1>Calibración del robot</h1>
<div id="error"></div>

<section>
  <h2>Estado del robot</h2>
  <div class="row">
    <span>Conexión: <b id="conn" class="bad-text">…</b></span>
    <span>Freedrive: <b id="fd">…</b></span>
    <span>Garra: <b id="grip">…</b></span>
  </div>
  <p class="mono" id="pose">TCP: …</p>
  <button id="fd-btn" onclick="toggleFreedrive()">Freedrive</button>
</section>

<section>
  <h2>Asistente de calibración</h2>
  <p class="dim">Activá el freedrive, llevá el TCP al punto indicado y capturá.
     La punta de la garra (cerrada) define el punto.</p>
  <div class="row">
    <button onclick="wizard('start')">Iniciar / reiniciar</button>
    <button onclick="wizard('capture')" id="capture-btn" class="ok">Capturar punto</button>
    <button onclick="wizard('back')">Deshacer último</button>
    <button onclick="save()" id="save-btn" class="warn">Guardar calibración</button>
  </div>
  <div id="instruction" hidden></div>
  <ul id="steps"></ul>
  <div id="summary" class="mono"></div>
  <div id="warnings"></div>
</section>

<section>
  <h2>Prueba de posicionamiento</h2>
  <p class="dim">Mueve el TCP LENTO hasta la altura segura sobre la casilla
     (requiere calibración guardada y freedrive desactivado).</p>
  <div class="row">
    <label>Casilla <input id="square" value="e4" size="3" maxlength="2"></label>
    <label>Altura sobre la casilla (mm) <input id="clearance" type="number" value="80" size="4"></label>
    <button onclick="goToSquare()">Ir a la casilla</button>
  </div>
  <div class="row" style="margin-top:.5rem">
    <label>Garra: apertura (mm) <input id="opening" type="number" value="50" size="4"></label>
    <button onclick="gripper()">Mover garra</button>
  </div>
</section>

<script>
const $ = id => document.getElementById(id);
let freedrive = false;

function showError(e) { $('error').textContent = e ? ('⚠ ' + e) : ''; }

async function api(path, opts) {
  showError('');
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { showError(data.detail || res.statusText); throw new Error(); }
  return data;
}

async function refreshStatus() {
  try {
    const s = await fetch('/api/robot/status').then(r => r.json());
    $('conn').textContent = s.connected ? (s.simulated ? 'simulado' : 'conectado') : 'SIN CONEXIÓN';
    $('conn').className = s.connected ? 'ok-text' : 'bad-text';
    freedrive = !!s.freedrive;
    $('fd').textContent = freedrive ? 'ACTIVO' : 'inactivo';
    $('fd').className = freedrive ? 'ok-text' : '';
    $('fd-btn').textContent = freedrive ? 'Desactivar freedrive' : 'Activar freedrive';
    const g = s.gripper || {};
    $('grip').textContent = !g.connected ? 'no conectada'
      : (g.active ? `activa (${g.opening_mm ?? '?'} mm)` : 'conectada, sin activar');
    $('grip').className = g.connected ? 'ok-text' : 'dim';
    if (s.tcp_pose) {
      const [x, y, z] = s.tcp_pose;
      $('pose').textContent = `TCP: x ${(x*1000).toFixed(1)}  y ${(y*1000).toFixed(1)}  z ${(z*1000).toFixed(1)} mm`;
    }
  } catch (e) { $('conn').textContent = 'backend sin respuesta'; $('conn').className = 'bad-text'; }
}

async function toggleFreedrive() {
  await api('/api/robot/freedrive', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled: !freedrive})});
  refreshStatus();
}

function renderWizard(state) {
  const list = $('steps'); list.innerHTML = '';
  let currentInstruction = null;
  for (const step of state.steps) {
    const li = document.createElement('li');
    const captured = step.captured;
    li.textContent = (captured ? '✔ ' : '· ') + step.title +
      (captured ? `  (${(captured.x*1000).toFixed(1)}, ${(captured.y*1000).toFixed(1)}, ${(captured.z*1000).toFixed(1)}) mm` : '');
    if (captured) li.className = 'done';
    if (step.key === state.current) { li.className = 'current'; currentInstruction = step.instruction; }
    list.appendChild(li);
  }
  $('instruction').hidden = !currentInstruction && !state.done;
  $('instruction').textContent = state.done
    ? '✔ Todos los puntos capturados. Revisá la lista y guardá.' : (currentInstruction || '');
  $('capture-btn').disabled = state.done;
  $('save-btn').disabled = !state.done;
}

async function wizard(action) {
  const state = await api('/api/calibration/' + action, {method: 'POST'});
  $('summary').textContent = ''; $('warnings').textContent = '';
  renderWizard(state);
}

async function loadWizard() {
  try {
    const state = await fetch('/api/calibration/state').then(r => r.ok ? r.json() : null);
    if (state && state.steps) renderWizard(state);
    else { $('capture-btn').disabled = true; $('save-btn').disabled = true; }
  } catch (e) {}
}

async function save() {
  const result = await api('/api/calibration/save', {method: 'POST'});
  const s = result.summary || {};
  let text = 'Guardado en ' + result.path;
  if (s.board) text += ` — casilla ${s.board.square_size_mm} mm`;
  $('summary').textContent = text;
  $('warnings').innerHTML = (s.warnings || []).map(w => '⚠ ' + w).join('<br>');
  if (result.note) $('warnings').innerHTML += '<br>ℹ ' + result.note;
}

async function goToSquare() {
  const square = $('square').value.trim().toLowerCase();
  if (!confirm(`El robot se va a MOVER hasta ${square}. ¿Área despejada?`)) return;
  await api('/api/calibration/goto', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({square, clearance_mm: Number($('clearance').value)})});
}

async function gripper() {
  await api('/api/robot/gripper', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({opening_mm: Number($('opening').value)})});
  refreshStatus();
}

refreshStatus();
setInterval(refreshStatus, 700);
loadWizard();
</script>
</body>
</html>
"""
