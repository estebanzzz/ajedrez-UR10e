# Detección de piezas por visión artificial (Basler + OpenCV)

Reemplaza a la matriz de reed como fuente de ocupación del tablero. Una
cámara **Basler** cenital (adquisición vía pylon/pypylon) alimenta un
pipeline OpenCV clásico que clasifica cada casilla en **vacía / ficha**
(solo presencia). Ambos bandos usan fichas **oscuras** sobre el tablero
claro (p. ej. azul y gris): se detectan por el mismo camino de píxeles
oscuros, robusto ante la luz, que siempre detectó a las negras al 100 %.
La identidad y el bando de cada pieza (peón, torre…, blanca/negra) los
sigue infiriendo `game_state` por seguimiento de estado, igual que antes:
el driver de visión publica el mismo bitmap de 64 bits que la matriz.

## 1. Preparación de la cámara (pylon Viewer, una sola vez)

1. Montar la cámara cenital, con las 64 casillas visibles y enfocadas.
2. En **pylon Viewer**:
   - Poner **Exposure Auto = Off** y fijar una exposición que no sature
     (el fondo claro del tablero no debe "quemar" a blanco puro).
   - Poner **Balance White Auto = Off** (cámaras color) tras un balance
     inicial correcto.
   - Ganancia fija (Gain Auto = Off).
   - Guardar en **UserSet1** y seleccionarlo como **Startup Set**
     (`UserSetDefault = UserSet1`), para que la cámara arranque siempre con
     esa configuración.
3. Cerrar pylon Viewer antes de usar el backend (la cámara solo admite una
   conexión de control a la vez).

> Los automáticos son el enemigo número uno de la clasificación: cuando el
> brazo entra en cuadro cambian exposición/balance y "mueven" los colores
> de todas las casillas. Iluminación estable y difusa (evitar reflejos
> especulares sobre las casillas) completa el cuadro.

## 2. Calibración por web (recomendado): `/calibracion`

Con el backend corriendo (`CHESS_DRIVER=vision`), abre
**`http://localhost:8000/calibracion`**. La página tiene todo el flujo:

1. **Esquinas del tablero**: click sobre el video en vivo en las 4 esquinas
   del área de juego (a1 → h1 → h8 → a8, "a1" del lado de las blancas) y
   *Guardar esquinas*. Se aplica en caliente, sin reiniciar el backend.
2. **Entrenamiento**: *Capturar tablero VACÍO* → colocar posición inicial →
   *Capturar POSICIÓN INICIAL*. Con ambas tandas el modelo se entrena,
   guarda y aplica solo; el reporte de separación aparece en pantalla.
   Para robustez, repetir las capturas bajo cada condición de luz.
3. **Vista del clasificador en vivo** para verificar el resultado.

Los CLIs de escritorio (secciones siguientes) siguen disponibles como
alternativa, p. ej. para calibrar sin backend corriendo.

## 2b. Calibración de perspectiva por CLI (~30 segundos)

```bash
cd backend
.venv/Scripts/python -m app.vision.calibrate
```

Click en las **4 esquinas exteriores del área de juego** (los vértices del
cuadrado 8×8, no del marco decorativo) en el orden **a1 → h1 → h8 → a8**.
Aparece la vista cenital rectificada con la grilla: verificar que las
líneas caen entre casillas y guardar con `s`. Resultado:
`config/vision_geometry.json`.

Opciones: `--camera basler:<serial>` (varias cámaras), `--camera 0`
(webcam de desarrollo), `--camera foto.png` (desde imagen).

Re-calibrar solo si la cámara o el tablero se mueven.

## 3. Entrenamiento auto-etiquetado (~1 minuto)

```bash
cd backend
.venv/Scripts/python -m app.vision.train
```

Dos pasos, sin etiquetar nada a mano:

1. Tablero **vacío** → ESPACIO (captura una tanda de frames).
2. **Posición inicial** → ESPACIO.

Las etiquetas se conocen solas (filas 1–2 y 7–8 con ficha, resto vacío).
El entrenamiento reporta la **separación** entre las distribuciones de
casillas vacías y ocupadas:

- `✔ Separación limpia` → listo, el modelo se guarda en
  `config/vision_model.json` y entra una vista previa en vivo para
  verificar (círculo magenta = ficha, punto azul = vacía; anillo rojo =
  lectura poco confiable).
- `⚠ las distribuciones se solapan` → mejorar iluminación/contraste y
  repetir.

### Robustez ante cambios de luz

Dos mecanismos protegen la clasificación cuando la iluminación no es la del
entrenamiento:

- **Umbral relativo por casilla**: como las fichas son discos inscritos
  en la celda, las 4 esquinas de cada celda muestran siempre el fondo de
  la casilla (ocupada o no). Cada píxel se compara contra ese fondo medido
  en el mismo frame — cambios globales y gradientes de luz se absorben sin
  re-entrenar.
- **Entrenamiento multi-iluminación**: desde la vista previa en vivo,
  cambia la condición de luz y presiona `[e]` (tablero vacío) o `[i]`
  (posición inicial) para agregar tandas extra. El umbral se aprende de
  las muestras acumuladas de todas las condiciones. Repite para cada
  condición realista del lugar (luces encendidas, atenuadas, luz de
  día…).

