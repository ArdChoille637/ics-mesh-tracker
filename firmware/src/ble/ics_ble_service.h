#pragma once
#include <Arduino.h>
#include <functional>
#include "../mesh/packet.h"

namespace icsmesh {

// BLE GATT service for phone pairing — the "lightweight background
// telemetry" half of the phone link (see docs/protocol.md). The captive
// portal (webportal/) handles the "responder actively viewing forms" half.
// Both can be active; a phone app would typically use BLE for the live
// map feed and only hit the AP when it needs the full ICS-214/205/201 UI.
//
// UUIDs below are placeholders generated for this prototype — regenerate
// before any real deployment so you don't collide with another project's
// GATT service if multiple are ever in range of the same phone.
namespace ble_uuid {
constexpr char kServiceUuid[] = "c5000000-0000-1000-8000-00805f9b34fb";
constexpr char kTelemetryCharUuid[] = "c5000001-0000-1000-8000-00805f9b34fb";  // notify, this node's latest TelemetryPayload
constexpr char kIcs214WriteCharUuid[] = "c5000002-0000-1000-8000-00805f9b34fb";  // write, phone -> node ICS-214 entry text
constexpr char kParWriteCharUuid[] = "c5000003-0000-1000-8000-00805f9b34fb";   // write, phone-initiated PAR status change (e.g. responder taps MAYDAY)
}  // namespace ble_uuid

class IcsBleService {
 public:
  using Ics214FromPhoneHandler = std::function<void(const String& text)>;
  using ParFromPhoneHandler = std::function<void(ParStatus status)>;

  void begin(const String& device_name);

  // Call whenever this node's own TelemetryPayload is refreshed, so a
  // connected phone gets a BLE notify with the latest data. Cheap no-op if
  // nothing is connected.
  void notifyTelemetry(const TelemetryPayload& payload);

  void onIcs214FromPhone(Ics214FromPhoneHandler h) { ics214_handler_ = std::move(h); }
  void onParFromPhone(ParFromPhoneHandler h) { par_handler_ = std::move(h); }

  bool clientConnected() const { return connected_; }

 private:
  Ics214FromPhoneHandler ics214_handler_;
  ParFromPhoneHandler par_handler_;
  bool connected_ = false;

  friend class IcsServerCallbacks;
  friend class Ics214WriteCallbacks;
  friend class ParWriteCallbacks;
};

}  // namespace icsmesh
