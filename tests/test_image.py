"""Tests for the rendered map image entity."""
from __future__ import annotations

from custom_components.aiper_irrisense.image import IrrisenseMapImage, _points_of

from ._helpers import make_coordinator

RAW_MAP = {
    "regions": [
        {
            "id": 1, "name": "Lawn", "type": 0,
            "points": [
                {"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 0.0},
                {"x": 10.0, "y": 8.0}, {"x": 0.0, "y": 8.0},
            ],
        },
        {"id": 2, "name": "Bed", "type": 2, "points": [{"x": 5.0, "y": 12.0}]},
    ]
}


def _img(hass, raw=RAW_MAP, active=None):
    coord = make_coordinator(hass, {"SN": {"map": {"raw": raw, "regions": []}}})
    coord.active_zone_state = lambda sn: active
    return IrrisenseMapImage(hass, coord, "SN")


def test_points_of_tolerates_shapes() -> None:
    assert _points_of({"points": [{"x": 1, "y": 2}, {"X": 3, "Y": 4}]}) == [(1.0, 2.0), (3.0, 4.0)]
    assert _points_of({"points": [{"x": "bad", "y": 1}, "notdict"]}) == []
    assert _points_of({}) == []


def test_render_produces_png(hass) -> None:
    png = _img(hass)._render()
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"   # PNG magic
    assert len(png) > 100


def test_render_empty_map_is_still_png(hass) -> None:
    png = _img(hass, raw={"regions": []})._render()
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"   # placeholder image, not a crash


def test_extra_state_attributes(hass) -> None:
    ent = _img(hass)
    attrs = ent.extra_state_attributes
    assert attrs["zone_count"] == 2
    assert attrs["point_count"] == 5   # 4 polygon points + 1 point zone


def test_signature_changes_with_active_zone(hass) -> None:
    idle = _img(hass, active=None)
    running = _img(hass, active={"is_running": True, "zone_id": 2})
    assert idle._signature() != running._signature()


def test_render_highlights_active_zone_without_error(hass) -> None:
    # Rendering the active-zone branch must not raise.
    png = _img(hass, active={"is_running": True, "zone_id": 1})._render()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
