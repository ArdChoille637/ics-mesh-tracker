"""Wire-protocol round-trip tests.

Runs with the repo's existing deps — no pytest required:

    python test_protocol.py

(also works under `pytest test_protocol.py` if you have it installed).

The headline test guards the TELEMETRY_BATCH splitting fix. Background: a
TELEMETRY_BATCH is count(1B) + N × TelemetryBatchMember (52B each: node_id 2B +
50B step-PDR TelemetryPayload). ESP-NOW frames cap at 250 bytes including the
9-byte header, so only 4 members fit in one frame. Before splitting existed, a team of 5+ (a standard ICS strike team
is 5 resources + a leader) produced a 280+ byte frame that the radio silently
dropped — the whole team's accountability data vanished at exactly doctrinal
team size (grounding study 2026-07-03, research-brief item 3.5). The fix
(EspNowMesh::sendTelemetryBatch, mirrored by TelemetryBatchPayload.split_frames)
splits a full team into several independently-valid frames the SBC merges as it
ingests members incrementally. These tests prove a 6-member batch survives
encode -> split -> decode with all 6 members recovered, and that no frame ever
exceeds the 250-byte cap.
"""
from __future__ import annotations

from protocol import (
    ESPNOW_MAX_FRAME,
    HEADER_SIZE,
    MAX_BATCH_MEMBERS_PER_FRAME,
    NodeRole,
    PacketHeader,
    PacketType,
    ParStatus,
    RssiSample,
    TelemetryBatchPayload,
    TelemetryPayload,
    decode_payload,
)

LEAD_NODE_ID = 0xA10C  # stand-in TEAM_LEAD node_id (the batch sender / anchor)


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def make_member(node_id: int) -> TelemetryPayload:
    """A TelemetryPayload whose every field is derived from node_id, so a
    recovered member can be checked for exact, per-field fidelity rather than
    just presence."""
    return TelemetryPayload(
        rssi=[
            RssiSample(neighbor_node_id=(node_id + 1) & 0xFFFF, rssi_dbm=-40 - (node_id % 50), age_ms=node_id % 1000),
            RssiSample(neighbor_node_id=(node_id + 2) & 0xFFFF, rssi_dbm=-55 - (node_id % 40), age_ms=(node_id * 3) % 1000),
        ],
        step_count=node_id % 20,
        stride_mm=500 + (node_id % 500),
        heading_mrad=node_id % 6283,
        heading_conf=node_id % 256,
        battery_pct=node_id % 101,
        par_status=ParStatus(node_id % 4),
        flags=node_id % 16,     # exercise bits 0-3 incl. the stationary bit (0x08)
    )


def make_batch(count: int) -> tuple[TelemetryBatchPayload, list[int]]:
    """A batch of `count` members with distinct node_ids (never 0, which is
    reserved). Returns the batch and the ordered node_id list."""
    node_ids = [1000 + i * 111 for i in range(count)]
    members = {nid: make_member(nid) for nid in node_ids}
    return TelemetryBatchPayload(members), node_ids


def merge_over_serial(batch: TelemetryBatchPayload) -> dict[int, TelemetryPayload]:
    """Simulate the full path a split batch takes: firmware splits it into
    frames, the gateway wraps each in a header and ships it, and the SBC
    (serial_link -> decode_payload -> server.on_frame) ingests each frame's
    members incrementally into one accumulating map. Asserts, per frame, that
    the whole ESP-NOW frame stays within the 250-byte cap."""
    hdr = PacketHeader(PacketType.TELEMETRY_BATCH, team_id=1, node_id=LEAD_NODE_ID,
                       seq=0, role=NodeRole.TEAM_LEAD)
    merged: dict[int, TelemetryPayload] = {}
    for body in batch.split_frames():
        frame = hdr.pack() + body
        assert len(frame) <= ESPNOW_MAX_FRAME, (
            f"frame is {len(frame)} B, exceeds ESP-NOW's {ESPNOW_MAX_FRAME} B cap "
            f"(would be silently dropped on the radio)")
        parsed = PacketHeader.unpack(frame[:HEADER_SIZE])
        payload = decode_payload(parsed, frame[HEADER_SIZE:])
        assert isinstance(payload, TelemetryBatchPayload), "frame did not decode as TELEMETRY_BATCH"
        for nid, t in payload.members.items():  # mirrors fusion.ingest_telemetry per member
            merged[nid] = t
    return merged


