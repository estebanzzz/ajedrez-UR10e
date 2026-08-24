"""Parámetros ajustables de la detección por visión.

Singleton mutable (``TUNING``): el clasificador y el driver los leen en cada
uso, así los cambios hechos desde la página ``/calibracion`` aplican en vivo
y se persisten en ``config/vision_tuning.json``.

Dos familias:

- **En vivo** (efecto inmediato): ``threshold_scale`` (sensibilidad de
  ocupación), ``motion_threshold`` (oclusión), ``stable_reads`` (debounce).
- **De extracción** (cambia cómo se miden las características → el
  modelo debe RE-ENTRENARSE tras tocarlo): ``disc_radius``.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

TUNING_FILE = "vision_tuning.json"

# (mínimo, máximo) por campo; también sirve de lista blanca de claves.
RANGES: dict[str, tuple[float, float]] = {
    "threshold_scale": (0.3, 3.0),
    "motion_threshold": (1.0, 40.0),
    "stable_reads": (1, 8),
    "disc_radius": (0.15, 0.45),
}

# Campos que exigen re-entrenar el modelo tras cambiarlos.
RETRAIN_FIELDS = frozenset({"disc_radius"})


@dataclass
class VisionTuning:
    # Sensibilidad de ocupación: multiplica el umbral aprendido. <1 = más
    # sensible (detecta fichas camufladas, más falsos positivos); >1 = más
    # conservador (menos fichas fantasma, puede perder fichas).
    threshold_scale: float = 1.0
    # Oclusión: diferencia media entre frames para retener la lectura.
    # Bajo = retiene ante cualquier movimiento; alto = casi nunca retiene.
    motion_threshold: float = 8.0
    # Lecturas idénticas consecutivas para aceptar un cambio (debounce).
    stable_reads: int = 3
    # Radio del disco central de análisis (fracción del lado de la celda).
    disc_radius: float = 0.30


TUNING = VisionTuning()

_lock = threading.Lock()
_version = 0
_FIELDS = {f.name for f in fields(VisionTuning)}


def version() -> int:
    """Contador que se incrementa con cada cambio (para caches derivados)."""
    return _version


def to_dict() -> dict:
    return asdict(TUNING)


def update(values: dict) -> set[str]:
    """Aplica cambios validados. Devuelve los nombres que cambiaron."""
    global _version
    with _lock:
        changed: set[str] = set()
        for key, value in values.items():
            if key not in _FIELDS:
                raise ValueError(f"Parámetro desconocido: {key!r}")
            lo, hi = RANGES[key]
            if not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key}: valor {value!r} fuera de rango [{lo}, {hi}]")
            value = int(value) if key == "stable_reads" else float(value)
            if getattr(TUNING, key) != value:
                setattr(TUNING, key, value)
                changed.add(key)
        if changed:
            _version += 1
    return changed


def reset() -> None:
    update(asdict(VisionTuning()))


def load(path: str | Path) -> None:
    """Carga la persistencia si existe (claves desconocidas se ignoran)."""
    path = Path(path)
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    update({k: v for k, v in data.items() if k in _FIELDS})


def save(path: str | Path) -> None:
    Path(path).write_text(json.dumps(to_dict(), indent=2), encoding="utf-8")
