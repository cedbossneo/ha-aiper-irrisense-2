"""Image platform: a rendered PNG of the device's zone map.

The zone-map JSON fetched from S3 (getMapList → mapUrl) carries, per region,
a list of points with device-local Cartesian coordinates (``x``/``y``, device
at the origin). Confirmed from the Aiper APK model
``com.aiper.device.i.widget.irrigation.MapLocation`` = {x, y, rotate, valve,
waterPressure} and ``MappingRegion`` = {id, name, type, points}.

We draw each region as a filled polygon (Area), polyline (Line) or marker
(Point), tint by type, highlight the zone that is watering right now, and
label each zone. Rendering runs in the executor so Pillow never blocks the
event loop; the frontend re-fetches whenever the map or the active zone
changes (signalled via ``image_last_updated``).
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, REGION_TYPE_LINE, REGION_TYPE_POINT
from .coordinator import IrrisenseCoordinator
from .entity import IrrisenseEntity

_LOGGER = logging.getLogger(__name__)

_CANVAS = 700          # px, square
_MARGIN = 48           # px padding around the drawing
_BG = (17, 24, 39)     # slate-900
_GRID = (31, 41, 55)   # slate-800

# Per region-type base colour (R,G,B). Area/Line/Point.
_TYPE_RGB: dict[int, tuple[int, int, int]] = {
    0: (14, 165, 233),   # Area  — sky
    1: (34, 197, 94),    # Line  — green
    2: (251, 191, 36),   # Point — amber
}
_ACTIVE_RGB = (56, 189, 248)  # highlight for the running zone


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IrrisenseCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [
        IrrisenseMapImage(hass, coordinator, sn)
        for dev in coordinator.devices
        if (sn := dev.get("sn"))
    ]
    async_add_entities(entities)


def _points_of(region: dict[str, Any]) -> list[tuple[float, float]]:
    """Extract (x, y) tuples from a region, tolerant of key spellings."""
    pts: list[tuple[float, float]] = []
    for p in region.get("points") or []:
        if not isinstance(p, dict):
            continue
        x = p.get("x", p.get("X"))
        y = p.get("y", p.get("Y"))
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            pts.append((float(x), float(y)))
    return pts


class IrrisenseMapImage(IrrisenseEntity, ImageEntity):
    """A rendered PNG of the zone map for one device."""

    _attr_content_type = "image/png"
    _attr_translation_key = "map"

    def __init__(
        self, hass: HomeAssistant, coordinator: IrrisenseCoordinator, sn: str
    ) -> None:
        IrrisenseEntity.__init__(self, coordinator, sn, "map")
        ImageEntity.__init__(self, hass)
        self._attr_name = "Map"
        self._attr_image_last_updated = datetime.now(timezone.utc)
        # Remember what we last rendered so we only bump the timestamp (and
        # force a frontend re-fetch) when the picture would actually change.
        self._last_signature: tuple | None = None

    # ----- change detection ------------------------------------------------

    def _signature(self) -> tuple:
        """A cheap fingerprint of everything that affects the rendered image."""
        regions = self._regions()
        active = self.coordinator.active_zone_state(self._sn)
        active_id = active.get("zone_id") if active and active.get("is_running") else None
        return (
            tuple((r.get("id"), r.get("name"), r.get("type"), len(r.get("points") or []))
                  for r in regions),
            active_id,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        sig = self._signature()
        if sig != self._last_signature:
            self._last_signature = sig
            self._attr_image_last_updated = datetime.now(timezone.utc)
        super()._handle_coordinator_update()

    # ----- data ------------------------------------------------------------

    def _regions(self) -> list[dict[str, Any]]:
        raw = (self._slot.get("map") or {}).get("raw")
        regions = raw.get("regions") if isinstance(raw, dict) else None
        return [r for r in regions if isinstance(r, dict)] if isinstance(regions, list) else []

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        regions = self._regions()
        total_points = sum(len(_points_of(r)) for r in regions)
        return {"zone_count": len(regions), "point_count": total_points}

    # ----- rendering -------------------------------------------------------

    async def async_image(self) -> bytes | None:
        return await self.hass.async_add_executor_job(self._render)

    def _render(self) -> bytes | None:
        try:
            from PIL import Image, ImageDraw  # noqa: WPS433 — HA bundles Pillow
        except ImportError:  # pragma: no cover
            _LOGGER.warning("Pillow unavailable; cannot render Irrisense map")
            return None

        regions = self._regions()
        all_pts = [pt for r in regions for pt in _points_of(r)]

        img = Image.new("RGB", (_CANVAS, _CANVAS), _BG)
        draw = ImageDraw.Draw(img, "RGBA")

        if not all_pts:
            draw.text((_MARGIN, _CANVAS // 2), "No map geometry available", fill=(148, 163, 184))
            return _encode(img)

        # World bounding box → canvas transform (uniform scale, y flipped).
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        scale = (_CANVAS - 2 * _MARGIN) / max(span_x, span_y)
        # Centre the drawing.
        off_x = (_CANVAS - span_x * scale) / 2
        off_y = (_CANVAS - span_y * scale) / 2

        def to_px(pt: tuple[float, float]) -> tuple[float, float]:
            px = off_x + (pt[0] - min_x) * scale
            py = _CANVAS - (off_y + (pt[1] - min_y) * scale)  # flip y
            return (px, py)

        active = self.coordinator.active_zone_state(self._sn)
        active_id = active.get("zone_id") if active and active.get("is_running") else None

        # Device origin marker (0,0) if within the frame.
        if min_x <= 0 <= max_x and min_y <= 0 <= max_y:
            ox, oy = to_px((0.0, 0.0))
            draw.line([(ox - 8, oy), (ox + 8, oy)], fill=_GRID, width=2)
            draw.line([(ox, oy - 8), (ox, oy + 8)], fill=_GRID, width=2)

        for r in regions:
            pts = [to_px(p) for p in _points_of(r)]
            if not pts:
                continue
            rtype = int(r.get("type", 0))
            is_active = r.get("id") == active_id
            base = _ACTIVE_RGB if is_active else _TYPE_RGB.get(rtype, _TYPE_RGB[0])

            if rtype == REGION_TYPE_POINT or len(pts) == 1:
                cx, cy = pts[0]
                rad = 9 if is_active else 6
                draw.ellipse([cx - rad, cy - rad, cx + rad, cy + rad],
                             fill=base + (230,), outline=(255, 255, 255, 180), width=2)
            elif rtype == REGION_TYPE_LINE or len(pts) == 2:
                draw.line(pts, fill=base + (255,), width=6 if is_active else 4, joint="curve")
            else:
                draw.polygon(pts, fill=base + (70 if not is_active else 110,),
                             outline=base + (255,))

            # Label at centroid.
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            name = r.get("name") or f"Zone {r.get('id')}"
            draw.text((cx + 6, cy - 6), str(name),
                      fill=(240, 249, 255) if is_active else (203, 213, 225))

        return _encode(img)


def _encode(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
