#pragma once
#include <Arduino.h>
#include <functional>
#include <map>
#include "packet.h"
#include <esp_now.h>

namespace icsmesh {

// One neighbor's most recent RSSI observation, as seen by THIS node.
struct NeighborObservation {
  int8_t rssi_dbm;
  uint32_t last_seen_ms;
};

// Handles ESP-NOW init, peer bookkeeping, beacon cadence, and RSSI capture.
// Role-specific behavior (aggregation, gateway bridging) lives in the role
// classes in main.cpp / gateway_bridge.cpp, which use this as their radio.
class EspNowMesh {
 public:
  using TelemetryHandler = std::function<void(uint16_t from_node, const TelemetryPayload&)>;
  using TelemetryBatchHandler = std::function<void(uint16_t from_node, const TelemetryBatchPayload&)>;
  using ParHandler = std::function<void(uint16_t from_node, const ParUpdatePayload&)>;
  using Ics214Handler = std::function<void(uint16_t from_node, const Ics214EntryPayload&)>;
  using IcsFormChunkHandler = std::function<void(uint16_t from_node, const IcsFormChunkPayload&)>;
  // Fires for EVERY received packet (in addition to the typed handlers
  // above), header + payload as raw bytes. This is what a GATEWAY-role
  // node uses exclusively — it forwards everything opaquely to the SBC
  // over serial without needing to understand any payload, so packet.h
  // changes never require touching gateway forwarding logic.
  using RawPacketHandler = std::function<void(const PacketHeader&, const uint8_t* payload, size_t payload_len)>;

  // node_id is derived from the low 16 bits of a hash of the MAC (see
  // node_id_from_mac below) so it's stable across reboots without needing
  // provisioning. team_id and role are set from NVS / captive portal.
  bool begin(uint8_t team_id, NodeRole role);

  // Broadcasts a BEACON. Call on a jittered timer (default handled by caller
  // in main.cpp's loop — jitter avoids every node in a team beaconing in
  // lockstep after a simultaneous power-on).
  void sendBeacon();

  // Unicast helpers. Peer is added via esp_now_add_peer on first sight if
  // not already known — see onRawRecv.
  bool sendTelemetry(uint16_t to_node, const TelemetryPayload& payload);
  bool sendTelemetryBatch(uint16_t to_node, const TelemetryBatchPayload& payload);
  bool sendParUpdate(uint16_t to_node, const ParUpdatePayload& payload);
  bool sendIcs214Entry(uint16_t to_node, const Ics214EntryPayload& payload);
  bool sendIcsFormChunk(uint16_t to_node, const IcsFormChunkPayload& payload);

  // Snapshot of RSSI to every neighbor heard from recently (BEACON or any
  // unicast). Feeds TelemetryPayload.rssi[] for this node's own report, and
  // is the raw input the SBC/phone fusion needs from every node.
  std::map<uint16_t, NeighborObservation> neighborRssi(uint32_t max_age_ms = 10000) const;

  void onTelemetry(TelemetryHandler h) { telemetry_handler_ = std::move(h); }
  void onTelemetryBatch(TelemetryBatchHandler h) { telemetry_batch_handler_ = std::move(h); }
  void onParUpdate(ParHandler h) { par_handler_ = std::move(h); }
  void onIcs214Entry(Ics214Handler h) { ics214_handler_ = std::move(h); }
  void onIcsFormChunk(IcsFormChunkHandler h) { ics_form_handler_ = std::move(h); }
  void onRawPacket(RawPacketHandler h) { raw_handler_ = std::move(h); }

  uint16_t selfNodeId() const { return self_node_id_; }
  uint16_t nextSeq() { return seq_++; }

  // FIELD nodes call this to find their team's uplink target; TEAM_LEAD
  // nodes call it to find the GATEWAY. Returns 0 (invalid) if none seen
  // yet. No re-election if the discovered node drops — see main.cpp's
  // watchdog, which just falls back to "hold telemetry locally" until a
  // new lead/gateway is heard (documented limitation, fine for a v0.1
  // single-team prototype; multi-team fleets should add re-election).
  uint16_t discoveredUplink(NodeRole target_role) const;

 private:
  static void staticOnRecv(const uint8_t* mac, const uint8_t* data, int len);
  static void staticOnRecvRssi(const esp_now_recv_info* info, const uint8_t* data, int len);
  void onRawRecv(const uint8_t* mac, const uint8_t* data, int len, int8_t rssi);
  bool ensurePeer(const uint8_t* mac);
  bool sendTo(uint16_t to_node, PacketType type, const uint8_t* payload, size_t payload_len);

  uint8_t team_id_ = 0;
  NodeRole role_ = NodeRole::FIELD;
  uint16_t self_node_id_ = 0;
  uint16_t seq_ = 0;

  std::map<uint16_t, std::array<uint8_t, 6>> node_id_to_mac_;
  std::map<uint16_t, NeighborObservation> neighbor_rssi_;
  std::map<uint16_t, NodeRole> node_roles_;
  std::map<uint16_t, uint8_t> node_teams_;

  TelemetryHandler telemetry_handler_;
  TelemetryBatchHandler telemetry_batch_handler_;
  ParHandler par_handler_;
  Ics214Handler ics214_handler_;
  IcsFormChunkHandler ics_form_handler_;
  RawPacketHandler raw_handler_;

  static EspNowMesh* self_;  // ESP-NOW callbacks are free functions; this is how they reach the instance
};

// Stable short ID from a MAC address — avoids needing a provisioning step
// to assign IDs. Collision probability is negligible for a fleet in the
// tens of nodes; if you scale past ~hundreds, add a collision-detect
// handshake before relying on this.
uint16_t node_id_from_mac(const uint8_t mac[6]);

}  // namespace icsmesh
