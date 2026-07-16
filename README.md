# ICS Mesh Tracker

A prototype ESP-NOW mesh for ICS field accountability/tracking: responders
carry XIAO ESP32S3 nodes that beacon to each other, estimate relative
position from RSSI + step-detection IMU PDR, and report status (PAR,
ICS-214 log entries) up through a strike-team lead to an on-site SBC that
renders a live command map. Nodes also stand up their own WiFi AP + BLE so
a strike team can operate fully independently, with just a phone, when no
SBC is in range.

**Read [docs/protocol.md](docs/protocol.md) first** — it has the wire
format and, importantly, the accuracy caveats for RSSI-based positioning
(±5-15m, not survey-grade) that should shape how you read the map.

## Architecture

```
FIELD node ---beacon/telemetry---\
FIELD node ---beacon/telemetry----+--> TEAM_LEAD node --(batch)--> GATEWAY node --(USB)--> SBC
FIELD node ---beacon/telemetry---/                                                          |
     |                                                                                       v
     +--(BLE/WiFi AP)--> responder's phone                                        live map + ICS-214/PAR
                                                                                    dashboard (FastAPI + WS)
```

- **FIELD / TEAM_LEAD** nodes: identical firmware image, different build
  flag. Beacon on ESP-NOW, capture RSSI to neighbors, integrate IMU
  displacement, report PAR/ICS-214, and each hosts its own captive-portal
  web UI + BLE GATT service so a phone can pair directly.
- **TEAM_LEAD** additionally aggregates its team's telemetry into batches
  and relays them toward the GATEWAY — auto-discovered by role+team_id,
  no manual configuration (see `EspNowMesh::discoveredUplink`).
- **GATEWAY** node: no AP, no BLE, no beacon-derived duties — it's a dumb
  USB-tethered bridge that forwards every raw ESP-NOW packet to the SBC
  over its native USB-CDC serial port.
- **SBC (`sbc-gateway/`)**: Python service. Reads the gateway's serial
  frames, fuses RSSI multilateration + step-detection IMU PDR into a relative
  2D position per node, and serves a live map + ICS-214/PAR dashboard over
  a self-contained (no-CDN) web page.
- **No-SBC mode**: with only a TEAM_LEAD and FIELD nodes in range (no
  gateway/SBC), the team still functions — PAR/ICS-214 travel to the
  team lead and are visible from any node's own captive portal or BLE
  link, they just don't reach a command map until a gateway comes into
  range. There's no local map rendering yet in this mode (see Roadmap).

## Hardware BOM

| Part | Qty | Notes |
|---|---|---|
| Seeed XIAO ESP32S3 | 4 | you already have these |
| External IMU (e.g. **MPU6050 / GY-521** breakout) | 3-4 | **the bare XIAO ESP32S3 has no onboard IMU** (the "Sense" variant adds a camera+mic, not an IMU) — step-detection PDR needs one wired over I2C. `firmware/src/imu/mpu6050_driver.h` targets a stock MPU6050 breakout (cheap, ubiquitous); swap in a different `IImuDriver` implementation if you use something else (it must provide a fixed-rate FIFO/streaming path — see `step_pdr.h`). GATEWAY node doesn't need one. |
| microSD adapter + card (**level-shifted**) | 1 | **TEAM_LEAD only** — a removable field record (`/ICSLOG.CSV`: PAR + ICS-214 + telemetry) that survives node loss / a dead SBC link. SPI. Use a **level-shifted** adapter (74LVC125) if powering from 5V — see the ⚠️ in [`docs/wiring.md`](docs/wiring.md). FIELD/GATEWAY nodes omit it. |
| USB battery / LiPo + charge circuit per FIELD/TEAM_LEAD node | 3-4 | for field-worn operation; the XIAO ESP32S3 has a JST-1.25 battery connector + built-in charger |
| On-site SBC (Raspberry Pi or similar) | 1 | runs `sbc-gateway/`; USB-connects to the GATEWAY node |
| USB cable, GATEWAY node → SBC | 1 | this is the only wired link in the system |

**Wiring:** see **[`docs/wiring.md`](docs/wiring.md)** for the full per-role pin
table (and the important 5V-microSD-adapter safety check). In short, on the
XIAO ESP32-S3 the MPU-6050 goes on the default I²C pins (SDA=D4/GPIO5,
SCL=D5/GPIO6) and the team-lead's microSD on the default SPI bus (SCK=D8, MISO=D9,
MOSI=D10, CS=D2). Pins live in `firmware/src/board_pins.h`; `main.cpp` now always
calls `Wire.begin()` at startup, so the default wiring works out of the box.
Field nodes are this layout minus the SD adapter; the gateway needs no external parts.

## Flashing the 4-board fleet