Para un cambio grande de escenario (montaje en la expo, otra sala) lo más
seguro sigue siendo re-entrenar desde cero: 1 minuto, vacío + inicial.

## 4. Uso en el backend

```bash
CHESS_DRIVER=vision python -m app.api.server
# Selección de cámara (opcional): CHESS_CAMERA=basler | basler:<serial> | 0
```

### Cámara GigE compartida entre la PC y la Pi

La cámara de la instalación es una **Basler acA3800-10gm GigE en
192.168.0.55**: no cuelga de un USB, está en la misma red que la PC de
desarrollo (192.168.0.x) y que la Pi (192.168.0.10). Cualquiera de las dos
puede usarla sin mover un cable — pero **solo una a la vez**: el control es
exclusivo del host que la abre primero.

En la práctica: para que la Pi tome la cámara hay que cerrar antes el backend
de la PC (`start_backend.bat`) y el pylon Viewer. El driver reintenta cada 3 s,
así que la Pi la agarra sola en cuanto queda libre — no hace falta reiniciar
el servicio.

- `VisionDriver` implementa la misma interfaz `SensorDriver` que los
  drivers legados; `BoardScanner` lo barre a 10 Hz con el debounce de
  siempre, y la jugada humana la resuelve el detector continuo estándar
  (las casillas tocadas transitoriamente desambiguan capturas). La jugada
  del robot se verifica cuando el brazo ya volvió a su **posición de
  espera** sobre la bandeja de capturas, fuera del encuadre.
- **Oclusión**: si el frame difiere bruscamente del anterior (mano o brazo
  sobre el tablero), se mantiene el último bitmap estable. `GET /api/status`
  expone `vision.occluded`.

## 5. Cómo funciona (resumen técnico)

Clasificador **v4** (solo presencia), diseñado para el tablero claro con
fichas disco oscuras de ambos bandos. Una única señal **relativa** —
inmune al nivel de luz absoluto — decide todo:

1. Homografía de las 4 esquinas → imagen cenital de 512 px → 64 celdas.
2. **Oscuridad relativa por píxel**: cada píxel se compara contra el fondo
   de su celda (brillo de las 4 esquinas, siempre visibles porque la ficha
   es un disco inscrito). Un cambio global de luz mueve píxel y fondo
   juntos y nada cambia; los gradientes a lo largo del tablero se absorben
   porque el fondo es por celda.
3. **Umbral aprendido**: el entrenamiento deja el corte a mitad de camino
   entre la ficha más clara y la casilla vacía más oscura observadas — así
   una ficha gris media separa igual de limpio que una casi negra, y las
   sombras vistas al entrenar quedan del lado "vacío".
4. Regla: casilla ocupada si su **disco central** está mayormente oscuro
   (funciona con filas de fichas que se tocan) o si el **centroide** de
   una mancha con área y solidez de disco cae en ella (ficha descentrada).
   Arcos de fichas vecinas vistas en ángulo y sombras chicas no pasan los
   filtros de relleno/solidez.

Sin deep learning: determinista, explicable, re-entrenable en 1 minuto y
sobrado para 2 clases con cámara y tablero fijos.

## 6. Ajustes de detección (página `/calibracion`)

Perillas persistidas en `config/vision_tuning.json`; los cambios aplican en
vivo (verificar en la vista del clasificador). Guía rápida:

| Parámetro | Default | Subirlo cuando… | Bajarlo cuando… |
|---|---|---|---|
| Sensibilidad de ocupación | 1.0 | aparecen fichas fantasma | no ve fichas reales |
| Umbral de oclusión | 8.0 | retiene lecturas todo el tiempo | lee mal durante movimientos |
| Lecturas estables | 3 | lecturas que parpadean | reacciona demasiado lento |
| Radio del disco central ↻ | 0.30 | fichas grandes y centradas | fichas descentradas |

El marcado ↻ cambia cómo se miden las características: tras tocarlo hay
que **re-entrenar** (dos capturas, 1 minuto). El resto aplica al
instante. Metodología recomendada: mover una sola perilla por vez y mirar
los anillos rojos (margen bajo) en la vista en vivo.

## 7. Solución de problemas

| Síntoma | Causa probable | Acción |
|---|---|---|
| Anillos rojos en la vista previa | Poca separación en esas casillas | Mejorar iluminación; re-entrenar |
| Lecturas cambian al entrar el brazo | Auto-exposición activa | Fijar exposición en pylon Viewer (§1) |
| Sombras leídas como fichas | Luz dura y direccional | Luz difusa (aro/domo); re-entrenar para que el umbral aprenda las sombras |
| Fichas claras (gris) no detectadas | La luz difiere del entrenamiento | Tandas extra `[e]`/`[i]` bajo esa luz; idealmente luz propia fija |
| `VisionError: pypylon no está instalado` | Falta el SDK | `pip install pypylon` |
| La cámara no entrega frames | pylon Viewer abierto o cable/red | Cerrar Viewer; revisar conexión |
| Se apagó/desenchufó la cámara con el backend corriendo | Sesión pylon perdida | Nada: el driver reconecta solo (reintento cada 3 s) |
| Deriva a lo largo del día (luz natural) | Iluminación variable | Luz artificial propia o re-entrenos periódicos |
