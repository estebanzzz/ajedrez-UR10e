# Kiosk en la Raspberry Pi (pantalla de exposición)

La UI se sirve en `http://localhost:8000/ui`. Para que la Pi arranque directo
en pantalla completa con Chromium:

## Raspberry Pi OS (Wayland/labwc — default en Pi 5)

`~/.config/labwc/autostart`:

```sh
# Espera al backend y abre Chromium fullscreen.
(until curl -sf -o /dev/null http://localhost:8000/api/status; do sleep 2; done
chromium --ozone-platform=wayland --kiosk --noerrdialogs --disable-infobars \
  --disable-features=Translate --disable-session-crashed-bubble \
  --password-store=basic --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required \
  http://localhost:8000/ui) &
```

- `--autoplay-policy=no-user-gesture-required`: la voz del robot se reproduce
  desde el navegador (ver `docs/voice.md`); sin este flag Chromium bloquea el
  audio hasta que alguien toque la pantalla y el robot queda mudo. No hace
  falta si el audio sale por el backend (`CHESS_ROBOT_SPEECH_PLAYER`).

Por qué cada flag no obvio:

- `--disable-features=Translate`: **no alcanza** por sí solo — Chromium sigue
  mostrando el globo de "traducir esta página" encima de la UI. Lo que sí lo
  apaga es la política administrada (ver abajo); el flag queda por prolijidad.
- `--password-store=basic`: el kiosk no guarda credenciales; sin esto Chromium
  puede pedir desbloquear el keyring de GNOME y dejar un diálogo de contraseña
  sobre la pantalla.
- `--ozone-platform=wayland`: solo hace falta si se relanza Chromium a mano
  desde SSH (fuera de la sesión gráfica intenta X11 y muere con
  `Missing X server or $DISPLAY`). Dentro del autostart es redundante pero
  inofensivo.

### Política administrada de Chromium

`/etc/chromium/policies/managed/robot-ajedrez.json` — es lo que realmente
silencia el globo de traducción y el resto de las burbujas que Chromium le
mostraría al público:

```json
{
  "TranslateEnabled": false,
  "BrowserSignin": 0,
  "PasswordManagerEnabled": false,
  "MetricsReportingEnabled": false,
  "DefaultBrowserSettingEnabled": false
}
```

Se aplica al reiniciar Chromium y sobrevive a un borrado del perfil.

### Fuente de emoji (obligatoria)

La UI usa emoji en el menú lateral (📷 🦾), el ranking (🥇) y el botón de
cámara. Una imagen limpia de Raspberry Pi OS **no** trae fuente de emoji y
todos salen como cuadraditos:

```sh
sudo apt install -y fonts-noto-color-emoji
```

Hay que reiniciar Chromium después de instalarla (o reiniciar la Pi).

## Recomendaciones para exposición

- Desactivar el blanking de pantalla: `raspi-config` → Display → Screen Blanking → Off.
- El menú lateral arranca plegado en `/ui`; el operador lo despliega con ☰ para
  saltar a calibración o diagnóstico, y el público no ve nada.
- El panel de operador se abre tocando 5 veces seguidas el título "Robot
  Ajedrecista" (no hay ningún control visible para el público).
- Ante cualquier problema: `sudo systemctl restart chess-backend` y F5 en el
  kiosk (o reiniciar la Pi; todo arranca solo).

## Instalación completa desde cero

Lo más simple es correr el deploy desde la PC, que copia el proyecto (con el
frontend ya buildeado) y deja venv + dependencias + servicio:

```powershell
.\deploy\deploy_to_pi.ps1 -PiHost <ip> -Install -RobotHost <ip-del-UR>
```

A mano, en la Pi:

```sh
sudo apt install -y stockfish fonts-noto-color-emoji
cd ~/robot-ajedrez/chess-robot/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-pi.txt
sudo cp ../deploy/chess-backend.service /etc/systemd/system/   # ajustar rutas/usuario
sudo systemctl daemon-reload && sudo systemctl enable --now chess-backend
```

Notas de la instalación real (Pi 5, aarch64, Python 3.13):

- **No hace falta Node en la Pi**: el frontend se buildea en la PC y se copia
  `frontend/dist/` ya armado.
- `numpy`, `opencv-python` y `pypylon` tienen wheels aarch64 — instalan directo.
- `ur_rtde` **no** tiene wheel aarch64: se compila desde fuente. Requiere
  `build-essential cmake libboost-system-dev libboost-thread-dev`. En la Pi
  conviene sacar el build del tmpfs y bajar el paralelismo — `/tmp` es RAM, así
  que compilar ahí le roba memoria al sistema (4 GB, sin swap) y además se
  pierde todo si la Pi se reinicia:

  ```sh
  export TMPDIR=$HOME/.build-tmp CMAKE_BUILD_PARALLEL_LEVEL=3
  .venv/bin/pip install ur_rtde==1.6.5
  ```
- `stockfish` queda en `/usr/games`, que systemd no tiene en el `PATH` por
  defecto: por eso el unit define `Environment=PATH=/usr/games:...`.

Ajustar en `chess-backend.service` la IP del UR10e (`CHESS_ROBOT_HOST`) y en
`backend/config/calibration.json` la calibración enseñada.
