"""Personalidad del robot: decide qué dice, con qué cara, voz y sonido.

Lógica pura, sin I/O ni hilos: el orquestador y el ``Heckler`` le informan
eventos (``say``) y el ``Commentator`` devuelve una ``Utterance`` (o ``None``
si decide callarse). Reglas:

- **Personalidades** (``voice_config.json``): cada una tiene su voz de
  ElevenLabs y su banco de frases (``lines_file``); las categorías que no
  define las hereda del banco base ``lines.es.json`` (grabadas con su propia
  voz). Se elige al configurar la partida o desde el panel de operador.
- **Nivel** (``level``): 0 = mudo, 1 = comentarista (solo eventos de la
  partida), 2 = provocador (además distrae: tardanzas, pieza en la mano,
  relleno, burlas). Ajustable desde el panel de operador.
- **Prioridad**: los eventos de baja prioridad (relleno, tardanza) respetan un
  enfriamiento desde la última frase; los importantes (jaque, captura, fin de
  partida) salen siempre y el reproductor corta lo que estuviera sonando.
- **Sin repetir**: cada frase se usa una vez por partida (se reinicia cuando
  se agotan todas las de su categoría o al empezar otra partida).

Los audios pregenerados viven en ``backend/voice`` (``manifest.json`` mapea
personalidad + texto → archivo; lo escribe ``scripts/build_voice.py``). Si
falta un audio, la frase igual se muestra en pantalla.
"""

from __future__ import annotations

import enum
import json
import random
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PERSONALITY_DIR = Path(__file__).resolve().parent
LINES_PATH = PERSONALITY_DIR / "lines.es.json"
CONFIG_PATH = PERSONALITY_DIR / "voice_config.json"
VOICE_DIR = PERSONALITY_DIR.parents[1] / "voice"
MANIFEST_PATH = VOICE_DIR / "manifest.json"

# Segundos mínimos entre frases de baja prioridad (0 y 1).
COOLDOWN_S = 8.0

_TAG_RE = re.compile(r"\[[a-z ]+\]\s*")

# Config mínima cuando los tests inyectan un banco propio.
_FALLBACK_CONFIG = {
    "default": "clasico",
    "personalities": {"clasico": {"label": "Clásico", "lines_file": "lines.es.json"}},
}


def strip_tags(text: str) -> str:
    """Quita las etiquetas de actuación (``[laughs]``) para mostrar en pantalla."""
    return _TAG_RE.sub("", text).strip()


class Event(enum.Enum):
    """Cada valor es la categoría correspondiente en el banco de frases."""

    BOOT = "boot"
    GAME_START = "game_start"
    ATTRACT = "attract"
    HUMAN_SLOW_20 = "human_slow_20"
    HUMAN_SLOW_45 = "human_slow_45"
    HUMAN_SLOW_90 = "human_slow_90"
    PIECE_LIFTED = "piece_lifted"
    PIECE_RETURNED = "piece_returned"
    HUMAN_GOOD_MOVE = "human_good_move"
    HUMAN_BLUNDER = "human_blunder"
    ILLEGAL_MOVE = "illegal_move"
    AMBIGUOUS_MOVE = "ambiguous_move"  # captura indistinguible: elegir en la UI
    ROBOT_MOVE = "robot_move"
    ROBOT_CAPTURE = "robot_capture"
    ROBOT_CHECK = "robot_check"
    ROBOT_MATE_SOON = "robot_mate_soon"
    ROBOT_BEHIND = "robot_behind"
    ROBOT_CASTLE = "robot_castle"
    ROBOT_PROMOTION = "robot_promotion"
    HUMAN_CAPTURE = "human_capture"
    HUMAN_CHECK = "human_check"
    RESYNC = "resync"
    GAME_STOP = "game_stop"
    TIMEOUT = "timeout"
    ROBOT_WINS = "robot_wins"
    ROBOT_LOSES = "robot_loses"
    DRAW = "draw"
    SELF_PLAY = "self_play"
    FILLER = "filler"


@dataclass(frozen=True)
class Meta:
    priority: int  # 0 relleno · 1 reacción · 2 evento de partida · 3 inicio/fin
    mood: str  # cara del avatar en la UI
    sfx: tuple[str, ...] = ()  # efectos candidatos (se elige uno)
    sfx_chance: float = 1.0
    chance: float = 1.0  # probabilidad de hablar cuando ocurre el evento
    distraction: bool = False  # solo en nivel 2


