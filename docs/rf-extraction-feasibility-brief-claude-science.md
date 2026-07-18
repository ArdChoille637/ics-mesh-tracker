# Claude Science — feasibility brief: mesh-RF "synchronized sweep" emergency-extraction concept

<!-- LIVING DOCUMENT — do not treat as one-shot. -->
**Status:** `PASS 1 FILED — coordinated sweep NO-GO; build (D) coded cadence + pursue FTM` · **Version:** v0.2 · **Updated:** 2026-07-17

> Iterated as the Code⇄Science collaboration runs: each pass, Claude Code
> verifies the claims, folds the outcome in, bumps the version, and records the
> verdict (full narrative in `docs/rf-extraction-log.md`). Raw handbacks relayed
> by the user. **Open agenda** = whatever below has no closing verdict.

### Revision log
- **v0.2 (2026-07-17)** — **Pass 1 filed + independently verified by Code.**
  Verdict: the coordinated RF sweep/beam/chirp is **NO-GO on stock ESP32-S3**
  (three independent reasons). Salvage: build **(D)** a single-node coded distress
  cadence + **pursue FTM RTT**; through-medium locating = a separate low-band
  device. Full record + Code's number-by-number verification in
  `docs/rf-extraction-log.md`.
- **v0.1 (2026-07-17)** — Initial brief drafted by Claude Code, pre-grounded with
  a 3-lens fact pass (ESP32-S3 RF/timing, the fielded SAR-beacon landscape, FCC
  Part 15 + DF physics).

### ✔ PASS 1 VERDICT (2026-07-17) — feasibility CLOSED
- **Tier 0:** (A) coherent beam = NO-GO · (B) RF chirp = NO-GO · (C) "chase" = a
  MAC schedule only, no value · **(D) coded cadence = the surviving, useful form.**
- **Tier 1:** 2.4 GHz is a *category error* for through-medium locating —
  near-field magnetic (457 kHz) vs far-field E-field (2.4 GHz); the link **"works
  where not needed (open air), fails where needed (buried/body-shadowed)."**
- **Tier 2:** coordination buys identity + a detectable cadence, **not** range/DF/
  penetration; coherent N² gain unreachable; sweeping ≯ one good coded node.
- **Tier 3:** no legal high-power point (§15.249 ≈ −1.2 dBm; the spread-spectrum
  vs sweep pincer; self-jams the mesh).
- **Tier 4:** marginal value over the existing RSSI/PDR map where the map works,
  zero where the band fails.
- **Tier 5:** **build (D)** + **elevate FTM RTT** (the real upside) + through-medium
  = a *separate* device. Dead ends: (A),(B), high-power 2.4 GHz through-rubble.

*(Agenda below is the original Pass-1 prompt, kept for the record.)*

---

## ⛔ Scope — this is a FEASIBILITY STUDY ONLY

**Do not write code. Do not design firmware. Do not prototype or test.** The
deliverable is a *reasoned physics-and-regulatory feasibility verdict* with
citations — go / no-go / salvageable-form, and *why*. If a question can only be
answered by building or measuring, say so and state what the bench test would
be; don't pretend to run it. Code will handle any implementation later, only if
your verdict says a version is worth building.

## Who you are

Claude Science on this team — RF propagation, antenna/EM physics, and
signals/estimation first principles (you already grounded this project's
positioning stack across 12 passes; see `research-brief-claude-science.md`). Here
the question is not "how accurate is the map" but "**is a proposed new capability
physically real at all.**"

## How you participate (relay protocol + house rules)

- You're **user-relayed** — end your reply with a `===SCI-LOG===` … `===END===`
  block so Code can file it under `Log — Claude Science`.
- **Tier every quantitative claim** `Validated` (checked against a primary
  source / first-principles derivation) vs `Indicative` (plausible, unverified).
- **Code has pre-loaded starting facts below** (from a quick grounding pass, with
  sources). Treat them as `Indicative` until you independently confirm — that
  cross-check is the point. Correct us where we're wrong; go deeper where we're
  shallow.
