// ICS mesh tracker — firmware entry point.
//
// One image, four possible roles (see docs/protocol.md). Role/team are set
// at build time via platformio.ini environments (env:field, env:team_lead,
// env:gateway) rather than runtime provisioning — with only 4 boards in
// this prototype fleet, re-flashing per role is simpler than building a
// provisioning UI. Promote that to a captive-portal config step once the
// fleet grows past what you can track by which USB port it's on.
#include <Arduino.h>
#include <Wire.h>
#include "board_pins.h"
#include "mesh/espnow_mesh.h"
#include "mesh/gateway_bridge.h"
#include "imu/mpu6050_driver.h"
#include "imu/step_pdr.h"
#include "ble/ics_ble_service.h"
#include "webportal/captive_portal.h"
#include "ics/ics_forms.h"
#include "storage/sd_logger.h"

using namespace icsmesh;

#ifndef NODE_ROLE
#define NODE_ROLE FIELD
#endif
#ifndef TEAM_ID
#define TEAM_ID 1
#endif

#define ROLE_FIELD 0
#define ROLE_TEAM_LEAD 1
#define ROLE_GATEWAY 2
#define CONCAT_ROLE(x) ROLE_##x
#define RESOLVE_ROLE(x) CONCAT_ROLE(x)
constexpr int kBuildRole = RESOLVE_ROLE(NODE_ROLE);

EspNowMesh mesh;
Mpu6050Driver imu_driver;
StepPdr pdr(imu_driver);   // step-detection PDR + NMNI heading (replaced double-integration — see step_pdr.h)
IcsBleService ble;             // FIELD / TEAM_LEAD only
CaptivePortal portal;          // FIELD / TEAM_LEAD only
GatewayBridge gateway_bridge;  // GATEWAY only
SdLogger sd_logger;            // TEAM_LEAD only (begin() called there); no-op elsewhere

ParStatus g_par_status = ParStatus::OK;
uint32_t g_last_beacon_ms = 0;
uint32_t g_beacon_interval_ms = 2000;
uint32_t g_last_telemetry_ms = 0;
uint32_t g_last_uplink_check_ms = 0;
uint32_t g_last_sd_tele_ms = 0;
uint32_t g_last_hb_ms = 0;
uint16_t g_uplink_node = 0;  // team lead (for FIELD) or gateway (for TEAM_LEAD), 0 = not yet discovered

// TEAM_LEAD only: buffered telemetry from its own team members, flushed as
// a TELEMETRY_BATCH on each aggregation cycle.
TelemetryBatchPayload g_team_batch;

void applyParStatus(ParStatus s) {
  g_par_status = s;
  if (kBuildRole != ROLE_GATEWAY) portal.setParStatus(s);
  ParUpdatePayload p{s, millis()};
  if (g_uplink_node) mesh.sendParUpdate(g_uplink_node, p);
  // Persist this node's own PAR transitions to the removable field record
  // (team lead only; no-op where no SD card is present).
  sd_logger.logEvent("PAR", String("self=") + (int)s);
}

TelemetryPayload buildOwnTelemetry() {
  TelemetryPayload t{};
  auto neighbors = mesh.neighborRssi();
  t.rssi_count = 0;
  uint32_t now = millis();
  for (const auto& [node_id, obs] : neighbors) {
    if (t.rssi_count >= kMaxRssiNeighbors) break;
    t.rssi[t.rssi_count].neighbor_node_id = node_id;
    t.rssi[t.rssi_count].rssi_dbm = obs.rssi_dbm;
    t.rssi[t.rssi_count].age_ms = static_cast<uint16_t>(now - obs.last_seen_ms);
    t.rssi_count++;
  }
  StepPdr::Report r = pdr.popReport();
  t.step_count = r.step_count;
  t.stride_mm = r.stride_mm;
  t.heading_mrad = r.heading_mrad;
  t.heading_conf = r.heading_conf;
  t.battery_pct = 100;  // TODO: wire to XIAO ESP32S3's battery ADC pin if running off the JST connector
  t.par_status = g_par_status;
  t.flags = (imu_driver.present() ? 0x01 : 0x00) | (r.stationary ? 0x08 : 0x00);
  return t;
}

