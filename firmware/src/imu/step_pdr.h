#pragma once
#include "imu_driver.h"

namespace icsmesh {

// Step-detection pedestrian dead reckoning (PDR). Replaces the earlier
// accelerometer double-integration (dead_reckoning.h), which the research
// pass proved non-viable on a torso mount — an uncorrected gyro bias leaks
// gravity onto the horizontal axes and the double integral diverges as t³
// (~8.6 m @10 s, ~1.9 km @60 s). See docs/research-log.md items 0.1/1.4/1.5.
//
// Instead of a displacement vector, each report carries DISCRETE STEPS + a
// stride estimate + an absolute heading in the node's own (arbitrary,
// magnetometer-free) frame. The SBC advances position as step_count × stride
// along heading (sbc-gateway/fusion.py predict()).
//
// Heading: gyro-z yaw integrated, with NMNI ("No Motion No Integration",
// Nguyen 2021 — the gyro analog of ZUPT) bias re-zeroing during detected
// stillness. No magnetometer (hard/soft-iron from SCBA/steel/tools moves
// with the sensor and can't be calibrated out — item 1.4).
//
// Stillness: a windowed angular-rate-energy statistic — the cheap surrogate
// for SHOE, which Skog 2010 shows carries the most reliable stillness
// information in the gyro alone. Tuned for WHOLE-BODY STANDING stillness
// (seconds-scale), NOT per-stride ZUPT — a torso has no stance event
// (item 1.5).
//
// SAMPLING CONTRACT: update() must be called at a FIXED rate (~100 Hz),
// driven by a timer or by draining the MPU6050 hardware FIFO — NOT from the
// WiFi/BLE-perturbed main loop. A wandering sample rate silently invalidates
// the fixed-window statistic and threshold (item 1.5). main.cpp drains the
// FIFO each loop and calls update(kSamplePeriodS) once per buffered sample.
//
// NOTE: kWeinbergK, kStillGammaDps2, and the step-detection thresholds below
// are LITERATURE STARTING POINTS flagged for bench calibration on the actual
// hardware + mounting (research-log.md Pass 4 defers these). They are not
// meant to be trusted before a walk-a-known-course validation.
class StepPdr {
 public:
  static constexpr float kSamplePeriodS = 0.01f;   // 100 Hz FIFO ODR

  explicit StepPdr(IImuDriver& imu) : imu_(imu) {}

  // Static boot calibration: seed the gyro-z bias and gravity magnitude while
  // the unit is held still. Returns false if the IMU isn't present.
  bool calibrate(int samples = 200) {
    if (!imu_.present()) return false;
    double gz = 0; int n = 0;
    for (int i = 0; i < samples; i++) {
      ImuSample s;
      if (imu_.read(s)) { gz += s.gyro_dps[2]; n++; }
      delay(5);
    }
    if (n == 0) return false;
    gyro_z_bias_ = static_cast<float>(gz / n);
    calibrated_ = true;
    last_rezero_ms_ = millis();
    return true;
  }

  // Feed one fixed-rate IMU sample. dt_s is the (fixed) FIFO sample period.
  void update(ImuSample& s, float dt_s) {
    if (!calibrated_) return;
    now_ms_ += static_cast<uint32_t>(dt_s * 1000.0f + 0.5f);

    // --- stillness: windowed angular-rate energy (|omega|^2, deg^2/s^2) ---
    float wsq = s.gyro_dps[0]*s.gyro_dps[0] + s.gyro_dps[1]*s.gyro_dps[1] + s.gyro_dps[2]*s.gyro_dps[2];
    omega_sq_sum_ -= ring_[ring_idx_];
    ring_[ring_idx_] = wsq;
    omega_sq_sum_ += wsq;
    ring_idx_ = (ring_idx_ + 1) % kStillWin;
    float win_energy = omega_sq_sum_ / kStillWin;      // mean |omega|^2 over the window
    bool now_still = win_energy < kStillGammaDps2;

    // --- NMNI: re-zero gyro-z bias from the mean gyro-z over a still window ---
    if (now_still) {
      still_gz_sum_ += s.gyro_dps[2];
      still_gz_n_++;
      // Once we've accumulated a full window of stillness, commit the re-zero.
      if (still_gz_n_ >= kStillWin) {
        gyro_z_bias_ = static_cast<float>(still_gz_sum_ / still_gz_n_);
        still_gz_sum_ = 0; still_gz_n_ = 0;
        last_rezero_ms_ = now_ms_;
      }
    } else {
      still_gz_sum_ = 0; still_gz_n_ = 0;
    }
    stationary_ = now_still;

    // --- heading: integrate bias-corrected gyro-z yaw, wrap to [0, 2pi) ---
    float yaw_rate_rad = (s.gyro_dps[2] - gyro_z_bias_) * (PI / 180.0f);
    heading_rad_ += yaw_rate_rad * dt_s;
    while (heading_rad_ < 0) heading_rad_ += 2 * PI;
    while (heading_rad_ >= 2 * PI) heading_rad_ -= 2 * PI;

    // --- step detection on low-passed accel magnitude, with hysteresis ---
    float amag = sqrtf(s.accel_g[0]*s.accel_g[0] + s.accel_g[1]*s.accel_g[1] + s.accel_g[2]*s.accel_g[2]);
    accel_lp_ += (amag - accel_lp_) * kAccelLpAlpha;   // ~2-3 Hz low-pass to isolate the gait bounce
    // track per-step accel excursion for the Weinberg stride estimate
    if (accel_lp_ > step_amax_) step_amax_ = accel_lp_;
    if (accel_lp_ < step_amin_) step_amin_ = accel_lp_;

    if (!above_ && accel_lp_ > 1.0f + kStepHighG) {
      above_ = true;
    } else if (above_ && accel_lp_ < 1.0f + kStepLowG) {
      above_ = false;
      // falling edge = one step, if enough time has passed (debounce fast bounce)
      if (now_ms_ - last_step_ms_ >= kMinStepMs && steps_ < 255) {
        // Weinberg: stride length = K * (a_max - a_min)^(1/4)
        float excursion = step_amax_ - step_amin_;
        if (excursion < 0) excursion = 0;
        float stride_m = kWeinbergK * powf(excursion, 0.25f);
        stride_sum_mm_ += stride_m * 1000.0f;
        steps_++;
        last_step_ms_ = now_ms_;
      }
      step_amax_ = 0; step_amin_ = 10;   // reset excursion window for the next step
    }
  }