- **Give a verdict, with the physics.** "Infeasible because X" or "feasible in
  form Y but not Z." Rank options; say which you'd pursue.

## The concept (in the user's words)

> "Use the mesh network to generate a **synchronized pulse of its RF in a sweep**
> as an emergency-extraction concept."

The intent: when a responder is downed/lost, the ICS mesh (body-worn Seeed XIAO
**ESP32-S3** nodes on 2.4 GHz ESP-NOW) stops being just a tracking fabric and
becomes an **active locator** — coordinating its radios into a synchronized,
swept emission that a rescue/extraction team can detect and home in on. The map
already knows *roughly* where everyone is (RSSI + step-PDR, proximity-grade); the
extraction beacon is about the **last-tens-of-meters, get-a-team-to-the-body**
problem, ideally through foliage / rubble / a collapsed structure.

## The system, in one page (so you can reason without the repo)

4+ ESP32-S3 nodes, 2.4 GHz **ESP-NOW** mesh, body-worn. One is a USB-tethered
**gateway** at an on-site laptop (the "SBC"). Positioning today is RSSI
multilateration + step-detection PDR, **explicitly proximity-grade, not
survey-grade**, and the system is deliberately honest about that. Framing for the
whole project: a **training / exercise / wildland-SAR accountability aid — NOT an
NFPA-1982 PASS or a certified life-safety device.** That honesty constraint
extends here: an extraction beacon that *looks* like a certified rescue locator
but isn't would be worse than none.

## The central decomposition you must resolve first

"Synchronized pulse of RF **in a sweep**" is not yet a defined signal. It could
mean physically-distinct things, and they do NOT share a feasibility answer:

- **(A) Coherent distributed phased-array beam that sweeps** — nodes phase-align
  their carriers to form a directional beam and steer it. *(This is the most
  powerful reading and the one Code believes fails hardest — see Tier 0.)*
- **(B) A frequency chirp / RF sweep** across the band — a swept-carrier the
  detector correlates against.
- **(C) A time-sequenced spatial pulse pattern** — nodes fire standard packets in
  a choreographed order/timing so the *emission location* sweeps across the team
  (a "chase"), giving a detector a recognizable spatiotemporal signature and
  possibly TDOA geometry.
- **(D) An amplitude/rate-coded distress train** — a single recognizable pulse
  pattern (like a strobe/PASS cadence) that says "downed responder here," DF'd by
  a handheld.

**Tier 0 asks you to fix this decomposition, tell us which interpretations are
physically viable on stock ESP32-S3 hardware, and which the concept actually
needs.** Everything else styles around that.

---

## What Code already found (Indicative — verify + extend)

These are the grounding facts we loaded; each has a source; treat as starting
points, not answers.

**ESP32-S3 radio reality:**
- TX ~19–21 dBm conducted; XIAO's bundled antenna ~1–3 dBi ⇒ **~20–23 dBm EIRP,
  omnidirectional** (no high-gain aperture). *(Espressif ESP32-S3 datasheet; Seeed XIAO wiki.)*
- **The PHY is 802.11-locked.** `esp_wifi_80211_tx` injects arbitrary 802.11
  *frame content* but only through the fixed DSSS/OFDM PHY at 802.11 rates —
  **no baseband/DAC access to synthesize a chirp, sweep, or non-802.11
  waveform.** *(esp32-80211-tx project; ESP-IDF vendor-tx docs.)* → **This looks
  like the single highest-leverage constraint: interpretation (B) and any
  arbitrary-waveform reading of (A) may be dead on this silicon.**
- Cross-node time sync over ESP-NOW ≈ **microsecond to tens-of-µs** (1 µs timer,
  but ~48 µs TSF read latency + jitter). *(esp32.com; ESP-IDF esp_timer.)*
