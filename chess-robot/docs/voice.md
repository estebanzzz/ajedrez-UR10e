# Voz y personalidad del robot

El robot habla durante la partida: comenta jugadas, se burla, distrae al rival
mientras piensa y reacciona al resultado. Todo sale de frases **pregrabadas**
con ElevenLabs, así que en la Pi no hace falta internet ni ninguna API: solo
archivos MP3 y un reproductor.

## Piezas

| Dónde | Qué hace |
| --- | --- |
| `backend/app/personality/lines.es.json` | Banco de frases por evento (110) y lista de efectos de sonido (18). Editable sin tocar código. |
| `backend/app/personality/voice_config.json` | Voz, modelo y ajustes de ElevenLabs. La API key **no** va acá. |
| `backend/scripts/build_voice.py` | Genera los audios que falten en `backend/voice/` y escribe `manifest.json`. Corre en la PC. |
| `backend/app/personality/commentator.py` | Reglas: nivel, prioridad, enfriamiento, no repetir. Lógica pura, sin hilos. |
| `backend/app/personality/heckler.py` | Disparadores por tiempo: tardanza del humano, pieza en la mano, relleno, modo atractor. |
| `backend/app/personality/speaker.py` | Reproducción local en la Pi (cola + `mpg123`). Opcional. |
| `frontend/src/RobotVoice.jsx` | Cara animada + globo de diálogo; reproduce el audio en el navegador si no hay parlante en el backend. |

El orquestador avisa los eventos de la partida (`GAME_START`, `ROBOT_CAPTURE`,
`HUMAN_BLUNDER`, `ROBOT_WINS`…); el `Heckler` sondea `status()` y agrega los
que dependen del reloj y de la cámara. El `Commentator` elige la frase, la
cara (`mood`) y el efecto, y lo publica en `status()["speech"]`, que viaja por
`/ws/game` como el resto del estado.

## Eventos

| Evento | Cuándo |
| --- | --- |
| `boot` | 3 s después de arrancar el backend |
| `game_start` / `self_play` | nueva partida / demo |
| `attract` | cada 90 s sin partida (llama gente) |
| `human_slow_20` / `45` / `90` | el humano lleva ese tiempo sin mover (una vez por turno cada escalón) |
| `piece_lifted` / `piece_returned` | la cámara ve una pieza levantada / devuelta sin mover |
| `filler` | relleno aleatorio durante el turno humano (~cada 30 s, mínimo 25 s entre dos) |
| `human_check` / `human_blunder` / `human_capture` / `human_good_move` | tras la jugada humana, en ese orden de preferencia (blunder = la evaluación mejora ≥ 1.5 peones para el robot; buena = empeora ≥ 1) |
| `illegal_move` | confirmó una posición ilegal |
| `robot_mate_soon` / `robot_check` / `robot_capture` / `robot_promotion` / `robot_castle` / `robot_behind` / `robot_move` | tras la jugada del robot, en ese orden de preferencia (`robot_move` solo el 35 % de las veces) |
| `resync` | el tablero no refleja la jugada del robot |
| `game_stop` | el humano abandonó (botón "Terminar") o el operador detuvo la partida |
| `timeout` | al humano se le acabó el reloj de partida (derrota por tiempo) |
| `robot_wins` / `robot_loses` / `draw` | fin de partida |

En la demo robot vs robot solo hablan `self_play` (cada 45 s), `resync` y el
resultado; las burlas al humano no tienen sentido sin humano.

## Reglas de convivencia

- **Nivel** (`CHESS_ROBOT_VOICE_LEVEL`, o el panel de operador): `0` mudo,
  `1` comentarista (solo eventos de la partida), `2` provocador (además
  tardanzas, pieza en la mano, relleno y burlas). Default `2`.
- **Prioridad**: relleno y tardanzas (0) y reacciones (1) respetan un
  enfriamiento de 8 s desde la última frase; jaques, capturas (2) e inicio/fin
  (3) salen siempre y cortan lo que estuviera sonando.
- **Sin repetir**: cada frase se usa una vez por partida. Con 4–6 variantes
  por evento, una tarde de exposición no suena a loro.
- Las etiquetas `[laughs]`, `[sighs]`, `[whispers]`, `[yawns]` del banco son
  indicaciones de actuación para ElevenLabs v3; nunca se muestran en pantalla.
- Nada variable se habla (nombre del jugador, jugada): lo que se pregraba es
  fijo. El nombre aparece en el banner, no en la voz.

## Por dónde sale el audio

1. **Navegador del kiosk** (default). La UI reproduce efecto y voz desde
   `/voice/<archivo>.mp3`. Chromium bloquea el audio sin interacción del
   usuario: el autostart del kiosk lleva
   `--autoplay-policy=no-user-gesture-required` (ver `deploy/kiosk.md`).
2. **Backend** (parlante USB/HDMI en la Pi): `sudo apt install mpg123` y en el
   servicio `Environment=CHESS_ROBOT_SPEECH_PLAYER=mpg123 -q`. El backend
   reproduce y la UI solo muestra el globo (`speech.local_playback = true`).
   Sirve aunque la UI se recargue y no depende del autoplay.

La Pi 5 no tiene jack de 3,5 mm: el audio va por HDMI (si el monitor tiene
parlantes) o por un parlante USB. Para una sala con público conviene USB con
algo de potencia.

## Regenerar o ampliar los audios

```powershell
cd chess-robot\backend
$env:ELEVENLABS_API_KEY = "sk_..."
python scripts\build_voice.py --dry-run     # qué falta y cuánto cuesta
python scripts\build_voice.py --sfx         # genera frases y efectos faltantes
python scripts\build_voice.py --only "viste venir" --force   # nueva toma de una frase
```

- Cada archivo lleva un hash de (voz, modelo, texto, ajustes): editar una
  frase, agregar variantes o cambiar la voz solo regenera lo afectado y nunca
  se paga dos veces lo mismo.
- `--dry-run` siempre antes de gastar. Costo aproximado: 1 crédito por
  carácter con `eleven_v3` / `eleven_multilingual_v2` (la mitad con
  `eleven_flash_v2_5`), ~20 créditos por segundo de efecto.
- Si una toma sale rara (v3 es expresivo pero a veces improvisa), `--only`
  + `--force` la reemplaza. `stability` en `voice_config.json`: `0.5` natural,
  `0.0` más actuado, `1.0` más plano.
- Frases nuevas: agregarlas a `lines.es.json` en la categoría que corresponda,
  correr el script, y desplegar `backend/voice/` junto con el resto
  (`deploy_to_pi.ps1` ya lo incluye).

## API

- `GET /api/speech` — nivel, modo de reproducción y última frase.
- `POST /api/speech/level {"level": 0|1|2}`.
- `POST /api/speech/test {"event": "robot_wins"}` — fuerza una frase (prueba
  de parlante; salta nivel y enfriamiento).

## Tests

`tests/test_personality.py`: reglas del `Commentator` con reloj y azar
inyectados, `Heckler.tick()` sin hilos, y partidas sobre el mundo simulado
verificando qué evento sale en cada jugada.