Requires [PlatformIO](https://platformio.org/) (CLI or VS Code extension). The
firmware targets the **`pioarduino` platform** (Arduino Core 3.0 / ESP-IDF 5) —
`platformio.ini` pins it, so `pio` fetches it automatically.

```
cd firmware
pio run -e field -t upload             # XIAO ESP32S3 field node (flash to as many as you like)
pio run -e team_lead -t upload         # 1 board
pio run -e gateway -t upload           # 1 board (USB-tethered to the SBC)
pio run -e field_nano_esp32 -t upload  # a field node on an Arduino Nano ESP32 (esptool upload, avoids Mac DFU issues)
```

> **Status: this has been built and flashed to a real 4-node fleet** (Gateway,
> Team Lead, 2× Field incl. one Arduino Nano ESP32) and run end-to-end against
> the SBC — see [`docs/hardware-bringup.ipynb`](docs/hardware-bringup.ipynb).
> Node registration, telemetry, PAR status, and ICS-214 logging all worked live.
> Positioning constants are still `indicative` (bench-calibration pending).

Board role is baked in at build time (`platformio.ini`'s environments) — see
`docs/protocol.md` for why this beats runtime provisioning at a 4-board scale.
`TEAM_ID` defaults to `1` for all of them; only change it if you're running more
than one strike team's worth of nodes and need them to not auto-uplink to each
other's lead.

After flashing, `pio device monitor` on the `field`/`team_lead` boards
shows debug logs over the same USB port. The `gateway` board's USB port is
reserved for the SBC link (see `main.cpp`'s `setupGateway()`) — don't open
a serial monitor on it while the SBC service is also reading it.

## Running the SBC service

```
cd sbc-gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 server.py --serial-port /dev/ttyACM0   # find yours with `ls /dev/tty*` (Linux/Pi) before/after plugging in
```

Then open `http://<sbc-ip>:8000/` from any device on the same network —
this is the command map + ICS-214/PAR dashboard. It's a separate surface
from the on-node captive portals; a phone connected to a FIELD node's own
AP won't see this page unless it's also on the SBC's network.

## Tests

Wire-protocol round-trip tests run with only the stdlib (no extra deps):

```
cd sbc-gateway
python3 test_protocol.py
```

They cover the `TELEMETRY_BATCH` splitting contract — a full team is split
into multiple ≤250-byte ESP-NOW frames and must reassemble on the SBC with
every member recovered. See the frame math in
`firmware/src/mesh/espnow_mesh.cpp` (`sendTelemetryBatch`) and its Python
mirror in `sbc-gateway/protocol.py` (`TelemetryBatchPayload.split_frames`).

## Using it in the field

- **On-node captive portal**: join `ICS-<node_id>`'s WiFi AP from any
  phone, any browser pops the sign-in page automatically (or browse to
  any address). Status/PAR buttons, ICS-214 entry form, and read-only
  ICS-205/201 reference views.
- **BLE**: a companion phone app (not built yet — see Roadmap) would
  connect to the same node's BLE GATT service for lower-power background
  telemetry instead of holding a WiFi AP connection open.
- **PAR status**: tap OK/EMERGENCY/MAYDAY from either the captive portal
  or BLE; propagates node → team lead → gateway → SBC, logged with a full
  history in the SBC's SQLite DB (`sbc-gateway/db/telemetry.db`).
- **Command map**: color-coded by PAR status, team lead shown as a
  diamond anchor at the map's center, everyone else relative to it. Stale
  nodes (no report in 20s) render faded. Read the on-page caution note —
  it's there because the RSSI positions really do jump around.

## Known limitations / roadmap

- **No team-lead re-election.** If the TEAM_LEAD node drops, FIELD nodes
  currently just lose their uplink until it comes back — they don't
  promote a replacement. Fine for a 4-board single-team prototype; add
  election logic before trusting a larger fleet.
- **RSSI path-loss constants are placeholders** (`TX_POWER_AT_1M_DBM`,
  `PATH_LOSS_EXPONENT` in `sbc-gateway/fusion.py`). Calibrate against your
  actual hardware/mounting before trusting distances — see the comment
  there for the walk-and-measure procedure.
- **No phone companion app yet** — BLE and the AP are both live on the
  firmware side, but nothing's built to consume BLE from a phone besides
  a generic BLE scanner app. That's the natural next build once the mesh
  itself is validated on real hardware.
- **No local map in strike-team-only (no-SBC) mode.** The data reaches the
  team lead, but rendering a relative map from a phone directly (rather
  than only via the SBC) would need the phone app to run the same
  multilateration logic as `fusion.py` — worth porting once that phone
  app exists.
- **Single incident, single team_id space per fleet as configured** — the
  code supports multiple `team_id`s (see `TEAM_ID` build flag and
  `discoveredUplink`'s team-scoped matching), just untested with more
  than 4 boards.
