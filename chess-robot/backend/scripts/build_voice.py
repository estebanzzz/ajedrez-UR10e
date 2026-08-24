"""Genera los audios del robot con ElevenLabs (corre en la PC, no en la Pi).

Soporta varias **personalidades** (``voice_config.json``): cada una tiene su
voz, sus ajustes y su banco de frases (``lines_file``); las categorías que no
define las hereda del banco base ``lines.es.json`` y se graban con SU voz.
Escribe ``voice/manifest.json`` (personalidad + texto → archivo) para el
backend.

Caché por hash: el nombre de archivo incluye un hash de
(voz, modelo, texto, ajustes), así que una frase ya generada nunca se vuelve a
pagar; editar una frase, cambiar la voz o los ajustes solo regenera lo
afectado.

Uso típico::

    $env:ELEVENLABS_API_KEY = "sk_..."                (PowerShell)
    python scripts/build_voice.py --dry-run           # qué falta y cuánto cuesta
    python scripts/build_voice.py                     # genera lo que falte
    python scripts/build_voice.py --personality agustin
    python scripts/build_voice.py --sfx               # también efectos de sonido
    python scripts/build_voice.py --only "viste venir" --force   # nueva toma

Las etiquetas de actuación ``[laughs]`` etc. solo las entiende ``eleven_v3``;
con cualquier otro modelo se quitan del texto enviado. En pantalla nunca se
muestran.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PERSONALITY_DIR = BACKEND / "app" / "personality"
LINES_PATH = PERSONALITY_DIR / "lines.es.json"
CONFIG_PATH = PERSONALITY_DIR / "voice_config.json"
OUT_DIR = BACKEND / "voice"
API = "https://api.elevenlabs.io/v1"

TAG_RE = re.compile(r"\[[a-z ]+\]\s*")


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text).strip()


def tags_of(text: str) -> list[str]:
    return [t.strip("[]") for t in re.findall(r"\[[a-z ]+\]", text)]


def digest(*parts: object) -> str:
    raw = "|".join(json.dumps(p, sort_keys=True, ensure_ascii=False) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Falta ELEVENLABS_API_KEY en el entorno.")
    return key


def resolve_banks(config: dict, base_bank: dict) -> dict[str, dict[str, list[str]]]:
    """Igual que app.personality.commentator.resolve_banks (sin importar app)."""
    base_lines = base_bank.get("lines", {})
    banks: dict[str, dict[str, list[str]]] = {}
    for name, spec in config["personalities"].items():
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


def post(path: str, body: dict, query: str = "", retries: int = 3) -> bytes:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}{query}",
        data=data,
        method="POST",
        headers={
            "xi-api-key": api_key(),
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "audio/mpeg",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")
            if err.code in (429, 500, 502, 503) and attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"    HTTP {err.code}, reintento en {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {err.code}: {detail}") from None
    raise RuntimeError("sin respuesta")


def tts(text: str, spec: dict, model_id: str, output_format: str) -> bytes:
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": spec["voice_settings"],
    }
    return post(
        f"/text-to-speech/{spec['voice_id']}",
        body,
        query=f"?output_format={output_format}",
    )


def sfx(prompt: str, seconds: float, cfg: dict) -> bytes:
    body = {
        "text": prompt,
        "duration_seconds": seconds,
        "prompt_influence": cfg.get("sfx", {}).get("prompt_influence", 0.3),
    }
    return post("/sound-generation", body, query=f"?output_format={cfg['output_format']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--personality", action="append", default=[], help="solo estas personalidades (repetible)")
    ap.add_argument("--only", action="append", default=[], help="solo frases que contengan este texto (repetible)")
    ap.add_argument("--category", action="append", default=[], help="solo estas categorías (repetible)")
    ap.add_argument("--sfx", action="store_true", help="generar también los efectos de sonido")
    ap.add_argument("--sfx-only", action="store_true", help="solo efectos de sonido")
    ap.add_argument("--force", action="store_true", help="regenerar aunque exista (nueva toma)")
    ap.add_argument("--dry-run", action="store_true", help="listar qué se generaría y el costo, sin llamar a la API")
    ap.add_argument("--model", help="forzar model_id (p. ej. eleven_multilingual_v2)")
    ap.add_argument("--limit", type=int, default=0, help="máximo de ítems a generar en esta corrida")
    args = ap.parse_args()

    base_bank = json.loads(LINES_PATH.read_text(encoding="utf-8"))
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    banks = resolve_banks(cfg, base_bank)
    unknown = [p for p in args.personality if p not in banks]
    if unknown:
        sys.exit(f"Personalidades desconocidas: {unknown}; hay: {', '.join(banks)}")
    OUT_DIR.mkdir(exist_ok=True)

    manifest: dict = {"personalities": {}, "sfx": {}}
    todo: list[tuple[str, str, Path, dict]] = []  # (kind, label, path, payload)
    chars_by_personality: dict[str, int] = {}
    seconds = 0.0

    if not args.sfx_only:
        for pname, spec in cfg["personalities"].items():
            model_id = args.model or spec["model_id"]
            section: dict = {"voice_id": spec["voice_id"], "model_id": model_id, "lines": {}}
            manifest["personalities"][pname] = section
            selected_personality = not args.personality or pname in args.personality
            for category, texts in banks[pname].items():
                entries = []
                for index, raw in enumerate(texts):
                    spoken = raw if model_id.startswith("eleven_v3") else strip_tags(raw)
                    key = digest(spec["voice_id"], model_id, spoken, spec["voice_settings"])
                    path = OUT_DIR / f"{category}_{index:02d}_{key}.mp3"
                    entries.append({"text": strip_tags(raw), "tags": tags_of(raw), "file": path.name})
                    selected = (
                        selected_personality
                        and (not args.category or category in args.category)
                        and (not args.only or any(o.lower() in raw.lower() for o in args.only))
                    )
                    if selected and (args.force or not path.exists()):
                        todo.append(("tts", f"[{pname}] {category}[{index}] {raw}", path, {"text": spoken, "spec": spec, "model": model_id}))
                        chars_by_personality[pname] = chars_by_personality.get(pname, 0) + len(spoken)
                section["lines"][category] = entries

    for name, spec in base_bank["sfx"].items():
        key = digest("sfx", spec, cfg.get("sfx", {}))
        path = OUT_DIR / f"sfx_{name}_{key}.mp3"
        if path.exists():
            manifest["sfx"][name] = path.name
        if not (args.sfx or args.sfx_only):
            continue
        manifest["sfx"][name] = path.name
        selected = not args.only or any(o.lower() in name.lower() for o in args.only)
        if selected and (args.force or not path.exists()):
            todo.append(("sfx", f"sfx {name} ({spec['seconds']}s)", path, spec))
            seconds += spec["seconds"]

    if args.limit:
        todo = todo[: args.limit]

    total_chars = sum(chars_by_personality.values())
    print(f"A generar: {len(todo)} items - ~{total_chars} creditos de voz + ~{int(seconds * 20)} de efectos")
    for pname, chars in chars_by_personality.items():
        spec = cfg["personalities"][pname]
        print(f"  {pname}: voz {spec['voice_id']} ~{chars} creditos")
    for _, label, path, _ in todo:
        print(f"  - {label}  -> {path.name}")
    if args.dry_run:
        return 0

    failures = 0
    for kind, label, path, payload in todo:
        print(f"-> {label}")
        try:
            if kind == "tts":
                audio = tts(payload["text"], payload["spec"], payload["model"], cfg["output_format"])
            else:
                audio = sfx(payload["prompt"], payload["seconds"], cfg)
        except RuntimeError as err:
            failures += 1
            print(f"    ERROR {err}")
            continue
        if not (audio.startswith(b"ID3") or audio.startswith(b"\xff")):
            failures += 1
            print(f"    ERROR la respuesta no es MP3: {audio[:120]!r}")
            continue
        path.write_bytes(audio)
        print(f"    ok {len(audio) // 1024} KB")
        time.sleep(0.5)

    missing = sum(
        1
        for section in manifest["personalities"].values()
        for entries in section["lines"].values()
        for e in entries
        if not (OUT_DIR / e["file"]).exists()
    )
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"manifest.json escrito - frases sin audio todavia: {missing} - fallos: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
