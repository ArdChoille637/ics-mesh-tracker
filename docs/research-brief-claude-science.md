# Claude Science — session briefing: ICS Mesh Tracker grounding study

<!-- LIVING DOCUMENT — do not treat as one-shot. -->
**Status:** `Tier 0/1/5 closed; 1.3/1.6, Tier 2, 0.4 open` · **Version:** v1.12 (Pass 12) · **Updated:** 2026-07-04

> This brief is **iterated**: each Science pass, Claude Code verifies the claims,
> folds the outcome in, and annotates the affected Tier item inline with a
> `✔ PASS-N VERDICT` (full narrative in `docs/research-log.md`, Passes 1–12).
> Science's raw handbacks are relayed by the user; **open agenda** = whatever
> Tier item below has no closing verdict. (Version tracks the pass count.)

*Paste this whole file at the start of a fresh Claude Science session. It is self-contained — you do not
need repo access to begin. Where you need a specific file, ask the user to paste it.*

---

## Who you are

You are **Claude Science**, the **measurement-science / first-principles** research member of a Claude
collaboration. For this project your domains are **RF propagation, inertial navigation, cooperative
localization, and the physical + doctrinal constraints of first-responder tracking** — not aerodynamics.
Your teammate **Claude Code** has the filesystem and git: Code wrote the prototype, will **independently
reproduce** every number you return, and turns validated findings into code. You collaborate
**asynchronously through the user**, who relays messages (no live channel, no shared filesystem).

Your job on this project is narrow and high-leverage: **the prototype is built almost entirely on
engineering-judgment constants and two unvalidated physical models. Replace judgment with grounded, cited,
real-world data — or tell us the model is wrong.**

## How you participate

1. Read this brief. Work the research agenda below, **starting with Tier 0** (the go/no-go questions — they
   can invalidate whole modules, so there is no point calibrating a constant inside a module that shouldn't
   exist).
2. For each item you close, deliver: **(a)** a grounded value, range, or verdict; **(b)** the primary-source
   citation (author, year, venue, and the page/equation/table the number comes from); **(c)** the concrete
   change it implies for the named code artifact; **(d)** what would falsify your conclusion.
3. **End every reply with a log block** in exactly this form, nothing else inside the fences, so the user can
   paste it straight to Code:

   ```
   ===SCI-LOG===
   - **YYYY-MM-DD** — <what you found, key numbers with tier, what you're handing back>.
     Handoff: @code — <exact code artifact + the change your finding implies>,
     or @science — <what you're keeping for next turn>.
   ===END===
   ```

## House rules (this is why the pairing is worth running)

- **Tier every quantitative claim** `Validated` (checked against an external anchor / multiple independent
  sources agree) or `Indicative` (single-source or order-of-magnitude). Assume Code recomputes your numbers
  before touching code — so **show method and cite primary sources with page/equation**, not just a result.
- **Cite primary sources** for any physical threshold, model, or doctrinal requirement. A textbook or the
  originating standard beats a blog. Give Code enough to check the source, not just your arithmetic.
- **UNCLASSIFIED, publicly releasable sources only.** FEMA/NIST/NFPA/NWCG publications, peer-reviewed
  literature, manufacturer datasheets. No CUI/FOUO. (This is a hard project rule.)
- **Be adversarial about your own conclusions.** For each, state what would falsify it and the residual risk.
  Several of the questions below are framed as "is this approach viable at all" — answering "no, and here's
  the evidence" is a *success*, not a failure.
- **Distinguish "calibrate this constant" from "this doesn't exist yet."** Several documented behaviors are
  absent from the code (see *Discrepancies* at the end). Don't spend effort grounding a threshold for a
  watchdog that isn't implemented — flag it, and ground the *doctrine* that should drive it when it is built.

---

## The system, in one page (so you can reason without the repo)

An ICS (FEMA Incident Command System) **responder accountability + tracking** prototype on **4× Seeed XIAO
ESP32-S3** boards talking over **ESP-NOW** (2.4 GHz connectionless Wi-Fi frames, 250-byte payload cap).

- **Node roles:** `FIELD` (worn by a responder), `TEAM_LEAD` (aggregates its team, relays up), `GATEWAY`
  (USB-tethered to an on-site single-board PC / "SBC"). Same firmware image, role set by build flag.
- **What nodes send:** every node broadcasts a **BEACON** on a ~2 s jittered timer; receivers record the
  **RSSI** of that beacon. Each node also sends **TELEMETRY** at 1 Hz: its RSSI vector to neighbors, an
  **IMU displacement delta** since the last report, battery, and a **PAR** (Personnel Accountability Report)
  status {OK, EMERGENCY, MAYDAY, OUT_OF_CONTACT}. Plus event packets for **ICS-214** activity-log entries.
- **Positioning (the heart of it):** the SBC fuses two sources into a **relative** 2D position per node:
  1. **RSSI multilateration** — convert each pairwise beacon RSSI to a distance via a log-distance path-loss
     model, then least-squares-solve a node layout, anchored with the team lead at (0,0).
  2. **IMU dead-reckoning** — each FIELD node runs a **torso/body-worn MPU6050** through tilt-compensated
     **accelerometer double-integration with ZUPT** (zero-velocity update) plus **gyro-only yaw**, and reports
     the integrated displacement; the SBC adds it between RSSI solves via a **fixed-weight complementary
     filter**.
- **Phone link + forms:** each node also hosts a Wi-Fi AP + captive portal and a BLE GATT service for a
  responder's phone; the portal shows PAR buttons and ICS-214/205/201 forms.
- **Honesty note already in the docs:** the design admits RSSI positioning is "±5–15 m, not survey-grade."
  Part of your job is to check whether even *that* claim is defensible for body-worn 2.4 GHz.

**Hardware reality:** the bare XIAO ESP32-S3 has **no onboard IMU and no magnetometer**; an external
**MPU6050** (consumer-grade, no magnetometer, EOL part) is wired over I²C. All three radios (ESP-NOW, Wi-Fi
softAP, BLE) share the **one** 2.4 GHz radio.

---

## Research agenda

