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
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
from fusion import NetworkFusion
from protocol import (
    Ics214EntryPayload,
    ParUpdatePayload,
    PacketType,
    TelemetryBatchPayload,
    TelemetryPayload,
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


@app.get("/api/par/{node_id}")
def api_par_history(node_id: int, limit: int = 20):
    return db.fetch_par_history(db_conn, node_id, limit)


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


@app.on_event("startup")
async def startup():
    asyncio.create_task(broadcast_loop())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial-port", required=True, help="e.g. /dev/ttyACM0 (Linux/Pi) or /dev/cu.usbmodemXXXX (Mac)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    link = SerialLink(args.serial_port, args.baud, on_frame=on_frame)
    link.start()

    import uvicorn
    try:
        uvicorn.run(app, host=args.host, port=args.port)
    finally:
        link.stop()


if __name__ == "__main__":
    main()
