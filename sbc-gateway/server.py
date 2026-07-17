"""SBC gateway service — reads the GATEWAY node over USB serial, fuses
RSSI+IMU telemetry into relative positions, and serves a live map +
ICS-214/PAR dashboard.

Run: python server.py --serial-port /dev/ttyACM0
(see ../README.md for finding the right port and full setup)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import geo
from fusion import NetworkFusion
from protocol import (
    Ics214EntryPayload,
    ParUpdatePayload,
    PacketType,
    TelemetryBatchPayload,
    TelemetryPayload,
    node_id_from_mac,
    parse_mac,
)
from serial_link import DecodedFrame, SerialLink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("server")

app = FastAPI()
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

fusion = NetworkFusion()
fusion_lock = threading.Lock()
db_conn = db.connect()

_ws_clients: set[WebSocket] = set()
_ws_clients_lock = threading.Lock()


ESPRESSIF_USB_VID = 0x303A  # ESP32-S3 native USB (JTAG/serial + the Nano ESP32)


def _same_port(dev: str, want: str) -> bool:
    """True if two device paths name the same physical port. Handles symlinks
    (Linux /dev/serial/by-id/…) and the macOS cu./tty. alias for one device."""
    import os
    try:
        if os.path.realpath(dev) == os.path.realpath(want):
            return True
    except Exception:
        pass

    def stem(p):
        b = os.path.basename(p)
        for pre in ("cu.", "tty."):
            if b.startswith(pre):
                return b[len(pre):]
        return b
    return stem(dev) == stem(want)


def detect_gateway_node_id(serial_port: str) -> int | None:
    """node_id of the gateway the SBC is USB-connected to, from that port's USB
    serial number (the ESP32-S3 base MAC). Returns None if the port isn't found,
    isn't an Espressif native-USB device, or carries no MAC-shaped serial (e.g.
    --serial-port none, or a UART-bridge board — pass --gateway-node-id then)."""
    if not serial_port or serial_port.lower() == "none":
        return None
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            if not _same_port(p.device, serial_port) or not p.serial_number:
                continue
            if p.vid is not None and p.vid != ESPRESSIF_USB_VID:
                log.warning("port %s is not an Espressif native-USB device "
                            "(vid=%s) — not auto-anchoring; pass --gateway-node-id "
                            "if this really is the gateway", p.device, hex(p.vid))
                return None
            mac = parse_mac(p.serial_number)
            if mac:
                return node_id_from_mac(mac)
    except Exception:
        pass
    return None


def on_frame(frame: DecodedFrame):
    """Runs on SerialLink's background thread — keep this fast and avoid
    touching the asyncio loop directly; broadcast happens from the
    periodic asyncio task instead, which reads fusion state under the lock."""
    hdr = frame.header
    with fusion_lock:
        if hdr.type == PacketType.TELEMETRY and isinstance(frame.payload, TelemetryPayload):
            fusion.ingest_telemetry(hdr.node_id, frame.payload)

        elif hdr.type == PacketType.TELEMETRY_BATCH and isinstance(frame.payload, TelemetryBatchPayload):
            # Only TEAM_LEAD nodes send batches — the sender of a batch IS
            # the anchor for its team's relative coordinate frame.
            fusion.set_anchor(hdr.node_id)
            for member_node_id, telemetry in frame.payload.members.items():
                fusion.ingest_telemetry(member_node_id, telemetry)

        elif hdr.type == PacketType.PAR_UPDATE and isinstance(frame.payload, ParUpdatePayload):
            db.insert_par_event(db_conn, hdr.node_id, frame.payload.status.name, frame.payload.since_ms)
            log.warning("PAR update: node %d -> %s", hdr.node_id, frame.payload.status.name)

        elif hdr.type == PacketType.ICS214_ENTRY and isinstance(frame.payload, Ics214EntryPayload):
            db.insert_ics214(db_conn, hdr.node_id, frame.payload.timestamp_ms, frame.payload.text)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "map.html")


@app.get("/api/ics214")
def api_ics214(limit: int = 200):
    return db.fetch_ics214(db_conn, limit)


@app.get("/api/nodes")
def api_nodes():
    """Live snapshot for the QGIS plugin. Each node is enriched with a
    server-computed lat/lon (georeferenced through the incident origin =
    gateway/host-GPS position, WITH the frame rotation applied), so the plugin
    plots geometry directly — no client-side trig, and the magnetometer-free
    rotation is handled in one place (geo.py) instead of being wrongly assumed
    north-aligned. x_m/y_m stay in the payload for debugging."""
    with fusion_lock:
        snapshot = fusion.snapshot()
        env = fusion.environment_summary()
        anchor_id = fusion._anchor_node_id
    origin = geo.get_origin()
    from dataclasses import asdict
    for n in snapshot:
        lon, lat = geo.local_to_wgs84(n["x_m"], n["y_m"], origin)
        n["lon"], n["lat"] = lon, lat
    return {"nodes": snapshot, "env": env, "origin": asdict(origin),
            "anchor_node_id": anchor_id}


@app.get("/api/par/{node_id}")
def api_par_history(node_id: int, limit: int = 20):
    return db.fetch_par_history(db_conn, node_id, limit)


# --- QGIS command-map integration -----------------------------------------
# Three GeoJSON layers (points / 1-sigma ellipses / anchor-centred proximity
# rings) that QGIS loads straight from these URLs with auto-refresh — see
# qgis/setup_qgis.py. Positions are georeferenced through the operator-set
# incident origin (geo.py); until it's set, features carry origin_set=false.

class OriginBody(BaseModel):
    lat: float
    lon: float
    rotation_deg: float = 0.0
    rotation_surveyed: bool = False


@app.get("/api/origin")
def api_get_origin():
    from dataclasses import asdict
    return asdict(geo.get_origin())


@app.post("/api/origin")
def api_set_origin(body: OriginBody):
    from dataclasses import asdict
    try:
        return asdict(geo.set_origin(body.lat, body.lon, body.rotation_deg,
                                     body.rotation_surveyed))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def _read_snapshot() -> list[dict]:
    """READ-ONLY view for the /qgis endpoints. recompute_multilateration() is
    deliberately NOT called here — it is stateful (complementary-filter blend +
    the flip-instability history), so calling it per-GET would make the fusion
    dynamics scale with the number of QGIS viewers. broadcast_loop is the single
    writer, recomputing every 1 s; worst-case staleness here is 1 s, under the
    QGIS 2 s refresh."""
    with fusion_lock:
        return fusion.snapshot()


@app.get("/qgis/nodes.geojson")
def qgis_nodes():
    return geo.nodes_geojson(_read_snapshot())


@app.get("/qgis/ellipses.geojson")
def qgis_ellipses():
    return geo.ellipses_geojson(_read_snapshot())


@app.get("/qgis/rings.geojson")
def qgis_rings():
    return geo.rings_geojson(_read_snapshot())


@app.get("/qgis/status.geojson")
def qgis_status():
    with fusion_lock:
        snapshot = fusion.snapshot()
        env = fusion.environment_summary()
    return geo.status_geojson(snapshot, env)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    with _ws_clients_lock:
        _ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # not expecting client messages; just keeps the connection open
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_clients_lock:
            _ws_clients.discard(ws)


async def broadcast_loop():
    while True:
        await asyncio.sleep(1.0)
        with fusion_lock:
            fusion.recompute_multilateration()
            snapshot = fusion.snapshot()
            env = fusion.environment_summary()
        dead = []
        with _ws_clients_lock:
            clients = list(_ws_clients)
        for ws in clients:
            try:
                await ws.send_json({"nodes": snapshot, "env": env})
            except Exception:
                dead.append(ws)
        if dead:
            with _ws_clients_lock:
                for ws in dead:
                    _ws_clients.discard(ws)


_bg_tasks: set = set()  # keep a hard ref — the loop holds tasks weakly, and a
                        # GC'd broadcast_loop = silent staleness freeze


@app.on_event("startup")
async def startup():
    t = asyncio.create_task(broadcast_loop())
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial-port", required=True,
                        help="e.g. /dev/ttyACM0 (Linux/Pi) or /dev/cu.usbmodemXXXX (Mac); "
                             "'none' runs the server without a gateway (map/QGIS development)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--origin-lat", type=float, default=None,
                        help="incident origin: real-world latitude of the anchor node")
    parser.add_argument("--origin-lon", type=float, default=None)
    parser.add_argument("--origin-rotation-deg", type=float, default=0.0,
                        help="true-north bearing of the fusion frame's +Y axis (CW degrees)")
    parser.add_argument("--origin-from-host", action="store_true",
                        help="anchor the origin to THIS machine's position (CoreLocationCLI "
                             "on macOS / gpsd on a Pi) and re-anchor periodically")
    parser.add_argument("--origin-from-host-interval", type=float, default=60.0,
                        help="seconds between host-fix re-anchors (with --origin-from-host)")
    parser.add_argument("--gateway-node-id", type=int, default=None,
                        help="pin this node_id as the fusion anchor (the frame origin). "
                             "Default: auto-detect from the serial port's USB MAC — the "
                             "gateway is the node whose real position we know (host GPS).")
    parser.add_argument("--no-gateway-anchor", action="store_true",
                        help="disable gateway anchoring; fall back to the team-lead "
                             "(TELEMETRY_BATCH sender) as the soft anchor")
    args = parser.parse_args()

    geo.load_origin()  # persisted origin from a previous run, if any
    if args.origin_lat is not None and args.origin_lon is not None:
        geo.set_origin(args.origin_lat, args.origin_lon, args.origin_rotation_deg)
        log.info("incident origin set from CLI: %s", geo.get_origin())
    elif not geo.get_origin().origin_set and not args.origin_from_host:
        log.warning("incident origin NOT set — QGIS layers will carry origin_set=false "
                    "(set it via POST /api/origin, --origin-lat/--origin-lon, or --origin-from-host)")

    if args.origin_from_host:
        from origin_from_host import get_host_fix

        def _host_anchor_loop():
            # Rotation is preserved on every update — a host fix says where the
            # anchor is, not which way the frame points. Failures (permission
            # not yet granted, no GPS) log once per state change, not per tick.
            had_fix = None
            while True:
                fix = get_host_fix()
                if fix:
                    lat, lon, acc = fix
                    cur = geo.get_origin()
                    geo.set_origin(lat, lon, cur.rotation_deg, cur.rotation_surveyed)
                    if had_fix is not True:
                        log.info("origin anchored to host fix (%.6f, %.6f)%s — re-anchoring every %gs",
                                 lat, lon, f" ±{acc:.0f}m" if acc else "", args.origin_from_host_interval)
                    had_fix = True
                else:
                    if had_fix is not False:
                        log.warning("no host fix (grant Location Services / attach GPS) — origin unchanged")
                    had_fix = False
                time.sleep(args.origin_from_host_interval)

        threading.Thread(target=_host_anchor_loop, daemon=True, name="host-anchor").start()

    # Pin the GATEWAY as the fusion anchor (frame origin). The gateway sits at
    # the host/SBC, so origin (host GPS) IS the gateway's position and every
    # other node is triangulated relative to it. Identify the gateway by the
    # node_id derived from its USB serial (= the ESP32-S3 base MAC) — no
    # firmware handshake needed.
    if not args.no_gateway_anchor:
        gw_id = args.gateway_node_id or detect_gateway_node_id(args.serial_port)
        if gw_id:
            with fusion_lock:
                fusion.set_anchor(gw_id, lock=True)
            log.info("gateway node %d pinned as fusion anchor (frame origin)", gw_id)
        else:
            log.warning("could not identify the gateway node_id (no USB MAC on %s) — "
                        "falling back to team-lead soft anchor. Pass --gateway-node-id.",
                        args.serial_port)

    link = None
    if args.serial_port.lower() != "none":
        link = SerialLink(args.serial_port, args.baud, on_frame=on_frame)
        link.start()
    else:
        log.warning("running WITHOUT a gateway serial link (--serial-port none)")

    import uvicorn
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        if link:
            link.stop()


if __name__ == "__main__":
    main()
