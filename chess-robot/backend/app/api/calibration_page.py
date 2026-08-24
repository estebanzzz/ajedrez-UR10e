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
  #points-table { border-collapse: collapse; margin-top: .6rem; width: 100%; }
  #points-table th { text-align: left; color: #999; font-size: .85rem; padding: .2rem .4rem; }
  #points-table td { padding: .15rem .4rem; }
  #points-table input.coord { width: 6.5rem; font-family: ui-monospace, monospace; }
  #points-table button { padding: .3rem .6rem; font-size: .85rem; }
  #motion-table td { padding: .25rem .5rem; }
  #motion-table input { width: 6rem; }
  #warnings { color: #e6b23a; } #error { color: #e66; min-height: 1.2em; }
  .row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  .jog-wrap { display: flex; gap: 1.2rem; margin-top: .6rem; align-items: center; }
  .jog-xy { display: grid; grid-template-columns: repeat(3, 5.5rem); gap: .35rem; }
  .jog-z { display: flex; flex-direction: column; gap: .35rem; width: 6.5rem; }
  .jog-wrap button { font-size: 1.05rem; padding: .7rem 0; margin: 0; }
  .jog-wrap button:disabled { opacity: .5; }
  .jog-c { display: flex; align-items: center; justify-content: center; }
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
  <h2>Movimiento por coordenadas (jog)</h2>
  <p class="dim">Mueve el robot DE VERDAD, en pasos relativos con moveL lento y
     orientación fija. Para el teach: acércate con freedrive (grueso) o pasos
     grandes, y afiná la posición con pasos chicos. Requiere freedrive
     desactivado.</p>
  <div class="row">
    <span>Paso:</span>
    <label><input type="radio" name="step" value="20"> 20 mm</label>
    <label><input type="radio" name="step" value="5" checked> 5 mm</label>
    <label><input type="radio" name="step" value="1"> 1 mm</label>
    <label><input type="radio" name="step" value="0.2"> 0.2 mm</label>
  </div>
  <div class="jog-wrap">
    <div class="jog-xy">
      <span></span><button onclick="jog('y', 1)">Y+</button><span></span>
      <button onclick="jog('x', -1)">X−</button><span class="dim jog-c">XY</span><button onclick="jog('x', 1)">X+</button>
      <span></span><button onclick="jog('y', -1)">Y−</button><span></span>
    </div>
    <div class="jog-z">
      <button onclick="jog('z', 1)" class="ok">Z+ ↑</button>
      <button onclick="jog('z', -1)" class="warn">Z− ↓</button>
    </div>
  </div>
</section>

<section>
  <h2>Puntos de calibración</h2>
  <p class="dim">Coordenadas en <b>mm</b>, editables a mano y en el orden que
     quieras. <b>Capturar</b> toma la posición actual del TCP (posicionalo
     con jog o freedrive; la punta de la garra cerrada define el punto).
     <b>Ir</b> mueve el robot LENTO hasta 40 mm por encima del punto, para
     verificarlo. La <i>posición de espera</i> es donde queda el brazo
     mientras juega el humano — fuera del tablero, sin tapar la cámara.</p>
  <div class="row">
    <button onclick="loadPoints()">Recargar desde calibración guardada</button>
    <button onclick="save()" id="save-btn" class="warn">Guardar calibración</button>
  </div>
  <table id="points-table">
    <thead><tr><th>Punto</th><th>x (mm)</th><th>y (mm)</th><th>z (mm)</th><th></th></tr></thead>
    <tbody></tbody>
  </table>
  <div id="summary" class="mono"></div>
  <div id="warnings"></div>
</section>

<section>
  <h2>Movimiento y garra (juego)</h2>
  <p class="dim">Aplican en caliente y se guardan en la calibración.</p>
  <table id="motion-table"><tbody>
    <tr><td>Pausa de asentamiento al tomar/dejar la pieza (s)</td>
      <td><input type="number" id="mot-grip_settle_s" step="0.1"></td></tr>
    <tr><td>Apertura máxima de la garra en juego (mm)</td>
      <td><input type="number" id="mot-max_opening_mm" step="1"></td></tr>
    <tr><td>Cierre de la garra sobre la pieza (mm)<br>
      <span class="dim">diámetro de la ficha menos 2-3 mm; aplica a todas las piezas</span></td>
      <td><input type="number" id="mot-grip_opening_mm" step="0.5"></td></tr>
    <tr><td>Fuerza de agarre (0.05-1)</td>
      <td><input type="number" id="mot-grip_force" step="0.05"></td></tr>
    <tr><td>Velocidad de traslado (m/s)</td>
      <td><input type="number" id="mot-speed_travel" step="0.01"></td></tr>
    <tr><td>Velocidad de descenso/ascenso (m/s)</td>
      <td><input type="number" id="mot-speed_vertical" step="0.01"></td></tr>
  </tbody></table>
  <div id="motionMsg" class="dim"></div>
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

