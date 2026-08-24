"""Página de calibración de visión (tablero y piezas).

HTML autocontenido servido en ``/calibracion``: vista cruda de la cámara para
marcar las 4 esquinas del área de juego, captura guiada del entrenamiento
auto-etiquetado (tablero vacío + posición inicial) y vista del clasificador
en vivo. Reemplaza a los CLIs de OpenCV (app.vision.calibrate / train), que
siguen disponibles como alternativa de escritorio.
"""

VISION_CALIBRATION_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calibración de visión — Ajedrez</title>
<style>
  body { font-family: system-ui, sans-serif; background: #1e1e24; color: #eee;
         max-width: 980px; margin: 0 auto; padding: 1.5rem; }
  h1 { font-size: 1.2rem; } h2 { font-size: 1rem; margin: .2rem 0 .6rem; }
  section { background: #26262e; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
  button { background: #3a6ea5; color: #fff; border: 0; border-radius: 6px;
           padding: .55rem 1rem; font-size: .95rem; cursor: pointer; margin: .15rem; }
  button:disabled { background: #444; color: #888; cursor: default; }
  button.warn { background: #a3592a; } button.ok { background: #2f7d4f; }
  .ok-text { color: #6c6; } .bad-text { color: #e66; } .dim { color: #999; }
  .warn-text { color: #e6b23a; }
  .row { display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  .imgwrap { position: relative; display: inline-block; max-width: 100%; }
  .imgwrap img { max-width: 100%; display: block; border-radius: 6px;
                 border: 2px solid #3a3a46; background: #000; }
  .imgwrap canvas { position: absolute; inset: 0; cursor: crosshair; }
  #trainMsg, #cornersMsg { min-height: 1.4em; margin-top: .4rem; }
  .cols { display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; }
  .cols > div { flex: 1 1 420px; }
  #tuning-table { border-collapse: collapse; margin-top: .4rem; }
  #tuning-table td { padding: .35rem .6rem; vertical-align: top; }
  #tuning-table input { background: #1a1a20; color: #eee; border: 1px solid #555;
                        border-radius: 6px; padding: .4rem; width: 6.5rem;
                        font-size: .95rem; }
  #tuningMsg { min-height: 1.4em; margin-top: .4rem; }
</style>
</head>
<body>
<h1>Calibración de visión — tablero y piezas</h1>
<div id="error" class="bad-text"></div>

<section>
  <h2>Paso 1 — Esquinas del tablero</h2>
  <p class="dim">Haz click sobre el video en las 4 esquinas exteriores del área
  de juego (los vértices del cuadrado 8×8, no del marco), en el orden
  <b>a1 → h1 → h8 → a8</b>. La esquina "a1" es la que queda del lado de las
  piezas blancas.</p>
  <div class="imgwrap">
    <img id="raw" src="/api/vision/raw-stream" alt="cámara">
    <canvas id="overlay"></canvas>
  </div>
  <div class="row">
    <button id="resetPts">Reiniciar puntos</button>
    <button id="saveCorners" class="ok" disabled>Guardar esquinas</button>
    <span id="cornersMsg" class="dim"></span>
  </div>
</section>

<section>
  <h2>Paso 2 — Entrenamiento de piezas (auto-etiquetado)</h2>
  <p class="dim">1) Vacía el tablero por completo y captura. 2) Coloca la
  posición inicial (blancas en filas 1-2 desde la esquina a1) y captura.
  Nada se etiqueta a mano. Para robustez, repite las capturas bajo cada
  condición de luz realista. Durante la captura no toques el tablero.</p>
  <div class="row">
    <button id="capEmpty">&#128247; Capturar tablero VACÍO</button>
    <button id="capStart">&#128247; Capturar POSICIÓN INICIAL</button>
    <button id="resetTrain" class="warn">Reiniciar entrenamiento</button>
  </div>
  <div id="trainMsg"></div>
</section>

<section>
  <h2>Ajustes de detección</h2>
  <p class="dim">Perillas para afinar la confiabilidad. Los cambios aplican
  <b>en vivo</b> (verificá en la vista de abajo) y se guardan solos. Los
  marcados <span class="warn-text">↻</span> cambian cómo se miden las
  características: tras tocarlos hay que <b>re-entrenar</b> (Paso 2).</p>
  <table id="tuning-table">
    <tbody>
    <tr><td><b>Sensibilidad de ocupación</b><br><span class="dim">&lt;1 = más sensible
      (rescata fichas camufladas, riesgo de fichas fantasma) · &gt;1 = más
      conservador</span></td>
      <td><input type="number" id="tun-threshold_scale" step="0.05"></td></tr>
    <tr><td><b>Umbral de oclusión</b><br><span class="dim">bajo = retiene la lectura
      ante cualquier movimiento · alto = casi nunca retiene</span></td>
      <td><input type="number" id="tun-motion_threshold" step="0.5"></td></tr>
    <tr><td><b>Lecturas estables (debounce)</b><br><span class="dim">más = lecturas
      más firmes pero más lentas de reflejarse</span></td>
      <td><input type="number" id="tun-stable_reads" step="1"></td></tr>
    <tr><td><b>Radio del disco central</b> <span class="warn-text">↻</span><br>
      <span class="dim">fracción de la casilla que se analiza al centro; subilo si
      las fichas llenan la casilla, bajalo si quedan descentradas</span></td>
      <td><input type="number" id="tun-disc_radius" step="0.01"></td></tr>
    </tbody>
  </table>
  <div id="tuningMsg"></div>
</section>

<section>
  <h2>Vista del clasificador en vivo</h2>
  <div class="cols">
    <div>
      <div class="imgwrap"><img id="classified" src="/api/vision/stream" alt="clasificador"></div>
      <p class="dim">círculo verde = pieza blanca · magenta = negra ·
      punto azul = vacía · anillo rojo = lectura dudosa · "OCLUIDO" = lectura
      retenida por movimiento</p>
    </div>
  </div>
</section>

<script>
const CORNER_NAMES = ['a1', 'h1', 'h8', 'a8'];
let points = [];  // en píxeles del sensor (coordenadas naturales del frame)

const raw = document.getElementById('raw');
const overlay = document.getElementById('overlay');
const errorBox = document.getElementById('error');
const cornersMsg = document.getElementById('cornersMsg');
const trainMsg = document.getElementById('trainMsg');
const saveBtn = document.getElementById('saveCorners');

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

function syncOverlay() {
  const r = raw.getBoundingClientRect();
  overlay.width = r.width; overlay.height = r.height;
  const ctx = overlay.getContext('2d');
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  if (!raw.naturalWidth) return;
  const sx = r.width / raw.naturalWidth, sy = r.height / raw.naturalHeight;
  points.forEach(([x, y], i) => {
    const cx = x * sx, cy = y * sy;
    ctx.fillStyle = '#e63c3c';
    ctx.beginPath(); ctx.arc(cx, cy, 6, 0, 7); ctx.fill();
    ctx.font = 'bold 15px system-ui'; ctx.fillStyle = '#ffd24d';
    ctx.fillText(CORNER_NAMES[i], cx + 9, cy - 9);
  });
  if (points.length === 4) {
    ctx.strokeStyle = '#6c6'; ctx.lineWidth = 1.5;
    ctx.beginPath();
    points.forEach(([x, y], i) => i ? ctx.lineTo(x * sx, y * sy) : ctx.moveTo(x * sx, y * sy));
    ctx.closePath(); ctx.stroke();
  }
}

overlay.addEventListener('click', (e) => {
  if (points.length >= 4 || !raw.naturalWidth) return;
  const r = raw.getBoundingClientRect();
  const x = (e.clientX - r.left) * raw.naturalWidth / r.width;
  const y = (e.clientY - r.top) * raw.naturalHeight / r.height;
  points.push([x, y]);
  cornersMsg.textContent = points.length < 4
    ? `Marcada ${CORNER_NAMES[points.length - 1]} — ahora click en ${CORNER_NAMES[points.length]}`
    : 'Cuatro esquinas marcadas: revisa el contorno y guarda.';
  saveBtn.disabled = points.length !== 4;
  syncOverlay();
});

document.getElementById('resetPts').onclick = () => {
  points = []; saveBtn.disabled = true;
  cornersMsg.textContent = ''; syncOverlay();
};

saveBtn.onclick = async () => {
  saveBtn.disabled = true;
  try {
    await api('/api/vision/corners', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ corners: points }),
    });
    cornersMsg.innerHTML = '<span class="ok-text">Esquinas guardadas.</span> ' +
      '<span class="warn-text">El modelo anterior quedó obsoleto: haz el Paso 2.</span>';
  } catch (err) {
    cornersMsg.innerHTML = `<span class="bad-text">${err.message}</span>`;
    saveBtn.disabled = false;
  }
};

function showTrainResult(data) {
  let html = `Capturas: ${data.empty_frames} de tablero vacío, ` +
             `${data.start_frames} de posición inicial.`;
  if (data.trained) {
    const s = data.stats;
    html += s.separated
      ? ` <span class="ok-text">&#10004; Modelo entrenado y aplicado — separación limpia (×${s.separation.toFixed(1)}).</span>`
      : ' <span class="warn-text">&#9888; Modelo entrenado pero las clases se solapan: mejora la iluminación y recaptura.</span>';
    html += ' Verifica en la vista del clasificador de abajo.';
  } else {
    html += ' <span class="dim">Falta la otra tanda para entrenar.</span>';
  }
  trainMsg.innerHTML = html;
}

async function capture(phase, btn) {
  const buttons = [capEmpty, capStart, resetTrain];
  buttons.forEach(b => b.disabled = true);
  trainMsg.innerHTML = '<span class="warn-text">Capturando… no toques el tablero.</span>';
  try {
    showTrainResult(await api('/api/vision/train/' + phase, { method: 'POST' }));
  } catch (err) {
    trainMsg.innerHTML = `<span class="bad-text">${err.message}</span>`;
  } finally {
    buttons.forEach(b => b.disabled = false);
  }
}

const capEmpty = document.getElementById('capEmpty');
const capStart = document.getElementById('capStart');
const resetTrain = document.getElementById('resetTrain');
capEmpty.onclick = () => capture('empty', capEmpty);
capStart.onclick = () => capture('start', capStart);
resetTrain.onclick = async () => {
  try {
    await api('/api/vision/train/reset', { method: 'POST' });
    trainMsg.innerHTML = '<span class="dim">Sesión de entrenamiento reiniciada.</span>';
  } catch (err) {
    trainMsg.innerHTML = `<span class="bad-text">${err.message}</span>`;
  }
};

// ------------------------------------------------- ajustes de detección
const tuningMsg = document.getElementById('tuningMsg');

async function loadTuning() {
  try {
    const data = await api('/api/vision/tuning');
    for (const [key, value] of Object.entries(data.values)) {
      const input = document.getElementById('tun-' + key);
      if (!input) continue;
      input.value = value;
      const [lo, hi] = data.ranges[key];
      input.min = lo; input.max = hi;
      input.title = `rango: ${lo} – ${hi}`;
    }
  } catch (err) {
    tuningMsg.innerHTML = `<span class="dim">${err.message}</span>`;
  }
}

document.querySelectorAll('[id^="tun-"]').forEach(input => {
  input.addEventListener('change', async () => {
    const key = input.id.slice(4);
    const value = parseFloat(input.value);
    if (!isFinite(value)) return;
    try {
      const res = await api('/api/vision/tuning', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({[key]: value})});
      input.value = res.values[key];
      tuningMsg.innerHTML = res.retrain_required
        ? '<span class="warn-text">&#8635; Aplicado — este parámetro requiere RE-ENTRENAR (Paso 2) para que el modelo sea consistente.</span>'
        : '<span class="ok-text">&#10004; Aplicado en vivo y guardado.</span>';
    } catch (err) {
      tuningMsg.innerHTML = `<span class="bad-text">${err.message}</span>`;
      loadTuning();
    }
  });
});
loadTuning();

new ResizeObserver(syncOverlay).observe(raw);
setInterval(syncOverlay, 1000);  // por si el stream tarda en dar naturalWidth
raw.addEventListener('error', () => {
  errorBox.textContent = 'Cámara no disponible: el backend debe correr con CHESS_DRIVER=vision y la cámara conectada.';
});
</script>
</body>
</html>
"""
