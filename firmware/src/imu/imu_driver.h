#pragma once
#include <cstdint>

namespace icsmesh {

struct ImuSample {
  float accel_g[3];    // x,y,z in g
  float gyro_dps[3];   // x,y,z in degrees/sec
};

// Abstraction so a different IMU (QMI8658, LSM6DS3, BMI160, LSM6DSOX —
// whatever ends up on the add-on board, since the bare XIAO ESP32S3 has no
// onboard IMU) can be swapped in without touching step_pdr.h. mpu6050_driver.h
// is the one concrete implementation shipped here because it's the cheapest,
// best-documented option to bring up first. NOTE: step_pdr.h's fixed-rate
// contract needs a FIFO/streaming path — an alternative driver should provide
// its own equivalent of enableFifo100Hz()/drainFifo() (see mpu6050_driver.h).
class IImuDriver {
 public:
  virtual ~IImuDriver() = default;
  virtual bool begin() = 0;
  virtual bool read(ImuSample& out) = 0;
  virtual bool present() const = 0;
};

}  // namespace icsmesh