EVENT_META: dict[Event, Meta] = {
    Event.BOOT: Meta(0, "smug", ("robot_beeps",)),
    Event.GAME_START: Meta(3, "smug", ("drumroll",), sfx_chance=0.5),
    Event.ATTRACT: Meta(0, "bored", ("crickets",), sfx_chance=0.5),
    Event.HUMAN_SLOW_20: Meta(0, "bored", ("tick_tock",), sfx_chance=0.6, distraction=True),
    Event.HUMAN_SLOW_45: Meta(0, "bored", ("yawn", "sigh"), distraction=True),
    Event.HUMAN_SLOW_90: Meta(0, "sleepy", ("snore", "crickets"), distraction=True),
    Event.PIECE_LIFTED: Meta(1, "sneaky", ("dun_dun_dun",), sfx_chance=0.4, distraction=True),
    Event.PIECE_RETURNED: Meta(1, "sneaky", ("whistle",), sfx_chance=0.5, distraction=True),
    Event.HUMAN_GOOD_MOVE: Meta(1, "shocked", ("ding",), sfx_chance=0.7),
    Event.HUMAN_BLUNDER: Meta(1, "gloating", ("evil_laugh", "sad_trombone"), sfx_chance=0.7, distraction=True),
    Event.ILLEGAL_MOVE: Meta(2, "angry", ("buzzer",)),
    # No es un reto: la jugada está bien, la cámara no vio cuál captura fue.
    Event.AMBIGUOUS_MOVE: Meta(2, "shocked", ()),
    Event.ROBOT_MOVE: Meta(1, "smug", (), chance=0.35),
    Event.ROBOT_CAPTURE: Meta(2, "gloating", ("evil_laugh",), sfx_chance=0.5),
    Event.ROBOT_CHECK: Meta(2, "gloating", ("dun_dun_dun",), sfx_chance=0.6),
    Event.ROBOT_MATE_SOON: Meta(2, "gloating", ("heartbeat",)),
    Event.ROBOT_BEHIND: Meta(1, "sad", (), chance=0.7),
    Event.ROBOT_CASTLE: Meta(1, "smug", (), chance=0.8),
    Event.ROBOT_PROMOTION: Meta(2, "gloating", ("fanfare",), sfx_chance=0.5),
    Event.HUMAN_CAPTURE: Meta(1, "shocked", ("sigh",), sfx_chance=0.5),
    Event.HUMAN_CHECK: Meta(2, "shocked", ()),
    Event.RESYNC: Meta(2, "angry", ("record_scratch",), sfx_chance=0.7),
    Event.GAME_STOP: Meta(3, "angry", ()),
    Event.TIMEOUT: Meta(3, "gloating", ("buzzer", "evil_laugh")),
    Event.ROBOT_WINS: Meta(3, "gloating", ("fanfare", "applause")),
    Event.ROBOT_LOSES: Meta(3, "sad", ("sad_trombone", "applause")),
    Event.DRAW: Meta(3, "bored", ("gong",)),
    Event.SELF_PLAY: Meta(0, "smug", ("robot_beeps",), sfx_chance=0.4),
    Event.FILLER: Meta(0, "smug", ("robot_beeps",), sfx_chance=0.3, distraction=True),
}

# Eventos que solo tienen sentido con un humano enfrente.
HUMAN_ONLY = frozenset(
    {
        Event.GAME_START, Event.HUMAN_SLOW_20, Event.HUMAN_SLOW_45,
        Event.HUMAN_SLOW_90, Event.PIECE_LIFTED, Event.PIECE_RETURNED,
        Event.HUMAN_GOOD_MOVE, Event.HUMAN_BLUNDER, Event.ILLEGAL_MOVE,
        Event.AMBIGUOUS_MOVE, Event.ROBOT_MOVE, Event.ROBOT_BEHIND,
        Event.HUMAN_CAPTURE,
        Event.HUMAN_CHECK, Event.GAME_STOP, Event.TIMEOUT, Event.ROBOT_WINS,
        Event.ROBOT_LOSES, Event.DRAW, Event.FILLER,
    }
)


@dataclass(frozen=True)
class Utterance:
    seq: int
    event: str
    text: str  # sin etiquetas, listo para pantalla
    mood: str
    priority: int
    audio: str | None  # nombre de archivo en backend/voice (o None)
    sfx: str | None
    ts: float  # time.time() de emisión

    def as_dict(self) -> dict:
        return {
            "seq": self.seq,
            "event": self.event,
            "text": self.text,
            "mood": self.mood,
            "priority": self.priority,
            "audio_url": f"/voice/{self.audio}" if self.audio else None,
            "sfx_url": f"/voice/{self.sfx}" if self.sfx else None,
            "ts": self.ts,
        }


Listener = Callable[[Utterance], None]


