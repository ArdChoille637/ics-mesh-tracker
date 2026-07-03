"""Serial link to the GATEWAY-role ESP32S3, USB-attached to this SBC.

Framing must match firmware/src/mesh/gateway_bridge.h exactly:
    0x7E <len:u16 LE> <payload:len bytes> <crc8>
where payload = PacketHeader (9 bytes) + type-specific bytes.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import serial

from protocol import PacketHeader, HEADER_SIZE, decode_payload

log = logging.getLogger("serial_link")


def crc8(data: bytes, crc: int = 0) -> int:
    """Must match gateway_bridge.h's crc8() bit-for-bit (poly 0x07)."""
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


@dataclass
class DecodedFrame:
    header: PacketHeader
    payload: object  # one of the dataclasses in protocol.py, or None for BEACON
    raw_payload: bytes


class SerialLink:
    """Reads frames in a background thread; dispatches via on_frame.

    Also exposes send_ctrl() for the (currently unused) SBC-to-node
    downlink — e.g. pushing an updated ICS-205 comms plan. GatewayBridge on
    the firmware side already has pollFromSbc() wired up to receive these;
    nothing calls send_ctrl() yet in server.py, that's the next feature to
    wire up once the SBC command-push UI exists.
    """

    def __init__(self, port: str, baud: int = 115200, on_frame: Optional[Callable[[DecodedFrame], None]] = None):
        self._port_name = port
        self._baud = baud
        self._on_frame = on_frame
        self._ser: Optional[serial.Serial] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._ser = serial.Serial(self._port_name, self._baud, timeout=0.2)
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        log.info("serial link open on %s", self._port_name)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._ser:
            self._ser.close()

    def _read_loop(self):
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except serial.SerialException:
                log.exception("serial read failed, retrying in 1s")
                time.sleep(1)
                continue
            if not chunk:
                continue
            buf.extend(chunk)
            self._drain_frames(buf)

    def _drain_frames(self, buf: bytearray):
        while True:
            start = buf.find(0x7E)
            if start < 0:
                buf.clear()
                return
            if start > 0:
                del buf[:start]
            if len(buf) < 3:
                return  # need at least start + 2-byte length
            length = buf[1] | (buf[2] << 8)
            frame_end = 3 + length + 1  # +1 for trailing crc byte
            if len(buf) < frame_end:
                return  # wait for more data
            payload_and_crc = buf[3:frame_end]
            payload_bytes = bytes(payload_and_crc[:-1])
            expect_crc = payload_and_crc[-1]
            del buf[:frame_end]

            if crc8(payload_bytes) != expect_crc:
                log.warning("crc mismatch, dropping frame")
                continue
            if len(payload_bytes) < HEADER_SIZE:
                continue
            try:
                hdr = PacketHeader.unpack(payload_bytes[:HEADER_SIZE])
            except ValueError:
                log.warning("bad header, dropping frame")
                continue
            raw_payload = payload_bytes[HEADER_SIZE:]
            decoded = decode_payload(hdr, raw_payload)
            if self._on_frame:
                self._on_frame(DecodedFrame(hdr, decoded, raw_payload))