- Crystals ~±10 ppm ⇒ ~**24 kHz** carrier offset between nodes at 2.4 GHz.
- **Coherent beamforming (A):** 2.4 GHz RF period ≈ **417 ps**; constructive
  combining needs alignment on the order of **tens of ps**. A 24 kHz offset slews
  relative phase a full 360° every **~40 µs** — a coherence window ~10⁶–10⁷×
  tighter than ESP-NOW sync, and **no RF phase-lock path is exposed.** Published
  distributed-coherent arrays need dedicated closed-loop RF phase-sync hardware.
  *(arXiv 2201.08931; derivation from λ.)* → **Code's read: coherent phased-array
  sweep is very likely infeasible on stock hardware — but the verdict word is
  yours.**
- **Adjacent upside:** ESP32-S3 *does* support **802.11mc FTM (Fine Timing
  Measurement) RTT ranging** — ~1 m open / ~5 m indoor, ~30 cm with multi-channel
  fusion (capped by 2.4 GHz 20/40 MHz). *(arXiv 2401.16517.)*

**The fielded SAR-beacon landscape deliberately avoids 2.4 GHz** (three
physics-driven tiers):
- **Tier A — last-tens-of-m through-medium:** avalanche **457 kHz** *near-field
  magnetic* coupling (media-agnostic — snow/body/rock barely attenuate it;
  range ~40–60 m, falls off 1/r³); **RECCO 917 MHz** harmonic radar (~200 m air,
  ~20 m snow). Both *chosen* to avoid GHz frequencies.
- **Tier B — global alert + ID:** **406 MHz** PLB/EPIRB (Cospas-Sarsat) + 121.5
  MHz homing.
- **Tier C — acoustic + accountability:** NFPA-1982 **PASS** = a ≥95 dBA acoustic
  alarm; RF is *optional accountability/evacuation signaling only*, not fine RF
  DF. (Existence proof that even the fire service hasn't solved through-structure
  RF locating.)
- **2.4 GHz penetration is the worst of any of these:** foliage ~0.5 dB/m (rising,
  +3–8 dB wet); heavy concrete ~23 dB; wet snow absorbs GHz within ~0.1 m; **the
  responder's own body shadows 2.4 GHz by tens of dB.** *(rfessentials; IJARCCE
  life-detection; MDPI Sensors 2018 body-shadowing; NASA NTRS snow.)*

**Regulatory / detection:**
- **FCC fork (the spine of the study):** a bespoke *pulsed/swept, single-freq*
  emitter is neither FHSS nor compliant digital modulation, so it falls under
  **Part 15.249 ≈ −1.2 dBm EIRP** (≈19 dB *below* the 15.247 spread-spectrum
  allowance). To use 15.247's +30 dBm you must be genuinely FHSS (≥15 ch, dwell
  limits) or DSSS/OFDM (≤8 dBm/3 kHz PSD) — which **constrains the very
  "swept/pulsed" feature.** The high-power legal path is **licensed & off-ISM**
  (Part 90, or the 406 MHz/Part 95 PLB path). **There is no emergency exemption
  in Part 15, and 15.5 forbids causing harmful interference.** *(47 CFR 15.247/
  15.249/15.5; FCC PLB page.)*
- **Self-jamming:** a high-duty in-band beacon busies the CSMA channel and
  desenses co-located receivers (~40 dB isolation cited) — it would **degrade the
  very positioning mesh (and bystander Wi-Fi) it rides on.** *(ESP-IDF coexist;
  Silabs coexistence.)*
- **Body-worn SAR:** measured ~1.18 W/kg at Wi-Fi power vs the 1.6 W/kg limit;
  near-1 W torso-worn high-duty plausibly breaches → power/duty backoff. *(arXiv 2202.05166; FCC RF-exposure.)*
- **2.4 GHz DF:** handheld Yagi + RSSI ≈ **±11–15° bearing**, multipath-dominated
  and *worse* in the foliage/rubble clutter that matters. *(Novelbits RTLS; RadioReference DF.)*

---

## Feasibility agenda

