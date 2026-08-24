"""Tests del módulo de visión con tableros sintéticos (sin cámara real)."""

from __future__ import annotations

import chess
import cv2
import numpy as np
import pytest

from app.vision import (
    BoardGeometry,
    LABEL_EMPTY,
    LABEL_PIECE,
    StaticCamera,
    Trainer,
    VisionDriver,
    VisionModel,
    cell_features,
)

# ----------------------------------------------------------- tablero sintético

CELL = 50
BOARD = CELL * 8
MARGIN = 60
# Tablero claro impreso: casillas blanco + gris claro. Fichas de AMBOS
# bandos oscuras (v4 solo detecta presencia): un bando gris medio, el otro
# casi negro — en cámara mono son dos niveles bien por debajo del fondo.
LIGHT = (250, 250, 250)  # BGR casilla clara
DARK = (215, 215, 215)  # BGR casilla "oscura" (gris claro)
GRAY_PIECE = (110, 110, 110)  # bando "w": ficha gris media
BLACK_PIECE = (35, 35, 35)  # bando "b": ficha casi negra
PIECE_R = 0.37  # radio de ficha relativo a la casilla (26 mm en 35 mm)


def synth_board(
    occupancy: dict[chess.Square, str],
    rng: np.random.Generator,
) -> np.ndarray:
    """Imagen sintética del tablero visto en cenital, con ruido de cámara.

    ``occupancy`` mapea casilla → "w"/"b" (gris media / casi negra). El
    tablero ocupa el cuadrado [MARGIN, MARGIN+BOARD) dentro de una imagen
    con borde de fondo.
    """
    size = BOARD + 2 * MARGIN
    img = np.full((size, size, 3), 200, dtype=np.uint8)
    for square in chess.SQUARES:
        file, rank = chess.square_file(square), chess.square_rank(square)
        x0 = MARGIN + file * CELL
        y0 = MARGIN + (7 - rank) * CELL
        color = LIGHT if (file + rank) % 2 else DARK
        img[y0 : y0 + CELL, x0 : x0 + CELL] = color
        piece = occupancy.get(square)
        if piece:
            cx, cy = x0 + CELL // 2, y0 + CELL // 2
            _draw_piece_at(img, cx, cy, piece)
    noise = rng.normal(0, 2.0, img.shape)
    return np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def _draw_piece_at(frame: np.ndarray, cx: int, cy: int, side: str) -> None:
    radius = int(CELL * PIECE_R)
    fill = GRAY_PIECE if side == "w" else BLACK_PIECE
    rim = (70, 70, 70) if side == "w" else (15, 15, 15)
    cv2.circle(frame, (cx, cy), radius, fill, -1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), radius, rim, 2, cv2.LINE_AA)


# Esquinas del área de juego en la imagen sintética, orden a1, h1, h8, a8.
FLAT_CORNERS = [
    [MARGIN, MARGIN + BOARD],
    [MARGIN + BOARD, MARGIN + BOARD],
    [MARGIN + BOARD, MARGIN],
    [MARGIN, MARGIN],
]


def start_occupancy() -> dict[chess.Square, str]:
    occ: dict[chess.Square, str] = {}
    for square in chess.SQUARES:
        rank = chess.square_rank(square)
        if rank in (0, 1):
            occ[square] = "w"
        elif rank in (6, 7):
            occ[square] = "b"
    return occ


def train_model(
    geometry: BoardGeometry, rng: np.random.Generator, frames: int = 4
) -> VisionModel:
    trainer = Trainer(geometry)
    for _ in range(frames):
        trainer.add_empty_frame(synth_board({}, rng))
        trainer.add_start_frame(synth_board(start_occupancy(), rng))
    return trainer.train()


@pytest.fixture(autouse=True)
def _reset_tuning():
    """Los ajustes son un singleton: dejarlos en default tras cada test."""
    from app.vision import tuning

    tuning.reset()
    yield
    tuning.reset()


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def geometry() -> BoardGeometry:
    return BoardGeometry(corners=FLAT_CORNERS, warp_size=400)


# ------------------------------------------------------------------- geometría