def test_max_members_per_frame_is_four():
    # Locks the wire math against silent struct drift: if a member ever grows
    # past 52 B (or shrinks), this catches it before it changes framing.
    assert MAX_BATCH_MEMBERS_PER_FRAME == 4, MAX_BATCH_MEMBERS_PER_FRAME
    four_body = TelemetryBatchPayload({i: make_member(i) for i in range(1, 5)}).pack()
    assert HEADER_SIZE + len(four_body) <= ESPNOW_MAX_FRAME
    five_body = TelemetryBatchPayload({i: make_member(i) for i in range(1, 6)}).pack()
    assert HEADER_SIZE + len(five_body) > ESPNOW_MAX_FRAME, (
        "a 5-member batch must NOT fit one frame — that's the bug being guarded")


def test_batch_split_round_trip_6_members():
    # The headline case: a doctrinal-sized team (6) survives split -> decode
    # with every member and every field recovered.
    batch, node_ids = make_batch(6)
    frames = batch.split_frames()
    assert len(frames) == 2, f"6 members should split into 2 frames (4 + 2), got {len(frames)}"

    merged = merge_over_serial(batch)
    assert set(merged) == set(node_ids), (
        f"lost members: expected {sorted(node_ids)}, recovered {sorted(merged)}")
    for nid in node_ids:
        assert merged[nid] == make_member(nid), f"member {nid} corrupted in round-trip"


def test_frame_sizes_and_counts_across_team_sizes():
    # Every team size from a single node up to the max the lead aggregates
    # (kMaxTelemetryBatch = 12): correct frame count, no oversize frame, and
    # all members recovered intact.
    for count in (1, 3, 4, 5, 6, 8, 12):
        batch, node_ids = make_batch(count)
        frames = batch.split_frames()
        assert len(frames) == ceil_div(count, MAX_BATCH_MEMBERS_PER_FRAME), (
            f"count={count}: expected {ceil_div(count, MAX_BATCH_MEMBERS_PER_FRAME)} frames, got {len(frames)}")
        merged = merge_over_serial(batch)
        assert set(merged) == set(node_ids), f"count={count}: members lost"
        for nid in node_ids:
            assert merged[nid] == make_member(nid), f"count={count}: member {nid} corrupted"


def test_empty_batch_yields_one_anchor_frame():
    # An empty team still emits one count=0 frame so the SBC can register the
    # lead as its anchor before any member has reported.
    batch = TelemetryBatchPayload({})
    frames = batch.split_frames()
    assert len(frames) == 1 and frames[0] == b"\x00"
    assert merge_over_serial(batch) == {}


def test_unsplit_oversize_would_be_dropped():
    # Documents the original bug directly: a single unsplit frame for a
    # strike-team-sized batch exceeds the cap and would be rejected by the
    # firmware's sendTo (>250 B) — which is why split_frames exists.
    batch, _ = make_batch(5)
    unsplit = PacketHeader(PacketType.TELEMETRY_BATCH, 1, LEAD_NODE_ID, 0, NodeRole.TEAM_LEAD).pack() + batch.pack()
    assert len(unsplit) > ESPNOW_MAX_FRAME
    # ...but split into frames, none exceed it:
    assert all(HEADER_SIZE + len(b) <= ESPNOW_MAX_FRAME for b in batch.split_frames())


def test_node_id_from_mac_matches_firmware():
    # Must reproduce firmware espnow_mesh.cpp node_id_from_mac() bit-for-bit —
    # the SBC uses it to identify the gateway from its USB MAC. Known fleet:
    from protocol import node_id_from_mac, parse_mac
    known = {
        "DC:B4:D9:3A:6D:08": 12457,  # gateway
        "14:C1:9F:51:12:54": 3098,   # team lead
        "A4:CB:8F:DF:D8:F8": 27420,  # field 1
        "14:C1:9F:52:84:FC": 34347,  # field 2
    }
    for mac, expect in known.items():
        assert node_id_from_mac(parse_mac(mac)) == expect, mac
    # colon-less form (some hosts) parses identically
    assert parse_mac("DCB4D93A6D08") == parse_mac("DC:B4:D9:3A:6D:08")
    # junk -> None (never a bogus anchor)
    assert parse_mac("not-a-mac") is None and parse_mac("DEAD") is None


def test_telemetry_payload_round_trip():
    # Base-payload fidelity (the kind of round-trip already validated in the
    # project) — the batch tests lean on this holding.
    t = make_member(4242)
    assert TelemetryPayload.unpack(t.pack()) == t


if __name__ == "__main__":
    import sys

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
