# Changelog

Iteration history for the ICS Mesh Tracker prototype. All dates 2026-07-03 (built
over one intensive session). Versions are development milestones, not releases —
nothing here has run on real hardware yet (see each entry's "Verified" line).

## v0.3.1 — Wildland path-loss preset corrected

- `fusion.py` WILDLAND preset **n 2.7→3.0, σ 8.7→7.0** (still `indicative`). The
  original pairing came from a mis-attributed ITU-R P.833 citation (withdrawn in
  research Pass 5). A follow-up re-cite (Wang 2012, suggesting n≈2.0) was also
  corrected: Wang measured open grassland LOS, not forest, and near-ground
  2.4 GHz is two-slope (n≈2 short-range, rising to n≈3.5–4 past the Fresnel
  breakpoint), so a single low exponent under-predicts loss. Grounded single-
  slope value is n≈3.0/σ≈6–8 dB (near-ground vegetation campaigns). Fractional
  range error ±74%→±54%. See `docs/research-log.md` Pass 6. Still bench-calibrate
  before trusting; two-slope model is the eventual upgrade.
  - *Provenance correction (Pass 7):* n≈3.0 is supported by Olasupo 2016 (IEEE
    TAP) + Klaina 2018 (MDPI Sensors), both verified as genuine near-ground
    2.4 GHz grass campaigns. But **σ≈7 dB is an assumption**, not from those
    papers — Klaina reports no shadowing σ and Olasupo's σ tables were unread —
    so σ is the least-grounded value and must be bench-measured. Preset values
    unchanged; only the sourcing comment was corrected.

## v0.3.0 — Research-grounded positioning rewrite

The positioning stack was re-grounded through a Code ⇄ Science research
collaboration (every empirical constant verified against primary sources; see
`docs/research-log.md` and `docs/research-brief-claude-science.md`).

- **Dead-reckoning replaced with step-detection PDR.** The original torso-worn
  accelerometer double-integration was proven non-viable (an uncorrected gyro
  bias leaks gravity onto the horizontal axes → the double integral diverges as
  t³: ~8.6 m @10 s, ~1.9 km @60 s). New `firmware/src/imu/step_pdr.h`: step
  detection on low-passed accel magnitude + Weinberg stride, **NMNI** gyro-bias
  re-zeroing for heading (no magnetometer), and a windowed angular-rate-energy
  stillness detector (SHOE surrogate). `dead_reckoning.h` deleted.
- **Fixed-rate IMU acquisition.** `mpu6050_driver.h` gained `enableFifo100Hz()` +
  `drainFifo()`; sampling now runs off the MPU6050 hardware FIFO at a fixed
  100 Hz, decoupled from the WiFi/BLE-perturbed main loop (a variable rate
  silently invalidates the fixed-window detection statistic).
- **TELEMETRY wire format changed** from `(dx,dy,dz,dθ)` displacement to
  `(step_count, stride_mm, heading_mrad, heading_conf)`, mirrored across
  `packet.h` (now `static_assert(sizeof==50)`), `protocol.py`, and
  `test_protocol.py`; `fusion.py` `predict()` advances by stride-along-heading.
- **Per-environment path-loss presets.** `fusion.py` `ENV_PRESETS`
  {WILDLAND, STRUCTURAL, INDUSTRIAL} selectable at incident start (a single
  global path-loss exponent is a systematic bias — same loss solves to ~100 m
  vs ~4.8 m across environments). Each preset carries its shadowing σ for map
  confidence sizing. STRUCTURAL/INDUSTRIAL are from a measured campaign; WILDLAND
  is indicative pending bench calibration.
- **Verified:** Python side only — 6/6 wire round-trip tests, fusion sanity
  (step-PDR placement + preset behavior). Firmware not yet compile-tested (no
  PlatformIO toolchain in the build environment); algorithm constants
  (Weinberg K, stillness γ, step thresholds) are literature defaults flagged for
  bench calibration.

## v0.2.0 — TELEMETRY_BATCH splitting fix

- Fixed a critical accountability bug: a team-lead `TELEMETRY_BATCH` of ≥5
  members exceeded ESP-NOW's 250-byte frame and was **silently dropped** — i.e.
  team telemetry vanished at exactly a doctrinal ICS strike-team size (5
  resources + a leader). `EspNowMesh::sendTelemetryBatch` now splits a team into
  `ceil(count/4)` independently-valid frames the SBC merges as it ingests
  members. Added `sbc-gateway/test_protocol.py` (round-trip proof).

## v0.1.0 — Initial prototype scaffold

- **Firmware** (PlatformIO, XIAO ESP32-S3): ESP-NOW mesh with FIELD / TEAM_LEAD /
  GATEWAY roles + auto-discovered uplinks; MPU6050 IMU dead-reckoning; BLE GATT;
  self-hosted WiFi AP captive portal serving ICS-214 / 205 / 201; three build
  environments (one image, role by build flag).
- **SBC gateway** (Python/FastAPI): USB serial bridge, `protocol.py` mirroring
  the firmware wire format, RSSI multilateration + IMU fusion, SQLite PAR/ICS-214
  history, and a self-contained (no-CDN) live command map.
- Two bugs caught and fixed during Python-side verification: a TelemetryBatch
  struct-of-arrays vs array-of-structs truncation mismatch, and a
  multilateration solver bug (early-return skipped the 2-node case; numpy
  float64 leaked into JSON).