def load_bank(path: Path = LINES_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_voice_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(path: Path = MANIFEST_PATH) -> dict | None:
    """``None`` si todavía no se generaron los audios (la UI muestra solo texto)."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_banks(config: dict, base_bank: dict) -> dict[str, dict[str, list[str]]]:
    """Banco de cada personalidad: sus frases + lo heredado del banco base.

    Cada personalidad puede traer ``lines`` inline (tests) o ``lines_file``
    (default ``lines.<nombre>.es.json``); el banco base es ``lines.es.json``.
    """
    base_lines = base_bank.get("lines", {})
    banks: dict[str, dict[str, list[str]]] = {}
    for name, spec in config["personalities"].items():
        if "lines" in spec:
            own = spec["lines"]
        else:
            path = PERSONALITY_DIR / spec.get("lines_file", f"lines.{name}.es.json")
            if path == LINES_PATH:
                own = base_lines
            elif path.exists():
                own = json.loads(path.read_text(encoding="utf-8")).get("lines", {})
            else:
                own = {}
        resolved = dict(base_lines)
        resolved.update(own)
        banks[name] = resolved
    return banks


class Commentator:
    def __init__(
        self,
        bank: dict | None = None,
        manifest: dict | None = None,
        *,
        level: int = 2,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        config: dict | None = None,
    ) -> None:
        explicit_bank = bank is not None
        bank = bank if explicit_bank else load_bank()
        manifest = manifest if manifest is not None else load_manifest()
        if config is None:
            config = _FALLBACK_CONFIG if explicit_bank else load_voice_config()
        self._config = config
        self._banks = resolve_banks(config, bank)
        self._personality: str = config.get("default") or next(iter(self._banks))
        # (personalidad, categoría, texto sin etiquetas) -> archivo de audio
        self._audio: dict[tuple[str, str, str], str] = {}
        self._sfx: dict[str, str] = {}
        if manifest:
            sections = manifest.get("personalities")
            if sections is None and "lines" in manifest:
                # Formato viejo (una sola voz): corresponde al banco base.
                sections = {self._personality: {"lines": manifest["lines"]}}
            for pname, section in (sections or {}).items():
                for category, entries in section.get("lines", {}).items():
                    for entry in entries:
                        if (VOICE_DIR / entry["file"]).exists():
                            self._audio[(pname, category, entry["text"])] = entry["file"]
            self._sfx = {
                name: file
                for name, file in manifest.get("sfx", {}).items()
                if (VOICE_DIR / file).exists()
            }
        self._rng = rng or random.Random()
        self._clock = clock
        self._lock = threading.Lock()
        self._level = level
        self._seq = 0
        self._last: Utterance | None = None
        self._last_at: float | None = None
        self._pool: dict[tuple[str, str], list[str]] = {}
        self._listeners: list[Listener] = []
        # True cuando un reproductor local (parlante de la Pi) está suscrito:
        # la UI entonces solo muestra el texto, no reproduce el audio.
        self.local_playback = False

    # ------------------------------------------------------------ ajustes

    @property
    def level(self) -> int:
        return self._level

    def set_level(self, level: int) -> None:
        if level not in (0, 1, 2):
            raise ValueError("level debe ser 0, 1 o 2")
        with self._lock:
            self._level = level

    @property
    def personality(self) -> str:
        return self._personality

    def personalities(self) -> list[dict]:
        return [
            {"id": name, "label": spec.get("label", name)}
            for name, spec in self._config["personalities"].items()
        ]

    def set_personality(self, name: str) -> None:
        if name not in self._banks:
            raise ValueError(f"Personalidades: {', '.join(self._banks)}")
        with self._lock:
            self._personality = name

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    @property
    def has_audio(self) -> bool:
        return bool(self._audio)

    # -------------------------------------------------------------- estado

    @property
    def last(self) -> Utterance | None:
        return self._last

    def status(self) -> dict:
        with self._lock:
            last = self._last
        return {
            "level": self._level,
            "local_playback": self.local_playback,
            "personality": self._personality,
            "personalities": self.personalities(),
            "last": last.as_dict() if last else None,
        }

    def new_game(self) -> None:
        """Vuelve a habilitar todas las frases (nueva partida, nuevo público)."""
        with self._lock:
            self._pool.clear()

    # --------------------------------------------------------------- hablar

    def say(self, event: Event, *, self_play: bool = False, force: bool = False) -> Utterance | None:
        """Decide si hablar por ``event``; devuelve la frase emitida o ``None``.

        ``force`` salta nivel, probabilidad y enfriamiento (botón "probar voz").
        """
        meta = EVENT_META[event]
        with self._lock:
            if not force:
                if self._level == 0:
                    return None
                if self._level < 2 and meta.distraction:
                    return None
                if self_play and event in HUMAN_ONLY:
                    return None
                if self._rng.random() > meta.chance:
                    return None
                now = self._clock()
                if (
                    meta.priority <= 1
                    and self._last_at is not None
                    and now - self._last_at < COOLDOWN_S
                ):
                    return None
            personality = self._personality
            text = self._pick(personality, event.value)
            if text is None:
                return None
            sfx = None
            if meta.sfx and self._rng.random() <= meta.sfx_chance:
                candidates = [self._sfx[n] for n in meta.sfx if n in self._sfx]
                if candidates:
                    sfx = self._rng.choice(candidates)
            self._seq += 1
            display = strip_tags(text)
            utterance = Utterance(
                seq=self._seq,
                event=event.value,
                text=display,
                mood=meta.mood,
                priority=meta.priority,
                audio=self._audio.get((personality, event.value, display)),
                sfx=sfx,
                ts=time.time(),
            )
            self._last = utterance
            self._last_at = self._clock()
            listeners = list(self._listeners)
        for listener in listeners:
            listener(utterance)
        return utterance

    def _pick(self, personality: str, category: str) -> str | None:
        lines = self._banks.get(personality, {}).get(category) or []
        if not lines:
            return None
        key = (personality, category)
        pool = self._pool.get(key)
        if not pool:
            pool = list(lines)
            self._rng.shuffle(pool)
            self._pool[key] = pool
        return pool.pop()
