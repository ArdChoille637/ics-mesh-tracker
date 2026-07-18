# RF-extraction feasibility — pass log

Companion to `docs/rf-extraction-feasibility-brief-claude-science.md` (the living
brief). Each pass = Claude Science's relayed feasibility handback + Claude Code's
independent verification + the resulting decisions. Raw handbacks are archived by
the user in ~/Downloads and summarized here.

---

## Pass 1 — 2026-07-17 · Science verdict + Code verification

**Handback:** `~/Downloads/rf_extraction_feasibility_findings.md` (Science pass 1).

### Science's verdict (summary)
The concept as literally stated — a coordinated multi-node RF **beam/chirp swept
across the 2.4 GHz mesh** — is **NO-GO on stock ESP32-S3**, for three independent,
each-sufficient reasons. Salvage: **(D) a single-node coded distress cadence** on
the existing radio + **FTM RTT** to sharpen the map; and, *only if* through-medium
locating is a hard requirement, a **separate low-band/near-field or acoustic
add-on** (a different device), because no 2.4 GHz coordination can penetrate the
target media.

Per-tier:
- **Tier 0 (decomposition + silicon):** (A) coherent phased-array beam = NO-GO;
  (B) RF chirp/swept carrier = NO-GO (802.11 PHY exposes frame *content*, not the
  waveform); (C) time-sequenced "chase" = realizable only as a MAC schedule, buys
  nothing; (D) coded distress cadence = the one interpretation that works *and* is
  useful. The concept collapses to (D).
- **Tier 1 (band):** 2.4 GHz is a *category error* for through-medium extraction —
  **near-field magnetic (457 kHz) vs far-field E-field (2.4 GHz)**; body-worn
  nodes are always far-field, so emissions are absorbed by the water content of
  the exact target media. Link budget inverts: **closes easily in open air (where
  you don't need a beacon) and collapses buried/body-shadowed (the whole point).**
- **Tier 2 (does coordination help?):** mostly NO. TDOA is useless at packet
  timing (300 m error at 1 µs; ns hardware timestamps needed — which is exactly
  why **FTM works and TDOA doesn't**). Sequential slots = 0 dB / 0°. Cadence
  detection is achievable by a *single* coded node. Coherent N² gain is
  unreachable. Sweeping the mesh does not beat one good coded node.
- **Tier 3 (regulatory):** the pincer — to be loud you must be spread-spectrum
  (not a sweep); to be a sweep you're stuck ~24 dB *below* the mesh's own data
  beacons (§15.249 ≈ −1.2 dBm). No Part-15 emergency exemption; self-jams the
  mesh. No legal high-power operating point exists.
- **Tier 4 (detection):** handheld Yagi ±11–15°; marginal value over the existing
  RSSI/PDR map exactly where the map already works, zero where the band fails.
- **Tier 5 (salvage, ranked):** (a) **build (D)** coded cadence + map; (b)
  **pursue FTM RTT** (the real step-change, ~1 m open / ~30 cm multi-channel —
  Indicative); (c) through-medium add-on only if required; dead ends = (A),(B),
  any high-power-2.4-GHz-through-rubble reading.

### Code's independent verification (concur)
Re-derived the load-bearing numbers from first principles; **all confirmed:**

| claim | Code's check | result |
|---|---|---|
| RF period @2.44 GHz | 1/2.44e9 | 410 ps ✓ |
| ±10 ppm crystal offset | 2.44e9 × 20e-6 | 48.8 kHz ✓ |
| phase-beat / coherence window | 1/48.8 kHz = 20.5 µs; /10 | ~2 µs within 36° ✓ |
| sync gap | 10 µs / 50 ps | 2×10⁵× ✓ |
| coherent N² prize | 10·log(4²), 10·log(8²) | +12 dB, +18 dB ✓ |
| 457 kHz near-field | λ=656 m, λ/2π | 104 m (rescue range is inside) ✓ |
| 2.4 GHz near-field | λ=12.3 cm, λ/2π | 2 cm (always far-field) ✓ |
| open-air link @100 m | FSPL 80.2 dB, 23−80.2 | −57 dBm ✓ |
| TDOA error | c·σ_t @1/10/48 µs | 300 m / 3 km / 14 km ✓ |
| §15.249 EIRP | (0.05·3)²/30 = 0.75 mW | −1.25 dBm ✓ (=Science's −1.2) |

**Assessment:** Pass 1 is correct and, on the band question, *deeper* than Code's
v0.1 grounding — the **near-field/far-field mechanism** is the right reason
penetration can't be coded around, and the **"works where not needed, fails where
needed" link-budget inversion** is the one-sentence go/no-go. No errors found; one
agreed refinement — **(C) should not be listed as a viable *extraction* mechanism**
(it's only a realizable MAC schedule with no range/DF/penetration value).

Two figures remain **Indicative** pending a bench measurement (Science's
falsification tests): the buried-wet-snow −117 dBm estimate, and the FTM ~30 cm
multi-channel-fusion accuracy on the actual XIAO boards.

### Decisions (go-forward)
1. **Drop** the coordinated RF sweep / phased-array / chirp — it's a confirmed
   dead end on this hardware and this band. Do not build it.
2. **Build (D)** when convenient: a single-node **coded distress cadence** (standard
   frames, low duty, legal) layered on the existing command map as a "downed
   responder — last-known node" cue. Honest; oversells nothing.
3. **Elevate FTM RTT** to the real next positioning upgrade — it sidesteps the
   TDOA timing wall via hardware timestamps and is the highest-value pivot the
   study surfaced. Bench-confirm accuracy on XIAO before committing.
4. **Through-medium locating**, if ever a hard requirement, is a **separate
   low-band/near-field/acoustic device** — correctly out of scope for the 2.4 GHz
   mesh.

**Status:** feasibility question CLOSED (no-go on the sweep; salvage path defined).
Next Science pass only needed if (b) FTM is pursued and wants a ranging-accuracy /
multi-channel-fusion grounding.
