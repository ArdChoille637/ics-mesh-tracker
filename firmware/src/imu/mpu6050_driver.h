#pragma once
#include "imu_driver.h"
#include <Wire.h>

namespace icsmesh {

// Minimal register-level MPU6050 driver — no external library dependency.
// Default I2C address 0x68 (AD0 low). If your breakout ties AD0 high, pass
// 0x69. Wire.begin(SDA, SCL) must be called before begin() if the XIAO's
// default I2C pins aren't wired to your breakout.
class Mpu6050Driver : public IImuDriver {
 public:
  explicit Mpu6050Driver(uint8_t addr = 0x68) : addr_(addr) {}

  bool begin() override {
    Wire.beginTransmission(addr_);
    Wire.write(0x75);  // WHO_AM_I
    if (Wire.endTransmission(false) != 0) { present_ = false; return false; }
    Wire.requestFrom(addr_, (uint8_t)1);
    if (Wire.available() < 1) { present_ = false; return false; }
    uint8_t whoami = Wire.read();
    present_ = (whoami == 0x68) || (whoami == 0x72) || (whoami == 0x98);
    if (!present_) return false;

    writeReg(0x6B, 0x00);  // PWR_MGMT_1: wake, internal 8MHz osc
    delay(50);
    writeReg(0x1C, 0x00);  // ACCEL_CONFIG: +-2g
    writeReg(0x1B, 0x00);  // GYRO_CONFIG: +-250 dps
    writeReg(0x1A, 0x03);  // CONFIG: DLPF ~44Hz, cuts vibration noise from being worn on a body
    return true;
  }

  bool read(ImuSample& out) override {
    if (!present_) return false;
    Wire.beginTransmission(addr_);
    Wire.write(0x3B);  // ACCEL_XOUT_H, then 14 contiguous regs through GYRO_ZOUT_L
    if (Wire.endTransmission(false) != 0) return false;
    Wire.requestFrom(addr_, (uint8_t)14);
    if (Wire.available() < 14) return false;

    int16_t ax = read16(), ay = read16(), az = read16();
    read16();  // temperature, unused
    int16_t gx = read16(), gy = read16(), gz = read16();

    constexpr float kAccelScale = 1.0f / 16384.0f;   // LSB/g at +-2g
    constexpr float kGyroScale = 1.0f / 131.0f;       // LSB/(deg/s) at +-250dps
    out.accel_g[0] = ax * kAccelScale;
    out.accel_g[1] = ay * kAccelScale;
    out.accel_g[2] = az * kAccelScale;
    out.gyro_dps[0] = gx * kGyroScale;
    out.gyro_dps[1] = gy * kGyroScale;
    out.gyro_dps[2] = gz * kGyroScale;
    return true;
  }

  bool present() const override { return present_; }

  // Switch to hardware-FIFO streaming at a fixed 100 Hz. Call once after
  // begin() + the boot calibration (which uses single-shot read()). The
  // MPU6050's internal ODR clocks the FIFO, so the sample rate stays fixed
  // even if the main loop stalls servicing WiFi/BLE — this is the item-1.5
  // "decouple the sample clock from the loop" fix. With DLPF enabled the
  // gyro output rate is 1 kHz; SMPLRT_DIV=9 -> 1000/(1+9) = 100 Hz.
  void enableFifo100Hz() {
    if (!present_) return;
    writeReg(0x19, 9);      // SMPLRT_DIV = 9 -> 100 Hz
    writeReg(0x6A, 0x04);   // USER_CTRL: FIFO_RESET
    delay(1);
    writeReg(0x6A, 0x40);   // USER_CTRL: FIFO_EN
    writeReg(0x23, 0x78);   // FIFO_EN: ACCEL + GYRO_X/Y/Z (0x08|0x10|0x20|0x40)
  }

  // Drain buffered FIFO frames into out[] (accel+gyro, 12 bytes each), up to
  // max_samples. Returns the number of samples read. On FIFO overflow (loop
  // stalled longer than the ~85-sample / ~0.85 s buffer) it resets the FIFO
  // and returns 0 rather than emit corrupted, misaligned frames.
  int drainFifo(ImuSample* out, int max_samples) {
    if (!present_) return 0;
    uint16_t count = fifoCount();
    if (count >= 1024) {          // overflow: buffer is 1024 bytes; realign by resetting
      writeReg(0x6A, 0x04);       // FIFO_RESET
      writeReg(0x6A, 0x40);       // FIFO_EN
      return 0;
    }
    int frames = count / 12;      // 12 bytes/frame (accel 6 + gyro 6)
    if (frames > max_samples) frames = max_samples;
    constexpr float kAccelScale = 1.0f / 16384.0f;
    constexpr float kGyroScale = 1.0f / 131.0f;
    int got = 0;
    for (int f = 0; f < frames; f++) {
      Wire.beginTransmission(addr_);
      Wire.write(0x74);           // FIFO_R_W
      if (Wire.endTransmission(false) != 0) break;
      Wire.requestFrom(addr_, (uint8_t)12);
      if (Wire.available() < 12) break;
      int16_t ax = read16(), ay = read16(), az = read16();
      int16_t gx = read16(), gy = read16(), gz = read16();
      out[got].accel_g[0] = ax * kAccelScale;
      out[got].accel_g[1] = ay * kAccelScale;
      out[got].accel_g[2] = az * kAccelScale;
      out[got].gyro_dps[0] = gx * kGyroScale;
      out[got].gyro_dps[1] = gy * kGyroScale;
      out[got].gyro_dps[2] = gz * kGyroScale;
      got++;
    }
    return got;
  }

 private:
  uint16_t fifoCount() {
    Wire.beginTransmission(addr_);
    Wire.write(0x72);             // FIFO_COUNT_H
    if (Wire.endTransmission(false) != 0) return 0;
    Wire.requestFrom(addr_, (uint8_t)2);
    if (Wire.available() < 2) return 0;
    uint16_t hi = Wire.read();
    uint16_t lo = Wire.read();
    return (hi << 8) | lo;
  }

  void writeReg(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(addr_);
    Wire.write(reg);
    Wire.write(val);
    Wire.endTransmission();
  }

  int16_t read16() {
    uint16_t hi = Wire.read();
    uint16_t lo = Wire.read();
    return static_cast<int16_t>((hi << 8) | lo);
  }

  uint8_t addr_;
  bool present_ = false;
};

}  // namespace icsmesh
