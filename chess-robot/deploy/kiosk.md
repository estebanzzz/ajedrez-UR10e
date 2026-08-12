# Kiosk en la Raspberry Pi (pantalla de exposición)

La UI se sirve en `http://localhost:8000/ui`. Para que la Pi arranque directo
en pantalla completa con Chromium:

## Raspberry Pi OS Bookworm (Wayland/labwc — default en Pi 5)

Crear `~/.config/labwc/autostart` (o agregar al existente):

```sh
chromium-browser --kiosk --noerrdialogs --disable-infobars \
  --check-for-update-interval=31536000 \
  http://localhost:8000/ui &
```

## Recomendaciones para exposición

- Desactivar el blanking de pantalla: `raspi-config` → Display → Screen Blanking → Off.
- Ocultar el cursor: instalar `interception-tools` o usar `--force-show-cursor=false`
  (en labwc: `unclutter` no aplica; alternativa: cursor transparente en el tema).
- El panel de operador se abre tocando 5 veces seguidas el título "Robot
  Ajedrecista" (no hay ningún control visible para el público).
- Ante cualquier problema: `sudo systemctl restart chess-backend` y F5 en el
  kiosk (o reiniciar la Pi; todo arranca solo).

## Instalación completa desde cero

```sh
# Backend
cd ~/chess-robot/backend
python -m venv .venv
.venv/bin/pip install -r requirements-pi.txt
sudo apt install stockfish

# Frontend (build en la Pi o copiar dist/ ya buildeado)
cd ~/chess-robot/frontend
npm install && npm run build

# Servicio
sudo cp ~/chess-robot/deploy/chess-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chess-backend
```

Ajustar en `chess-backend.service` la IP del UR10e (`CHESS_ROBOT_HOST`) y en
`backend/config/calibration.json` la calibración enseñada.
