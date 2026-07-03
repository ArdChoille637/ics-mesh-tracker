"""Wire protocol — must mirror firmware/src/mesh/packet.h exactly.

Struct layouts, byte order, and field order are the contract with the
firmware; if you change one side, change the other and re-check the sizes
below against packet.h's static_assert.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum


PROTO_MAGIC = 0xC5
PROTO_VERSION = 1
MAX_RSSI_NEIGHBORS = 8
MAX_TELEMETRY_BATCH = 12
ICS214_TEXT_MAX = 100


class NodeRole(IntEnum):
    FIELD = 0
    TEAM_LEAD = 1
    GATEWAY = 2


class PacketType(IntEnum):
    BEACON = 0
    TELEMETRY = 1
    TELEMETRY_BATCH = 2
    PAR_UPDATE = 3
    ICS214_ENTRY = 4
    ICS_FORM_REQUEST = 5
    ICS_FORM_DATA = 6
    CTRL = 7


class ParStatus(IntEnum):
    OK = 0
    EMERGENCY = 1
    MAYDAY = 2
    OUT_OF_CONTACT = 3


# PacketHeader: magic(B) version(B) type(B) team_id(B) node_id(H) seq(H) role(B) = 9 bytes
_HEADER_FMT = "<BBBBHHB"
HEADER_SIZE = struct.calcsize(_HEADER_FMT)
assert HEADER_SIZE == 9, f"protocol.py header size drifted from packet.h: {HEADER_SIZE}"


@dataclass
class PacketHeader:
    type: PacketType
    team_id: int
    node_id: int
    seq: int
    role: NodeRole
    magic: int = PROTO_MAGIC
    version: int = PROTO_VERSION

    def pack(self) -> bytes:
        return struct.pack(_HEADER_FMT, self.magic, self.version, int(self.type),
                            self.team_id, self.node_id, self.seq, int(self.role))

    @staticmethod
    def unpack(data: bytes) -> "PacketHeader":
        magic, version, type_, team_id, node_id, seq, role = struct.unpack(_HEADER_FMT, data[:HEADER_SIZE])
        if magic != PROTO_MAGIC:
            raise ValueError(f"bad magic byte 0x{magic:02x}")
        return PacketHeader(PacketType(type_), team_id, node_id, seq, NodeRole(role), magic, version)


# RssiSample: neighbor_node_id(H) rssi_dbm(b) age_ms(H) = 5 bytes
_RSSI_SAMPLE_FMT = "<Hbh"
RSSI_SAMPLE_SIZE = struct.calcsize(_RSSI_SAMPLE_FMT)


@dataclass
class RssiSample:
    neighbor_node_id: int
    rssi_dbm: int
    age_ms: int


# TelemetryPayload: rssi_count(B) rssi[8] step_count(B) stride_mm(H)
# heading_mrad(H) heading_conf(B) battery_pct(B) par_status(B) flags(B).
# Step-detection PDR fields (replaced the old dx/dy/dz/dtheta double-
# integration — see docs/research-log.md 0.1).
_TELEMETRY_FIXED_FMT = "<B"
_TELEMETRY_TAIL_FMT = "<BHHBBBB"


@dataclass
class TelemetryPayload:
    rssi: list[RssiSample] = field(default_factory=list)
    step_count: int = 0        # steps since last report
    stride_mm: int = 0         # mean stride length over those steps
    heading_mrad: int = 0      # heading in the node's arbitrary frame, 0..6283
    heading_conf: int = 0      # 255 just after an NMNI still-rezero, decays with time
    battery_pct: int = 0
    par_status: ParStatus = ParStatus.OK
    flags: int = 0

    @property
    def imu_present(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def stationary(self) -> bool:
        return bool(self.flags & 0x08)

    def pack(self) -> bytes:
        out = struct.pack(_TELEMETRY_FIXED_FMT, len(self.rssi))
        for i in range(MAX_RSSI_NEIGHBORS):
            if i < len(self.rssi):
                s = self.rssi[i]
                out += struct.pack(_RSSI_SAMPLE_FMT, s.neighbor_node_id, s.rssi_dbm, s.age_ms)
            else:
                out += b"\x00" * RSSI_SAMPLE_SIZE
        out += struct.pack(_TELEMETRY_TAIL_FMT, self.step_count, self.stride_mm, self.heading_mrad,
                            self.heading_conf, self.battery_pct, int(self.par_status), self.flags)
        return out

    @staticmethod
    def unpack(data: bytes) -> "TelemetryPayload":
        offset = 0
        (count,) = struct.unpack_from(_TELEMETRY_FIXED_FMT, data, offset)
        offset += struct.calcsize(_TELEMETRY_FIXED_FMT)
        rssi = []
        for i in range(MAX_RSSI_NEIGHBORS):
            node_id, rssi_dbm, age_ms = struct.unpack_from(_RSSI_SAMPLE_FMT, data, offset)
            offset += RSSI_SAMPLE_SIZE
            if i < count:
                rssi.append(RssiSample(node_id, rssi_dbm, age_ms))
        steps, stride, heading, hconf, batt, par, flags = struct.unpack_from(_TELEMETRY_TAIL_FMT, data, offset)
        return TelemetryPayload(rssi, steps, stride, heading, hconf, batt, ParStatus(par), flags)


TELEMETRY_PAYLOAD_SIZE = (struct.calcsize(_TELEMETRY_FIXED_FMT)
                          + MAX_RSSI_NEIGHBORS * RSSI_SAMPLE_SIZE
                          + struct.calcsize(_TELEMETRY_TAIL_FMT))

# One TelemetryBatchMember on the wire: node_id(H) + TelemetryPayload.
TELEMETRY_BATCH_MEMBER_SIZE = 2 + TELEMETRY_PAYLOAD_SIZE

# ESP-NOW frames cap at 250 bytes total (header + payload). A TELEMETRY_BATCH
# payload is count(1B) + N × TelemetryBatchMember, so at most this many members
# fit in one frame alongside the 9-byte header. Mirrors kMaxBatchMembersPerFrame
# in firmware/src/mesh/espnow_mesh.cpp — the firmware is authoritative; if these
# disagree, an oversize frame gets silently dropped on the radio.
ESPNOW_MAX_FRAME = 250
MAX_BATCH_MEMBERS_PER_FRAME = (ESPNOW_MAX_FRAME - HEADER_SIZE - 1) // TELEMETRY_BATCH_MEMBER_SIZE


@dataclass
class TelemetryBatchPayload:
    members: dict[int, TelemetryPayload]  # node_id -> telemetry

    def pack(self) -> bytes:
        """Array-of-structs wire form: count(B) then, per member, node_id(H)
        immediately followed by its TelemetryPayload. Inverse of unpack()."""
        out = struct.pack("<B", len(self.members))
        for node_id, t in self.members.items():
            out += struct.pack("<H", node_id) + t.pack()
        return out

    def split_frames(self) -> list[bytes]:
        """Break this batch into ESP-NOW-frame-sized TELEMETRY_BATCH payload
        bodies (<= MAX_BATCH_MEMBERS_PER_FRAME members each) so that
        HEADER_SIZE + len(body) never exceeds ESPNOW_MAX_FRAME. Each returned
        body is an independently-valid TELEMETRY_BATCH (its own count + a
        contiguous run of members) that unpack() round-trips. Mirror of
        firmware EspNowMesh::sendTelemetryBatch; an empty batch still yields one
        count=0 frame (the anchor ping)."""
        items = list(self.members.items())
        if not items:
            return [struct.pack("<B", 0)]
        frames = []
        for start in range(0, len(items), MAX_BATCH_MEMBERS_PER_FRAME):
            chunk = items[start:start + MAX_BATCH_MEMBERS_PER_FRAME]
            body = struct.pack("<B", len(chunk))
            for node_id, t in chunk:
                body += struct.pack("<H", node_id) + t.pack()
            frames.append(body)
        return frames

    @staticmethod
    def unpack(data: bytes) -> "TelemetryBatchPayload":
        # Array-of-structs on the wire (packet.h's TelemetryBatchMember) —
        # each record is node_id(H) immediately followed by its
        # TelemetryPayload, and only `count` records are sent (not padded
        # out to MAX_TELEMETRY_BATCH). Matches the truncation done in
        # EspNowMesh::sendTelemetryBatch.
        offset = 0
        (count,) = struct.unpack_from("<B", data, offset)
        offset += 1
        members = {}
        record_size = 2 + TELEMETRY_PAYLOAD_SIZE
        for _ in range(count):
            (nid,) = struct.unpack_from("<H", data, offset)
            t = TelemetryPayload.unpack(data[offset + 2:offset + record_size])
            members[nid] = t
            offset += record_size
        return TelemetryBatchPayload(members)


@dataclass
class ParUpdatePayload:
    status: ParStatus
    since_ms: int

    @staticmethod
    def unpack(data: bytes) -> "ParUpdatePayload":
        # packet.h has #pragma pack(push, 1) — no alignment padding between
        # the 1-byte enum and the following uint32_t, unlike a normal struct.
        status, since_ms = struct.unpack_from("<BI", data, 0)
        return ParUpdatePayload(ParStatus(status), since_ms)


@dataclass
class Ics214EntryPayload:
    timestamp_ms: int
    text: str

    @staticmethod
    def unpack(data: bytes) -> "Ics214EntryPayload":
        (ts,) = struct.unpack_from("<I", data, 0)
        raw = data[4:4 + ICS214_TEXT_MAX]
        text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        return Ics214EntryPayload(ts, text)


@dataclass
class IcsFormChunkPayload:
    form_id: int
    chunk_index: int
    chunk_total: int
    chunk_len: int
    data: bytes

    @staticmethod
    def unpack(data: bytes) -> "IcsFormChunkPayload":
        form_id, idx, total, length = struct.unpack_from("<BBBB", data, 0)
        payload = data[4:4 + length]
        return IcsFormChunkPayload(form_id, idx, total, length, payload)


def decode_payload(hdr: PacketHeader, payload: bytes):
    """Dispatch to the right dataclass based on hdr.type. Returns None for
    BEACON (no payload) or unrecognized/short payloads."""
    if hdr.type == PacketType.BEACON:
        return None
    try:
        if hdr.type == PacketType.TELEMETRY:
            return TelemetryPayload.unpack(payload)
        if hdr.type == PacketType.TELEMETRY_BATCH:
            return TelemetryBatchPayload.unpack(payload)
        if hdr.type == PacketType.PAR_UPDATE:
            return ParUpdatePayload.unpack(payload)
        if hdr.type == PacketType.ICS214_ENTRY:
            return Ics214EntryPayload.unpack(payload)
        if hdr.type in (PacketType.ICS_FORM_DATA, PacketType.ICS_FORM_REQUEST):
            return IcsFormChunkPayload.unpack(payload)
    except struct.error:
        return None
    return None
