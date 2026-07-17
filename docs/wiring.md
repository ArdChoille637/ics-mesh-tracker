# Node wiring — XIAO ESP32-S3

The firmware's single source of truth for pins is [`firmware/src/board_pins.h`](../firmware/src/board_pins.h); this doc is the human-readable version. All numbers are **GPIO** (what the code uses); the XIAO silk label (`D0`..`D10`) is in parentheses.

## Team-lead node (full build: MPU-6050 + microSD)

The team lead carries the most hardware; field nodes are a strict subset (below).

| Signal | XIAO pin | Peripheral pin | Bus |
|---|---|---|---|
| I2C SDA | GPIO5 (D4) | MPU-6050 SDA | I²C (default `Wire`) |
| I2C SCL | GPIO6 (D5) | MPU-6050 SCL | I²C |
| MPU INT | GPIO2 (D1) | MPU-6050 INT | wake-on-motion (RTC-capable; unused in firmware yet) |
| SPI SCK | GPIO7 (D8) | microSD SCK | SPI (default bus) |
| SPI MISO | GPIO8 (D9) | microSD MISO | SPI |
| SPI MOSI | GPIO9 (D10) | microSD MOSI | SPI |
| SPI CS | GPIO3 (D2) | microSD CS | SPI |
| 3V3 | 3V3 | MPU-6050 VCC | power |
| 5V | 5V | microSD VCC | power (see ⚠️ below) |
| GND | GND | MPU GND + AD0, microSD GND | ground |

Both buses land on the XIAO ESP32-S3's **default** peripheral pins, so `Wire.begin()` / `SD.begin()` work with or without the explicit pin args. AD0→GND sets the MPU I²C address to `0x68`. XDA/XCL are unused.

Free for expansion (status LED, buzzer, battery sense): **GPIO1 (D0), GPIO4 (D3)** preferred. GPIO43/44 (D6/D7) are UART0 and toggle at every boot — avoid for loads.

> ⚠️ **HARDWARE CHECK before you trust the microSD at 5V.** ESP32-S3 GPIOs are **not 5V-tolerant** (abs-max ~3.6 V). Powering the SD adapter from 5V is only safe if the adapter is **level-shifted** — i.e. it has a small 6-pin logic IC (74LVC125 / 74AHC125) next to the AMS1117 regulator, so its MISO/CS/SCK/MOSI pull-ups reference the on-board 3.3 V rail. Many cheap "microSD module" boards (Catalex-style, regulator-only) tie those pull-ups to the **5V** rail → ~5 V straight into GPIO7/8/9/3 (MISO/GPIO8 is worst, actively driven). **If your adapter has no logic IC, either meter every SPI line to GND with the adapter powered and confirm it idles at ~3.3 V not ~5 V, or move the adapter VCC to the 3V3 pin** (the AMS1117 dropout usually still runs an SD card). Adafruit/SparkFun breakouts are level-shifted and fine at 5V. (`I²C` is separate and unaffected — the MPU runs at 3V3.)

> **GPIO3 (SD CS) note:** GPIO3 is an ESP32-S3 strapping pin (JTAG-source select), but that only matters if the `JTAG_SEL` eFuse is burned (it isn't from the factory), so the SD CS pull-up is harmless for boot/flash. Only move CS (to D0/D3) if you later want to attach an external pin-JTAG debugger.

## Field node (soldered identically; SD card optional)

Field nodes are **soldered identically to the team lead** (MPU + microSD adapter, both buses). The firmware attempts the SD mount on every non-gateway node: with **no card inserted** the node just runs with `sd=0` on the heartbeat and logging off; insert a card and it keeps its own removable PAR/ICS-214 record too. (A minimal field build — I²C block only, SPI unconnected — also works; the mount attempt fails gracefully.)

**AD0 must be bridged to GND** (sets I²C address `0x68`, which the firmware expects). GY-521 boards float low without it, but an unbridged AD0 can flip to `0x69` with noise and the IMU "vanishes" — bridge it on every node.

## Gateway node (minimal build: bare board)

The gateway needs **no external parts** — it's USB-tethered to the SBC and just bridges the mesh. No IMU, no SD. Wire nothing.

## Pull-ups / future sensors

The GY-521 breakout has onboard ~2.2 kΩ I²C pull-ups — no external resistors needed. A **BME280** (environmental) can share the same I²C bus later (address `0x76` vs the MPU's `0x68`); if added, its pull-ups parallel the GY-521's — remove one board's if bus rise-time suffers.
