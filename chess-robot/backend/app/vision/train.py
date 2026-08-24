"""Entrenamiento auto-etiquetado del clasificador de casillas.

Modo guiado (con cámara, requiere calibración previa):

    python -m app.vision.train

  1. Deja el tablero **VACÍO** y presiona ESPACIO → captura una tanda.
  2. Coloca la **POSICIÓN INICIAL** y presiona ESPACIO → captura otra tanda.
  3. El modelo se entrena solo (las etiquetas se conocen: filas 1-2 blancas,
     7-8 negras, resto vacío), se guarda y entra en vista previa en vivo
     para verificar la clasificación. Nada se etiqueta a mano.
  4. Robustez ante cambios de luz: cambia la iluminación (luces encendidas,
     atenuadas…) y agrega tandas extra desde la vista previa con [e]
     (tablero vacío) e [i] (posición inicial). El modelo acumula todas las
     condiciones y se re-guarda solo.

  Teclas: [ESPACIO] capturar fase · [e]/[i] tanda extra · [q] salir

Modo por imágenes (sin cámara, para pruebas):

    python -m app.vision.train --empty vacio.png --start inicial.png
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from app.vision.camera import FrameSource, open_camera
from app.vision.classifier import Trainer, VisionModel
from app.vision.driver import GEOMETRY_FILE, MODEL_FILE
from app.vision.geometry import BoardGeometry
from app.vision.overlay import draw_grid, draw_labels, put_help

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _capture_burst(camera: FrameSource, trainer: Trainer, phase: str, frames: int) -> None:
    add = trainer.add_empty_frame if phase == "empty" else trainer.add_start_frame
    for i in range(frames):
        add(camera.read())
        print(f"  captura {i + 1}/{frames}")
        time.sleep(0.1)


def _report(model: VisionModel) -> None:
    stats = model.stats
    print(
        f"Umbral: píxel de ficha si gris < {model.dark_pixel_frac:.2f} × fondo "
        f"(ficha más clara {stats['piece_ratio_hi']:.2f} · "
        f"vacía más oscura {stats['empty_ratio_lo']:.2f})"
    )
    print(f"Separación ficha↔vacía: ×{stats['separation']:.1f}")
    if not stats["separated"]:
        print(
            "⚠ ADVERTENCIA: las distribuciones se solapan — mejora la "
            "iluminación o el contraste de las piezas y re-entrena."
        )
    else:
        print("✔ Separación limpia: el clasificador es confiable.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default="basler", help="basler | basler:<serial> | índice UVC")
    parser.add_argument("--config-dir", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--frames", type=int, default=8, help="frames por tanda de captura")
    parser.add_argument("--empty", nargs="+", type=Path, help="imágenes del tablero vacío (modo sin cámara)")
    parser.add_argument("--start", nargs="+", type=Path, help="imágenes de la posición inicial (modo sin cámara)")
    args = parser.parse_args()

    geometry_path = args.config_dir / GEOMETRY_FILE
    if not geometry_path.exists():
        raise SystemExit(
            f"Falta {geometry_path}: ejecuta primero 'python -m app.vision.calibrate'"
        )
    geometry = BoardGeometry.load(geometry_path)
    trainer = Trainer(geometry)
    model_path = args.config_dir / MODEL_FILE

    # ---------------------------------------------- modo por imágenes (batch)
    if args.empty or args.start:
        if not (args.empty and args.start):
            raise SystemExit("El modo por imágenes requiere --empty y --start")
        for path in args.empty:
            trainer.add_empty_frame(cv2.imread(str(path)))
        for path in args.start:
            trainer.add_start_frame(cv2.imread(str(path)))
        model = trainer.train()
        _report(model)
        model.save(model_path)
        print(f"Modelo guardado en {model_path}")
        return

    # -------------------------------------------------- modo guiado (cámara)
    camera = open_camera(args.camera)
    print(__doc__)
    phase = "empty"
    model: VisionModel | None = None
    try:
        while True:
            frame = camera.read()
            warped = geometry.warp(frame)
            if model is None:
                msg = (
                    "1/2: tablero VACIO listo -> ESPACIO"
                    if phase == "empty"
                    else "2/2: POSICION INICIAL lista -> ESPACIO"
                )
                view = put_help(draw_grid(warped, geometry), [msg, "[q] salir"])
            else:
                labels, margins = model.classify_board(warped, geometry)
                view = put_help(
                    draw_labels(warped, geometry, labels, margins),
                    [
                        "circulo magenta = ficha | punto azul = vacia",
                        "anillo rojo = poco confiable",
                        "[e] tanda extra vacio  [i] tanda extra inicial  [q] salir",
                    ],
                )
            cv2.imshow("entrenamiento", view)

            key = cv2.waitKey(30) & 0xFF
            if key == ord(" ") and model is None:
                print(f"Capturando tanda ({phase})…")
                _capture_burst(camera, trainer, phase, args.frames)
                if phase == "empty":
                    phase = "start"
                else:
                    model = trainer.train()
                    _report(model)
                    model.save(model_path)
                    print(f"Modelo guardado en {model_path} — vista previa en vivo")
            elif key in (ord("e"), ord("i")) and model is not None:
                extra = "empty" if key == ord("e") else "start"
                what = "tablero VACIO" if extra == "empty" else "POSICION INICIAL"
                print(f"Capturando tanda extra ({what}) con la luz actual…")
                _capture_burst(camera, trainer, extra, args.frames)
                model = trainer.train()
                _report(model)
                model.save(model_path)
                empties, starts = trainer.counts
                print(
                    f"Modelo re-guardado ({empties} frames vacíos, "
                    f"{starts} de posición inicial)"
                )
            elif key == ord("q"):
                break
    finally:
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
