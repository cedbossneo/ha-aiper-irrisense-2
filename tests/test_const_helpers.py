"""Unit tests for the pure label / preset helpers in const.py."""
from __future__ import annotations

import pytest

from custom_components.aiper_irrisense.const import (
    DEFAULT_POINT_TIME_LABEL,
    DEFAULT_WATER_YIELD_LABEL,
    POINT_TIME_HIGH,
    POINT_TIME_LOW,
    POINT_TIME_MEDIUM,
    REGION_TYPE_AREA,
    REGION_TYPE_LINE,
    REGION_TYPE_POINT,
    WATER_YIELD_HIGH,
    WATER_YIELD_LOW,
    WATER_YIELD_MEDIUM,
    default_dose_label_for_region_type,
    dose_options_for_region_type,
    label_for_point_time,
    label_for_water_yield,
    parse_dose_label,
    zone_display_label,
)


@pytest.mark.parametrize(
    ("region_type", "expected"),
    [
        (REGION_TYPE_AREA, ["3 mm", "6 mm", "13 mm"]),
        (REGION_TYPE_LINE, ["3 mm", "6 mm", "13 mm"]),
        (REGION_TYPE_POINT, ["1 min", "5 min", "10 min"]),
        (None, ["3 mm", "6 mm", "13 mm"]),  # unknown → mm fallback
    ],
)
def test_dose_options_for_region_type(region_type, expected) -> None:
    assert dose_options_for_region_type(region_type) == expected


@pytest.mark.parametrize(
    ("region_type", "expected"),
    [
        (REGION_TYPE_AREA, DEFAULT_WATER_YIELD_LABEL),
        (REGION_TYPE_POINT, DEFAULT_POINT_TIME_LABEL),
        (None, DEFAULT_WATER_YIELD_LABEL),
    ],
)
def test_default_dose_label_for_region_type(region_type, expected) -> None:
    assert default_dose_label_for_region_type(region_type) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("3 mm", ("waterYield", WATER_YIELD_LOW)),
        ("6 mm", ("waterYield", WATER_YIELD_MEDIUM)),
        ("13 mm", ("waterYield", WATER_YIELD_HIGH)),
        ("1 min", ("point_time", POINT_TIME_LOW)),
        ("5 min", ("point_time", POINT_TIME_MEDIUM)),
        ("10 min", ("point_time", POINT_TIME_HIGH)),
        ("nonsense", None),
        ("", None),
    ],
)
def test_parse_dose_label(label, expected) -> None:
    assert parse_dose_label(label) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (WATER_YIELD_LOW, "3 mm"),
        (WATER_YIELD_MEDIUM, "6 mm"),
        (WATER_YIELD_HIGH, "13 mm"),
        (0.11, "3 mm"),        # off-preset snaps to nearest
        (0.4, "13 mm"),        # 0.4 is closer to 0.5 than 0.25
        (None, None),
        ("bad", None),
    ],
)
def test_label_for_water_yield(value, expected) -> None:
    assert label_for_water_yield(value) == expected


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        ({"id": 1, "name": "Lawn", "type": 0}, "Lawn (Area)"),
        ({"id": 2, "name": "Path", "type": 1}, "Path (Line)"),
        ({"id": 3, "name": "Bed", "type": 2}, "Bed (Point)"),
        ({"id": 4, "name": "", "type": 0}, "Zone 4 (Area)"),      # synthesized name
        ({"id": 5, "name": "X", "type": 9}, "X (Zone)"),          # unknown type
    ],
)
def test_zone_display_label(region, expected) -> None:
    assert zone_display_label(region) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (POINT_TIME_LOW, "1 min"),
        (POINT_TIME_MEDIUM, "5 min"),
        (POINT_TIME_HIGH, "10 min"),
        (2, "1 min"),          # snaps to nearest preset
        (7, "5 min"),          # 7 closer to 5 than 10
        (9, "10 min"),
        (5.4, "5 min"),        # float rounds
        (None, None),
        ("bad", None),
    ],
)
def test_label_for_point_time(value, expected) -> None:
    assert label_for_point_time(value) == expected
