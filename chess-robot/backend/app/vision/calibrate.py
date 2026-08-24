"""Calibración de perspectiva de la cámara del tablero.

Uso (con la Basler conectada):

    python -m app.vision.calibrate
    python -m app.vision.calibrate --camera basler:24312345
    python -m app.vision.calibrate --camera foto_tablero.png   # desde imagen

Se abre la vista de la cámara: haz click en las 4 esquinas exteriores del
área de juego en el orden **a1 → h1 → h8 → a8** (los vértices del cuadrado
8x8, no del marco decorativo). Con las 4 marcadas aparece la vista cenital
rectificada con la grilla; verifica que las líneas caen entre casillas.

Teclas: [s] guardar y salir · [r] reiniciar puntos · [q] salir sin guardar
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2

from app.vision.camera import open_camera
from app.vision.driver import GEOMETRY_FILE
from app.vision.geometry import CORNER_NAMES, BoardGeometry
from app.vision.overlay import draw_grid, put_help

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config"

# La vista se escala para caber en pantalla (las Basler entregan frames de
# varios MP que a 1:1 no caben en el monitor y parecen "con zoom"). Los
# clicks se convierten de vuelta a píxeles originales del sensor.
_MAX_DISPLAY = (1280, 800)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", default="basler", help="basler | basler:<serial> | índice UVC | imagen")
    parser.add_argument("--config-dir", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--warp-size", type=int, default=512)
    args = parser.parse_args()

    camera = open_camera(args.camera)
    points: list[list[float]] = []
    scale = 1.0  # display = original * scale; se fija con el primer frame

    def on_mouse(event: int, x: int, y: int, flags: int, param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < 4:
            points.append([x / scale, y / scale])
            px, py = points[-1]
            print(f"  esquina {CORNER_NAMES[len(points) - 1]} = ({px:.0f}, {py:.0f})")

    cv2.namedWindow("calibracion")
    cv2.setMouseCallback("calibracion", on_mouse)
    print(__doc__)

    first = True
    try:
        while True:
            frame = camera.read()
            h, w = frame.shape[:2]
            scale = min(1.0, _MAX_DISPLAY[0] / w, _MAX_DISPLAY[1] / h)
            if first:
                print(f"Cámara: {w}x{h} px (vista escalada al {scale:.0%})")
                first = False
            view = (
                cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                if scale < 1.0
                else frame.copy()
            )
            for i, (x, y) in enumerate(points):
                dx, dy = int(x * scale), int(y * scale)
                cv2.circle(view, (dx, dy), 6, (60, 60, 230), -1)
                cv2.putText(
                    view, CORNER_NAMES[i], (dx + 8, dy - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 60, 230), 2, cv2.LINE_AA,
                )
            if len(points) < 4:
                next_corner = CORNER_NAMES[len(points)]
                view = put_help(view, [f"Click en la esquina {next_corner}", "[r] reiniciar  [q] salir"])
            else:
                view = put_help(view, ["Verifica la grilla", "[s] guardar  [r] reiniciar  [q] salir"])
                geometry = BoardGeometry(corners=points, warp_size=args.warp_size)
                cv2.imshow("vista cenital", draw_grid(geometry.warp(frame), geometry))
            cv2.imshow("calibracion", view)

            key = cv2.waitKey(30) & 0xFF
            if key == ord("r"):
                points.clear()
                cv2.destroyWindow("vista cenital")
            elif key == ord("s") and len(points) == 4:
                args.config_dir.mkdir(parents=True, exist_ok=True)
                path = args.config_dir / GEOMETRY_FILE
                BoardGeometry(corners=points, warp_size=args.warp_size).save(path)
                print(f"Geometría guardada en {path}")
                break
            elif key == ord("q"):
                print("Salida sin guardar")
                break
    finally:
        camera.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