### Tier 0 — Fix the concept + the hardware go/no-go *(do this first)*
Resolve the (A)/(B)/(C)/(D) decomposition. For each interpretation, is it
physically realizable on a stock ESP32-S3 (closed 802.11 PHY, ±10 ppm free-running
oscillators, µs-class packet sync)? Confirm or overturn Code's read that (A) and
(B) fail on this silicon and only (C)/(D) — packet-level choreography of standard
frames — survive. If the concept *requires* (A) or (B), does it need an SDR / a
different radio entirely? **State the surviving interpretation(s) precisely.**

### Tier 1 — The band reckoning: is 2.4 GHz even the right RF?
Given that every fielded SAR beacon deliberately avoids GHz frequencies for the
exact media this project targets (foliage, rubble, body, snow), what is the
**honest niche** — if any — for a 2.4 GHz mesh beacon? Candidate niches to test:
*zero extra hardware / already-worn / already-powered / complements the existing
map rather than replacing dedicated SAR gear.* Or does the physics say a locator
that must penetrate the target media **fundamentally belongs on a lower band /
near-field / acoustic**, making a 2.4 GHz extraction beacon a category error?

### Tier 2 — The crux: does *coordination/sweeping* add value over one node beaconing?
The concept's core premise is that synchronizing/sweeping the mesh beats a single
node just yelling. Test it. With coherent gain off the table (Tier 0), the real
mechanisms sync could buy are: **sequential time-slot separation** (resolve which
node, avoid packet collision), **TDOA geometry** (multi-receiver timing), and a
**recognizable spatiotemporal signature** for detection/noise-rejection. Quantify
what each actually gains (dB of detectability? degrees of DF? metres of range?)
vs the added complexity — or conclude the coordination is marginal.

### Tier 3 — Regulatory & safety envelope
For each surviving waveform (Tier 0), state which FCC regime it lives in — 15.249
(~−1.2 dBm, legal but feeble), 15.247 (higher power but must be compliant
FHSS/DSSS, constraining the "sweep"), or licensed/off-ISM. Address the
no-emergency-exemption + non-interference reality, the body-worn SAR ceiling at
high duty, and the self-jamming of the mesh. **Is there a legal, non-interfering,
safe operating point that still does something useful?**

### Tier 4 — The detection/extraction side + link budget
What actually homes in on this — handheld Yagi DF (~±11–15°), a drone/aircraft
receiver, another node? Close the **link budget** at ~20–23 dBm EIRP omni through
the target media to the ranges that matter for extraction (tens to a few hundred
m). Does the range that closes overlap the range where a team still needs help
finding the body? (If RSSI already localizes to ~10 m, what does the beacon add?)

### Tier 5 — The salvageable form (constructive)
If the coordinated 2.4 GHz sweep is infeasible/marginal, what's the honest best
path? Rank: **(a)** a simple single-node distress *cadence* + the existing map;
**(b)** pivot the effort to **FTM RTT ranging** to sharpen positioning (adjacent
real upside — is it worth it?); **(c)** a complementary **hardware add** on a band
that actually penetrates (457 kHz-class near-field, RECCO-style passive reflector,
or an acoustic PASS-style alarm) — with the mesh providing the coarse cue and the
add-on the last-tens-of-metres. Say which you'd build, and which is a dead end.

### Adjacent "further aspects" (lighter, optional)
The user asked for *further aspects* generally. If a pass has room: (i) **FTM
positioning** as a step-change over RSSI (Tier 5b) — quantify the accuracy/cost;
(ii) a **breadcrumb/egress** use of the mesh (last-known-safe-path back out).
Keep these secondary to the extraction concept.

## Suggested first turn

**Open with Tier 0 + Tier 1.** If (A) coherent beamforming and (B) RF chirp are
physically out on stock ESP32-S3 — and if 2.4 GHz is simply the wrong band to
penetrate the extraction media — then the whole concept narrows to (C)/(D)
packet-choreography on a feeble legal power budget, and the honest recommendation
may be Tier 5's pivot. Give us that verdict with the physics, tiered
`Validated`/`Indicative`, and *tell us where we (Code) framed it wrong.* The
regulatory fork (Tier 3) and link budget (Tier 4) can follow once the surviving
waveform is fixed.
