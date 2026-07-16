#pragma once
#include <cstdint>

// ICS Personnel Tracker — node wiring map (single source of truth).
//
// Seeed XIAO ESP32-S3 · MPU-6050 (GY-521) over I2C · microSD adapter over SPI.
// This mirrors the wiring diagram in docs/wiring.md / the README. GPIO numbers
// are what the code uses; the XIAO silk label (D0..D10) is in the comment.
//
// Both buses sit on the XIAO ESP32-S3's DEFAULT peripheral pins (I2C = D4/D5,
// SPI = D8/D9/D10), so this layout also works with a bare Wire.begin()/SD.begin()
// — the explicit pins here are for clarity and so a re-wire is a one-line change.
//
// Only the TEAM_LEAD node carries the microSD adapter; FIELD/GATEWAY nodes wire
// the MPU (FIELD) or nothing (GATEWAY) and leave the SPI pins free. That's the
// only way node roles differ electrically — see storage/sd_logger.h.
namespace icsmesh {
namespace pins {

// --- I2C: MPU-6050 (GY-521). BME280 can share this bus later (0x76 vs 0x68). ---
constexpr uint8_t kI2cSda = 5;   // D4  → MPU SDA
constexpr uint8_t kI2cScl = 6;   // D5  → MPU SCL
constexpr uint8_t kMpuInt = 2;   // D1  → MPU INT (GPIO2, RTC-capable: wake-on-motion, future)

// --- SPI: microSD adapter (TEAM_LEAD only). XIAO default SPI bus. ---
constexpr uint8_t kSpiSck  = 7;  // D8  → SD SCK
constexpr uint8_t kSpiMiso = 8;  // D9  → SD MISO
constexpr uint8_t kSpiMosi = 9;  // D10 → SD MOSI
constexpr uint8_t kSdCs    = 3;  // D2  → SD CS. GPIO3 is an S3 strapping pin
                                 // (JTAG-source select), but that only bites
                                 // if the JTAG_SEL eFuse is burned (it isn't
                                 // from the factory) — the SD CS pull-up is
                                 // harmless for boot/flash. Move to D0/D3 only
                                 // if you later want a pin-JTAG debugger.

// --- Free / expansion. PREFER D0/D3 for a status LED / buzzer / battery sense:
//     D6/D7 are UART0 (U0TXD/U0RXD) — the ROM bootloader toggles U0TXD on every
//     reset, so a load there twitches at boot and you lose a wired console. ---
constexpr uint8_t kFreeD0 = 1;   // D0  · GPIO1  (preferred for expansion)
constexpr uint8_t kFreeD3 = 4;   // D3  · GPIO4  (preferred for expansion)
constexpr uint8_t kFreeD6 = 43;  // D6  · GPIO43 (U0TXD — boot-time toggling)
constexpr uint8_t kFreeD7 = 44;  // D7  · GPIO44 (U0RXD)

}  // namespace pins
}  // namespace icsmesh