def test_geometry_cells_map_to_expected_colors(geometry, rng):
    warped = geometry.warp(synth_board({}, rng))
    a1 = geometry.cell(warped, chess.A1).mean(axis=(0, 1))  # a1 es oscura
    b1 = geometry.cell(warped, chess.B1).mean(axis=(0, 1))  # b1 es clara
    assert np.allclose(a1, DARK, atol=12)
    assert np.allclose(b1, LIGHT, atol=12)


def test_geometry_roundtrip(tmp_path, geometry):
    path = tmp_path / "geo.json"
    geometry.save(path)
    loaded = BoardGeometry.load(path)
    assert loaded.corners == geometry.corners
    assert loaded.warp_size == geometry.warp_size


# ----------------------------------------------------------------- clasificador


def test_training_separates_and_classifies_start(geometry, rng):
    model = train_model(geometry, rng)
    assert model.stats["separated"], model.stats

    warped = geometry.warp(synth_board(start_occupancy(), rng))
    labels, _ = model.classify_board(warped, geometry)
    for square in chess.SQUARES:
        rank = chess.square_rank(square)
        expected = LABEL_PIECE if rank in (0, 1, 6, 7) else LABEL_EMPTY
        assert labels[square] == expected, chess.square_name(square)


def test_classifies_midgame_position(geometry, rng):
    model = train_model(geometry, rng)
    # Piezas en casillas nunca vistas ocupadas durante el entrenamiento.
    occ = {chess.E4: "w", chess.D5: "b", chess.H3: "w", chess.A6: "b"}
    warped = geometry.warp(synth_board(occ, rng))
    labels, _ = model.classify_board(warped, geometry)
    for square in occ:
        assert labels[square] == LABEL_PIECE, chess.square_name(square)
    assert labels[chess.E2] == LABEL_EMPTY
    assert sum(1 for l in labels if l != LABEL_EMPTY) == len(occ)


def test_gray_piece_detected_on_both_square_tones(geometry, rng):
    """La ficha más clara (gris media) debe separar limpio tanto sobre
    casilla clara como sobre casilla gris claro — es el caso que motivó el
    v4 (con el umbral fijo del v3 la gris quedaba al borde)."""
    model = train_model(geometry, rng)
    occ = {chess.E4: "w", chess.D4: "w"}  # e4 clara, d4 oscura
    labels, margins = model.classify_board(geometry.warp(synth_board(occ, rng)), geometry)
    assert labels[chess.E4] == LABEL_PIECE
    assert labels[chess.D4] == LABEL_PIECE
    assert margins[chess.E4] >= 0.3
    assert margins[chess.D4] >= 0.3


def test_perspective_view(rng):
    """La misma escena vista en ángulo: la homografía debe rectificarla."""
    flat = synth_board(start_occupancy(), rng)
    h, w = flat.shape[:2]
    dst_quad = np.array(
        [[90, 70], [560, 40], [610, 420], [50, 460]], dtype=np.float32
    )
    src_quad = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(src_quad, dst_quad)

    def project(pt):
        v = H @ np.array([pt[0], pt[1], 1.0])
        return [float(v[0] / v[2]), float(v[1] / v[2])]

    def tilt(img):
        return cv2.warpPerspective(img, H, (660, 500))

    corners = [project(c) for c in FLAT_CORNERS]
    geometry = BoardGeometry(corners=corners, warp_size=400)

    trainer = Trainer(geometry)
    for _ in range(4):
        trainer.add_empty_frame(tilt(synth_board({}, rng)))
        trainer.add_start_frame(tilt(synth_board(start_occupancy(), rng)))
    model = trainer.train()
    assert model.stats["separated"], model.stats

    occ = {chess.E4: "w", chess.C5: "b"}
    labels, _ = model.classify_board(geometry.warp(tilt(synth_board(occ, rng))), geometry)
    assert labels[chess.E4] == LABEL_PIECE
    assert labels[chess.C5] == LABEL_PIECE
    assert sum(1 for l in labels if l != LABEL_EMPTY) == 2


