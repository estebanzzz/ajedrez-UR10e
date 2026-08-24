"""Driver de tablero por visión: implementa la interfaz ``SensorDriver``.

Cada ``read()`` captura un frame, lo rectifica, clasifica las 64 casillas y
devuelve el mismo bitmap de ocupación de 64 bits que producían los drivers
de matriz reed — el resto del sistema (scanner, debounce, move_detector,
game_state) no cambia. Solo presencia/ausencia: ambos bandos usan fichas
oscuras y el bando lo lleva el seguimiento de estado.

Oclusión: cuando una mano o el brazo del robot cruzan el tablero, el frame
difiere bruscamente del anterior; en ese caso se mantiene el último bitmap
calculado en lugar de publicar lecturas falsas. El ``Debouncer`` aguas
arriba filtra el resto de la transición.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

import chess
import cv2
import numpy as np

from app.board_sensor.bitmap import Bitmap
from app.vision import tuning
from app.vision.camera import FrameSource, VisionError, open_camera
from app.vision.classifier import (
    LABEL_EMPTY,
    LABEL_PIECE,
    VisionModel,
    corner_brightness,
)
from app.vision.geometry import BoardGeometry

logger = logging.getLogger(__name__)

GEOMETRY_FILE = "vision_geometry.json"
MODEL_FILE = "vision_model.json"


class VisionDriver:
    """Driver del tablero por cámara. Thread-safe para el patrón scanner.

    Dos guardias mantienen la última lectura estable en vez de clasificar
    basura:

    - **Movimiento**: el frame difiere bruscamente del anterior (mano o
      brazo cruzando).
    - **Intrusión**: celdas cuyo brillo de esquinas se desvía de forma
      anómala respecto de la mediana del tablero — detecta un objeto
      extraño (mano/brazo QUIETO sobre el tablero) que el movimiento no ve,
      sin confundirlo con un cambio global de iluminación (ese corre todas
      las esquinas parejo y lo absorbe la compensación de brillo).

    La retención por intrusión expira si la escena queda quieta más de
    ``STILL_UNFREEZE_S`` (un cambio legítimo grande del tablero también
    dispara celdas anómalas y sin esto la lectura quedaba congelada).
    """

    # Desviación (unidades L) de la esquina de una celda respecto de la
    # mediana para considerarla anómala, y cuántas celdas anómalas activan
    # la retención.
    ANOMALY_L = 40.0
    MIN_ANOMALOUS_CELLS = 3
    # Una casilla vacía pegada a una ocupada puede recibir el "derrame" de la
    # ficha vecina (cuerpo/sombra de una ficha alta corrida de centro, vista
    # en ángulo): para contarla como anómala se le exige el doble de
    # desviación. Sin esto, 3 fichas corridas dejaban la lectura ocluida
    # para siempre (visto en la feria el 2026-08-24).
    NEIGHBOR_ANOMALY_FACTOR = 2.0
    # La guardia de intrusión compara contra las etiquetas del último frame
    # clasificado: un cambio legítimo grande (reordenar el tablero entre
    # partidas) deja ≥3 celdas anómalas para siempre y la retención nunca se
    # suelta sola. Si la escena lleva este tiempo QUIETA, lo que hay sobre el
    # tablero es el tablero, no una mano: se vuelve a clasificar.
    STILL_UNFREEZE_S = 5.0
    # Un pico AISLADO de movimiento (ruido de cámara, parpadeo de luz) no
    # reinicia la ventana de quietud: solo el movimiento sostenido cuenta.
    SUSTAINED_MOTION_FRAMES = 3
    # Tope duro de la retención por intrusión: una mano no se queda sobre el
    # tablero tanto tiempo. Destraba incluso si picos de movimiento espurios
    # impiden completar la ventana de quietud (causa del "quedó ocluido").
    INTRUSION_MAX_HOLD_S = 15.0

    def __init__(
        self,
        camera: FrameSource,
        geometry: BoardGeometry,
        model: VisionModel | None,  # None → sin entrenar: todo vacío + aviso
        motion_threshold: float | None = None,  # None → tuning en vivo
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._camera = camera
        self._geometry = geometry
        self._model = model
        self._motion_threshold = motion_threshold
        self._clock = clock
        self._lock = threading.Lock()
        self._last_gray: np.ndarray | None = None
        self._last_raw: np.ndarray | None = None
        self._last_warped: np.ndarray | None = None
        self._occupancy: Bitmap | None = None
        self._labels: list[int] = [0] * 64
        self._margins: list[float] = [0.0] * 64
        self._occluded = False
        self._anomalous_cells = 0
        self._anomalous_squares: list[int] = []
        self._motion = 0.0
        self._implausible = False
        # Desde cuándo hay celdas anómalas ininterrumpidas durante la
        # retención (tope duro), desde cuándo la escena está quieta (ventana
        # de destrabe) y racha de frames consecutivos con movimiento.
        self._intrusion_since: float | None = None
        self._still_since: float | None = None
        self._motion_streak = 0

    # ------------------------------------------------------- interfaz driver

    def read(self) -> Bitmap:
        # Referencias locales: la página de calibración puede reemplazar
        # geometría/modelo en caliente desde otro hilo.
        geometry, model = self._geometry, self._model
        frame = self._camera.read()
        warped = geometry.warp(frame)
        gray = cv2.GaussianBlur(cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY), (5, 5), 0)

        if model is None:
            # Sin modelo entrenado: publicar tablero vacío y dejar el aviso
            # en /api/status. El backend queda operable para entrenar desde
            # /calibracion (que solo necesita frames).
            with self._lock:
                self._last_gray = gray
                self._last_raw = frame
                self._last_warped = warped
                self._occupancy = 0
                self._labels = [0] * 64
                self._margins = [0.0] * 64
                self._occluded = False
                self._anomalous_cells = 0
                self._implausible = False
            return 0

        # Intrusión: celdas cuyo brillo de esquinas se desvía anómalamente
        # de la mediana (objeto extraño quieto — el movimiento no lo ve).
        # Solo cuentan las casillas que estaban VACÍAS en la última lectura:
        # una ficha alta vista en ángulo puede cubrir las esquinas de su
        # propia casilla, y eso no es una intrusión.
        offsets = (
            np.array(
                [
                    corner_brightness(geometry.cell(warped, sq))
                    for sq in chess.SQUARES
                ],
                dtype=np.float32,
            )
            - model.corner_refs
        )
        deviations = np.abs(offsets - float(np.median(offsets)))

        with self._lock:
            # Una vacía pegada a una ocupada tolera el doble de desviación:
            # el "derrame" de la ficha vecina no es una intrusión.
            anomalous_squares = [
                sq
                for sq in chess.SQUARES
                if self._labels[sq] == LABEL_EMPTY
                and deviations[sq]
                > (
                    self.ANOMALY_L * self.NEIGHBOR_ANOMALY_FACTOR
                    if self._has_occupied_neighbor(sq)
                    else self.ANOMALY_L
                )
            ]
            anomalous = len(anomalous_squares)
            if self._last_gray is not None and gray.shape == self._last_gray.shape:
                delta = gray.astype(np.float32) - self._last_gray.astype(np.float32)
                # Descontar el corrimiento global (parpadeo de la iluminación
                # contra el obturador): solo el cambio LOCAL es movimiento.
                motion = float(np.abs(delta - float(np.median(delta))).mean())
            else:
                motion = 0.0
            self._last_gray = gray
            self._last_raw = frame
            self._last_warped = warped
            self._anomalous_cells = anomalous
            self._anomalous_squares = anomalous_squares
            self._motion = motion
            motion_threshold = (
                self._motion_threshold
                if self._motion_threshold is not None
                else tuning.TUNING.motion_threshold
            )
            blocked = (
                motion > motion_threshold or anomalous >= self.MIN_ANOMALOUS_CELLS
            )
            if blocked and self._occupancy is not None:
                now = self._clock()
                # ¿Desde cuándo hay intrusión ininterrumpida? (para el tope)
                if anomalous >= self.MIN_ANOMALOUS_CELLS:
                    if self._intrusion_since is None:
                        self._intrusion_since = now
                else:
                    self._intrusion_since = None
                # Ventana de quietud: solo el movimiento SOSTENIDO la
                # reinicia; un pico aislado (ruido) no cuenta.
                if motion > motion_threshold:
                    self._motion_streak += 1
                else:
                    self._motion_streak = 0
                if self._motion_streak >= self.SUSTAINED_MOTION_FRAMES:
                    self._still_since = None
                elif self._still_since is None:
                    self._still_since = now
                still_for = (
                    0.0 if self._still_since is None else now - self._still_since
                )
                intrusion_for = (
                    0.0 if self._intrusion_since is None else now - self._intrusion_since
                )
                unfreeze = (
                    # Escena quieta un buen rato: lo que hay ES el tablero.
                    self._intrusion_since is not None
                    and still_for >= self.STILL_UNFREEZE_S
                ) or intrusion_for >= self.INTRUSION_MAX_HOLD_S
                if not unfreeze:
                    self._occluded = True
                    return self._occupancy
                logger.info(
                    "Retención por intrusión destrabada tras %.1fs (quietud "
                    "%.1fs, %d celdas anómalas): re-clasificando el tablero",
                    intrusion_for,
                    still_for,
                    anomalous,
                )
            self._intrusion_since = None
            self._still_since = None
            self._motion_streak = 0
            self._occluded = False

        labels, margins = model.classify_board(warped, geometry)
        occupancy = 0
        for square, label in enumerate(labels):
            if label == LABEL_PIECE:
                occupancy |= 1 << square

        with self._lock:
            self._occupancy = occupancy
            self._labels, self._margins = labels, margins
            # Cordura: más de 32 ocupadas es imposible en ajedrez → el
            # modelo no corresponde a lo que ve la cámara (típico: se
            # entrenó bajo otra iluminación/exposición). Re-entrenar.
            self._implausible = bin(occupancy).count("1") > 32
        return occupancy

    def close(self) -> None:
        self._camera.close()

    def _has_occupied_neighbor(self, square: int) -> bool:
        """¿Alguna de las 8 vecinas estaba ocupada en la última lectura?
        (Llamar con el lock tomado: lee ``self._labels``.)"""
        file, rank = chess.square_file(square), chess.square_rank(square)
        for dfile in (-1, 0, 1):
            for drank in (-1, 0, 1):
                if dfile == 0 and drank == 0:
                    continue
                nfile, nrank = file + dfile, rank + drank
                if (
                    0 <= nfile < 8
                    and 0 <= nrank < 8
                    and self._labels[chess.square(nfile, nrank)] == LABEL_PIECE
                ):
                    return True
        return False

    # --------------------------------------------------------- diagnóstico

    @property
    def occluded(self) -> bool:
        with self._lock:
            return self._occluded

    @property
    def anomalous_cells(self) -> int:
        """Celdas con esquinas anómalas en el último frame (diagnóstico)."""
        with self._lock:
            return self._anomalous_cells

    @property
    def anomalous_squares(self) -> list[int]:
        """Qué casillas están anómalas (índices 0–63; diagnóstico)."""
        with self._lock:
            return list(self._anomalous_squares)

    @property
    def motion(self) -> float:
        """Movimiento local del último frame (comparar con motion_threshold)."""
        with self._lock:
            return self._motion

    @property
    def implausible(self) -> bool:
        """True si la última lectura es imposible (>32 ocupadas): el modelo
        no corresponde a lo que ve la cámara — re-entrenar."""
        with self._lock:
            return self._implausible

    @property
    def model_ready(self) -> bool:
        """False si no hay modelo entrenado válido (todo se lee vacío)."""
        return self._model is not None

    @property
    def labels(self) -> list[int]:
        with self._lock:
            return list(self._labels)

    @property
    def margins(self) -> list[float]:
        with self._lock:
            return list(self._margins)

    @property
    def geometry(self) -> BoardGeometry:
        return self._geometry

    def raw_frame(self) -> np.ndarray | None:
        """Último frame crudo de la cámara (para la página de calibración)."""
        with self._lock:
            return None if self._last_raw is None else self._last_raw.copy()

    def set_geometry(self, geometry: BoardGeometry) -> None:
        """Aplica una nueva calibración de esquinas en caliente. El modelo
        entrenado con la geometría anterior queda obsoleto: re-entrenar."""
        with self._lock:
            self._geometry = geometry
            self._last_gray = None
            self._last_warped = None
            self._occupancy = None

    def set_model(self, model: VisionModel) -> None:
        """Aplica un modelo recién entrenado en caliente."""
        with self._lock:
            self._model = model
            self._occupancy = None

    def debug_frame(self) -> np.ndarray | None:
        """Vista rectificada con la clasificación superpuesta, para depurar
        desde la UI (lo mismo que muestra la vista previa de entrenamiento).
        None hasta el primer frame."""
        with self._lock:
            if self._last_warped is None:
                return None
            warped = self._last_warped.copy()
            labels = list(self._labels)
            margins = list(self._margins)
            occluded = self._occluded
            anomalous_squares = list(self._anomalous_squares)
            motion = self._motion

        from app.vision.overlay import draw_labels  # evita costo si no se usa

        out = draw_labels(warped, self._geometry, labels, margins)
        # Celdas anómalas (las que disparan la retención): recuadro naranja.
        half = self._geometry.cell_size // 2
        for square in anomalous_squares:
            x, y = self._geometry.cell_center(square)
            cv2.rectangle(
                out, (x - half + 3, y - half + 3), (x + half - 3, y + half - 3),
                (0, 165, 255), 2, cv2.LINE_AA,
            )
        if occluded:
            cv2.putText(
                out,
                f"OCLUIDO ({len(anomalous_squares)} celdas, mov {motion:.1f})",
                (10, out.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (60, 60, 230), 2, cv2.LINE_AA,
            )
        return out


def open_vision(
    camera_spec: str | None = None,
    config_dir: str | Path | None = None,
    motion_threshold: float | None = None,
) -> VisionDriver:
    """Abre el driver de visión con la configuración persistida.

    Requiere ``vision_geometry.json`` (CLI ``python -m app.vision.calibrate``)
    y ``vision_model.json`` (CLI ``python -m app.vision.train``) en el
    directorio de configuración.
    """
    if config_dir is None:
        config_dir = Path(__file__).resolve().parents[2] / "config"
    config_dir = Path(config_dir)
    tuning.load(config_dir / tuning.TUNING_FILE)

    geometry_path = config_dir / GEOMETRY_FILE
    model_path = config_dir / MODEL_FILE
    if not geometry_path.exists():
        raise VisionError(
            f"Falta {geometry_path}: marca las 4 esquinas del tablero en "
            "/calibracion (o con 'python -m app.vision.calibrate')"
        )

    # Modelo ausente u obsoleto: NO impedir el arranque — el backend queda
    # operable (lecturas vacías + aviso) para poder entrenar desde la web.
    model: VisionModel | None = None
    if model_path.exists():
        try:
            model = VisionModel.load(model_path)
        except ValueError as exc:
            logger.warning("Modelo de visión descartado: %s", exc)
    else:
        logger.warning(
            "Sin modelo de visión (%s): entrenar en /calibracion", model_path
        )

    camera_spec = camera_spec or os.environ.get("CHESS_CAMERA", "basler")
    return VisionDriver(
        camera=open_camera(camera_spec),
        geometry=BoardGeometry.load(geometry_path),
        model=model,
        motion_threshold=motion_threshold,
    )
