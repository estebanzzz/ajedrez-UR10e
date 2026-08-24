"""Fuentes de imagen para el módulo de visión.

La cámara de producción es una **Basler** adquirida vía pypylon
(``BaslerCamera``). Para desarrollo sin la cámara hay una webcam UVC
(``OpenCVCamera``) y una fuente estática para tests (``StaticCamera``).

Todas devuelven frames BGR (convención OpenCV) desde ``read()``.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class VisionError(RuntimeError):
    """Error de adquisición o de configuración del módulo de visión."""


class FrameSource(Protocol):
    """Protocolo que implementan todas las fuentes de imagen."""

    def read(self) -> np.ndarray:
        """Devuelve el frame más reciente en BGR. Lanza VisionError si falla."""
        ...

    def close(self) -> None:
        ...


class BaslerCamera:
    """Cámara Basler vía pypylon (GigE o USB3, la que detecte pylon).

    Usa ``GrabStrategy_LatestImageOnly`` para que ``read()`` devuelva siempre
    el frame más reciente sin acumular buffer. La configuración de exposición
    y balance de blancos se asume fijada desde pylon Viewer y guardada como
    UserSet de arranque (ver docs/vision.md).

    Tolerante a cortes: si la cámara no está al arrancar, o desaparece en
    caliente (corte de energía, cable), ``read()`` falla con ``VisionError``
    (el scanner lo tolera) y reintenta la reconexión con backoff hasta
    recuperarla, sin reiniciar el backend.
    """

    RECONNECT_INTERVAL = 3.0  # segundos entre intentos de reconexión

    def __init__(self, serial: str | None = None, timeout_ms: int = 2000) -> None:
        try:
            from pypylon import pylon
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise VisionError(
                "pypylon no está instalado (pip install pypylon)"
            ) from exc

        self._pylon = pylon
        self._serial = serial
        self._timeout_ms = timeout_ms
        self._camera = None
        self._next_attempt = 0.0
        self._converter = pylon.ImageFormatConverter()
        self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
        self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
        try:
            self._connect()
        except Exception as exc:
            logger.warning(
                "Cámara Basler no disponible al arrancar (%s); se reintentará", exc
            )
            self._next_attempt = time.monotonic() + self.RECONNECT_INTERVAL

    # ----------------------------------------------------------- conexión

    def _connect(self) -> None:
        pylon = self._pylon
        factory = pylon.TlFactory.GetInstance()
        if self._serial:
            info = pylon.DeviceInfo()
            info.SetSerialNumber(self._serial)
            device = factory.CreateDevice(info)
        else:
            device = factory.CreateFirstDevice()
        camera = pylon.InstantCamera(device)
        camera.Open()
        camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        self._camera = camera

    def _disconnect(self) -> None:
        camera, self._camera = self._camera, None
        if camera is not None:
            try:
                camera.StopGrabbing()
                camera.Close()
            except Exception:  # pragma: no cover - cierre de hardware
                pass
        self._next_attempt = time.monotonic() + self.RECONNECT_INTERVAL

    def _ensure_connected(self) -> None:
        if self._camera is not None:
            return
        now = time.monotonic()
        if now < self._next_attempt:
            raise VisionError("Cámara Basler desconectada (reintento pendiente)")
        self._next_attempt = now + self.RECONNECT_INTERVAL
        try:
            self._connect()
        except Exception as exc:
            raise VisionError(f"No se pudo reconectar la cámara Basler: {exc}") from exc
        logger.info("Cámara Basler reconectada")

    # ------------------------------------------------------------ lectura

    def read(self) -> np.ndarray:
        self._ensure_connected()
        try:
            result = self._camera.RetrieveResult(
                self._timeout_ms, self._pylon.TimeoutHandling_Return
            )
            try:
                if not result or not result.GrabSucceeded():
                    raise VisionError(
                        "La cámara Basler no entregó frame (timeout/error)"
                    )
                return self._converter.Convert(result).GetArray()
            finally:
                if result:
                    result.Release()
        except VisionError:
            self._disconnect()
            raise
        except Exception as exc:  # pypylon lanza genéricas al perder el dispositivo
            self._disconnect()
            raise VisionError(f"Fallo de adquisición Basler: {exc}") from exc

    def close(self) -> None:
        self._disconnect()


class OpenCVCamera:
    """Webcam UVC vía cv2.VideoCapture — respaldo de desarrollo sin la Basler."""

    def __init__(self, index: int = 0, width: int = 1280, height: int = 720) -> None:
        self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            raise VisionError(f"No se pudo abrir la cámara UVC índice {index}")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> np.ndarray:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            raise VisionError("La cámara UVC no entregó frame")
        return frame

    def close(self) -> None:
        self._cap.release()


class StaticCamera:
    """Fuente fija por software, para tests y para clasificar fotos sueltas.

    Thread-safe: el scanner lee desde su hilo mientras los tests cambian el
    frame (mismo patrón que MockDriver).
    """

    def __init__(self, frame: np.ndarray | None = None) -> None:
        self._lock = threading.Lock()
        self._frame = frame

    def set_frame(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame

    def read(self) -> np.ndarray:
        with self._lock:
            if self._frame is None:
                raise VisionError("StaticCamera sin frame asignado")
            return self._frame.copy()

    def close(self) -> None:
        pass


def open_camera(spec: str = "basler") -> FrameSource:
    """Abre una fuente de imagen a partir de una especificación de texto.

    - ``"basler"`` → primera cámara Basler detectada por pylon.
    - ``"basler:<serial>"`` → Basler con ese número de serie.
    - ``"0"``, ``"1"``, … → webcam UVC por índice (respaldo de desarrollo).
    - ruta a un archivo de imagen → fuente estática (diagnóstico).
    """
    spec = spec.strip()
    if spec == "basler":
        return BaslerCamera()
    if spec.startswith("basler:"):
        return BaslerCamera(serial=spec.split(":", 1)[1])
    if spec.isdigit():
        return OpenCVCamera(index=int(spec))
    path = Path(spec)
    if path.is_file():
        frame = cv2.imread(str(path))
        if frame is None:
            raise VisionError(f"No se pudo leer la imagen {path}")
        return StaticCamera(frame)
    raise VisionError(
        f"Cámara desconocida: {spec!r} (opciones: basler, basler:<serial>, "
        "índice UVC o ruta a imagen)"
    )
