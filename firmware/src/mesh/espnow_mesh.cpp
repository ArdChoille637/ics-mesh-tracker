#include "espnow_mesh.h"
#include <esp_now.h>
#include <WiFi.h>
#include <esp_wifi.h>
#include <algorithm>

namespace icsmesh {

EspNowMesh* EspNowMesh::self_ = nullptr;

// ESP-NOW frames cap at 250 bytes total (header + payload — see sendTo). A
// TELEMETRY_BATCH payload is count(1B) + N × TelemetryBatchMember (54B each),
// so at most this many members ride in one frame alongside the 9-byte header:
//   (250 - 9 - 1) / 54 = 4
// kMaxTelemetryBatch (12) is larger than that on purpose, so a full team MUST
// be split across several frames — see sendTelemetryBatch.
constexpr size_t kMaxBatchMembersPerFrame =
    (250 - sizeof(PacketHeader) - sizeof(uint8_t)) / sizeof(TelemetryBatchMember);
static_assert(kMaxBatchMembersPerFrame >= 1,
              "TelemetryBatchMember too large to fit even one per ESP-NOW frame");

uint16_t node_id_from_mac(const uint8_t mac[6]) {
  // FNV-1a over the MAC, folded to 16 bits. Never returns 0 — 0 is reserved
  // for "broadcast/unknown" in application logic.
  uint32_t h = 2166136261u;
  for (int i = 0; i < 6; i++) {
    h ^= mac[i];
    h *= 16777619u;
  }
  uint16_t id = static_cast<uint16_t>((h ^ (h >> 16)) & 0xFFFF);
  return id == 0 ? 1 : id;
}

bool EspNowMesh::begin(uint8_t team_id, NodeRole role) {
  team_id_ = team_id;
  role_ = role;
  self_ = this;

  WiFi.mode(WIFI_STA);
  uint8_t mac[6];
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  self_node_id_ = node_id_from_mac(mac);

  if (esp_now_init() != ESP_OK) {
    Serial.println("[mesh] esp_now_init failed");
    return false;
  }

  esp_now_register_recv_cb(staticOnRecvRssi);

  // Broadcast peer, required before esp_now_send to FF:FF:FF:FF:FF:FF.
  esp_now_peer_info_t broadcast_peer = {};
  memset(broadcast_peer.peer_addr, 0xFF, 6);
  broadcast_peer.channel = 0;
  broadcast_peer.encrypt = false;
  if (!esp_now_is_peer_exist(broadcast_peer.peer_addr)) {
    esp_now_add_peer(&broadcast_peer);
  }

  Serial.printf("[mesh] node_id=%u team=%u role=%d ready\n", self_node_id_, team_id_, (int)role_);
  return true;
}

void EspNowMesh::sendBeacon() {
  PacketHeader hdr{};
  hdr.type = static_cast<uint8_t>(PacketType::BEACON);
  hdr.team_id = team_id_;
  hdr.node_id = self_node_id_;
  hdr.seq = nextSeq();
  hdr.role = role_;

  uint8_t broadcast_mac[6];
  memset(broadcast_mac, 0xFF, 6);
  esp_now_send(broadcast_mac, reinterpret_cast<uint8_t*>(&hdr), sizeof(hdr));
}

bool EspNowMesh::ensurePeer(const uint8_t* mac) {
  if (esp_now_is_peer_exist(mac)) return true;
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, mac, 6);
  peer.channel = 0;
  peer.encrypt = false;
  return esp_now_add_peer(&peer) == ESP_OK;
}

