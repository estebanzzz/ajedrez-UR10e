"""Simulador de partida por consola (Fase 1, sin hardware).

El humano escribe su jugada (SAN o UCI); el simulador genera los snapshots de
sensores que produciría esa jugada física, los pasa por el ``MoveDetector``
—exactamente el mismo camino que usará el tablero real— y "pulsa" el botón de
confirmación. Luego responde el motor.

Uso:
    python -m app.simulator.console [--color blanco|negro] [--nivel <dificultad>]
"""

from __future__ import annotations

import argparse
import sys

import chess

from app.board_sensor.bitmap import format_bitmap
from app.engine import DIFFICULTY_PRESETS, RandomEngine, StockfishEngine, find_stockfish
from app.game_state import GameState
from app.move_detector import DetectionError, MoveDetector
from app.simulator.sim_sensor import snapshots_for_move

COMMANDS = """Comandos:
  <jugada>   jugada en SAN (Cf3, exd5, O-O) o UCI (e2e4)
  tablero    mostrar el tablero
  sensores   mostrar el bitmap de ocupación simulado
  jugadas    listar jugadas legales
  nivel <n>  cambiar dificultad (principiante/intermedio/avanzado/maximo)
  salir      terminar la partida
"""


def _parse_move(board: chess.Board, text: str) -> chess.Move | None:
    for parser in (board.parse_san, board.parse_uci):
        try:
            return parser(text)
        except ValueError:
            continue
    return None


def _print_state(game: GameState, engine) -> None:
    print()
    try:
        print(game.board.unicode(borders=False, empty_square="·"))
    except UnicodeEncodeError:
        print(game.board)  # fallback ASCII para consolas sin UTF-8
    evaluation = engine.evaluate(game.board)
    print(f"\nEvaluación: {evaluation}   Jugadas: {' '.join(game.san_history) or '—'}")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Simulador de partida sin hardware")
    parser.add_argument("--color", choices=["blanco", "negro"], default="blanco")
    parser.add_argument(
        "--nivel", choices=list(DIFFICULTY_PRESETS), default="intermedio"
    )
    args = parser.parse_args(argv)

    human_color = chess.WHITE if args.color == "blanco" else chess.BLACK
    game = GameState(human_color=human_color)

    stockfish_path = find_stockfish()
    if stockfish_path:
        engine = StockfishEngine(stockfish_path, difficulty=args.nivel)
        print(f"Motor: Stockfish ({args.nivel}) — {stockfish_path}")
    else:
        engine = RandomEngine()
        print("Motor: aleatorio (Stockfish no encontrado en PATH)")

    print(COMMANDS)

    try:
        while game.outcome() is None:
            if game.is_human_turn:
                detector = MoveDetector(game.board)
                text = input("Tu jugada > ").strip()
                if not text:
                    continue
                if text == "salir":
                    break
                if text == "tablero":
                    _print_state(game, engine)
                    continue
                if text == "sensores":
                    print(format_bitmap(game.expected_bitmap))
                    continue
                if text == "jugadas":
                    print(" ".join(game.board.san(m) for m in game.legal_moves()))
                    continue
                if text.startswith("nivel "):
                    level = text.split(maxsplit=1)[1]
                    if level in DIFFICULTY_PRESETS:
                        engine.set_difficulty(level)
                        print(f"Dificultad: {level}")
                    else:
                        print(f"Niveles: {', '.join(DIFFICULTY_PRESETS)}")
                    continue

                move = _parse_move(game.board, text)
                if move is None or move not in game.board.legal_moves:
                    print("Jugada ilegal o no reconocida. Escribí 'jugadas' para ver opciones.")
                    continue

                # Camino real: snapshots de sensores → detector → confirmación.
                for snapshot in snapshots_for_move(game.board, move):
                    detector.update(snapshot)
                result = detector.confirm()
                if isinstance(result, DetectionError):
                    print(f"Error de detección: {result.message}")
                    continue
                if result.is_promotion:
                    print("Promoción detectada: se asume dama (bandeja de reserva).")
                game.apply_move(result.move)
                _print_state(game, engine)
            else:
                print("Pensando…")
                move = engine.choose_move(game.board)
                san = game.board.san(move)
                game.apply_move(move)
                print(f"El robot juega: {san}")
                _print_state(game, engine)

        outcome = game.outcome()
        if outcome:
            print(f"\nFin de partida: {outcome.termination} — {outcome.result}")
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
