"""Personalidad del robot: frases, voz y efectos (ver commentator.py)."""

from app.personality.commentator import VOICE_DIR, Commentator, Event, Utterance
from app.personality.heckler import Heckler
from app.personality.speaker import LocalSpeaker

__all__ = ["VOICE_DIR", "Commentator", "Event", "Heckler", "LocalSpeaker", "Utterance"]