  struct Report {
    uint8_t step_count;
    uint16_t stride_mm;      // mean stride over the popped steps (0 if none)
    uint16_t heading_mrad;   // current heading, 0..6283
    uint8_t heading_conf;    // 255 just after a re-zero, decays with time-since-rezero
    bool stationary;
  };

  // Called at the TELEMETRY cadence. Returns the accumulated steps/stride and
  // the current heading, and resets the step accumulators.
  Report popReport() {
    Report r{};
    r.step_count = steps_;
    r.stride_mm = steps_ > 0 ? static_cast<uint16_t>(stride_sum_mm_ / steps_) : 0;
    r.heading_mrad = static_cast<uint16_t>(heading_rad_ * 1000.0f);   // 0..6283
    r.stationary = stationary_;
    // heading confidence decays linearly with time since the last NMNI re-zero;
    // full at re-zero, ~0 after kHeadingConfDecayMs. The SBC uses this to size
    // the map's heading uncertainty (drift accrues at the bias-instability rate
    // between re-zeros — see item 1.4).
    uint32_t since = now_ms_ - last_rezero_ms_;
    r.heading_conf = since >= kHeadingConfDecayMs ? 0
                     : static_cast<uint8_t>(255 - (since * 255) / kHeadingConfDecayMs);
    steps_ = 0;
    stride_sum_mm_ = 0;
    return r;
  }

  bool calibrated() const { return calibrated_; }

 private:
  // --- calibration constants: LITERATURE DEFAULTS, calibrate on hardware ---
  static constexpr float kWeinbergK = 0.45f;        // Weinberg stride gain (m / g^0.25) — tune to wearer
  static constexpr float kStillGammaDps2 = 25.0f;   // mean |omega|^2 threshold for "standing still" (deg^2/s^2)
  static constexpr int   kStillWin = 50;            // 0.5 s window at 100 Hz
  static constexpr float kAccelLpAlpha = 0.15f;     // accel-magnitude low-pass coefficient
  static constexpr float kStepHighG = 0.12f;        // rising-edge accel threshold above 1 g
  static constexpr float kStepLowG = 0.05f;         // falling-edge threshold (hysteresis)
  static constexpr uint32_t kMinStepMs = 250;       // debounce: max ~4 steps/s
  static constexpr uint32_t kHeadingConfDecayMs = 60000;  // heading confidence hits 0 ~60 s after a re-zero

  IImuDriver& imu_;
  bool calibrated_ = false;

  // heading
  float gyro_z_bias_ = 0;
  float heading_rad_ = 0;
  uint32_t last_rezero_ms_ = 0;

  // stillness window
  float ring_[kStillWin] = {0};
  int ring_idx_ = 0;
  float omega_sq_sum_ = 0;
  bool stationary_ = false;
  double still_gz_sum_ = 0;
  int still_gz_n_ = 0;

  // step detection
  float accel_lp_ = 1.0f;
  bool above_ = false;
  uint32_t last_step_ms_ = 0;
  float step_amax_ = 0, step_amin_ = 10;

  // report accumulators
  uint8_t steps_ = 0;
  float stride_sum_mm_ = 0;
  uint32_t now_ms_ = 0;
};

}  // namespace icsmesh