bool EspNowMesh::sendTo(uint16_t to_node, PacketType type, const uint8_t* payload, size_t payload_len) {
  auto it = node_id_to_mac_.find(to_node);
  if (it == node_id_to_mac_.end()) {
    Serial.printf("[mesh] send failed: unknown node_id %u (no MAC seen yet)\n", to_node);
    return false;
  }
  if (!ensurePeer(it->second.data())) return false;

  PacketHeader hdr{};
  hdr.type = static_cast<uint8_t>(type);
  hdr.team_id = team_id_;
  hdr.node_id = self_node_id_;
  hdr.seq = nextSeq();
  hdr.role = role_;

  // ESP-NOW payload cap is 250 bytes; header + largest payload
  // (TelemetryBatchPayload) must fit under that at the caller's chunking
  // level — TelemetryBatchPayload alone can exceed it for large teams, so
  // sendTelemetryBatch splits by count before calling sendTo per chunk.
  uint8_t buf[250];
  if (sizeof(hdr) + payload_len > sizeof(buf)) {
    Serial.println("[mesh] payload too large for one ESP-NOW frame");
    return false;
  }
  memcpy(buf, &hdr, sizeof(hdr));
  memcpy(buf + sizeof(hdr), payload, payload_len);
  return esp_now_send(it->second.data(), buf, sizeof(hdr) + payload_len) == ESP_OK;
}

bool EspNowMesh::sendTelemetry(uint16_t to_node, const TelemetryPayload& payload) {
  return sendTo(to_node, PacketType::TELEMETRY, reinterpret_cast<const uint8_t*>(&payload), sizeof(payload));
}

bool EspNowMesh::sendTelemetryBatch(uint16_t to_node, const TelemetryBatchPayload& payload) {
  // A full team's batch (up to kMaxTelemetryBatch = 12 members) does NOT fit
  // in one ESP-NOW frame — only kMaxBatchMembersPerFrame (4) members fit, and
  // a standard ICS strike team (5 resources + a leader = 6) already exceeds
  // that. An unsplit oversize send is silently rejected by sendTo (>250 B),
  // which would drop the WHOLE team's accountability data. So split into
  // ceil(count / kMaxBatchMembersPerFrame) frames, each an independently-valid
  // TELEMETRY_BATCH with its own count and a contiguous run of members (the
  // array-of-structs layout in packet.h is what lets each run be one memcpy).
  // The SBC merges frames from the same team lead as it ingests members
  // incrementally (sbc-gateway/server.py on_frame → fusion.ingest_telemetry),
  // so a partial batch is never lost.
  uint8_t total = std::min<uint8_t>(payload.count, kMaxTelemetryBatch);

  if (total == 0) {
    // Empty team: still send one count=0 frame so the SBC registers the lead
    // as its team's anchor (set_anchor) even before any member reports.
    uint8_t zero = 0;
    return sendTo(to_node, PacketType::TELEMETRY_BATCH, &zero, sizeof(zero));
  }

  bool all_ok = true;
  for (uint8_t start = 0; start < total; start += kMaxBatchMembersPerFrame) {
    uint8_t n = static_cast<uint8_t>(std::min<size_t>(kMaxBatchMembersPerFrame, total - start));
    TelemetryBatchPayload frame{};
    frame.count = n;
    for (uint8_t i = 0; i < n; i++) {
      frame.member[i] = payload.member[start + i];
    }
    size_t used = sizeof(uint8_t) + static_cast<size_t>(n) * sizeof(TelemetryBatchMember);
    if (!sendTo(to_node, PacketType::TELEMETRY_BATCH,
                reinterpret_cast<const uint8_t*>(&frame), used)) {
      all_ok = false;
    }
  }
  return all_ok;
}

bool EspNowMesh::sendParUpdate(uint16_t to_node, const ParUpdatePayload& payload) {
  return sendTo(to_node, PacketType::PAR_UPDATE, reinterpret_cast<const uint8_t*>(&payload), sizeof(payload));
}

bool EspNowMesh::sendIcs214Entry(uint16_t to_node, const Ics214EntryPayload& payload) {
  return sendTo(to_node, PacketType::ICS214_ENTRY, reinterpret_cast<const uint8_t*>(&payload), sizeof(payload));
}

bool EspNowMesh::sendIcsFormChunk(uint16_t to_node, const IcsFormChunkPayload& payload) {
  return sendTo(to_node, PacketType::ICS_FORM_DATA, reinterpret_cast<const uint8_t*>(&payload), sizeof(payload));
}

