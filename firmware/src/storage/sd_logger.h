#pragma once
#include <Arduino.h>
#include <SPI.h>
#include <SD.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "../board_pins.h"

namespace icsmesh {

// Removable-media field record for the TEAM_LEAD node.
//
// The team lead is the aggregation point for its strike team, so it's the
// natural place to keep a persistent log that (a) survives the node being lost,
// crushed, or its internal flash wiped, and (b) is a pull-the-card-and-read-it
// record for after-action review — neither of which LittleFS (internal, not
// removable) gives you. It also backstops the "strike-team-only, no gateway/SBC
// in range" mode: the 214 activity log and PAR history are still captured on
// removable media even when nothing reaches a command map.
//
// CONCURRENCY MODEL (this is the important part): logEvent() is called from
// THREE task contexts on a team lead — loop() (BOOT + the 5 s telemetry
// snapshot), the ESP-NOW receive callback (PAR/214 relayed from field nodes,
// which runs in the WiFi task, NOT loop()), and the BLE host task (214 typed on
// the phone). If each of those did its own SD.open/write/close we'd get (1)
// interleaved writes racing on the shared SPI bus + FAT → corruption, and (2) a
// tens-of-ms blocking card write inside the ESP-NOW recv callback, which
// Espressif requires to be short — stalling the WiFi task drops mesh frames.
// So logEvent() only ENQUEUES (lock-free, never blocks, safe from any task);
// the actual card I/O happens in service(), called ONLY from loop(). All SD
// access is thus serialized to one context and decoupled from the radio tasks.
//
// FIELD/GATEWAY nodes don't wire the SD adapter and never call begin(), so the
// queue is never created and every logEvent() is a no-op — the same firmware
// image runs on every role (see board_pins.h). SD.h/SPI.h ship with the
// arduino-esp32 core, so this adds no PlatformIO lib_deps.
class SdLogger {
 public:
  // Mount the card on the XIAO's default SPI bus (board_pins.h). Returns false
  // if no card/adapter is present (a FIELD-style build with no SD wired just
  // quietly skips logging). Idempotent on the queue.
  bool begin() {
    if (!queue_) queue_ = xQueueCreate(kQueueDepth, sizeof(LogEvent));
    present_ = mount_();
    if (present_) {
      char msg[48];
      snprintf(msg, sizeof(msg), "team-lead SD log, %llu MB",
               (unsigned long long)(SD.cardSize() / (1024ULL * 1024ULL)));
      logEvent("BOOT", msg);
    }
    return present_;
  }

  bool present() const { return present_; }

  // Enqueue one event. NON-BLOCKING and safe from any task context (loop,
  // ESP-NOW WiFi recv callback, BLE host task): it timestamps + copies into a
  // fixed struct and xQueueSend()s with zero wait. A full queue drops the line
  // (counted) rather than block — losing a log line beats dropping a mesh
  // frame. Does no SD/SPI I/O.
  void logEvent(const char* tag, const String& text) {
    if (!queue_ || !present_) return;
    LogEvent e;
    e.ms = millis();
    strncpy(e.tag, tag, sizeof(e.tag) - 1);
    e.tag[sizeof(e.tag) - 1] = '\0';
    strncpy(e.text, text.c_str(), sizeof(e.text) - 1);
    e.text[sizeof(e.text) - 1] = '\0';
    if (xQueueSend(queue_, &e, 0) != pdTRUE) dropped_++;
  }

  // Drain the queue to the card. Call from loop() ONLY — this is where the
  // blocking open/write/close lives, off the WiFi/BLE tasks. A transient open
  // failure (SPI glitch, busy card, housekeeping stall on a vibrating node)
  // drops just that line and keeps going; after several in a row it transparently
  // remounts. It never latches logging off permanently the way a naive
  // one-strike disable would — the field record is the whole point of this role.
  void service() {
    if (!queue_ || !present_) return;
    LogEvent e;
    while (xQueueReceive(queue_, &e, 0) == pdTRUE) {
      File f = SD.open(kLogPath, FILE_APPEND);
      if (!f) {
        dropped_++;
        if (++open_fails_ >= kRemountAfter) mount_();  // best-effort recover
        continue;
      }
      open_fails_ = 0;
      f.print(e.ms);
      f.print(',');
      f.print(e.tag);
      f.print(",\"");
      writeEscaped_(f, e.text);   // RFC4180: double embedded quotes, kill newlines
      f.println('"');
      f.close();
    }
  }

  uint32_t dropped() const { return dropped_; }

 private:
  struct LogEvent {
    uint32_t ms;
    char tag[6];
    char text[150];
  };
  static constexpr int kQueueDepth = 16;
  static constexpr uint8_t kRemountAfter = 5;
  static constexpr const char* kLogPath = "/ICSLOG.CSV";

  bool mount_() {
    SPI.begin(pins::kSpiSck, pins::kSpiMiso, pins::kSpiMosi, pins::kSdCs);
    bool ok = SD.begin(pins::kSdCs, SPI) && SD.cardType() != CARD_NONE;
    open_fails_ = 0;
    return ok;
  }

  // Write one CSV text field body with RFC4180 escaping: an embedded double
  // quote is doubled; a CR/LF (which would split the record onto two physical
  // lines and desync a line-based reader) becomes a space. Caller supplies the
  // surrounding quotes. The fixed tags never need this — only free text.
  static void writeEscaped_(File& f, const char* s) {
    for (const char* p = s; *p; ++p) {
      char c = *p;
      if (c == '"') { f.print('"'); f.print('"'); }
      else if (c == '\n' || c == '\r') f.print(' ');
      else f.print(c);
    }
  }

  QueueHandle_t queue_ = nullptr;
  volatile bool present_ = false;
  uint8_t open_fails_ = 0;
  uint32_t dropped_ = 0;
};

}  // namespace icsmesh
