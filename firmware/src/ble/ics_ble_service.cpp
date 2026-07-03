#include "ics_ble_service.h"
#include <NimBLEDevice.h>

// Uses NimBLE-Arduino (lighter than the stock ESP32 BLE stack, and the
// stack most XIAO ESP32S3 Arduino-core setups use for coexistence with
// WiFi/ESP-NOW). Add `NimBLE-Arduino` to platformio.ini lib_deps.

namespace icsmesh {

namespace {
NimBLEServer* g_server = nullptr;
NimBLECharacteristic* g_telemetry_char = nullptr;
IcsBleService* g_owner = nullptr;
}  // namespace

class IcsServerCallbacks : public NimBLEServerCallbacks {
 public:
  void onConnect(NimBLEServer*, NimBLEConnInfo&) override { if (g_owner) g_owner->connected_ = true; }
  void onDisconnect(NimBLEServer* server, NimBLEConnInfo&, int) override {
    if (g_owner) g_owner->connected_ = false;
    server->startAdvertising();  // keep advertising so another (or the same) phone can reconnect
  }
};

class Ics214WriteCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c, NimBLEConnInfo&) override {
    if (g_owner && g_owner->ics214_handler_) {
      g_owner->ics214_handler_(String(c->getValue().c_str()));
    }
  }
};

class ParWriteCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c, NimBLEConnInfo&) override {
    if (g_owner && g_owner->par_handler_ && c->getValue().length() >= 1) {
      g_owner->par_handler_(static_cast<ParStatus>(c->getValue()[0]));
    }
  }
};

void IcsBleService::begin(const String& device_name) {
  g_owner = this;
  NimBLEDevice::init(device_name.c_str());
  g_server = NimBLEDevice::createServer();
  g_server->setCallbacks(new IcsServerCallbacks());

  NimBLEService* svc = g_server->createService(ble_uuid::kServiceUuid);

  g_telemetry_char = svc->createCharacteristic(
      ble_uuid::kTelemetryCharUuid, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);

  NimBLECharacteristic* ics214_char = svc->createCharacteristic(
      ble_uuid::kIcs214WriteCharUuid, NIMBLE_PROPERTY::WRITE);
  ics214_char->setCallbacks(new Ics214WriteCallbacks());

  NimBLECharacteristic* par_char = svc->createCharacteristic(
      ble_uuid::kParWriteCharUuid, NIMBLE_PROPERTY::WRITE);
  par_char->setCallbacks(new ParWriteCallbacks());

  svc->start();

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  adv->addServiceUUID(ble_uuid::kServiceUuid);
  adv->start();

  Serial.println("[ble] advertising as " + device_name);
}

void IcsBleService::notifyTelemetry(const TelemetryPayload& payload) {
  if (!g_telemetry_char) return;
  g_telemetry_char->setValue(reinterpret_cast<const uint8_t*>(&payload), sizeof(payload));
  if (connected_) g_telemetry_char->notify();
}

}  // namespace icsmesh