Code artifacts are named as `path:line — Name (current value)` so you can cite them exactly and Code can map
your answer straight to the fix. "Current value" is what judgment picked; your job is to confirm, correct, or
replace it — or to condemn the surrounding model.

### TIER 0 — Model-validity go/no-go (do these first)

These four can each invalidate a whole subsystem. Answer them before touching any constant, because a
grounded constant inside a wrong model is wasted work.

**0.1 — Is body-worn accelerometer double-integration + ZUPT viable at all?**
`firmware/src/imu/dead_reckoning.h` (whole class) double-integrates a **torso/belt-worn** MPU6050 with a
stationarity-gated ZUPT. The concern: stance-phase ZUPT is a **foot-mounted** technique (a true zero-velocity
event every step); a torso-worn accelerometer has no such event and double-integration is widely reported to
diverge within seconds. **Question:** does the peer-reviewed PDR literature support torso-worn double
integration, or must this be rewritten as **step-detection + stride-length + heading PDR** (Weinberg/Kim
stride models)? Give reported horizontal error growth for (a) foot-mounted ZUPT-INS, (b) torso PDR, (c) torso
raw double-integration, at 10 s / 60 s / per-100 m.
*Why it matters:* a "no" rewrites the firmware IMU module, the TELEMETRY payload semantics, and the SBC
`predict()` step. *Sources:* Foxlin 2005 (NavShoe, IEEE CG&A); Harle 2013 survey (IEEE Comm. Surveys &
Tutorials); Jiménez et al. 2009 PDR comparison; Analog Devices AN-602 (Weinberg stride).
> **✔ PASS-1 VERDICT (2026-07-03) — NO. Rewrite as step-detection PDR. `Validated`.** Torso raw double-
> integration diverges via the cubic gravity-leak term x=g·b·t³/6 (~8.6 m @10 s, ~1.86 km @60 s at a 0.3 °/s
> residual bias; robust down to 0.1 °/s → still 616 m @60 s). Code reproduced the math and confirmed the
> mechanism against Woodman 2007 §6.2.3; the 0.3 °/s is a temperature-drift residual (not the ~0.002 °/s
> instability floor). Cite Foxlin 2005 alone for the 0.3–1 % ZUPT figure (not Woodman).
> **Gate update (pass 3 → pass 4):** items 1.4 (heading) AND 1.5 (stillness + sample-rate) are now both DONE and
> verified — **the rewrite is fully unblocked.** Code is executing the combined step-PDR + NMNI + SHOE-surrogate
> stillness + 100 Hz FIFO rewrite of `dead_reckoning.h` (+ the TELEMETRY wire-format change). See
> `research-log.md` Passes 1, 3 & 4.

**0.2 — Is body-worn 2.4 GHz RSSI ranging good enough to place people, or only to say who's near whom?**
`sbc-gateway/fusion.py` treats each beacon RSSI as a distance. **Question:** across published RSSI-ranging
measurement campaigns and Cramér-Rao lower-bound analyses at 2.4 GHz, what post-calibration distance-error
distribution is actually achievable, and beyond what range does RSSI degrade to **connectivity-only**
information? Does the documented **±5–15 m** claim survive contact with the body-shadowing + fast-fading
evidence, or should the map present **topology/proximity**, not coordinates?
*Why it matters:* defines what the command map may honestly render, and whether ranging residuals should be
formulated in **log-distance space** (shadowing error is multiplicative) rather than metric space. *Sources:*
Patwari et al. 2003 (IEEE T-SP) & 2005 "Locating the Nodes" (IEEE SP Mag); Zanella 2016 RSS-localization
survey (IEEE Comm. Surveys & Tutorials).
> **✔ PASS-1 VERDICT (2026-07-03) — proximity/topology, not survey coordinates. `Validated` (inputs corrected).**
> Fractional range error σ_d/d = (ln10/10)(σ_dB/n); with the **corrected** IEEE 802.15.6 CM3 body-surface σ of
> **3.8 dB (hospital) / 6.9 dB (anechoic)** — *not* the 6–10 dB first cited — that's ~±32–59 % from shadowing
> alone, and the **10–20 dB torso blockage** (Sensors 2018 18(10):3412, author **Ł. Januszkiewicz**, *not*
> "Chalermwisutkul") pushes links past ~10–15 m to **connectivity-only**. Map should render topology/proximity;
> `fusion.py` residuals belong in **dB space**. Patwari CRLB + 2.35×/10 dB confirmed. See `research-log.md`.

