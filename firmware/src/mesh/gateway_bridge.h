#pragma once
#include <Arduino.h>
#include "packet.h"

namespace icsmesh {

// Only meaningful on a GATEWAY-role node (USB-attached to the SBC). Wraps
// whatever ESP-NOW packet it hears in the serial frame described in
// docs/protocol.md, and unwraps CTRL frames coming back down from the SBC.
// Framing: 0x7E <len:u16 LE> <payload...> <crc8>
class GatewayBridge {
 public:
  void begin(Stream& port) { port_ = &port; }

  void forwardToSbc(const PacketHeader& hdr, const uint8_t* payload, size_t payload_len) {
    if (!port_) return;
    uint16_t len = sizeof(hdr) + payload_len;
    uint8_t crc = 0;
    port_->write(0x7E);
    port_->write(reinterpret_cast<uint8_t*>(&len), sizeof(len));
    port_->write(reinterpret_cast<const uint8_t*>(&hdr), sizeof(hdr));
    crc = crc8(crc, reinterpret_cast<const uint8_t*>(&hdr), sizeof(hdr));
    if (payload_len) {
      port_->write(payload, payload_len);
      crc = crc8(crc, payload, payload_len);
    }
    port_->write(crc);
  }

  // Call from loop(); returns true and fills out_* if a full CTRL frame from
  // the SBC was decoded this call. Non-blocking, byte-at-a-time parser.
  bool pollFromSbc(PacketHeader& out_hdr, uint8_t* out_payload, size_t max_payload, size_t& out_payload_len) {
    if (!port_) return false;
    while (port_->available()) {
      uint8_t b = port_->read();
      switch (state_) {
        case State::WAIT_START:
          if (b == 0x7E) { state_ = State::LEN_LOW; }
          break;
        case State::LEN_LOW:
          len_ = b;
          state_ = State::LEN_HIGH;
          break;
        case State::LEN_HIGH:
          len_ |= (static_cast<uint16_t>(b) << 8);
          if (len_ > sizeof(buf_)) { state_ = State::WAIT_START; break; }
          idx_ = 0;
          state_ = State::PAYLOAD;
          break;
        case State::PAYLOAD:
          buf_[idx_++] = b;
          if (idx_ >= len_) state_ = State::CRC;
          break;
        case State::CRC: {
          uint8_t expect = crc8(0, buf_, len_);
          state_ = State::WAIT_START;
          if (expect != b) break;  // drop frame, resync
          if (len_ < sizeof(PacketHeader)) break;
          memcpy(&out_hdr, buf_, sizeof(PacketHeader));
          out_payload_len = std::min(len_ - sizeof(PacketHeader), max_payload);
          memcpy(out_payload, buf_ + sizeof(PacketHeader), out_payload_len);
          return true;
        }
      }
    }
    return false;
  }

 private:
  enum class State { WAIT_START, LEN_LOW, LEN_HIGH, PAYLOAD, CRC };

  static uint8_t crc8(uint8_t crc, const uint8_t* data, size_t len) {
    for (size_t i = 0; i < len; i++) {
      crc ^= data[i];
      for (int b = 0; b < 8; b++) crc = (crc & 0x80) ? (crc << 1) ^ 0x07 : (crc << 1);
    }
    return crc;
  }

  Stream* port_ = nullptr;
  State state_ = State::WAIT_START;
  uint16_t len_ = 0;
  uint16_t idx_ = 0;
  uint8_t buf_[300];
};

}  // namespace icsmesh
