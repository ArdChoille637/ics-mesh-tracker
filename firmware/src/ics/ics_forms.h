#pragma once
#include <Arduino.h>
#include <vector>

namespace icsmesh {

// Reference forms (201, 205) are authored on the SBC / command side and
// pushed down for on-node display so a responder can check comms plan or
// incident briefing from their own captive portal even without reaching
// the phone app. 214 is the reverse — generated at the edge (any node),
// synced upward. All three are stored on LittleFS as small JSON-ish text
// blobs (not real JSON to avoid pulling in ArduinoJson for a prototype —
// see serialize()/deserialize() below) so they survive reboot.

constexpr uint8_t kFormId201 = 201;
constexpr uint8_t kFormId205 = 205;
constexpr uint8_t kFormId214 = 214;

struct Ics201Briefing {
  String incident_name;
  String incident_commander;
  String objectives;      // free text, one line per objective, \n separated
  String situation_summary;
  uint32_t updated_ms = 0;
};

struct CommsChannel {
  String name;        // e.g. "TAC-3"
  String freq_or_tone;
  String assignment;  // e.g. "Strike Team 2 primary"
};

struct Ics205CommsPlan {
  std::vector<CommsChannel> channels;
  uint32_t updated_ms = 0;
};

struct Ics214Entry {
  uint32_t timestamp_ms;
  uint16_t author_node_id;
  String text;
};

struct Ics214Log {
  std::vector<Ics214Entry> entries;  // capped, oldest dropped past kMaxEntries
  static constexpr size_t kMaxEntries = 200;

  void append(uint32_t ts, uint16_t author, const String& text) {
    entries.push_back({ts, author, text});
    if (entries.size() > kMaxEntries) entries.erase(entries.begin());
  }
};

// Loads/saves each form to LittleFS. Call icsFormsBegin() once at startup
// (mounts/formats LittleFS if needed) before using any of these.
bool icsFormsBegin();

bool loadIcs201(Ics201Briefing& out);
bool saveIcs201(const Ics201Briefing& in);

bool loadIcs205(Ics205CommsPlan& out);
bool saveIcs205(const Ics205CommsPlan& in);

bool loadIcs214(Ics214Log& out);
bool saveIcs214(const Ics214Log& in);

// Flat serialization for mesh transport, chunked to fit IcsFormChunkPayload
// (200 bytes/chunk). Returns chunks as raw byte blobs; caller assigns
// chunk_index/chunk_total and sends via EspNowMesh::sendIcsFormChunk.
std::vector<std::vector<uint8_t>> serializeFormForTransport(uint8_t form_id);

// Reassembles chunks (caller collects until chunk_index==chunk_total-1 for
// every index 0..total-1) and writes the result to the matching LittleFS
// file, returning true once complete and saved.
bool receiveFormChunk(uint8_t form_id, uint8_t chunk_index, uint8_t chunk_total,
                      const uint8_t* data, uint8_t len);

}  // namespace icsmesh