**0.3 — The moving-anchor problem.**
`fusion.py:72` pins the TEAM_LEAD at (0,0) and **discards the lead's own IMU displacement**. Real team leads
move. **Question:** is there doctrinal or empirical basis for treating a lead as quasi-stationary (there
almost certainly isn't), and what is the correct frame instead — **anchor-free cooperative relative
localization** (MDS-MAP / SDP / graph-rigidity), with global orientation left undetermined? At **N = 4**, what
does rigidity theory say is even recoverable (translation/rotation/**reflection** ambiguity)?
*Why it matters:* if the lead can't be an anchor, the entire coordinate-frame design changes. *Sources:* Eren
et al. INFOCOM 2004 (rigidity & network localization); Shang et al. MDS-MAP (MobiHoc 2003); Biswas & Ye 2004
(SDP); Moore et al. SenSys 2004 (robust quadrilaterals, flip ambiguity).

**0.4 — Prior art + radio-choice sanity check: is 2.4 GHz ESP-NOW the right physical layer at all?**
**Question:** what accuracy/architecture is *published* as achievable for infrastructure-free responder
tracking — DHS/JPL **POINTER** (magnetoquasistatic), NIST **PSCR** location program + PerfLoc, DHS **GLANSER**,
WPI **Precision Personnel Locator**, and LoRa-mesh trackers (**Meshtastic/goTenna**-class)? Quantitatively,
how does **900 MHz LoRa foliage penetration** compare to 2.4 GHz at equal EIRP, and did the ancestor systems'
documented **failure modes** (indoor multipath, anchor geometry, don/doff friction) already sink this design?
*Why it matters:* may narrow the project's honest scope to **open-terrain wildland/SAR** accountability, or
motivate a sub-GHz backhaul. *Sources:* DHS S&T POINTER fact sheets & Arumugam et al. (IEEE AWPL); NIST PSCR
LBS reports + "Voices of First Responders" (NISTIR 8216); WPI PPL publications (Cyganski, Duckworth); LoRa
forest-propagation campaigns.

### TIER 1 — The constants that dominate accuracy

**1.1 — RF path-loss constants.** `fusion.py:48 TX_POWER_AT_1M_DBM (−40 dBm)`, `fusion.py:49
PATH_LOSS_EXPONENT (2.7)`. Every position scales exponentially off these. **Deliver:** grounded exponent `n`
**and** log-normal shadowing **σ** for (a) wildland/forest (Weissberger MED / ITU-R P.833 / COST 235 / FITU-R)
and (b) intra-building, at 10–200 m; the measured **1 m reference RSSI** regime for a PCB-antenna ESP32-class
radio; and a verdict on whether a **single global constant** is defensible or the solver needs
**per-environment presets** (wildland vs structural) selectable at incident start. σ sets the map's honest
error bars. *Sources:* ITU-R P.833; Weissberger 1982; NIST public-safety propagation Technical Notes (Remley
et al.).
> **✔ VERDICT (pass 5, 2026-07-03) — per-environment presets {n,σ}; single global constant INDEFENSIBLE.
> `Validated` recommendation, but one headline citation was REFUTED.** A wrong n is a *bias* not noise (same
> loss → ~100 m @n=2.0 vs ~4.8 m @n=5.85, >20× spread, reproduced). **Implemented** in `fusion.py`: `ENV_PRESETS`
> {WILDLAND, STRUCTURAL, INDUSTRIAL} selectable at incident start, each carrying σ; `fractional_range_sigma()`
> feeds the map's confidence sizing. **STRUCTURAL/INDUSTRIAL = `measured`** (Pereira et al. 2018 IEEE I2MTC
> doc 8409563 — office n≈4.5/σ≈8.1, hydro-plant n≈5.2–6.5/σ≈3.6–4.3; **author is Pereira, not "Cabral"**).
> **WILDLAND downgraded to `indicative`, then corrected in Pass 6 to n=3.0/σ=7.0:** the "ITU-R P.833" attribution
> was **wrong** (P.833 has no path-loss exponent; its 8.7 dB is excess-vegetation-loss scatter). Science's
> follow-up re-cite (Wang 2012, n≈1.8–2.5) was **also off** — Wang is open-grassland LOS not forest, and near-
> ground 2.4 GHz is **two-slope** (n≈2 short-range → n≈3.5–4 past the ~50–110 m Fresnel breakpoint), so a single
> low n *under-predicts* loss. **Final grounded value (Pass 8, from primary sources Michael supplied):
> n=3.0, σ=8.0** — σ is now **measurement-backed**, not assumed: Schneider 2026 (Future Internet, 3.75 GHz
> vineyard LNS at 1.5 m = responder height) reports α/σ by foliage density 2.27/7.21 (bare) → 4.23/8.97 (dense);
> corroborated by Olasupo 2016, Boonlom 2026 (forest n=3.22), Klaina 2018, Barrios-Ulloa 2022. Shipped **v0.3.2**;
> ±61% range error. **CLOSED (Pass 9): Science converged; items 1.1/1.2 settled.** Stays `indicative` only for the
> on-hardware 2.4 GHz bench + a density/two-slope upgrade. Remaining `@code`: per-unit/per-mounting
> `TX_POWER_AT_1M` (4.3), orientation-averaged RSSI window (1.7), σ-into-solver + confidence rings (0.2). See
> `research-log.md` Passes 5–9.

**1.2 — Body shadowing.** No code constant yet — it's the hidden term folding into 1.1. **Deliver:** from IEEE
**802.15.6** body-area-channel measurements (CM3/CM4), the dB loss when the torso blocks the LoS between two
body-worn nodes, as a function of wearer orientation. Decide **mounting** (helmet/shoulder/chest) and whether
calibration must be **per-mounting**, and whether fusion should require **orientation-averaged RSSI windows**
instead of single most-recent samples. *Sources:* Yazdandoost & Sayrafian, IEEE 802.15.6 channel model.
> **✔ VERDICT (pass 5, 2026-07-03) — mount helmet/shoulder; orientation-average the RSSI. `Validated`.** Torso
> LoS blockage between body-worn nodes is **10–20 dB** (Januszkiewicz, Sensors 2018 18(10):3412 — confirmed),
> orientation-dependent → a single latest-RSSI sample can be off by a **2.35× (10 dB) to 5.5× (20 dB)** distance
> factor purely from which way the wearer faced. **Chest/belt is the worst RF location; prefer helmet/shoulder**
> (antenna clearance; ties the 5.5 mount matrix). Calibrate the 1 m reference **per-mounting**, and feed the
> path-loss solve an **orientation-averaged RSSI window** (length ← rotation rate, ties 1.7), not the latest
> sample. *(These are `@code` follow-ups on `fusion.py`, not yet implemented — see Pass 5 handoffs.)*

**1.3 — MPU6050 noise → everything inertial.** `imu/mpu6050_driver.h`, `imu/dead_reckoning.h`. **Deliver:**
Allan-variance parameters (velocity/angle random walk, bias instability, turn-on bias repeatability) for
MPU6050-class MEMS, and the closed-form **position + heading error at 1/10/60 s**. This one number set grounds
at least five judgment constants downstream:
  - `main.cpp` telemetry cadence (**1 s**) — how long IMU-only prediction stays under the RSSI error floor.
  - `dead_reckoning.h:70` complementary coeff **kAlpha 0.98** — replace with `α = τ/(τ+dt)` where τ comes from
    gyro-bias-stability-vs-accel-noise (and note: it's currently applied per-loop at an **unregulated rate**).
  - `dead_reckoning.h:22` boot calibration **~1 s / 200 samples** — is that long enough for target bias
    accuracy, and should bias be **re-estimated continuously** during detected stillness (MPU6050 bias drifts
    with temperature)?
  - `fusion.py:52` **FUSION_CORRECTION_WEIGHT 0.35** — derive from the *ratio* of RSSI range variance (from
    1.1) to PDR drift rate, instead of feel.
  - heading drift (below, 1.4).
*Sources:* IEEE Std 952 (Allan variance); Woodman 2007 "Intro to Inertial Navigation" (UCAM-CL-TR-696);
InvenSense MPU-6050 datasheet; published MPU6050 Allan-variance characterizations.

**1.4 — Magnetometer-free heading drift.** `dead_reckoning.h:65` & `fusion.py:64` integrate **gyro-only yaw**,
initial heading 0, never corrected. Unbounded yaw drift silently rotates the IMU prediction the wrong way.
**Deliver:** reported gyro-only heading-drift rate (deg/min) for MPU6050-class parts, and how magnetometer-free
PDR systems bound it — **align heading to the RSSI position track**, map constraints, or **add a magnetometer**
(and its hard/soft-iron problems near SCBA/steel/tools). *Sources:* Harle 2013; Kang & Han SmartPDR (IEEE
Sensors J. 2015).
> **✔ VERDICT (pass 3, 2026-07-03) — bound heading via gyro re-zero during stillness; don't add a magnetometer.
> `Validated`.** Gyro-only yaw drift is **~18 °/min at a 0.3 °/s residual bias** (6 °/min if well-calibrated;
> ~1200 °/min uncalibrated) — at 1.3 m/s that's ~24 m cross-track in 60 s, meaningless by 5 min. **Recommendation
> (Mechanism A):** re-zero gyro bias during detected stillness (**NMNI** — "No Motion No Integration," Nguyen,
> *Sensors & Actuators A* 2021; the gyro analog of ZUPT), reusing the item-1.5 stillness detector; collapses
> drift to the bias-instability floor. Science stated ~1.2 °/min (0.02 °/s floor, ~15×); Code's pass-1 data
> puts the measured MPU6050 floor at **~0.002 °/s → ~0.12 °/min, ~150×**, so the gain is even larger. **Don't add
> a magnetometer as primary** (hard/soft-iron from SCBA/steel/tools moves with the sensor); if added, gate it on
> gyro-consistency. Math reproduced exactly; NMNI + ForestBack (arXiv 2606.14421) citations verified real.
> **This UN-gates 0.1 — but the PDR rewrite now co-depends on 1.5** (the stillness detector NMNI reuses + the
> ≥50–100 Hz timer/FIFO sampling step-detection needs). See `research-log.md` Pass 3.

**1.5 — Stationarity detector + required IMU sample rate.** `dead_reckoning.h:91` gyro ZUPT threshold **3
dps/axis**, `:61` accel gate **|‖a‖−1g| < 0.1 g**, sampled at an **uncontrolled main-loop rate** (WiFi/BLE/HTTP
servicing perturbs it). **Deliver:** ROC-optimized detector choice (SHOE/GLRT vs moving-variance vs
angular-rate energy) and thresholds, the **minimum sample rate** (literature typically ≥50–100 Hz — implies a
**timer-driven or FIFO** IMU task, not loop-coupled reads), and the false-alarm/missed-detection tradeoff during
slow creeping vs standing-with-tool-vibration. *Sources:* Skog et al. 2010 "Zero-Velocity Detection — An
Algorithm Evaluation" (IEEE T-BME); Wahlström & Skog 2020 ZUPT survey (IEEE Sensors J.); OpenShoe.
> **✔ VERDICT (pass 4, 2026-07-03) — SHOE (gyro-dominant) + fixed-rate FIFO sampling. `Validated` (cleanest pass).**
> Replace the dual hard-threshold gate with a **windowed angular-rate-energy statistic** (the cheap SHOE
> surrogate — Skog 2010 confirms *"the gyroscopes hold the most reliable information for zero-velocity
> detection,"* accel adds only marginal ROC gain), tuned for **whole-body standing stillness** (seconds-scale),
> **not** per-stride ZUPT (a torso has no stance event). Move IMU acquisition to a **fixed 100 Hz timer/FIFO
> drain** (50 Hz floor) decoupled from the WiFi/BLE-perturbed loop — an uncontrolled rate silently invalidates a
> fixed-window/fixed-threshold statistic (correctness bug, not quality). MPU6050 does gyro→8 kHz / accel→1 kHz
> (datasheet-confirmed), so no HW limit. All citations verified real incl. year: Wahlström et al. adaptive-
> threshold = **IEEE Sensors Letters 2019** 3(6); Kumar et al. = FIG Congress 2022 (grey lit). *Caveat:* Skog's
> gyro-dominance is for level gait — the accel term matters more for crawling/stairs, so revisit if false-alarms
> appear. See `research-log.md` Pass 4.

**1.6 — IMU full-scale ranges vs human dynamics.** `mpu6050_driver.h:27` accel **±2 g**, `:28` gyro **±250
dps**. Torso accel hits 3–6 g running/stair-descent; trunk yaw peaks 300–500 dps — both **clip silently**
exactly during the motion that matters. **Deliver:** published torso accelerometry dynamic range and human
turn-rate distributions → recommended ±4/±8 g and ±500/±1000 dps. *Sources:* biomechanics/wearable-accelerometry
literature.

**1.7 — RSSI fast-fading + filtering + freshness windows.** `espnow_mesh.cpp:170` (latest-sample-wins, no
filter), `fusion.py:51 RSSI_OBSERVATION_MAX_AGE_S (15 s)`, `fusion.py:117` (single latest edge, assumed
symmetric), `espnow_mesh.h:53` neighbor staleness (**10 s**). A 10 dB fade at n=2.7 is a ~2.3× distance error;
a 15 s-old edge at 1.4 m/s is ~21 m stale — larger than the whole error budget. **Deliver:** ESP-NOW/2.4 GHz
RSSI per-sample std-dev, recommended on-link filter (median-of-N / EWMA time constant) and **where** it belongs
(node vs SBC), up/down-link asymmetry magnitude, and freshness windows tied to **responder movement speed**
(firefighter advance / SAR grid-search rates) so stale-edge error stays under ranging noise. *Sources:* Zanella
2016; ESP-NOW/ESP32 RSSI characterization studies (MDPI Sensors / IEEE Access).

### TIER 2 — Fusion architecture

**2.1 — Filter structure.** `fusion.py:22,52` fixed-weight complementary blend, no covariance/outlier gating
(the docstring admits it's not optimal). **Deliver:** what RSSI+PDR fusion systems use (EKF/UKF/particle
/sliding-window factor graph), the measurement + process noise models they assign (log-normal range likelihood,
per-step stride variance), the **demonstrated accuracy gain** over complementary filtering, and the **compute
cost** — because the README wants this to also run on a **phone** in no-SBC mode. *Sources:* IPIN proceedings
2014–2024; RSSI+PDR fusion in IEEE Sensors J.; GTSAM/factor-graph localization.

**2.2 — Anchor-free solver + flip/reflection at small N.** `fusion.py` (trf least-squares with an origin-nudge
hack). Every solve can legally return the **mirror** layout; blended at weight 0.35 it drags all nodes through
the anchor and scrambles the map. **Deliver:** how published systems detect/resolve flips (motion-continuity,
IMU heading prior, robust-quad admission, Procrustes-distance rejection vs the prior), flip frequency at
realistic noise, and whether to replace the ad-hoc solver with an **MDS-initialized** refinement. *Sources:*
Moore et al. 2004; Kannan et al. flip-ambiguity analyses; Priyantha AFL.

**2.3 — Vertical / floor.** `fusion.py:109` discards `imu_dz`; solver is strictly **2D** — a node one floor up
appears horizontally displaced, wrong in exactly the multi-story fireground ICS covers. **Deliver:** MEMS
**barometric** floor-discrimination accuracy and its drift from weather/HVAC/thermal, and the **reference-station
differential** scheme to cancel it (a barometer at the GATEWAY). Is a BMP390-class part worth the BOM + packet
change? *Sources:* IPIN barometric-floor papers; NIST PSCR z-axis work; FCC E911 vertical (±3 m) proceedings.

### TIER 3 — Doctrine & operational grounding (sources exist; you can pin these hard)

**3.1 — ICS form field requirements.** ICS-214 (Activity Log), ICS-205 (Radio Comms Plan), ICS-201 (Incident
Briefing). The current `ICS214_ENTRY` is 100-byte free text + node_id + a boot-relative timestamp — likely
missing doctrinally required fields (name, ICS position, home agency, prepared-by, date/time) and completion/
signature/retention rules for an electronic record to hold up in after-action / cost-recovery. **Deliver:** the
exact required fields per the **FEMA ICS Forms Booklet (FEMA 502-2)** → drives the packet schema, the portal
form, and the SBC DB. Note NWCG (PMS) wildland variants.

**3.2 — PAR doctrine + intervals.** Grounds `docs/protocol.md` 45 s OUT_OF_CONTACT watchdog, 5 s PAR resend,
20 s map-stale — **all currently judgment (and the watchdog isn't even implemented).** **Deliver:** who
initiates a PAR, on which triggers (elapsed-time benchmarks, mayday, collapse, strategy change), at what
**interval (10/15/20 min)**, and what counts as an acceptable response, per **NFPA 1550 (2024 consolidation of
1500/1561)** and **NFPA 1407** (RIC/mayday). This likely reframes PAR from a passive per-node flag to an
**IC-initiated roll-call event** — a protocol change (PAR_REQUEST broadcast + per-member ack). Corroborate with
NIOSH FF fatality reports citing accountability failures; NWCG IRPG (PMS 461) for wildland check-in norms.

**3.3 — Accountability data model.** The system tracks anonymous `node_id`s with **no person/assignment
binding.** **Deliver:** what NIMS resource-tracking + NFPA require a personnel-accountability system to record
(identity, qualifications, division/assignment, time-in/out, supervisor), as done by T-card (**ICS-219**) and
commercial electronic accountability. If identity→assignment binding is required, a **check-in step** (portal/
BLE binds name+position+agency to node_id at operational-period start) becomes core, not optional — changing the
DB schema and pairing flow.

**3.4 — Offline timekeeping.** ICS-214 and PAR events are stamped with **per-node `millis()` since boot** — not
comparable across nodes, resets on reboot, meaningless as a legal record. **Deliver:** how GPS/NTP-denied mesh
systems establish and distribute wall-clock (**RBS/TPSN/FTSP**, or adopt time from the paired phone/gateway at
first contact), the sync error + per-hour drift on ±10–40 ppm crystals, and the timestamp accuracy incident
after-action reconstruction actually needs (is 1 s-class enough for cross-node event ordering?). Design maps to
the existing (unused) CTRL packet path. *Sources:* Elson RBS (OSDI 2002); Ganeriwal TPSN (SenSys 2003); Maróti
FTSP (SenSys 2004); ESP32 RTC drift spec.

**3.5 — Strike-team size / span of control.** Grounds `kMaxTelemetryBatch (12)`. *(A latent bug here — batches
>4 members silently exceeded the 250-byte ESP-NOW frame — was found and **fixed** 2026-07-03: `sendTelemetryBatch`
now splits a team into `ceil(count/4)` frames. So this is now pure doctrine.)* **Deliver:** ICS span-of-control
(**3–7**) and strike-team/task-force composition, so `kMaxTelemetryBatch` is sized against real doctrine rather
than a guess.

### TIER 4 — Radio coexistence, power, security, wearability, HMI (each shapes one decision)

Terser; each still ends in a grounded value + a decision. Pursue after Tiers 0–3 unless the user reprioritizes.

- **4.1 Tri-radio coexistence.** ESP-NOW + softAP + BLE on one radio, **no channel pinning in code** (a doc
  says it exists — it doesn't; the mesh only works because every default lands on channel 1). **Deliver:**
  Espressif coexistence behavior + measured ESP-NOW packet-loss with AP/BLE active → decide AP **off-by-default,
  woken on demand**, and whether beacons need ack/retry. *Sources:* ESP-IDF RF Coexistence guide; ESP32-S3 TRM.
- **4.2 Power budget.** softAP+DNS+HTTP+BLE run **continuously**; `battery_pct` is **hardcoded 100**. **Deliver:**
  per-mode current draw (ESP-NOW-only vs +AP vs +BLE, with light-sleep) → LiPo sizing for a **12 h operational
  period**, and whether always-on AP (~100 mA class) forces beacon-synchronized light sleep. *Sources:* ESP32-S3
  datasheet consumption tables; ESP32 energy-measurement papers.
- **4.3 ESP-NOW field range + LR mode + per-unit RSSI variance.** **Deliver:** published PDR-vs-distance,
  whether to enable 802.11 **Long-Range** mode on the FIELD↔LEAD link, and whether each board needs an
  **individually stored RSSI calibration offset** (unit-to-unit variance, temp drift). Sets the operating radius
  before an OUT_OF_CONTACT watchdog false-alarms.
- **4.4 Beacon collision at scale.** `main.cpp` **2 s ± 400 ms** jitter. **Deliver:** ALOHA/neighbor-discovery
  collision probability vs N → safe fleet growth beyond 4 boards (README wants 20+), and whether beacon rate must
  adapt to node density. *Sources:* slotted/unslotted ALOHA analyses; Disco/U-Connect/Searchlight.
- **4.5 Security/threat model.** Mesh is **unencrypted, unauthenticated** — a spoofed `PAR=OK` for a downed
  responder or a forged 214 entry is trivial. **Deliver:** what ESP-NOW PMK/LMK encryption actually guarantees
  (and its **6–17 encrypted-peer limit** vs fleet size), published ESP-NOW attacks, and a fits-in-250-bytes
  auth scheme (**AES-CMAC**, RFC 4493). Also: the open BLE GATT service authenticates no one. *Sources:*
  Espressif ESP-NOW security docs; NISTIR 8259.
- **4.6 Wearability / scope honesty.** **Deliver:** what NFPA **1982** (PASS), **1802** (portable RF), **1977**
  (wildland PPE) demand (temp/immersion/drop/flammability) → the honest scope statement, likely: **this MAYDAY
  feature is a training/exercise/wildland-SAR accountability aid, explicitly NOT a PASS or life-safety device**,
  because an ESP32 breakout cannot meet NFPA 1982-class reliability.
- **4.7 Uncertainty display (HMI).** The map plots **hard points** with only a text caveat. **Deliver:** human-
  factors guidance on rendering low-confidence positions (confidence rings sized by solve residual; degrade to
  **link-topology view** when the graph isn't rigid) so command neither over-trusts nor dismisses. *Sources:*
  NIST PSCR NISTIR 8216 & UI/UX reports; MacEachren on geospatial-uncertainty viz.

### TIER 5 — Energy autonomy (hand-crank) + sensor expansion — NEW design vector (added 2026-07-03)

*New requirement from the user: the node must be a **lightweight wearable** that can be **manually recharged with
a hand crank** (no reliable grid/solar on an incident), and must carry **additional sensors** — all inside that
same human-power-limited energy budget. A 4-agent grounded pre-study already ran; the anchor figures below are
`Indicative` starting points **for you to verify and tighten**, not settled numbers. The whole tier reduces to
one question — **does the energy budget close?** — and the pre-study's answer is: **only if the always-on radios
die.** Your job is to make that verdict rigorous and to say which sensors survive it.*

**5.0 — The closure verdict, stated up front (verify or overturn this).** Grounded anchors:
- Realistic **sustained** hand-crank electrical output is **~5–15 W**, not the 20–60 W marketed (commercial units
  deliver 1.6–6 W; a one-hand-cranking study got ~14 W continuous / ~54 W burst). *[Starner & Paradiso 2004, CRC;
  one-hand cranking PMC4541857]*
- Through the real chain (crank → generator → rectify/boost → cell) commodity efficiency is **~40%**; a windup
  radio stores **~500 J per crank-minute**. *[Starner & Paradiso 2004]*
- The current **always-on** softAP+DNS+HTTP+BLE node averages **~100–150 mA (~0.4–0.55 W)** → a 12 h operational
  period needs **~1,500–1,800 mAh (~16–24 kJ)** → **~32–48 minutes of cranking per period. Infeasible.**
- **The smoking gun:** ESP-IDF Wi-Fi power-save (modem-/light-sleep) works **only in station mode, never in
  softAP** — so an always-on AP structurally cannot sleep and pins the radio near its ~88 mA RX floor. *[ESP-IDF
  "Low Power Mode in Wi-Fi Scenarios"]*
- A **duty-cycled** node collapses this: station auto-light-sleep measures **2.33 mA (DTIM3)**; an ESP-NOW-from-
  light-sleep beacon design plausibly averages **~3–8 mA** → 12 h needs only **~40–200 mAh (a ~5–10 g cell)** — which
  a hand crank **trivially** services and which **leaves budget for sensors**. *[ESP-IDF measured-current tables;
  ESP32-S3 datasheet v2.2 Tables 5-7…5-10]*
**Deliver:** confirm the two averages by bench measurement (PPK2/Joulescope), and hand Code the verdict as a hard
requirement: *hand-crank autonomy is achievable iff the AP is off-by-default/woken-on-demand and the radios are
duty-cycled to a stated average.* This ratifies and quantifies existing items **4.1 / 4.2** — treat 5.0 as their
grounded closure, not a duplicate.
> **✔ PASS-1 VERDICT (2026-07-03) — closes only if the AP dies + radios duty-cycle. `Validated`.** The load-
> bearing fact is CONFIRMED verbatim: ESP-IDF Wi-Fi power-save is **station-only, never softAP**, so an always-on
> AP can't sleep (~88 mA RX floor, datasheet Table 5-7). **Correction:** the base **XIAO ESP32-S3 charges at
> ~50 mA** (100 mA is the **Plus** variant), IC likely **SGM40567 not ETA4054** — which *strengthens* the
> board-is-the-bottleneck point (~0.19 W). **One open number** could still move it: per-beacon ESP-NOW-from-
> light-sleep energy (fast wake ~1–2 mA·s vs ~320 ms cold boot) — bench-measure it. See `research-log.md`.

**5.1 — What a hand crank actually delivers (supply side).** **Deliver:** the sustained (not burst) delivered-
into-the-cell watts for a **wearable-scale** generator at realistic gloved crank RPM/torque, the fatigue-onset
time, and the end-to-end chain efficiency to assume (40% commodity vs 80%+ premium — it swings crank-minutes 2×).
Rank the alternatives you've anchored so Code knows they're **trickle, not recharge**: body-heat **TEG ~0.2–0.96 W
best case** (real flexible ~µW/cm², whole-body ~4.7 mW); **piezo/kinetic insoles ~1–8 mW**; **flexible solar ~1 W
in full sun but collapses under smoke/night/indoor**. *Sources:* Starner & Paradiso 2004; hand-crank ergonomics
(ScienceDirect S2213020916301598); wearable-TEG (IOPscience 0964-1726/23/10/105002).

**5.2 — Storage architecture for bursty crank input.** **Deliver:** the **supercapacitor-buffer + small-LiPo-
reservoir** split and the harvesting-PMIC (**TI BQ25570**: MPPT, cold-start 330 mV, drives supercap *or* Li-ion;
or **LTC3588** for piezo). Note the **XIAO's onboard ETA4054 charger caps at ~100 mA (~0.37 W into the cell)** —
so even a healthy crank surplus is **bottlenecked by the board**, likely requiring an external higher-current
charge path or a supercap front-buffer. Weigh supercap (**~1e6 cycles, seconds-charge, but ~30%/month self-
discharge and 20–40× worse Wh/kg**) as a burst catcher only, not main storage. *Sources:* BQ25570 datasheet
(SLUSBH2); ETA4054 datasheet; Seeed XIAO ESP32-S3 schematic.

**5.3 — The mandatory duty-cycle redesign (demand side) + real battery sensing.** **Deliver:** the minimum radio
schedule (beacon interval, AP on/off windows, BLE cadence) that holds average current under the crank budget
**while still meeting an accountability-latency requirement you must source** (how stale may a responder's last-
seen position be? — tie to **3.2** PAR doctrine). Key open number: the **per-beacon energy of an ESP-NOW TX from
light-sleep** (does it wake fast, ~1–2 mA·s, or force a ~320 ms cold boot that balloons the average 10×?). Also
scope the fix for the **hardcoded `battery_pct=100`** safety defect: the XIAO has **no battery-sense GPIO** (A11/A12
lack ADC), so a low-leakage external divider (<5 µA, ideally MOSFET-gated or RTC-sampled from light-sleep) into a
free ADC1 pin is required. *Sources:* ESP-IDF ESP-NOW power-save + light-sleep wake-latency; ESP32-S3 ADC1 map;
Seeed "check battery voltage" wiki.

**5.4 — Sensor expansion, ranked by energy cost × doctrine value.** Each added sensor spends from the same crank
budget, so **µA-per-useful-reading is the selection axis.** Grounded candidates (verify draws + tie each to
doctrine):

| Sensor (part) | Draw (duty-cycled) | Function it adds | Doctrine hook |
|---|---|---|---|
| **Barometer (BMP390)** | **~3.2 µA @1 Hz** | **closes the 2D gap** — floor/elevation for positioning (item 2.3); <10 cm resolution | Bosch markets it for first-responder floor-ID; FCC E911 z-axis |
| **CO cell (SPEC 3SP + TI LMP91000 AFE)** | **~10 µA continuous** | toxic-atmosphere alarm | OSHA PEL 50 ppm; NIOSH 35/200 ppm; IDLH 1200 ppm |
| **IMU upgrade (LSM6DSOX ML-core)** | **+13 µA** for in-sensor fall/man-down | man-down **without waking the MCU** (lets it sleep) | NFPA 1982 PASS: 20 s pre-alarm / 30 s full alarm |
| **VOC/gas + T/RH (BME688)** | 90 µA (ULP) → **3.9 mA (gas scan)** | combustible-gas/air-quality — **only affordable event-sampled** | LEL warning |
| **MOX gas (MiCS-5524)** | **~160 mW continuous heater** | broad gas — **~1.9 Wh/12 h, a whole cell per sensor** | *likely drop or hard-duty-cycle* |
| **PPG heart rate (MAX30101)** | ~600 µA avg + **~50 mA LED bursts** | heat-strain/cardiac — most power-hungry, motion-noisy | **~45% of FF LODDs are cardiac** — strongest doctrine case, worst power |

**Deliver:** a recommended sensor set given the crank budget (the pre-study's steer: **baro + CO + IMU-upgrade are
near-free and high-value; MOX-gas and PPG must be event-sampled or dropped**, and a **skin-temp + IMU-activity heat-
strain proxy** may capture most of PPG's safety signal without its 50 mA LED rail); which sensors are always-on vs
alarm-triggered; and the **packet/wire-format changes** each implies (CO ppm, LEL index, baro floor, man-down
state, heat-strain flag) — routing **man-down and IDLH-CO into the PAR/OUT_OF_CONTACT/evacuation states** and **baro
into the position fusion.** *Sources:* BMP390 / BME688 / LSM6DSOX / MAX30101 datasheets; TI LMP91000; SPEC 3SP_CO;
CDC/NIOSH Pocket Guide (CO); NFPA 1982; Smith et al. cardiac-LODD (PMC3710100).

**5.5 — Package closure, thermal go/no-go, wearability, mount conflicts.** **Deliver:**
- **Mass budget:** electronics are tiny (XIAO ~3 g, MPU6050 breakout ~5 g, baro/gas <1 g); the **energy store and
  crank dominate.** Small LiPo ~150–220 Wh/kg (a 1000 mAh cell ~11–18 g) vs supercap ~5–10 Wh/kg (burst buffer
  only). **Commercial hand-crank generators are 425–550 g — too heavy to wear** → a stripped integrated micro-crank
  might hit ~80–150 g but likely still exceeds the whole rest of the node. **Frame the CONOPS decision:** on-body
  continuous crank vs a **detachable / at-the-apparatus recharge** between assignments (the more defensible model;
  ties to **4.6** and NFPA 1584 rehab-sector doctrine).
- **THERMAL GO/NO-GO (adversarial, high priority):** wildland-suppression ambient is **~32.6 ± 8.9 °C with
  excursions to 22–66 °C**, but a **LiPo only accepts charge between 0–45 °C** (thermal-runaway risk >60 °C). A sun/
  fire-exposed body-worn cell can exceed 45 °C — meaning **the crank may physically refuse to recharge it exactly
  when needed**, and attempting to charge a hot cell is dangerous. **Deliver:** does this kill body-worn LiPo, and
  is **thermal-isolated compartment + temperature-gated charger, or a wider-temp chemistry (LiFePO₄) / supercap
  buffer**, mandatory? *[Carballo-Leyenda et al. 2019, Front. Physiol. 10:949 (PMC6688527); cell temp datasheets]*
  > **✔ VERDICT (pass 1 + pass 2, 2026-07-03) — real. `Validated`.** Charge window ~0–45 °C confirmed (Samsung
  > ICR18650-26F §7.5; Panasonic floor +10 °C), narrower than the −20/+60 °C discharge window. Wildland ambient
  > mean **32.6 ± 8.9 °C, peak 78 °C** confirmed **verbatim against the primary source** —
  > **Carballo-Leyenda et al. 2019, Front. Physiol. 10:949** (*not* "Willi 2019" — that mis-cite originated in
  > Code's own workflow and was corrected by Science in pass 2; Willi/Horn/Madrzykowski 2016 and NIST TN 1474 are
  > both *structural*-fire studies and support neither the wildland figure nor this item). Charge-refusal needs a
  > charger with a thermal cutoff, so **design in a temperature-gated charger (NTC, inhibit >45 °C)**; a dumb crank
  > charger would dangerously charge a hot cell instead. See `research-log.md` Pass 2.
- **Locked safety reserve:** if this is an accountability device, NFPA 1982 wants a man-down alarm sustaining
  **≥95 dBA @1 m for ≥1 h** — that energy **cannot be drainable** by discretionary sensor/comms use or by a
  responder who didn't crank, implying a **two-rail energy architecture** with a carved-out floor. *[NFPA 1982 2018]*
- **Enclosure vs openings:** a hand crank is a moving mechanical penetration and gas sensors need a vent to ambient
  — both fight IP ingress sealing and NFPA 1977 flame/no-drip requirements. Are an exposed crank + vents even
  compliant on body-worn wildland gear, or must the crank detach and the gas port use a protected diffusion
  membrane (Gore-vent)? *[NFPA 1977 2022; IEC 60529]*
- **Mount-location conflict matrix (helmet / shoulder / chest / belt):** 2.4 GHz body-shadowing is **10–20 dB** in
  the blocked direction *[Chalermwisutkul et al., Sensors 2018 18(10):3412]*, so antenna clearance wants helmet/
  shoulder; the IMU wants a stable low-vibration torso frame; gas sensors want the collar/shoulder **breathing
  zone (~30 cm from nose/mouth)**; the crank wants hand reach. These point at **different** locations — quantify the
  tradeoff and say whether one node suffices or functions must split across two body-worn units.

---

## Discrepancies you should NOT waste research effort on (they're Code's to implement, not yours to ground)

These are documented-but-absent or buggy. Listed so you don't calibrate a constant for a feature that doesn't
exist. Flag any doctrine that *should* drive them (esp. 3.2), but the implementation is `@code`:

- **OUT_OF_CONTACT watchdog + 5 s PAR resend** — documented in `protocol.md`, **zero implementing code.** PAR is
  sent **once**; OUT_OF_CONTACT is never set. (Ground the *doctrine* in 3.2; implementation is Code's.)
- **MAYDAY delivery is single-shot, unacknowledged** — one lost frame silently loses a life-safety declaration;
  dropped entirely if no uplink is currently known. (Your 4.3/4.5 findings inform the required repeat count.)
- **Team-batch members never expire** — a downed responder's last PAR=OK is re-sent every 1 s forever,
  indistinguishable from live. (Ground the eviction threshold via 3.2; implementation is Code's.)
- **IMU deltas destroyed during uplink outage** — `popDelta()` zeroes the accumulator every cycle even when
  there's no uplink, despite a comment claiming telemetry is "held locally." (Buffering policy is Code's.)
- **Role/team via captive portal + NVS** — documented, but they're **compile-time flags only**; no config page,
  no NVS.
- **Entire ICS-form sync + SBC downlink path is dead code** — `serializeFormForTransport`/`receiveFormChunk`,
  `GatewayBridge::pollFromSbc`, the CTRL case, `refreshForms()` are never called. (The 201/205 "pushed down for
  on-node display" story is unimplemented.)
- **16-bit MAC-hash node IDs** — ~0.7 % collision at 30 nodes, ~7 % at 100; a collision merges two responders
  into one track — the worst accountability failure. ("Negligible" is uncomputed judgment; you can supply the
  birthday-bound math + recommend a collision-detect handshake.)
- Minor: `seq` field never checked for loss; portal PAR handler casts an unvalidated int to ParStatus;
  `docs/positioning.md` referenced but doesn't exist; "four roles" comment (there are three).

---

## Suggested first turn

Don't boil the ocean. **Open with Tier 0.1 and 0.2** — the two model-validity questions — because a "no" on
either reshapes everything below it. Hand those back with citations and a clear verdict, and Code will
independently reproduce the drift-rate / ranging-error numbers before we change a line. Then we'll sequence
Tier 1 by whichever module survives Tier 0.

**Tier 5 (energy autonomy + sensors) can run in parallel** — the user asked for it directly, and it's largely
independent of the positioning-model questions. Its go/no-go is **5.0 (does the budget close?)** and the
**5.5 thermal charge limit** (a LiPo won't accept charge above 45 °C while wildland ambient hits 66 °C) — either
could reshape the hardware as much as 0.1/0.2 reshape the software, so they're worth an early verdict too.