let jogging = false;
async function jog(axis, sign) {
  if (jogging) return;  // un paso a la vez
  const step = Number(document.querySelector('input[name="step"]:checked').value);
  jogging = true;
  document.querySelectorAll('.jog-wrap button').forEach(b => b.disabled = true);
  try {
    await api('/api/robot/jog', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({axis, delta_mm: sign * step})});
    refreshStatus();
  } catch (e) {
  } finally {
    jogging = false;
    document.querySelectorAll('.jog-wrap button').forEach(b => b.disabled = false);
  }
}

async function toggleFreedrive() {
  await api('/api/robot/freedrive', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled: !freedrive})});
  refreshStatus();
}

function renderPoints(state) {
  const tbody = document.querySelector('#points-table tbody');
  tbody.innerHTML = '';
  for (const step of state.steps) {
    const tr = document.createElement('tr');
    const captured = step.captured;
    const mm = axis => captured ? (captured[axis] * 1000).toFixed(1) : '';
    tr.innerHTML =
      `<td title="${step.instruction}">${captured ? '✔ ' : '· '}${step.title}</td>` +
      ['x', 'y', 'z'].map(a =>
        `<td><input class="coord" data-key="${step.key}" data-axis="${a}" value="${mm(a)}" size="8"></td>`
      ).join('') +
      `<td><button onclick="capturePoint('${step.key}')">Capturar</button>` +
      `<button onclick="gotoPoint('${step.key}')" ${captured ? '' : 'disabled'}>Ir</button></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('input.coord').forEach(input => {
    input.addEventListener('change', () => setPointFromRow(input.dataset.key));
  });
  $('save-btn').disabled = !state.done;
}

async function setPointFromRow(key) {
  const inputs = document.querySelectorAll(`input.coord[data-key="${key}"]`);
  const values = {};
  for (const input of inputs) {
    const v = parseFloat(input.value);
    if (!isFinite(v)) return;  // fila incompleta: esperar a que estén las 3
    values[input.dataset.axis] = v;
  }
  const state = await api('/api/calibration/point', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key, x_mm: values.x, y_mm: values.y, z_mm: values.z})});
  renderPoints(state);
}

async function capturePoint(key) {
  const state = await api('/api/calibration/capture-point', {method: 'POST',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key})});
  renderPoints(state);
}

async function gotoPoint(key) {
  if (!confirm(`El robot se va a MOVER hasta 40 mm sobre el punto "${key}". ¿Área despejada?`)) return;
  await api('/api/calibration/goto-point', {method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key, clearance_mm: 40})});
}

async function loadPoints() {
  const state = await api('/api/calibration/start', {method: 'POST'});
  $('summary').textContent = ''; $('warnings').textContent = '';
  renderPoints(state);
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

async function loadMotion() {
  try {
    const data = await api('/api/robot/motion');
    for (const [key, value] of Object.entries(data.values)) {
      const input = $('mot-' + key);
      if (!input) continue;
      input.value = value;
      if (data.ranges[key]) {
        const [lo, hi] = data.ranges[key];
        input.min = lo; input.max = hi; input.title = `rango: ${lo} – ${hi}`;
      }
    }
  } catch (e) {}
}

document.querySelectorAll('[id^="mot-"]').forEach(input => {
  input.addEventListener('change', async () => {
    const value = parseFloat(input.value);
    if (!isFinite(value)) return;
    try {
      const res = await api('/api/robot/motion', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[input.id.slice(4)]: value})});
      input.value = res.values[input.id.slice(4)];
      $('motionMsg').innerHTML = '<span class="ok-text">✔ Aplicado y guardado.</span>';
    } catch (e) { loadMotion(); }
  });
});

refreshStatus();
setInterval(refreshStatus, 700);
loadPoints();
loadMotion();
</script>
</body>
</html>
"""
