# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **MQTT transport rewritten: `paho-mqtt` 2.x + SigV4 instead of
  `AWSIoTPythonSDK`** (ported from fdebrus's fork). This fixes the persistent
  "have to restart Home Assistant to get realtime back" problem at the root,
  not with a watchdog band-aid on top of a broken SDK:
  - Every reconnect attempt re-signs a **fresh** SigV4 WebSocket URL, so an
    outage that outlives the Cognito credentials (~55 min) recovers instead of
    looping on 403 forever. (The AWSIoTPythonSDK reused the stale presigned
    URL.)
  - paho 2.x reports socket teardown cleanly via `on_disconnect` — no more
    paho-loop-thread `AttributeError` crash, so the whole process-wide
    "crash-shield" excepthook is gone.
  - Reconnection is driven by us (`reconnect_on_failure=False`) with an
    exponential-backoff supervisor; the coordinator health watchdog stays as a
    backstop for the half-open/silent case.
  - `manifest.json`: `AWSIoTPythonSDK` → `paho-mqtt>=2.1.0` (Home Assistant
    already ships paho-mqtt 2.x). New `aws_sigv4.py` (plain AWS SigV4,
    verifiable against AWS's published test vectors).

### Added

- **Rendered map image** (`image.<device>_map`). A PNG of the zone map drawn
  from the S3 map geometry (each region's `points[x,y]`, device at the
  origin): filled polygons for Area zones, polylines for Line, markers for
  Point, tinted by type, with the currently-watering zone highlighted. Shows
  in any Picture card — no HACS dependency (rendered server-side via Pillow).
- **`delete_zone` service** — permanently delete a zone from the device's map
  via cloud REST (`/wr/deleteMapRegion`). Endpoint + schema confirmed from the
  Aiper APK. (Zone *creation* is a Bluetooth-only flow on the device and is
  not reproducible from Home Assistant; renaming requires re-uploading the
  whole map file to S3 and is not yet implemented.)

- **Dynamic zone list for dashboards.** A new per-device sensor
  `sensor.<device>_zones` exposes the live zone map as a `zones` attribute —
  a list of `{id, name, select_label, type, type_label, dose_unit,
  default_dose_label, water_yield, point_time, n_points, is_running}`. A
  Lovelace card (e.g. `custom:auto-entities`) can render one card per zone
  straight from this attribute, so adding / renaming / deleting a zone in the
  Aiper app flows through to the dashboard with no YAML edits. See
  `examples/dashboard-dynamic-zones.yaml`. The `select_label` field is the
  exact Watering Zone select option, so a card can drive `select.select_option`
  directly.
- **Test suite.** A `pytest` harness (`pytest-homeassistant-custom-component`)
  with CI on Python 3.12/3.13. Covers the MQTT reconnection paths, the
  coordinator logic, all entity platforms, config flow and diagnostics.

### Fixed

- **MQTT reconnection after a power / network outage.** The realtime link no
  longer stays dead after the connection drops. Previously the AWS IoT SDK's
  own auto-reconnect re-signed the WebSocket URL with the stale Cognito
  credentials it was configured with and looped forever once they expired
  (~55 min), so after an outage entities only updated on the slow REST poll
  until the integration was reloaded. Now the coordinator runs a per-poll MQTT
  health watchdog that forces a clean teardown + reconnect (with fresh
  credentials) when the link is down or has gone silently idle, and the
  boot-time MQTT connect retries with backoff (the router is often still
  offline when HA starts after a whole-house power cut).

## [0.3.0] — Bug fixes, point-zone watchdog, robust setup

### Added

- **Point-zone overrun watchdog** (#6, #18 by @Patch76). HA-side stop at
  `point_time + 30s` grace when V3.8.7+ firmware mistracks point-zone
  duration. Auto-cancels on a clean device stop or a manual Stop.
- **Skip disabled devices** (#10, #14 by @Patch76). Devices disabled in HA's
  device registry are excluded from setup, MQTT subscribe, and coordinator
  refresh.
- **Integration icon** (#8 by @CtznSniiips).

### Changed

- **Bounded setup latency** (#11, #19 by @Patch76). Login and device discovery
  are wrapped in 15s timeouts that raise `ConfigEntryNotReady` /
  `ConfigEntryAuthFailed` for proper Home Assistant retry, and the MQTT
  connect moved to an entry-bound background task so a slow AWS IoT handshake
  can't push setup past HA's 60s bootstrap window.
- **Water totals now reported in gallons** (#22 by @tiloman). The backend
  reports gallons; the sensors were mislabelled as liters and Home Assistant
  converts for metric users. **Note:** existing history for the water-total
  sensors will shift to the corrected unit.

### Fixed

- **`binary_sensor.*_watering` stuck `off`** during active runs (#4, #15 by
  @Patch76). Now reads `is_running` from the coordinator's `active_zone_state()`
  rather than walking a non-existent nested MQTT `data` wrapper.
- **`water_pressure` permanently `unknown`** (#5, #16 by @Patch76). Removed the
  unreliable sensor and the `water_pressure_kpa` attribute — V3.8.7 firmware
  doesn't publish `waterpress` on progress frames and the fallback scan latched
  stale values from unrelated shadow frames.

## [0.2.2] — US region hostname fix + broader WGX coverage

### Fixed

- **US region login failed** with `Name does not resolve` for
  `apius.aiper.com`. Corrected hostname to `apiamerica.aiper.com` — the
  Aiper cloud's actual US REST endpoint (the EU and Asia endpoints were
  already correct and are unchanged).

### Changed

- Broadened the WGX serial-prefix handling started in 0.2.1 so the rest
  of the integration's user-facing surface no longer says "WRX only":
  - `IRRISENSE_SERIAL_PREFIXES` constant updated to `("WRX", "WGX")`.
  - Config-flow description and `no_devices` error message (English +
    translation) now reference both prefixes.
  - "No devices found" warning log and `NoIrrisenseDevices` docstring
    updated to match.

  `WRX` is the original / online-store SKU; `WGX` is the big-box-retail
  variant (e.g. Costco). Both speak the same wire protocol.

## [0.2.1] — WGX serial-prefix support

### Fixed

- Device-list filter rejected Irrisense units with a `WGX` serial
  prefix (sold via big-box retail) because it only matched `WRX`. The
  filter in `api.get_devices` now accepts both prefixes.
  (Thanks to [@n0k0m3](https://github.com/n0k0m3) — PR #1.)

## [0.2.0] — Initial public release

First public release. The integration has been iterated on privately; this
snapshot is the cleaned-up baseline from which future changes will be tracked.

### Features

- Cloud-polled control of Aiper Irrisense 2 devices via MQTT over AWS IoT.
- Per-device entities: active zone, progress %, coverage passes, elapsed /
  remaining seconds, water pressure, rain-sensing state, firmware versions,
  Wi-Fi signal, lifetime water totals.
- Start / stop watering buttons plus a shape-shifting Dose / Duration select
  that adapts to the currently-selected zone's region type (Area / Line /
  Point).
- Progress-spike filter in the coordinator to suppress transient 0→100→low
  blips from the device's `realTimeProgress` stream.
- Three example Lovelace dashboards (single-device, dual-device, and a
  side-by-side alternative) under `examples/`.
