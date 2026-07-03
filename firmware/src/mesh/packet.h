#pragma once
#include <cstdint>

// Shared wire format — see docs/protocol.md. Keep this struct layout in sync
// with sbc-gateway/protocol.py; both sides parse the same bytes.

namespace icsmesh {

constexpr uint8_t kProtoMagic = 0xC5;
constexpr uint8_t kProtoVersion = 1;
constexpr uint8_t kMaxRssiNeighbors = 8;
constexpr uint8_t kMaxTelemetryBatch = 12;   // biggest team a lead aggregates; sent as
                                             // multiple ESP-NOW frames (only ~4 members
                                             // fit one 250B frame) — see sendTelemetryBatch
constexpr uint8_t kIcs214TextMax = 100;

enum class NodeRole : uint8_t { FIELD = 0, TEAM_LEAD = 1, GATEWAY = 2 };

enum class PacketType : uint8_t {
  BEACON = 0,
  TELEMETRY = 1,
  TELEMETRY_BATCH = 2,
  PAR_UPDATE = 3,
  ICS214_ENTRY = 4,
  ICS_FORM_REQUEST = 5,
  ICS_FORM_DATA = 6,
  CTRL = 7,
};

enum class ParStatus : uint8_t { OK = 0, EMERGENCY = 1, MAYDAY = 2, OUT_OF_CONTACT = 3 };

#pragma pack(push, 1)

struct PacketHeader {
  uint8_t magic = kProtoMagic;
  uint8_t version = kProtoVersion;
  uint8_t type;
  uint8_t team_id;
  uint16_t node_id;
  uint16_t seq;
  NodeRole role;   // lets a FIELD node auto-discover its TEAM_LEAD by team_id match, no fixed-ID config needed
};
static_assert(sizeof(PacketHeader) == 9, "header must stay 9 bytes on the wire");

// BEACON carries no payload — header alone. RSSI is read by the receiver's
// esp_now_recv callback, never transmitted by the sender.

struct RssiSample {
  uint16_t neighbor_node_id;
  int8_t rssi_dbm;
  uint16_t age_ms;   // how stale this sample was when packed
};

// Step-detection PDR telemetry (replaced the earlier dx/dy/dz/dtheta
// double-integration, which diverges on a torso mount — see
// docs/research-log.md 0.1). The node reports discrete steps + a stride
// estimate + an absolute heading in its OWN arbitrary frame (no shared
// north — there's no magnetometer); the SBC advances position as
// step_count × stride along heading (sbc-gateway/fusion.py predict()).
struct TelemetryPayload {
  uint8_t rssi_count;                          // <= kMaxRssiNeighbors, valid entries in rssi[]
  RssiSample rssi[kMaxRssiNeighbors];
  uint8_t step_count;                          // steps detected since last report
  uint16_t stride_mm;                          // mean stride length over those steps (Weinberg); 0 if no steps
  uint16_t heading_mrad;                        // current heading in the node's arbitrary frame, 0..6283 (milliradians)
  uint8_t heading_conf;                         // 255 right after an NMNI still-rezero, decays with time-since-rezero — SBC sizes map heading confidence
  uint8_t battery_pct;
  ParStatus par_status;
  uint8_t flags;                               // bit0 imu_present, bit1 gps_present, bit2 low_battery, bit3 stationary
};
// Locks the wire size against silent struct drift — must stay in sync with
// TELEMETRY_PAYLOAD_SIZE in sbc-gateway/protocol.py (mirrored by
// test_protocol.py). Changing it shifts the batch framing math.
static_assert(sizeof(TelemetryPayload) == 50, "TelemetryPayload must stay 50 bytes on the wire");

struct TelemetryBatchMember {
  uint16_t node_id;
  TelemetryPayload telemetry;
};

// Array-of-structs, NOT struct-of-arrays: this lets a truncated send (only
// the first `count` of kMaxTelemetryBatch slots, see
// EspNowMesh::sendTelemetryBatch) be a single contiguous memcpy that lines
// up with what the receiver expects. A SoA layout (parallel id[] and
// telemetry[] arrays) would desync mid-struct for any count < capacity,
// since the truncated byte range would spill from the tail of one array
// into the head of the other instead of cleanly stopping after `count`
// whole records.
struct TelemetryBatchPayload {
  uint8_t count;                                // <= kMaxTelemetryBatch
  TelemetryBatchMember member[kMaxTelemetryBatch];
};

struct ParUpdatePayload {
  ParStatus status;
  uint32_t since_ms;   // millis() timestamp status changed, node-local clock
};

struct Ics214EntryPayload {
  uint32_t timestamp_ms;
  char text[kIcs214TextMax];
};

struct IcsFormChunkPayload {
  uint8_t form_id;         // 201, 205, 214 mapped to small ints in ics_forms.h
  uint8_t chunk_index;
  uint8_t chunk_total;
  uint8_t chunk_len;
  uint8_t data[200];
};

#pragma pack(pop)

}  // namespace icsmesh
