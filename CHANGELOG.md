# Changelog

Iteration history for the ICS Mesh Tracker prototype. All dates 2026-07-03 (built
over one intensive session). Versions are development milestones, not releases —
nothing here has run on real hardware yet (see each entry's "Verified" line).

## v0.6.0 — Node wiring bring-up: MPU-6050 + microSD field logger (team lead)

Wiring the first real TEAM_LEAD node (MPU-6050 GY-521 over I²C + a microSD adapter
over SPI). Firmware updated to match.

- **`firmware/src/board_pins.h`** (new) — single source of truth for the wiring
  (I²C SDA=GPIO5/SCL=GPIO6, MPU INT=GPIO2; SPI SD SCK=GPIO7/MISO=GPIO8/MOSI=GPIO9/
  CS=GPIO3). Both buses on the XIAO ESP32-S3 defaults. See `docs/wiring.md`.
- **`main.cpp` now calls `Wire.begin()`** — it never did. I²C was never initialized,
  so the MPU would have failed to probe; the earlier bring-up only passed because no
  IMU was physically wired. This is the fix that makes the sensor actually work.
- **`firmware/src/storage/sd_logger.h`** (new) — TEAM_LEAD-only removable field
  record `/ICSLOG.CSV` (PAR changes, ICS-214 entries, 5 s telemetry snapshots).
  Lock-free FreeRTOS queue: any task enqueues (non-blocking, safe from the ESP-NOW
  WiFi + BLE callbacks), only `loop()` writes the card; RFC4180 CSV escaping;
  remount-on-repeated-failure instead of latching off. FIELD/GATEWAY never mount it.
- **Serial `[hb]` health heartbeat** (FIELD/TEAM_LEAD, every 3 s): `imu=`/`sd=`/
  `peers=`/`team=`/`dropped=` for quick node-health checks on a USB console.

Adversarial pre-flash review (4 agents) caught 3 real HIGH SD bugs (CSV escaping,
transient-failure latch, cross-task blocking I/O) — all fixed above — plus a HIGH
**hardware** caution: power the microSD from 5V only with a level-shifted adapter
(74LVC125), else move VCC→3V3 (the S3 GPIOs are not 5V-tolerant). See `docs/wiring.md`.

Verified **on real hardware**: all 3 role images compile (pioarduino); `team_lead` +
`gateway` flashed to the two boards; the team-lead heartbeat reads
`[hb] node=3098 role=LEAD imu=1 sd=1 peers=0/1 team=1 dropped=0` with a steadily
climbing uptime — i.e. MPU/I²C up (the `Wire.begin()` fix), microSD mounted, the SD
queue losing nothing, and meshing to the gateway. (The heartbeat had to be read from an
interactive terminal — the S3's native-USB HWCDC only streams to a host that asserts a
"connected"/DTR state, which a headless build environment doesn't.)

## v0.5.1 — Firmware compiles + flashed on real hardware (Core 3.0 migration)

The firmware — written but never compile-tested in earlier versions — was
brought up on **real hardware**: a 4-node ESP-NOW fleet (Gateway, Team Lead,
2× Field incl. an Arduino Nano ESP32) flashed and run end-to-end against the SBC.
Live test confirmed node registration, telemetry, PAR (OK/EMERGENCY/MAYDAY),
and ICS-214 logging over the v0.5.0 command map, including the 0.2 honesty
behaviors (a silent node correctly aging to `stale`, topology grading, the
proximity banner). See `docs/hardware-bringup.ipynb`.

Firmware fixes for Arduino Core 3.0 / ESP-IDF 5 (`pioarduino` platform):
- `platformio.ini`: platform → the `pioarduino` fork; NimBLE-Arduino → `^2.1.0`;
  new `[env:field_nano_esp32]` (Arduino Nano ESP32, `esptool` upload).
- `GatewayBridge::begin` takes a generic `Stream&` (was `HardwareSerial&`) so it
  binds the ESP32-S3's native USB CDC (`HWCDC`) — required for the gateway role.
- `esp_now_recv_info_t` → `esp_now_recv_info` (Core 3.0 struct-tag) + `<esp_now.h>`
  include.

Compatibility only — no logic change. (Firmware bring-up + fixes by the Antigravity/
Gemini collaboration; reconciled into this repo. SBC code unchanged from v0.5.0.)

## v0.5.0 — Honest fusion (item 0.2): dB-space solve, confidence ellipses, topology grading

The RSSI solve was redesigned to be honest about what range-only body-worn RSSI
can and can't know (research item 0.2). Designed by a judge panel, implemented,
then adversarially reviewed — the review caught 5 real bugs (incl. a critical
lifecycle one), all fixed with regression tests. In `sbc-gateway/fusion.py`:

- **Whitened dB-space residuals** `r = (10n/σ)·log10(d_model/d_meas)` replace the
  metric residual. Shadowing is log-normal (Gaussian in dB), so this is
  homoscedastic — far edges no longer dominate near ones — with no hand-coded
  distance weight. Anchor gauge is hard-eliminated (only non-anchor coords are
  free); robust `soft_l1` loss down-weights NLOS/multipath edges.
- **Per-node confidence ellipse.** Closed-form polar covariance: radial from the
  preset σ + edge count, tangential from range-only GDOP (`σ_t ~ σ_r/sin(sep)`) —
  deliberately NOT from the solver Jacobian (which reports ~0 tangential variance
  for range-only geometry, a confident dot exactly where bearing is least known).
  So a node the geometry can't fix in bearing renders as a long tangential smear,
  not a false pinpoint.
- **Coordinate- vs topology-grade** per node, failing SAFE to topology (a range
  ring around the anchor, bearing left explicitly ambiguous) whenever a node is
  under-constrained (rigidity / collinear geometry), under-sampled, moving,
  reflection-unstable, inconsistent, or off-air. The overriding rule: never show
  a possibly-downed responder as more precisely located than the data supports.
- **Flip guard**: Procrustes detect/repair/align of each solve onto the previous
  frame (un-mirrors reflections; removes the arbitrary gauge rotation before the
  blend so ranges can't shrink), with a flip-instability backstop that forces the
  whole map to topology when chronically reflection-ambiguous.
- **Edge weighting** inflates one-way and sample-starved edges' σ; **bidirectional
  averaging** already recovers the reciprocal path.
- **Snapshot** gains per-node `grade`, `pos_confidence`, `ellipse{a_m,b_m,
  theta_deg,bearing_ambiguous}`, `ring_m`, `range_from_anchor_m`, `range_sigma_m`,
  `n_edges`, `n_indep_edges`, `best_n_samp`, `yaw_cov`; `environment_summary`
  gains `flip_unstable`, `graph_rigid`, and coordinate/topology census.
- **Map (`static/map.html`)** renders it: located nodes as dots + 1σ ellipses,
  topology/stale as dashed range rings, grade-colored table, and a "TOPOLOGY MODE"
  banner when the mesh can't be located.
- **Verified:** `test_fusion.py` (9 tests incl. the review regressions) + wire
  tests 6/6; map data-contract + JS syntax checked. Constants are bench-tunable
  (`indicative`); the map render wasn't visually confirmed in this environment.
  See `docs/research-log.md` Pass 12.

## v0.4.0 — RSSI hygiene: windowed percentile estimator, speed-adaptive freshness, bidirectional edges

Implements the correct-regardless-of-sampling parts of research item 1.7 (RSSI
fast-fading / filtering / freshness), in `sbc-gateway/fusion.py`:

- **Windowed high-percentile estimator** replaces latest-sample-wins per edge.
  The old single-sample approach aliased the 10–20 dB body-shadow swing to a
  random distance. The new estimator takes a **~75th percentile** (bench-tunable
  75–90) of a median-pre-filtered per-edge window: mean/median sit mid-shadow
  (distance over-estimate), raw **MAX** chases symmetric fast-fading peaks
  (distance *under*-estimate — a downed responder would look closer/safer, the
  dangerous direction), and a high percentile recovers the near-LoS side. Both
  Science's simulation and an independent Code reproduction confirm the ranking.
- **Speed-adaptive freshness** replaces the fixed 15 s max-age (a 21 m stale
  error at 1.4 m/s): per-edge window is now ~2 s when the observer moves briskly,
  relaxing to ~8 s when slow/still (derived from PDR step-rate), bounding stale-
  edge displacement under ~3 m.
- **Bidirectional edge averaging**: RSSI(A→B) and RSSI(B→A) are kept separately
  and averaged when both exist (recovers the reciprocal path, halves per-unit
  TX/RX offset error); one-way edges are used as-is (lower confidence).
- **NOT done (flagged to Science):** the proposed node-side median-of-N. At the
  ~2 s beacon cadence a node gets ~1 RSSI sample / neighbor / 2 s, so a node-side
  median-of-5 would span ~10 s (orientation scale) and inject the −3 dB median
  bias it was meant to avoid — and leaves too few samples for the SBC percentile.
  Item 1.7's two-stage estimator needs a faster beacon rate than the current
  design provides, coupling it to the Tier-5 beacon-rate/power decision.
- **Verified:** estimator behavior, bidirectional solve (4.94 m vs 5.0), speed-
  adaptive clamping, JSON snapshot, wire tests 6/6. See `docs/research-log.md`
  Pass 10. Exact percentile + the beacon-rate coupling remain bench items.

## v0.3.2 — Wildland σ grounded in measured vegetation LNS

- User supplied primary-source PDFs, letting the wildland `{n, σ}` finally be
  set from **measured** data instead of assumption. WILDLAND **σ 7.0→8.0 dB**
  (n=3.0 unchanged); σ is no longer flagged as an assumption. Grounding: Schneider
  et al. 2026 (Future Internet, 3.75 GHz vineyard LNS at 1.5 m RX = responder
  height) reports α/σ by foliage density = 2.27/7.21 (bare), 3.28/8.21 (growing),
  4.23/8.97 (dense canopy); corroborated by Olasupo 2016 (IEEE TAP, 2.4 GHz grass
  n≈2.9–4), Klaina 2018 (Sensors, 2.4 GHz near-ground), Boonlom 2026 (Sensors,
  923 MHz LoRa forest n=3.22), and Barrios-Ulloa 2022 (Sensors review). Fractional
  range error ±54%→±61% (measured vegetation σ is higher than the earlier assumed
  value — more honest error bars). Still `indicative` (σ source is 3.75 GHz, not
  2.4 GHz forest); a density-parameterized or two-slope model is the next upgrade.
  See `docs/research-log.md` Pass 8.

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
