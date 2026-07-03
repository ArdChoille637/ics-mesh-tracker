# Wire protocol — ICS mesh tracker

One binary packet format is shared by three transports: ESP-NOW (node↔node),
BLE GATT (node↔phone), and USB-serial (gateway node↔SBC). Same struct,
different envelope. This keeps the fusion math (on phone or SBC) working from
one schema regardless of which transport delivered it.

## Node roles

| Role       | Count (typical strike team) | Job |
|------------|------------------------------|-----|
| `FIELD`    | most nodes                   | worn by a responder; beacons, reports RSSI+IMU, PAR status, ICS-214 entries |
| `TEAM_LEAD`| 1 per team                   | everything FIELD does, plus aggregates its team's telemetry and relays to GATEWAY (or acts as the fusion authority when no SBC is present) |
| `GATEWAY`  | 1 per incident (optional)    | USB-attached to the on-site SBC; bridges ESP-NOW traffic to serial, no beacon/PAR duties |

Role is a build-time flag (`-D NODE_ROLE=...`) or set via the captive portal
and stored in NVS, so the same firmware image works for all four XIAO boards.

## Packet header (9 bytes, little-endian)

```
uint8_t  magic        // 0xC5 fixed
uint8_t  version      // protocol version, currently 1
uint8_t  type         // PacketType enum, see packet.h
uint8_t  team_id       // 0-255, which strike team this node belongs to
uint16_t node_id       // short ID assigned from MAC hash, not the full MAC
uint16_t seq           // rolling sequence number, for loss detection
uint8_t  role          // NodeRole enum — lets FIELD nodes auto-discover their TEAM_LEAD by team_id+role match
```

Followed by a type-specific payload (see `firmware/src/mesh/packet.h` for the
canonical struct definitions — this doc mirrors them, but the header is
authoritative if they ever drift).

## Packet types

- **BEACON** (broadcast, ESP-NOW only): node_id + team_id, no payload beyond
  header. Every node broadcasts this on a jittered interval (default 2s ±
  400ms to avoid collision lockstep). Receivers record RSSI + timestamp
  against the sender's node_id. This is the raw input to trilateration —
  RSSI is read off the ESP-NOW receive callback, not transmitted by the
  sender.
- **TELEMETRY**: one node's current state — RSSI vector (up to 8 nearest
  neighbors: node_id + rssi_dbm + age_ms), **step-detection PDR** since last
  report (step_count, mean stride_mm, absolute heading_mrad in the node's own
  arbitrary frame, heading_conf 0-255), battery_pct, par_status (see below),
  flags (bit0 imu_present, bit1 gps_present, bit2 low_battery, bit3
  stationary). The SBC advances each node's position by step_count × stride
  along heading (`fusion.py` `predict()`). This replaced an earlier (dx,dy,dz,
  dtheta) double-integration, which diverges on a torso mount — see
  `research-log.md` item 0.1. Heading is gyro-yaw integrated with NMNI bias
  re-zeroing during detected stillness (no magnetometer — item 1.4);
  heading_conf decays with time since the last re-zero so the map can size
  heading uncertainty. On-node sampling is a fixed 100 Hz MPU6050-FIFO drain,
  decoupled from the radio-perturbed main loop (item 1.5).
- **TELEMETRY_BATCH** (TEAM_LEAD → GATEWAY / SBC only): array of TELEMETRY
  payloads collected from the lead's team, sent every 1s regardless of
  whether members' individual reports changed, so the SBC always has a
  full team snapshot. A team larger than 4 members is split across multiple
  ESP-NOW frames — see *Transport specifics* below.
- **PAR_UPDATE**: explicit accountability status change — `OK`, `EMERGENCY`,
  `MAYDAY`, `OUT_OF_CONTACT` (set by watchdog when a node hasn't been heard
  from in > 45s). Sent immediately, out of band from the periodic
  TELEMETRY cadence, and re-sent every 5s while status != OK.
- **ICS214_ENTRY**: one activity-log line (timestamp, free-text up to 100
  bytes, entered on the node's captive-portal form or relayed from the
  phone app over BLE). Relayed team-lead → SBC and merged into that team's
  ICS-214.
- **ICS_FORM_REQUEST / ICS_FORM_DATA**: a node asks its team lead (or the
  SBC, via the lead) for the current ICS-205/201 reference data so it can
  render it on its own captive portal even if the phone never talked to
  the SBC directly. Chunked (fits in ESP-NOW's 250-byte payload cap) using
  a simple `chunk_index/chunk_total` pair.
- **CTRL**: command→node message (e.g. SBC pushes an updated ICS-205 comms
  plan, or tells a node to re-elect a new team lead if the old one drops).

## Why RSSI trilateration needs a caveat

ESP-NOW RSSI-to-distance is a log-distance path-loss estimate, not a
measurement. Expect ±5-15m of error even after calibration, worse indoors or
with body-worn attenuation. Treat the SBC's/phone's output as "relative
topology with soft position hints," not a survey. The IMU dead-reckoning
fusion (see `docs/positioning.md`, and `sbc-gateway/fusion.py`) exists to
smooth RSSI's noise between beacon intervals, not to replace it — IMU alone
drifts unbounded, RSSI alone is noisy and quantized. Fusing both is a
1D-per-axis complementary/Kalman filter, reset softly toward RSSI on every
beacon cycle.

## Transport specifics

- **ESP-NOW**: `esp_now_send` broadcast for BEACON, unicast for everything
  else (peer list built from observed node_ids). Max payload 250 bytes
  (header + body). A `TELEMETRY_BATCH` from a full team exceeds that — each
  member is 54 bytes, so only 4 fit alongside the 9-byte header — so
  `EspNowMesh::sendTelemetryBatch` splits the team into
  `ceil(count / 4)` frames, each an independently-valid `TELEMETRY_BATCH`
  with its own `count` and a whole number of members (never split
  mid-member). The SBC merges the frames from one team lead as it ingests
  members incrementally (`server.py` `on_frame` → `fusion.ingest_telemetry`),
  so a partial batch is never lost. Before this splitting existed, any team
  larger than 4 produced an oversize frame that `sendTo` silently rejected,
  dropping the entire batch.
- **BLE**: GATT service UUID `c5000000-0000-1000-8000-00805f9b34fb`
  (placeholder — regenerate before shipping). One characteristic per
  packet type family (telemetry, ics_forms, ctrl), notify-on-change for
  telemetry, write for phone→node ICS-214 entries.
- **Serial (gateway↔SBC)**: same header+payload, wrapped in a minimal frame:
  `0x7E <len:u16> <payload> <crc8>`, sent over the GATEWAY node's native
  USB-CDC port (no separate UART needed — same cable that powers it).
  Baud rate is nominal for USB-CDC; `sbc-gateway/serial_link.py` opens it
  at 115200 for compatibility but the actual transfer isn't rate-limited
  by that setting. `sbc-gateway/protocol.py` and
  `firmware/src/mesh/gateway_bridge.h` must stay in sync on the framing.
