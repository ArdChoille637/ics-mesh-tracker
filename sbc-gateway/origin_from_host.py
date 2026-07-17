"""Anchor the incident origin to the HOST machine's own position.

The SBC (this Mac during bench work; a Pi in the field) sits with the GATEWAY
node, and the team lead — the fusion frame's anchor — stands next to it at
incident start. So the host's position is the natural georeference for the
whole cloud: set origin = host fix, and the gateway + nearby nodes land where
the host actually is on the QGIS basemap.

Providers, tried in order:
  1. CoreLocationCLI (macOS; `brew install corelocationcli`; needs a ONE-TIME
     Location Services grant: System Settings > Privacy & Security > Location
     Services — enable for the terminal running this).
  2. gpsd (Linux/Pi with a USB GPS: `gpspipe -w -n 10` and take the first TPV).

Rotation is deliberately PRESERVED, never touched: a host fix says where the
anchor is, not which way the local frame points — rotation stays whatever the
operator surveyed (or unsurveyed, and honestly flagged as such).

Standalone:  python3 origin_from_host.py [--server http://127.0.0.1:8000] [--watch 60]
In-server:   server.py --origin-from-host   (imports get_host_fix from here)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.request

CORELOCATION_PATHS = ["/opt/homebrew/bin/CoreLocationCLI", "CoreLocationCLI"]


def _fix_from_corelocation(timeout_s: float = 15.0):
    exe = next((p for p in CORELOCATION_PATHS if shutil.which(p)), None)
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-once", "-format", "%latitude %longitude %h_accuracy"],
            capture_output=True, text=True, timeout=timeout_s)
        parts = out.stdout.split()
        if out.returncode == 0 and len(parts) >= 2:
            lat, lon = float(parts[0]), float(parts[1])
            acc = float(parts[2]) if len(parts) > 2 else None
            return (lat, lon, acc)
    except Exception:
        pass
    return None


def _fix_from_gpsd(timeout_s: float = 15.0):
    if not shutil.which("gpspipe"):
        return None
    try:
        out = subprocess.run(["gpspipe", "-w", "-n", "12"],
                             capture_output=True, text=True, timeout=timeout_s)
        for line in out.stdout.splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("class") == "TPV" and d.get("mode", 0) >= 2 and "lat" in d and "lon" in d:
                return (float(d["lat"]), float(d["lon"]), d.get("eph"))
    except Exception:
        pass
    return None


def get_host_fix(timeout_s: float = 15.0):
    """(lat, lon, accuracy_m|None) from the first working provider, else None."""
    return _fix_from_corelocation(timeout_s) or _fix_from_gpsd(timeout_s)


def post_origin(server: str, lat: float, lon: float) -> dict:
    """Update lat/lon over the API, PRESERVING the current rotation fields."""
    with urllib.request.urlopen(f"{server}/api/origin", timeout=5) as r:
        cur = json.load(r)
    body = json.dumps({
        "lat": lat, "lon": lon,
        "rotation_deg": cur.get("rotation_deg", 0.0),
        "rotation_surveyed": bool(cur.get("rotation_surveyed", False)),
    }).encode()
    req = urllib.request.Request(f"{server}/api/origin", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--watch", type=float, default=0,
                    help="re-anchor every N seconds (0 = once and exit)")
    args = ap.parse_args()

    while True:
        fix = get_host_fix()
        if fix:
            lat, lon, acc = fix
            got = post_origin(args.server, lat, lon)
            print(f"origin anchored to host fix: ({lat:.6f}, {lon:.6f})"
                  + (f" ±{acc:.0f} m" if acc else "") + f" -> {got}")
        else:
            print("no host fix available (grant Location Services / attach GPS) — origin unchanged")
        if not args.watch:
            break
        time.sleep(args.watch)


if __name__ == "__main__":
    main()