def _with_gain(img: np.ndarray, gain: float) -> np.ndarray:
    """Simula un cambio de iluminación global (más/menos luz)."""
    return np.clip(img.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def test_lighting_change_is_compensated(geometry, rng):
    """Entrenado con una luz, clasifica bien con la sala más oscura o más
    clara: el umbral es relativo al fondo de cada celda, que se mide en el
    mismo frame."""
    model = train_model(geometry, rng)
    occ = {chess.E4: "w", chess.D5: "b", chess.B2: "w", chess.G7: "b"}
    for gain in (0.65, 1.3):
        frame = _with_gain(synth_board(occ, rng), gain)
        labels, _ = model.classify_board(geometry.warp(frame), geometry)
        for square in occ:
            assert labels[square] == LABEL_PIECE, (gain, chess.square_name(square))
        assert sum(1 for l in labels if l != LABEL_EMPTY) == len(occ), gain


def test_multi_lighting_training(geometry, rng):
    """Tandas bajo dos condiciones de luz: el modelo acumulado cubre ambas."""
    trainer = Trainer(geometry)
    for gain in (1.0, 0.55):
        for _ in range(3):
            trainer.add_empty_frame(_with_gain(synth_board({}, rng), gain))
            trainer.add_start_frame(_with_gain(synth_board(start_occupancy(), rng), gain))
    model = trainer.train()
    assert model.stats["separated"], model.stats

    occ = {chess.C4: "w", chess.F5: "b"}
    for gain in (1.0, 0.55):
        frame = _with_gain(synth_board(occ, rng), gain)
        labels, _ = model.classify_board(geometry.warp(frame), geometry)
        assert labels[chess.C4] == LABEL_PIECE, gain
        assert labels[chess.F5] == LABEL_PIECE, gain
        assert sum(1 for l in labels if l != LABEL_EMPTY) == 2, gain


def test_soft_shadow_is_not_a_piece(geometry, rng):
    """La sombra suave que una ficha proyecta sobre la casilla vecina no
    debe leerse como ficha: queda por encima del umbral aprendido."""
    model = train_model(geometry, rng)
    frame = synth_board({chess.E4: "b"}, rng)
    # Sombra suave (≈82% del fondo) invadiendo el centro de e5.
    fe = chess.square_file(chess.E5)
    cx = MARGIN + fe * CELL + CELL // 2
    cy = MARGIN + (7 - chess.square_rank(chess.E5)) * CELL + CELL // 2
    cv2.ellipse(frame, (cx, cy + 8), (18, 12), 0, 0, 360, (205, 205, 205), -1, cv2.LINE_AA)

    labels, _ = model.classify_board(geometry.warp(frame), geometry)
    assert labels[chess.E4] == LABEL_PIECE
    assert labels[chess.E5] == LABEL_EMPTY
    assert sum(1 for l in labels if l != LABEL_EMPTY) == 1


def test_tall_neighbor_edge_does_not_fake_piece(geometry, rng):
    """El contorno de una ficha alta asomándose por paralaje en la casilla
    vecina NO debe leerse como ficha: un arco no llena el disco central ni
    tiene solidez de disco."""
    model = train_model(geometry, rng)
    frame = synth_board({chess.E4: "b"}, rng)
    fe, re = chess.square_file(chess.E4), chess.square_rank(chess.E4)
    cx = MARGIN + fe * CELL + CELL // 2
    cy = MARGIN + (7 - re) * CELL + CELL // 2
    # "Altura" de la ficha: su contorno se proyecta más grande que la casilla
    # e invade el borde de e5.
    cv2.circle(frame, (cx, cy), int(CELL * 0.55), (30, 30, 30), 3, cv2.LINE_AA)

    labels, _ = model.classify_board(geometry.warp(frame), geometry)
    assert labels[chess.E4] == LABEL_PIECE
    assert labels[chess.E5] == LABEL_EMPTY
    assert sum(1 for l in labels if l != LABEL_EMPTY) == 1


@pytest.mark.parametrize("side", ["w", "b"])
def test_offcenter_piece_assigned_to_majority_cell(geometry, rng, side):
    """Ficha a caballo entre dos casillas: se adjudica a la casilla donde
    cae su centro (detección por objeto), y la vecina invadida no da falso
    positivo. Vale para ambos tonos de ficha."""
    frame = synth_board({}, rng)
    fe = chess.square_file(chess.D5)
    # Centro desplazado: mayormente en d5, invadiendo d6 (un tercio).
    cx = MARGIN + fe * CELL + CELL // 2
    boundary_y = MARGIN + (7 - chess.square_rank(chess.D6)) * CELL + CELL
    _draw_piece_at(frame, cx, boundary_y + int(CELL * 0.18), side)

    model = train_model(geometry, rng)
    labels, _ = model.classify_board(geometry.warp(frame), geometry)
    assert labels[chess.D5] == LABEL_PIECE
    assert labels[chess.D6] == LABEL_EMPTY
    assert sum(1 for l in labels if l != LABEL_EMPTY) == 1


def test_adjacent_pieces_of_both_tones(geometry, rng):
    """Dos fichas pegadas (una de cada tono) se leen como dos casillas
    ocupadas — el relleno del disco central decide aunque los objetos se
    toquen en el borde."""
    model = train_model(geometry, rng)
    frame = synth_board({chess.E4: "b", chess.E5: "w"}, rng)
    labels, _ = model.classify_board(geometry.warp(frame), geometry)
    assert labels[chess.E4] == LABEL_PIECE
    assert labels[chess.E5] == LABEL_PIECE
    assert sum(1 for l in labels if l != LABEL_EMPTY) == 2


def test_model_roundtrip(tmp_path, geometry, rng):
    model = train_model(geometry, rng)
    path = tmp_path / "model.json"
    model.save(path)
    loaded = VisionModel.load(path)

    warped = geometry.warp(synth_board(start_occupancy(), rng))
    assert loaded.classify_board(warped, geometry)[0] == model.classify_board(warped, geometry)[0]


# ----------------------------------------------------------------------- tuning


def test_tuning_threshold_scale_applies_live(geometry, rng):
    from app.vision import tuning

    model = train_model(geometry, rng)
    warped = geometry.warp(synth_board({chess.E4: "w"}, rng))

    labels, _ = model.classify_board(warped, geometry)
    assert labels[chess.E4] == LABEL_PIECE

    # Escala conservadora extrema: la ficha gris media (la más clara) queda
    # fuera del umbral — sin re-entrenar, aplica en vivo.
    tuning.update({"threshold_scale": 3.0})
    strict_labels, _ = model.classify_board(warped, geometry)
    assert strict_labels[chess.E4] == LABEL_EMPTY


def test_tuning_validation_and_roundtrip(tmp_path):
    from app.vision import tuning

    with pytest.raises(ValueError, match="fuera de rango"):
        tuning.update({"threshold_scale": 99.0})
    with pytest.raises(ValueError, match="desconocido"):
        tuning.update({"no_existe": 1.0})

    changed = tuning.update({"motion_threshold": 12.0, "stable_reads": 4})
    assert changed == {"motion_threshold", "stable_reads"}
    path = tmp_path / "vision_tuning.json"
    tuning.save(path)
    tuning.reset()
    assert tuning.TUNING.motion_threshold == 8.0
    tuning.load(path)
    assert tuning.TUNING.motion_threshold == 12.0
    assert tuning.TUNING.stable_reads == 4


def test_tuning_disc_radius_changes_features(geometry, rng):
    from app.vision import tuning

    cell = geometry.cell(geometry.warp(synth_board({chess.E4: "w"}, rng)), chess.E4)
    base = cell_features(cell)
    tuning.update({"disc_radius": 0.42})
    wider = cell_features(cell)
    assert not np.allclose(base, wider)


# ----------------------------------------------------------------------- driver


def test_vision_driver_produces_start_bitmap(geometry, rng):
    model = train_model(geometry, rng)
    camera = StaticCamera(synth_board(start_occupancy(), rng))
    driver = VisionDriver(camera, geometry, model)

    bitmap = driver.read()
    assert bitmap == chess.Board().occupied
    assert not driver.occluded


def test_driver_holds_when_still_object_covers_board(geometry, rng):
    """Un objeto QUIETO sobre el tablero (brazo del robot en pausa, mano
    apoyada): sin movimiento entre frames, la detección de intrusión por
    esquinas anómalas debe retener la lectura — antes se clasificaba el
    brazo como fichas."""
    model = train_model(geometry, rng)
    start_frame = synth_board(start_occupancy(), rng)
    camera = StaticCamera(start_frame)
    driver = VisionDriver(camera, geometry, model)
    stable = driver.read()
    driver.read()

    covered = start_frame.copy()
    covered[MARGIN + 100 : MARGIN + 250, MARGIN : MARGIN + BOARD] = (170, 170, 170)
    camera.set_frame(covered)
    assert driver.read() == stable  # 1er frame: lo ve el movimiento
    assert driver.read() == stable  # 2do frame idéntico: lo ve la intrusión
    assert driver.occluded
    assert driver.anomalous_cells >= 3

    camera.set_frame(start_frame)
    driver.read()
    assert driver.read() == stable
    assert not driver.occluded


def test_still_intrusion_unfreezes_after_timeout(geometry, rng):
    """La retención por intrusión no puede ser eterna: si la escena queda
    quieta más de STILL_UNFREEZE_S se re-clasifica lo que haya. Sin esto,
    reordenar el tablero entre partidas (muchas celdas anómalas de una vez)
    dejaba la lectura congelada hasta reiniciar el backend."""
    model = train_model(geometry, rng)
    start_frame = synth_board(start_occupancy(), rng)
    camera = StaticCamera(start_frame)
    now = {"t": 0.0}
    driver = VisionDriver(camera, geometry, model, clock=lambda: now["t"])
    stable = driver.read()
    driver.read()

    covered = start_frame.copy()
    covered[MARGIN + 100 : MARGIN + 250, MARGIN : MARGIN + BOARD] = (170, 170, 170)
    camera.set_frame(covered)
    assert driver.read() == stable  # 1er frame: retiene el movimiento
    assert driver.read() == stable  # escena quieta: retiene la intrusión
    assert driver.occluded

    now["t"] += VisionDriver.STILL_UNFREEZE_S - 1.0
    assert driver.read() == stable  # aún dentro de la ventana de retención
    assert driver.occluded

    now["t"] += 2.0  # expiró: lo que está quieto sobre el tablero ES el tablero
    driver.read()
    assert not driver.occluded

    # Y un movimiento posterior vuelve a retener normalmente.
    camera.set_frame(start_frame)
    driver.read()  # transición con movimiento
    assert driver.occluded


def test_driver_classifies_through_global_light_change(geometry, rng):
    """Un cambio de luz GLOBAL no es intrusión: corre todas las esquinas
    parejo y el umbral relativo lo absorbe — el driver debe seguir
    clasificando, no retener para siempre."""
    model = train_model(geometry, rng)
    camera = StaticCamera(synth_board(start_occupancy(), rng))
    driver = VisionDriver(camera, geometry, model)
    expected = chess.Board().occupied
    assert driver.read() == expected
    driver.read()

    camera.set_frame(_with_gain(synth_board(start_occupancy(), rng), 0.65))
    driver.read()  # transición: la retiene el movimiento
    assert driver.read() == expected  # ya estable: clasifica compensando
    assert not driver.occluded


def test_vision_driver_holds_bitmap_during_occlusion(geometry, rng):
    model = train_model(geometry, rng)
    start_frame = synth_board(start_occupancy(), rng)
    camera = StaticCamera(start_frame)
    driver = VisionDriver(camera, geometry, model)

    stable = driver.read()
    driver.read()  # segunda lectura estable (sin movimiento)

    # Una "mano" cruza el tablero: mancha grande sobre la mitad de la imagen.
    occluded_frame = start_frame.copy()
    occluded_frame[100:350, 100:350] = (200, 180, 170)
    camera.set_frame(occluded_frame)
    assert driver.read() == stable
    assert driver.occluded

    # Vuelve la vista normal: la primera lectura aún detecta movimiento
    # (frame distinto al ocluido), la segunda ya clasifica normalmente.
    camera.set_frame(start_frame)
    driver.read()
    assert driver.read() == stable
    assert not driver.occluded


# ------------------------------------------- retención por intrusión (fixes)


def _dim_cell(frame: np.ndarray, square: chess.Square, delta: int = 60) -> None:
    """Oscurece una casilla entera ~delta L: anomalía de esquinas moderada
    sin cambiar el ratio disco/esquinas (sigue clasificando como vacía)."""
    file, rank = chess.square_file(square), chess.square_rank(square)
    x0 = MARGIN + file * CELL
    y0 = MARGIN + (7 - rank) * CELL
    region = frame[y0 : y0 + CELL, x0 : x0 + CELL].astype(np.int16)
    frame[y0 : y0 + CELL, x0 : x0 + CELL] = np.clip(region - delta, 0, 255).astype(
        np.uint8
    )


def test_piece_spill_next_to_occupied_cells_is_not_intrusion(geometry, rng):
    """El "derrame" (cuerpo/sombra de una ficha alta corrida de centro) sobre
    casillas vacías PEGADAS a fichas no es intrusión: esas toleran el doble
    de desviación. Antes, 3 de esas dejaban la lectura ocluida para siempre.
    En celdas aisladas la guardia sigue firme."""
    model = train_model(geometry, rng)
    start_frame = synth_board(start_occupancy(), rng)
    camera = StaticCamera(start_frame)
    driver = VisionDriver(camera, geometry, model)
    stable = driver.read()
    driver.read()

    # Derrame moderado (~60L) en 3 vacías pegadas a la fila de peones negros.
    spill = synth_board(start_occupancy(), rng)
    for square in (chess.C6, chess.D6, chess.E6):
        _dim_cell(spill, square)
    camera.set_frame(spill)
    driver.read()  # transición
    bitmap = driver.read()
    assert not driver.occluded
    assert bitmap == stable  # y las celdas sombreadas siguen leyéndose vacías

    # El mismo derrame en 3 celdas SIN vecinas ocupadas sí retiene.
    isolated = synth_board(start_occupancy(), rng)
    for square in (chess.B4, chess.D4, chess.F4):
        _dim_cell(isolated, square)
    camera.set_frame(isolated)
    driver.read()
    driver.read()
    assert driver.occluded
    assert driver.anomalous_cells >= 3
    assert set(driver.anomalous_squares) >= {chess.B4, chess.D4, chess.F4}


def test_isolated_motion_spikes_do_not_block_the_unfreeze(geometry, rng):
    """Un pico AISLADO de movimiento (ruido, parpadeo de luz) no reinicia la
    ventana de quietud: la retención por intrusión se destraba igual. Antes,
    un pico cada <5 s dejaba la lectura ocluida indefinidamente."""
    model = train_model(geometry, rng)
    start_frame = synth_board(start_occupancy(), rng)
    camera = StaticCamera(start_frame)
    now = {"t": 0.0}
    driver = VisionDriver(camera, geometry, model, clock=lambda: now["t"])
    stable = driver.read()
    driver.read()

    covered = start_frame.copy()
    covered[MARGIN + 100 : MARGIN + 250, MARGIN : MARGIN + BOARD] = (170, 170, 170)
    flash = covered.copy()
    flash[MARGIN : MARGIN + BOARD, MARGIN : MARGIN + BOARD // 2] = (240, 240, 240)

    camera.set_frame(covered)
    assert driver.read() == stable  # transición: retiene el movimiento
    for step in range(1, 6):
        now["t"] = float(step)
        camera.set_frame(flash if step == 3 else covered)  # pico aislado en t=3
        driver.read()
    # Pese al pico (y su vuelta), la quietud acumulada destrabó a los 5 s.
    assert not driver.occluded


def test_intrusion_hold_expires_at_hard_cap_despite_sustained_motion(geometry, rng):
    """Tope duro: aunque haya movimiento sostenido que impida la ventana de
    quietud, una intrusión no puede retener la lectura más de
    INTRUSION_MAX_HOLD_S — nada ajeno queda sobre el tablero tanto tiempo."""
    model = train_model(geometry, rng)
    start_frame = synth_board(start_occupancy(), rng)
    camera = StaticCamera(start_frame)
    now = {"t": 0.0}
    driver = VisionDriver(camera, geometry, model, clock=lambda: now["t"])
    stable = driver.read()
    driver.read()

    covered = start_frame.copy()
    covered[MARGIN + 100 : MARGIN + 250, MARGIN : MARGIN + BOARD] = (170, 170, 170)
    flash = covered.copy()
    flash[MARGIN : MARGIN + BOARD, MARGIN : MARGIN + BOARD // 2] = (240, 240, 240)

    camera.set_frame(covered)
    assert driver.read() == stable
    step = 0
    while now["t"] < VisionDriver.INTRUSION_MAX_HOLD_S - 1.0:
        step += 1
        now["t"] = float(step)
        camera.set_frame(flash if step % 2 else covered)  # movimiento continuo
        driver.read()
        assert driver.occluded  # sigue retenida dentro del tope
    now["t"] = VisionDriver.INTRUSION_MAX_HOLD_S + 1.0
    camera.set_frame(covered)
    driver.read()
    assert not driver.occluded  # tope cumplido: re-clasifica lo que haya
