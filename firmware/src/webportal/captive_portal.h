#pragma once
#include <Arduino.h>
#include <functional>
#include "../ics/ics_forms.h"
#include "../mesh/packet.h"

namespace icsmesh {

// Self-hosted WiFi AP + web UI, no app install required. Any responder can
// join this node's AP and get the status/forms page in a browser. This is
// deliberately plain HTML/inline CSS with zero external asset loads (no
// CDN, no fonts, no JS framework) — it has to work with no internet
// reachable, which is the whole point of a field AP.
//
// Coexists with ESP-NOW by running WiFi in AP mode alongside the STA
// interface ESP-NOW uses (ESP32 supports AP+STA concurrently on the same
// channel — see main.cpp for the channel-pinning this requires).
class CaptivePortal {
 public:
  using ParChangeHandler = std::function<void(ParStatus)>;
  using Ics214AddHandler = std::function<void(const String& text)>;

  void begin(const String& node_label, uint16_t self_node_id);
  void handleClient();  // call every loop()

  void onParChange(ParChangeHandler h) { par_handler_ = std::move(h); }
  void onIcs214Add(Ics214AddHandler h) { ics214_handler_ = std::move(h); }

  // Called by the mesh handler when a fresh 201/205 push arrives from
  // command, so the portal always reflects the latest synced copy.
  void refreshForms() {}  // forms are read from LittleFS per-request; nothing to cache here

  void setParStatus(ParStatus s) { current_par_ = s; }

  // Public rather than private: this is a process-wide singleton (one AP
  // per node) and its request handlers are free functions in
  // captive_portal.cpp's anonymous namespace, not methods, so they need
  // direct field access. Not intended for use outside that file.
  ParChangeHandler par_handler_;
  Ics214AddHandler ics214_handler_;
  ParStatus current_par_ = ParStatus::OK;
  uint16_t self_node_id_ = 0;
  String node_label_;
};

}  // namespace icsmesh