void setupFieldOrLead() {
  ble.begin("ICS-" + String(mesh.selfNodeId()));
  ble.onIcs214FromPhone([](const String& text) {
    Ics214EntryPayload p{};
    p.timestamp_ms = millis();
    strncpy(p.text, text.c_str(), kIcs214TextMax - 1);
    if (g_uplink_node) mesh.sendIcs214Entry(g_uplink_node, p);
    Ics214Log log;
    loadIcs214(log);
    log.append(p.timestamp_ms, mesh.selfNodeId(), text);
    saveIcs214(log);
    sd_logger.logEvent("214", String(mesh.selfNodeId()) + ": " + text);
  });
  ble.onParFromPhone(applyParStatus);

  portal.begin(String(mesh.selfNodeId()), mesh.selfNodeId());
  portal.onParChange(applyParStatus);
  portal.onIcs214Add([](const String& text) {
    Ics214EntryPayload p{};
    p.timestamp_ms = millis();
    strncpy(p.text, text.c_str(), kIcs214TextMax - 1);
    if (g_uplink_node) mesh.sendIcs214Entry(g_uplink_node, p);
    sd_logger.logEvent("214", String(mesh.selfNodeId()) + ": " + text);
  });

  // Initialize the I2C bus BEFORE probing the IMU. The MPU-6050 driver only
  // issues Wire transactions; without this begin() the ESP32 I2C peripheral is
  // never brought up and every probe fails (SDA/SCL = D4/D5, see board_pins.h).
  Wire.begin(pins::kI2cSda, pins::kI2cScl);

  // Mount the microSD field record if a card is wired + inserted. FIELD nodes
  // are soldered identically to the TEAM_LEAD (same SD adapter), so every
  // non-gateway node ATTEMPTS the mount — a node without a card just gets
  // sd=0 on the heartbeat and logEvent() stays a no-op. Removable /ICSLOG.CSV
  // (PAR + ICS-214) survives node loss / a dead uplink on whichever node
  // carries a card.
  if (sd_logger.begin()) {
    Serial.println("[main] microSD mounted — logging to /ICSLOG.CSV");
  } else {
    Serial.println("[main] no microSD detected — field record disabled");
  }

  if (imu_driver.begin()) {
    pdr.calibrate();                 // static boot: seed gyro-z bias (hold unit still ~1 s)
    imu_driver.enableFifo100Hz();    // switch to fixed-rate FIFO streaming (item 1.5)
    Serial.println("[main] IMU present, calibrated, FIFO @100 Hz");
  } else {
    Serial.println("[main] no IMU detected — PDR disabled, RSSI-only positioning");
  }
}

void setupTeamLead() {
  setupFieldOrLead();  // includes the SD mount attempt (shared with FIELD)
  g_team_batch.count = 0;

  // Team lead relays its team's telemetry upward, and also forwards
  // ICS-214 entries / PAR updates it receives from its own team FIELD
  // nodes on to the gateway (or holds them if no gateway is in range yet
  // — this is the "strike-team-only, no SBC" deployment mode).
  mesh.onTelemetry([](uint16_t from_node, const TelemetryPayload& t) {
    for (uint8_t i = 0; i < g_team_batch.count; i++) {
      if (g_team_batch.member[i].node_id == from_node) {
        g_team_batch.member[i].telemetry = t;
        return;
      }
    }
    if (g_team_batch.count < kMaxTelemetryBatch) {
      g_team_batch.member[g_team_batch.count].node_id = from_node;
      g_team_batch.member[g_team_batch.count].telemetry = t;
      g_team_batch.count++;
    }
  });
  mesh.onParUpdate([](uint16_t from_node, const ParUpdatePayload& p) {
    Serial.printf("[lead] PAR from %u: %d\n", from_node, (int)p.status);
    if (g_uplink_node) mesh.sendParUpdate(g_uplink_node, p);
    sd_logger.logEvent("PAR", String(from_node) + "=" + (int)p.status);
  });
  mesh.onIcs214Entry([](uint16_t from_node, const Ics214EntryPayload& p) {
    if (g_uplink_node) mesh.sendIcs214Entry(g_uplink_node, p);
    Ics214Log log;
    loadIcs214(log);
    log.append(p.timestamp_ms, from_node, String(p.text));
    saveIcs214(log);
    sd_logger.logEvent("214", String(from_node) + ": " + String(p.text));
  });
}

void setupGateway() {
  // GATEWAY is the one role that's USB-tethered to the SBC, so the native
  // USB-CDC port (Serial) IS the SBC link — there's no separate debug
  // console on this role. Watch the SBC-side log instead when debugging a
  // gateway node.
  gateway_bridge.begin(Serial);
  mesh.onRawPacket([](const PacketHeader& hdr, const uint8_t* payload, size_t len) {
    gateway_bridge.forwardToSbc(hdr, payload, len);
  });
}

void setup() {
  Serial.begin(115200);
  icsFormsBegin();

  NodeRole role = static_cast<NodeRole>(kBuildRole);
  mesh.begin(TEAM_ID, role);

  if (kBuildRole == ROLE_FIELD) {
    setupFieldOrLead();
  } else if (kBuildRole == ROLE_TEAM_LEAD) {
    setupTeamLead();
  } else {
    setupGateway();
  }
}