uint16_t EspNowMesh::discoveredUplink(NodeRole target_role) const {
  uint32_t now = millis();
  for (const auto& [node_id, role] : node_roles_) {
    if (role != target_role) continue;
    // GATEWAY relays every team; TEAM_LEAD only relays its own team.
    if (target_role == NodeRole::TEAM_LEAD) {
      auto team_it = node_teams_.find(node_id);
      if (team_it == node_teams_.end() || team_it->second != team_id_) continue;
    }
    auto obs_it = neighbor_rssi_.find(node_id);
    if (obs_it == neighbor_rssi_.end() || now - obs_it->second.last_seen_ms > 15000) continue;
    return node_id;
  }
  return 0;
}

std::map<uint16_t, NeighborObservation> EspNowMesh::neighborRssi(uint32_t max_age_ms) const {
  std::map<uint16_t, NeighborObservation> fresh;
  uint32_t now = millis();
  for (const auto& [node_id, obs] : neighbor_rssi_) {
    if (now - obs.last_seen_ms <= max_age_ms) fresh[node_id] = obs;
  }
  return fresh;
}

void EspNowMesh::staticOnRecvRssi(const esp_now_recv_info* info, const uint8_t* data, int len) {
  if (!self_) return;
  int8_t rssi = info->rx_ctrl ? info->rx_ctrl->rssi : 0;
  self_->onRawRecv(info->src_addr, data, len, rssi);
}

void EspNowMesh::onRawRecv(const uint8_t* mac, const uint8_t* data, int len, int8_t rssi) {
  if (len < static_cast<int>(sizeof(PacketHeader))) return;
  PacketHeader hdr;
  memcpy(&hdr, data, sizeof(hdr));
  if (hdr.magic != kProtoMagic || hdr.version != kProtoVersion) return;
  if (hdr.node_id == self_node_id_) return;  // heard our own broadcast echo

  std::array<uint8_t, 6> mac_arr;
  memcpy(mac_arr.data(), mac, 6);
  node_id_to_mac_[hdr.node_id] = mac_arr;
  neighbor_rssi_[hdr.node_id] = {rssi, millis()};
  node_roles_[hdr.node_id] = hdr.role;
  node_teams_[hdr.node_id] = hdr.team_id;

  const uint8_t* payload = data + sizeof(hdr);
  int payload_len = len - sizeof(hdr);
  auto type = static_cast<PacketType>(hdr.type);

  if (raw_handler_) raw_handler_(hdr, payload, payload_len);

  switch (type) {
    case PacketType::BEACON:
      break;  // RSSI already recorded above; nothing else to do
    case PacketType::TELEMETRY:
      if (telemetry_handler_ && payload_len >= static_cast<int>(sizeof(TelemetryPayload))) {
        TelemetryPayload p;
        memcpy(&p, payload, sizeof(p));
        telemetry_handler_(hdr.node_id, p);
      }
      break;
    case PacketType::TELEMETRY_BATCH:
      if (telemetry_batch_handler_ && payload_len >= 1) {
        TelemetryBatchPayload p{};
        int copy_len = std::min(payload_len, static_cast<int>(sizeof(p)));
        memcpy(&p, payload, copy_len);
        telemetry_batch_handler_(hdr.node_id, p);
      }
      break;
    case PacketType::PAR_UPDATE:
      if (par_handler_ && payload_len >= static_cast<int>(sizeof(ParUpdatePayload))) {
        ParUpdatePayload p;
        memcpy(&p, payload, sizeof(p));
        par_handler_(hdr.node_id, p);
      }
      break;
    case PacketType::ICS214_ENTRY:
      if (ics214_handler_ && payload_len >= static_cast<int>(sizeof(Ics214EntryPayload))) {
        Ics214EntryPayload p;
        memcpy(&p, payload, sizeof(p));
        ics214_handler_(hdr.node_id, p);
      }
      break;
    case PacketType::ICS_FORM_DATA:
    case PacketType::ICS_FORM_REQUEST:
      if (ics_form_handler_ && payload_len >= static_cast<int>(sizeof(IcsFormChunkPayload))) {
        IcsFormChunkPayload p;
        memcpy(&p, payload, sizeof(p));
        ics_form_handler_(hdr.node_id, p);
      }
      break;
    case PacketType::CTRL:
      break;  // reserved — no handler wired yet
  }
}

}  // namespace icsmesh
