"""Embedding cosine calibration is env-overridable, query-side only.

The band (floor 0.45 / span 0.35) was measured on 2026-08-14 against
expansion-bloated queries; the 2026-08-26 diagnostic showed true matches
calibrating to 0.000 after the expansion fix, i.e. sitting below the floor.
Until the sweep locks a new band, experiments override via env — and because
`_calibrate` runs on every query and the stored matrix is raw cosines, an
override never needs a reindex and never persists anywhere.
"""
import math

from app.services import embeddings


def test_calibrate_band_defaults():
    # The shipped band: 0.45→0.0, 0.80→1.0, linear between.
    assert embeddings._calibrate(0.45) == 0.0
    assert math.isclose(embeddings._calibrate(0.80), 1.0)
    assert math.isclose(embeddings._calibrate(0.625), 0.5, abs_tol=1e-9)


def test_calibrate_clamps():
    assert embeddings._calibrate(0.10) == 0.0
    assert embeddings._calibrate(0.99) == 1.0


def test_env_override_moves_the_band(monkeypatch):
    monkeypatch.setattr(embeddings, "COSINE_FLOOR", 0.35)
    monkeypatch.setattr(embeddings, "COSINE_SPAN", 0.35)
    # A raw cosine of 0.40 was 0.000 under the shipped floor; under 0.35 it
    # is a real (if modest) signal — exactly the rescue the sweep explores.
    assert embeddings._calibrate(0.40) > 0.0
    assert embeddings._calibrate(0.35) == 0.0