void loop() {
  uint32_t now = millis();

  if (now - g_last_beacon_ms > g_beacon_interval_ms) {
    g_last_beacon_ms = now;
    g_beacon_interval_ms = 1600 + (esp_random() % 800);  // re-jitter each cycle
    mesh.sendBeacon();
  }

  // Drain the MPU6050 FIFO and feed each buffered sample to the PDR at its
  // FIXED FIFO period (not the wall-clock loop delta). Because the FIFO's
  // internal ODR is the sample clock, a loop stall just means a bigger burst
  // next drain — the statistic stays valid (item 1.5). 24 samples covers a
  // ~240 ms stall; anything longer is caught by the FIFO-overflow reset.
  if (kBuildRole != ROLE_GATEWAY && imu_driver.present()) {
    ImuSample burst[24];
    int n = imu_driver.drainFifo(burst, 24);
    for (int i = 0; i < n; i++) pdr.update(burst[i], StepPdr::kSamplePeriodS);
  }

  if (now - g_last_uplink_check_ms > 3000) {
    g_last_uplink_check_ms = now;
    NodeRole want = (kBuildRole == ROLE_TEAM_LEAD) ? NodeRole::GATEWAY : NodeRole::TEAM_LEAD;
    if (kBuildRole != ROLE_GATEWAY) g_uplink_node = mesh.discoveredUplink(want);
  }

  if (kBuildRole == ROLE_FIELD && now - g_last_telemetry_ms > 1000) {
    g_last_telemetry_ms = now;
    TelemetryPayload t = buildOwnTelemetry();
    ble.notifyTelemetry(t);
    if (g_uplink_node) mesh.sendTelemetry(g_uplink_node, t);
  }

  if (kBuildRole == ROLE_TEAM_LEAD && now - g_last_telemetry_ms > 1000) {
    g_last_telemetry_ms = now;
    TelemetryPayload self_t = buildOwnTelemetry();
    ble.notifyTelemetry(self_t);
    // fold self into the batch too, so the SBC/phone sees the lead's own position
    if (g_team_batch.count < kMaxTelemetryBatch) {
      bool found = false;
      for (uint8_t i = 0; i < g_team_batch.count; i++) {
        if (g_team_batch.member[i].node_id == mesh.selfNodeId()) { g_team_batch.member[i].telemetry = self_t; found = true; break; }
      }
      if (!found) {
        g_team_batch.member[g_team_batch.count].node_id = mesh.selfNodeId();
        g_team_batch.member[g_team_batch.count].telemetry = self_t;
        g_team_batch.count++;
      }
    }
    if (g_uplink_node) mesh.sendTelemetryBatch(g_uplink_node, g_team_batch);

    // Periodic team snapshot to the SD field record (every ~5 s, not every
    // beat — enough to reconstruct the incident, easy on the card).
    if (now - g_last_sd_tele_ms > 5000) {
      g_last_sd_tele_ms = now;
      sd_logger.logEvent("TELE", String("team=") + g_team_batch.count +
                                 " steps=" + self_t.step_count +
                                 " hdg_mrad=" + self_t.heading_mrad +
                                 " par=" + (int)self_t.par_status);
    }
  }

  // Drain any queued SD writes here, in loop context — logEvent() only enqueues
  // (it's called from the WiFi/BLE tasks too), so this is the one place the card
  // is actually touched. No-op on FIELD/GATEWAY (queue never created).
  sd_logger.service();

  // Serial health heartbeat (FIELD / TEAM_LEAD only — the GATEWAY's Serial is
  // the binary SBC link and must stay clean). One line every 3 s so a laptop on
  // the USB port can confirm node health (IMU wired? SD mounted? mesh peers?)
  // without having to catch the boot banner across the native-USB re-enumerate.
  if (kBuildRole != ROLE_GATEWAY && now - g_last_hb_ms > 3000) {
    g_last_hb_ms = now;
    Serial.printf("[hb] node=%u role=%s imu=%d sd=%d peers=%u team=%u dropped=%u up=%lus\n",
                  mesh.selfNodeId(),
                  (kBuildRole == ROLE_TEAM_LEAD ? "LEAD" : "FIELD"),
                  imu_driver.present() ? 1 : 0,
                  sd_logger.present() ? 1 : 0,
                  (unsigned)mesh.neighborRssi().size(),
                  (unsigned)g_team_batch.count,
                  (unsigned)sd_logger.dropped(),
                  (unsigned long)(now / 1000));
  }

  if (kBuildRole != ROLE_GATEWAY) portal.handleClient();
}
