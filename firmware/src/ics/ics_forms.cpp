#include "ics_forms.h"
#include <LittleFS.h>
#include <map>

namespace icsmesh {

namespace {
constexpr const char* kPath201 = "/ics201.txt";
constexpr const char* kPath205 = "/ics205.txt";
constexpr const char* kPath214 = "/ics214.txt";

// Deliberately not JSON — this is a flat-file, one-field-per-line format
// (field\tvalue) to avoid pulling in ArduinoJson for a prototype with only
// three simple record shapes. If the form schemas grow much, switch to
// ArduinoJson rather than extending this ad hoc.
String escapeField(const String& s) {
  String out = s;
  out.replace("\\", "\\\\");
  out.replace("\n", "\\n");
  out.replace("\t", "\\t");
  return out;
}
String unescapeField(const String& s) {
  String out;
  out.reserve(s.length());
  for (size_t i = 0; i < s.length(); i++) {
    if (s[i] == '\\' && i + 1 < s.length()) {
      char n = s[i + 1];
      if (n == 'n') { out += '\n'; i++; continue; }
      if (n == 't') { out += '\t'; i++; continue; }
      if (n == '\\') { out += '\\'; i++; continue; }
    }
    out += s[i];
  }
  return out;
}

// Pending chunk reassembly buffers, keyed by form_id. Cleared once complete.
std::map<uint8_t, std::vector<std::vector<uint8_t>>> pending_chunks;
std::map<uint8_t, uint8_t> pending_total;
}  // namespace

bool icsFormsBegin() {
  if (!LittleFS.begin(true)) {  // true = format on mount failure
    Serial.println("[ics] LittleFS mount failed even after format attempt");
    return false;
  }
  return true;
}

bool loadIcs201(Ics201Briefing& out) {
  File f = LittleFS.open(kPath201, "r");
  if (!f) return false;
  out.incident_name = unescapeField(f.readStringUntil('\t'));
  out.incident_commander = unescapeField(f.readStringUntil('\t'));
  out.objectives = unescapeField(f.readStringUntil('\t'));
  out.situation_summary = unescapeField(f.readStringUntil('\t'));
  out.updated_ms = f.readStringUntil('\n').toInt();
  f.close();
  return true;
}

bool saveIcs201(const Ics201Briefing& in) {
  File f = LittleFS.open(kPath201, "w");
  if (!f) return false;
  f.print(escapeField(in.incident_name)); f.print('\t');
  f.print(escapeField(in.incident_commander)); f.print('\t');
  f.print(escapeField(in.objectives)); f.print('\t');
  f.print(escapeField(in.situation_summary)); f.print('\t');
  f.print(in.updated_ms); f.print('\n');
  f.close();
  return true;
}

bool loadIcs205(Ics205CommsPlan& out) {
  File f = LittleFS.open(kPath205, "r");
  if (!f) return false;
  out.channels.clear();
  out.updated_ms = f.readStringUntil('\n').toInt();
  while (f.available()) {
    String line = f.readStringUntil('\n');
    if (line.length() == 0) continue;
    int t1 = line.indexOf('\t');
    int t2 = line.indexOf('\t', t1 + 1);
    if (t1 < 0 || t2 < 0) continue;
    CommsChannel c;
    c.name = unescapeField(line.substring(0, t1));
    c.freq_or_tone = unescapeField(line.substring(t1 + 1, t2));
    c.assignment = unescapeField(line.substring(t2 + 1));
    out.channels.push_back(c);
  }
  f.close();
  return true;
}

bool saveIcs205(const Ics205CommsPlan& in) {
  File f = LittleFS.open(kPath205, "w");
  if (!f) return false;
  f.print(in.updated_ms); f.print('\n');
  for (const auto& c : in.channels) {
    f.print(escapeField(c.name)); f.print('\t');
    f.print(escapeField(c.freq_or_tone)); f.print('\t');
    f.print(escapeField(c.assignment)); f.print('\n');
  }
  f.close();
  return true;
}

bool loadIcs214(Ics214Log& out) {
  File f = LittleFS.open(kPath214, "r");
  if (!f) return false;
  out.entries.clear();
  while (f.available()) {
    String line = f.readStringUntil('\n');
    if (line.length() == 0) continue;
    int t1 = line.indexOf('\t');
    int t2 = line.indexOf('\t', t1 + 1);
    if (t1 < 0 || t2 < 0) continue;
    Ics214Entry e;
    e.timestamp_ms = line.substring(0, t1).toInt();
    e.author_node_id = line.substring(t1 + 1, t2).toInt();
    e.text = unescapeField(line.substring(t2 + 1));
    out.entries.push_back(e);
  }
  f.close();
  return true;
}

bool saveIcs214(const Ics214Log& in) {
  File f = LittleFS.open(kPath214, "w");
  if (!f) return false;
  for (const auto& e : in.entries) {
    f.print(e.timestamp_ms); f.print('\t');
    f.print(e.author_node_id); f.print('\t');
    f.print(escapeField(e.text)); f.print('\n');
  }
  f.close();
  return true;
}

std::vector<std::vector<uint8_t>> serializeFormForTransport(uint8_t form_id) {
  const char* path = form_id == kFormId201 ? kPath201 : form_id == kFormId205 ? kPath205 : kPath214;
  std::vector<std::vector<uint8_t>> chunks;
  File f = LittleFS.open(path, "r");
  if (!f) return chunks;
  constexpr size_t kChunkSize = 200;
  while (f.available()) {
    std::vector<uint8_t> chunk(kChunkSize);
    size_t n = f.read(chunk.data(), kChunkSize);
    chunk.resize(n);
    chunks.push_back(std::move(chunk));
  }
  f.close();
  return chunks;
}

bool receiveFormChunk(uint8_t form_id, uint8_t chunk_index, uint8_t chunk_total,
                      const uint8_t* data, uint8_t len) {
  auto& buf = pending_chunks[form_id];
  if (buf.size() != chunk_total) buf.assign(chunk_total, {});
  pending_total[form_id] = chunk_total;
  buf[chunk_index].assign(data, data + len);

  bool complete = true;
  for (auto& c : buf) {
    if (c.empty()) { complete = false; break; }
  }
  if (!complete) return false;

  const char* path = form_id == kFormId201 ? kPath201 : form_id == kFormId205 ? kPath205 : kPath214;
  File f = LittleFS.open(path, "w");
  if (!f) return false;
  for (auto& c : buf) f.write(c.data(), c.size());
  f.close();

  pending_chunks.erase(form_id);
  pending_total.erase(form_id);
  return true;
}

}  // namespace icsmesh
