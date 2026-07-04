# ICS Mesh Tracker — research collaboration log

Coordination record for the Science ⇄ Code research loop on this project (the analogue of the Claude Team
`SHARED_MEMORY.md`, scoped to this repo). **Science** researches from first principles and hands back cited
verdicts; the user relays them; **Code** independently reproduces the math, adversarially verifies the citations
against primary sources, tiers each number `Validated`/`Indicative`, files it here, and sequences the code impact.
The research agenda lives in `research-brief-claude-science.md`.

Tiering rule: a number Science produces is `Indicative` to Code until Code reproduces it AND confirms the cited
source actually says it. Cross-checking across the two passes is the point.

---

## Pass 1 — Tier 0 + Tier 5 go/no-go verdicts (2026-07-03)

Science answered the four highest-stakes go/no-go questions (0.1, 0.2, 5.0, 5.5). Code reproduced every
calculation (all matched to ~3 sig figs) and ran a 4-agent adversarial source-verification pass. **All four
engineering verdicts survive. Corrections are to citations and one input value — none overturn a verdict.**

### 0.1 — Torso raw double-integration + ZUPT → **NO, rewrite as step-detection PDR.** `Validated` ✔
- **Reproduced:** the cubic gravity-leak term x(t)=g·b_gyro·t³/6 with a 0.3 °/s residual gyro bias gives 8.6 m
  @10 s and 1.86 km @60 s — matches Science exactly. **Robust:** even an optimistic 0.1 °/s residual still
  yields 616 m @60 s, so "diverges" does not depend on the exact bias.
- **Source check (CONFIRMED):** the cubic form is the correct *extension* of Woodman 2007 (UCAM-CL-TR-696 §6.2.3)
  — Woodman's own printed example is the *quadratic* fixed-tilt case; the cubic arises because a constant gyro
  bias makes the tilt grow linearly (tilt = b·t), which is exactly the un-ZUPT'd torso case. The 0.3 °/s is a
  **temperature-drift residual** (MPU6050 ZRO ±20 °/s over 125 °C ⇒ ~0.16 °/s/°C ⇒ ~0.3 °/s from ~2 °C
  self-heat), *not* the in-run instability floor (measured ~0.0012–0.0024 °/s, ~150× smaller — Allan-variance
  study PMC7506677). Foxlin 2005's "0.3 %" ZUPT figure confirmed verbatim.
- **Correction to carry:** don't co-cite Woodman for the "0.3–1 %" ZUPT number (that's Foxlin 2005 alone);
  present x=g·b·t³/6 as a derived extension of Woodman's mechanism, not a Woodman quotation.
- **Code impact (gated):** replace `firmware/src/imu/dead_reckoning.h`'s accel double-integration with
  step-detection + stride-length + heading PDR; TELEMETRY payload changes from (dx,dy) displacement → (step
  count, stride length, heading delta); SBC `predict()` advances by stride along heading. **Gate:** PDR needs a
  bounded heading or the stride vector rotates — so this rewrite is blocked on brief item **1.4** (magnetometer-
  free heading), which Science has not yet done. Do 1.4 before the rewrite.

### 0.2 — Body-worn 2.4 GHz RSSI → **proximity/topology, not survey coordinates.** `Validated` ✔ (inputs corrected)
- **Reproduced:** σ_d/d = (ln10/10)(σ_dB/n) = 0.682 at σ=8 dB, n=2.7; 10 dB → 2.35× distance error. Both match.
- **Source check (PLAUSIBLE → verdict holds, two corrections):**
  - **Citation error:** the 10–20 dB torso-blockage number is real and on-topic (Sensors 2018, 18(10):3412 —
    ~10 dB one body, ~15 dB two, ~21 dB FDTD) but the author is **Łukasz Januszkiewicz (Lodz Univ. of Tech.)**,
    **not "Chalermwisutkul et al."** Fix the author.
  - **Inflated input:** IEEE 802.15.6 **CM3 body-surface σ at 2.4 GHz is 3.8 dB (hospital room) / 6.9 dB
    (anechoic)** per Yazdandoost & Sayrafian (IEEE P802.15-08-0780-09-0006, §8.2.6), **not the claimed 6–10 dB.**
    So shadowing-only fractional range error is ~**±32 % (σ=3.8) to ±59 % (σ=6.9)**, not ±68 % (σ=8 was above the
    model's range). The verdict is **unchanged and arguably strengthened**: once the 10–20 dB torso blockage is
    added on top, links past ~10–15 m collapse to connectivity-only regardless.
  - Patwari 2003/2005 CRLB formula + attribution + the 2.35× are correct.
- **Code impact:** `sbc-gateway/fusion.py` — formulate range residuals in **log-distance (dB) space** (shadowing
  error is multiplicative in metres), gate long/weak links to connectivity edges, degrade the map to
  link-topology when the graph isn't rigid (ties to HMI item 4.7).

### 5.0 — Hand-crank energy budget → **closes only if the always-on AP dies + radios duty-cycle.** `Validated` ✔
- **Reproduced:** always-on 100–150 mA → 1200–1800 mAh → 44–200 crank-min (@2–6 W delivered); duty-cycled
  3–8 mA → 36–96 mAh → 1.3–11 crank-min. All match.
- **Source check (PLAUSIBLE → core CONFIRMED, charger sub-claim corrected):**
  - **CONFIRMED verbatim:** ESP-IDF states Wi-Fi power-save "works in station-only mode" and "ESP32 AP does not
    support all of the power-saving feature" — an always-on softAP genuinely cannot sleep. The ~88 mA RX floor
    is ESP32-S3 datasheet v2.2 Table 5-7. This is the load-bearing claim and it holds.
  - **Correction:** the base **XIAO ESP32-S3 charges at ~50 mA** (100 mA is the ESP32-S3 **Plus** variant), and
    the charger IC is likely **SGM40567, not ETA4054** (ETA4054 unconfirmed for this board). This **strengthens**
    the "board is the recharge bottleneck" point (~0.19 W not 0.37 W into the cell).
- **Open item to bench-measure:** per-beacon energy of an ESP-NOW TX from light-sleep (fast wake ~1–2 mA·s vs a
  ~320 ms cold boot that would balloon the average ~10×). This is the one number that could still move the
  verdict, so it needs a PPK2/Joulescope reading on real hardware.
- **Code impact (requirement):** AP off-by-default / woken-on-demand; duty-cycle the radios to a stated average.

### 5.5 — Thermal charge limit → **real; a hot body-worn cell can refuse charge.** `Validated` ✔ (one cite dropped)
- **Source check (PLAUSIBLE → core CONFIRMED, one misattribution):**
  - **CONFIRMED:** Li-ion charge window ~0–45 °C (Samsung ICR18650-26F datasheet §7.5 characterizes charge at
    0/25/45 °C; Panasonic floor is +10 °C, so "roughly 0–45" is fair), narrower than the −20/+60 °C discharge
    window. Wildland-suppression ambient mean **32.6 ± 8.9 °C, peak 78.0 °C** confirmed (PMC6688527) — the peak is
    actually *higher* than the 66 °C we quoted, so the problem is worse, not better. *[Author corrected in Pass 2 —
    see below: this is **Carballo-Leyenda et al. 2019**, not "Willi"; Code's pass-1 agent repeated the workflow's
    wrong author.]*
  - **Correction:** **drop the NIST TN 1474 citation** for the wildland ambient number — TN 1474 is a
    *structural-fire* floor-assembly thermal study and does not report it.
  - **Caveat that reinforces the design:** charge-refusal requires the charger's BMS to actually implement a
    temperature cutoff. A cheap crank charger with no thermistor would attempt to charge a hot cell anyway (worse
    — degradation/runaway risk). So the implication is firm: **design in a temperature-gated charger (NTC on the
    cell, inhibit >45 °C)**, plus thermal-isolated compartment / wider-temp chemistry (LiFePO₄) / supercap buffer.

---

### Handoffs out of pass 1

- **@science (next):**
  - **1.4 — magnetometer-free heading** is now on the critical path: it *gates* the 0.1 PDR rewrite (PDR is
    useless with unbounded yaw drift). Prioritize it.
  - Then Tier 1 path-loss constants (1.1/1.2) and the remaining Tier 5 open numbers (5.1 wearable-crank delivered
    watts, 5.3 duty-cycle schedule, 5.4 sensor set).
  - Housekeeping for your records: fix the three citations above (Januszkiewicz author; σ = 3.8–6.9 dB not 6–10;
    no TN 1474 for the wildland temp; Foxlin-only for ZUPT 0.3–1 %).
- **@code (next):**
  - Correct those citations + the σ input in `research-brief-claude-science.md` (done this pass).
  - Hold the `dead_reckoning.h` → PDR rewrite until 1.4 lands; then rewrite + change the TELEMETRY wire format.

---

## Pass 2 — reconciliation + one counter-correction (2026-07-03)

Science's second handback folded in Code's pass-1 corrections and pushed back on one. Net: **Tier 0 + Tier 5
go/no-go is now converged and locked** (pending only bench numbers + the gated code work). No verdict moved.

- **0.1 / 0.2 / 5.0 — converged.** Science accepted all pass-1 corrections verbatim: the 0.1 rewrite-gated-on-1.4
  note; 0.2's σ = 3.8–6.9 dB and the Januszkiewicz author fix (fractional range now stated as ±32–59 %); 5.0's
  ~50 mA base-board charge current (100 mA = Plus) and SGM40567-not-ETA4054. Nothing further to verify — these
  were already Code-verified in pass 1.
- **5.5 — Science counter-corrected Code, and Science is right (I re-verified against the primary source).**
  Code's pass-1 note (inherited from Code's own grounding workflow) attributed the wildland-temperature figure to
  "Willi et al. 2019." Science flagged that as wrong. **Code independently checked the PMC page for PMC6688527:**
  the paper is **Carballo-Leyenda, Villa, López-Satué & Rodríguez-Marroyo (2019), "Characterizing Wildland
  Firefighters' Thermal Environment During Live-Fire Suppression," *Front. Physiol.* 10:949**, and it states
  verbatim *"…suppression environment temperature (24.6 ± 8.9°C vs. 32.6 ± 8.9°C), which reached a maximum value
  of 78.0 ± 8.9°C."* So: mean 32.6 °C, **peak 78 °C — confirmed at the primary source.** "Willi, Horn &
  Madrzykowski (2016)" and "NIST TN 1474" are both *structural*-fire studies and support neither the wildland
  figure nor this item. **The temperature numbers were always right; only the author label was wrong, and it's
  now fixed** (brief 5.5 + this log). `Validated`.
  - *Process note:* this is the three-pass cross-check paying off — the wrong author originated in Code's grounding
    workflow, survived Code's own pass-1 adversarial verify (which caught the TN 1474 error but not this one), and
    was caught by Science as the independent third pass. Exactly the failure mode the loop exists to catch.

### Status after Pass 2

- **Tier 0 + Tier 5 go/no-go: CLOSED/locked.** All four verdicts `Validated`, citations corrected, no open
  disagreements. Two empirical numbers still want a **bench measurement** before final design commit: the
  duty-cycled ESP-NOW-from-light-sleep average current (5.0) and the wearable-crank delivered watts (5.1) — these
  are hardware-measurement items, not research disputes.
- **Critical path unchanged:** `@science` → **item 1.4 (magnetometer-free bounded heading)**, which gates the
  `dead_reckoning.h` → step-PDR rewrite; then Tier 1 path-loss (1.1/1.2). `@code` holds the PDR rewrite +
  TELEMETRY wire-format change until 1.4 lands.

---

## Pass 3 — item 1.4 (magnetometer-free heading) (2026-07-03)

Science delivered the gating item. Code reproduced every number (exact) and verified the citations. **Verdict
`Validated`; 0.1 is now un-gated by 1.4 — but a new co-requisite (1.5) surfaced.**

- **Recommendation (CONFIRMED):** bound heading by **re-zeroing gyro bias during detected stillness (NMNI — "No
  Motion No Integration," the gyro analog of ZUPT)**; do **not** add a magnetometer as primary (hard/soft-iron
  from the responder's own SCBA/steel/tools moves rigidly with the sensor and can't be calibrated out).
- **Math reproduced (exact):** gyro-only drift = residual-bias × time → 18 °/min @0.3 °/s (3°/10 s, 180°/600 s);
  6 °/min @0.1 °/s; ~1200 °/min uncalibrated. At 1.3 m/s: ~24 m cross-track in 60 s, ~90° error by 5 min. ARW
  (0.016°/10 s) and bias-instability terms reproduce too.
- **Citations checked (WebFetch/WebSearch):**
  - **NMNI — Nguyen, *Sensors & Actuators A: Physical* 2021 (S0924424721001540):** REAL, exactly on-topic (NMNI
    drift elimination). This is the load-bearing cite for Mechanism A. *Minor:* ADS bibcode lists first-author
    initial "H" — verify the "Nguyen" author label (third author-slip of the collaboration; doesn't affect the
    method).
  - **ForestBack — arXiv 2606.14421:** REAL, correct ID, on-topic (PDR heading). Used only for a qualitative
    point; fine.
  - **NeurIT — arXiv 2404.08939:** REAL, but **mischaracterized** — the paper *strategically uses* magnetometers
    (body-frame differentiation), it does not "drop them for ferromagnetic distortion." Swap it for a source that
    actually documents magnetometer abandonment, or soften the claim.
  - **Patents US 9,677,889 / US 10,139,233:** specific numbers **not verified**; the concept (gyro-consistency-
    gated magnetic heading) is real — canonical patent is **US 8,531,180 B2**. Use that or drop the numbers.
- **One number to tighten (strengthens the verdict):** Science's Allan floor 0.02 °/s → ~1.2 °/min (~15×). Code's
  pass-1-verified measured MPU6050 bias instability is **~0.002 °/s → ~0.12 °/min (~150×)**. Either way NMNI is
  the right bound; the achievable floor is likely an order of magnitude better than stated. `Indicative` until a
  bench Allan-variance of the actual units.

### Status after Pass 3 — the gate moved, it didn't open

**0.1 is no longer blocked on heading — but the PDR rewrite is *not* fully unblocked.** Science's own 1.4
handback notes Mechanism A "reuses the stillness detector already needed for item 1.5," and step-detection PDR
needs the **≥50–100 Hz timer-driven / FIFO sampling** that item **1.5** is meant to settle. So **1.5 is a genuine
co-requisite** of the rewrite, not a nice-to-have. Doing the rewrite before 1.5 means picking interim stillness
thresholds + sample-rate architecture and redoing them later.

- **Recommended sequence:** `@science` → **item 1.5** (ROC-optimized stillness detector + sample-rate/FIFO
  architecture) next; then `@code` does **one combined rewrite** of `firmware/src/imu/dead_reckoning.h` (step
  detection + Weinberg/Kim stride + NMNI heading), the TELEMETRY wire-format change (`packet.h` + `protocol.py`
  mirror + `test_protocol.py`), and the SBC `fusion.py` `predict()` (advance by stride-along-heading). One clean
  pass instead of two.
- **Alternative if the user wants motion now:** `@code` can do the rewrite immediately with sensible interim
  defaults (moving-variance stillness detector, timer-driven 100 Hz read) flagged for 1.5 calibration.
- **@science housekeeping:** fix the NeurIT characterization + the patent numbers; verify the Nguyen author label.

---

## Pass 4 — item 1.5 (stillness detector + sample-rate) (2026-07-03)

The last gate. Code ran a 3-agent adversarial verification — **all CONFIRMED, the cleanest pass yet: every
citation real and correctly attributed, no corrections.** `Validated`.

- **Detector (CONFIRMED):** replace the dual hard-threshold gate with the **SHOE GLRT statistic** (Skog, Händel,
  Nilsson, Rantakokko 2010, IEEE T-BME 57(11):2657); test statistic transcribed correctly (cross-checked against
  the SHOE eq. in Wagstaff & Kelly PyShoe, arXiv:1910.00529). The strong sub-claim survived an adversarial
  refutation attempt: Skog 2010 does conclude *"the gyroscopes hold the most reliable information for
  zero-velocity detection,"* with SHOE (accel+gyro) beating gyro-only ARED only *marginally* — so keeping just a
  windowed **angular-rate-energy** term is justified. **Caveat (scoping):** that result is for **level forward
  gait**; for crawling/stairs/running the accel term matters more, so the gyro-only budget choice is condition-
  dependent — revisit if bench false-alarms appear.
- **Secondary citations (CONFIRMED, incl. the flagged year):** Wahlström, Skog, Gustafsson, Markham & Trigoni,
  "Zero-Velocity Detection — A Bayesian Approach to Adaptive Thresholding," **IEEE Sensors Letters 3(6), 2019**
  (DOI 10.1109/LSENS.2019.2917055) — supports adaptive thresholding. Kumar, N. Singh, D.K. Singh & Goel (IIT
  Kanpur), FIG Congress 2022 — real, hosted on fig.net, supports "fixed thresholds can't handle motion/gait/user
  variability" near-verbatim; note it's **grey literature** (non-peer-reviewed proceedings).
- **Sample rate (CONFIRMED):** MPU6050 gyro ODR→8 kHz / accel→1 kHz verbatim from InvenSense PS/RM, so 100 Hz is
  trivially reachable — the constraint is purely firmware scheduling. Gait content to ~10–20 Hz; ≥50–100 Hz is
  the documented ZUPT/PDR norm (the specific "100 rec / 50 floor" numbers are Science's engineering synthesis,
  not a verbatim Skog prescription — fine). The loop-coupling-invalidates-the-statistic argument is sound.

### Status after Pass 4 — GATE OPEN. Rewrite DONE (Python-verified; firmware not compile-tested).

Both 1.4 and 1.5 landed + verified → the rewrite was executed this session:
- **New `firmware/src/imu/step_pdr.h`** (replaces `dead_reckoning.h`, deleted): step detection on low-passed
  accel magnitude + Weinberg stride, gyro-yaw heading with NMNI bias re-zero during detected stillness, windowed
  angular-rate-energy stillness statistic, heading-confidence decay.
- **`mpu6050_driver.h`**: added `enableFifo100Hz()` + `drainFifo()` (fixed-rate FIFO, decoupled from the loop).
- **`main.cpp`**: drains the FIFO each loop and feeds `pdr.update()` per sample; `buildOwnTelemetry()` emits the
  new fields; boot does calibrate → enableFifo.
- **Wire format** changed (dx,dy,dz,dθ) → (step_count, stride_mm, heading_mrad, heading_conf) across `packet.h`
  (now `static_assert(sizeof==50)`), `protocol.py`, `test_protocol.py`; `fusion.py` `predict()` advances by
  stride-along-heading and `snapshot()` exposes heading_deg + heading_conf.
- **Verified here:** all 6 wire round-trip tests pass at the new 50 B payload / 52 B member / 4-per-frame (batch-
  split invariants intact); fusion predict places a 10-step-east-then-10-north walker at (7.5, 7.5) with the
  anchor pinned; all Python compiles; snapshot stays JSON-serializable. **NOT verified:** the ESP32 firmware is
  not compile-tested here (no PlatformIO toolchain) — needs a `pio run` on real hardware.
- **Bench-tuning deferred to real hardware (flagged in `step_pdr.h`):** Weinberg K (0.45), stillness γ
  (25 deg²/s²), step-detection accel thresholds, and a bench Allan-variance to confirm the ~0.002 °/s re-zero
  floor. Validate with a walk-a-known-course test.
- **Still open (0.2 code impact, NOT this rewrite):** render the map as topology/proximity + formulate RSSI
  residuals in dB space + degrade to link-graph when non-rigid. The README/protocol.md "±5-15 m" caveat still
  overstates; leave until that fusion change lands.

---

## Pass 5 — items 1.1 + 1.2 (RF path-loss + body shadowing) (2026-07-03)

Science delivered the path-loss presets. Code reproduced all arithmetic (exact) and ran a 3-agent adversarial
citation check. **The design recommendation is `Validated` and IMPLEMENTED — but the cross-check REFUTED the
single most important citation, the one Science labelled "Validated."**

- **Recommendation (CONFIRMED, implemented):** a single global path-loss exponent is indefensible — a wrong n is
  a systematic **bias**, not noise (same loss → ~100 m @n=2.0 vs ~4.8 m @n=5.85, a 20.7× spread; reproduced).
  Use **per-environment presets {n, σ}** selected at incident start, σ carried so the map can size honest error
  bars. **Done in `fusion.py`:** `EnvPreset` + `ENV_PRESETS` {WILDLAND/STRUCTURAL/INDUSTRIAL}, `set_environment()`,
  `fractional_range_sigma()`, `environment_summary()`; `rssi_to_distance_m()` now takes the active preset.
- **STRUCTURAL / INDUSTRIAL — `Validated` numbers, author fixed:** office n≈4.5/σ≈8.1 and hydro-plant
  n≈5.2–6.5/σ≈3.6–4.3 are from a real campaign — **IEEE doc 8409563, Pereira, Romero, Fernandes & de Sousa,
  2018 IEEE I2MTC.** The cited author **"Cabral" is WRONG** (that's a different paper); fixed to Pereira et al.
  Secondary WARP campaign (n=4/σ=6.4, IEEE 5676625) also real.
- **WILDLAND — citation REFUTED, downgraded to `indicative`:** the headline "ITU-R P.833-4, mixed forest, n=2.7,
  σ=8.7 dB" does **not** survive. P.833 ("Attenuation in vegetation") is **not a log-distance model** — it has
  **no exponent and no log-normal σ**. The 8.7 dB *is* in P.833 but it's the scatter of the *excess-vegetation-
  loss* fit for one Mulhouse woodland dataset, **mislabelled** as shadowing σ. **n=2.7 is nowhere in P.833.**
  "105–2200 MHz" (it's 900–2200) and "mixed forest" (it's "woodland near Mulhouse") were **fabricated**, and the
  λ/4-monopole detail was an elevated-Tx→ground-Rx geometry, not a peer mesh link. The **magnitudes** (n≈1.9–3,
  σ≈6–10 dB) are plausible per the near-ground forest literature, so WILDLAND is kept as a preset but marked
  `indicative` with the ITU cite dropped — **bench-calibrate before trusting it.** This is the most consequential
  cross-check catch of the collaboration: the wildland preset is the project's *primary* use case, so the number
  that would be trusted most was the least sourced.
- **Arithmetic note (Code):** the body-shadow multiplier Science stated as "2.3–4.6× for 10–20 dB" is 2.35×
  (10 dB) to **5.5× (20 dB)** at n=2.7 — 4.6× is ~18 dB. Corrected in the brief; effect is slightly *worse* than
  stated. Rappaport table (in-building LOS 1.6–1.8, shadowed urban 3–5) confirmed (Table 2.2, 2nd ed).

### Handoffs out of Pass 5

- **@code (done this pass):** `fusion.py` env presets implemented + Python-verified (WILDLAND frac-σ=0.742;
  same RSSI → 30.3/7.7/4.8 m across presets; wire tests still 6/6).
- **@code (follow-ups, NOT done):** (1) per-unit + per-mounting `TX_POWER_AT_1M` offset (item 4.3) upstream of
  the solve; (2) orientation-averaged RSSI window instead of latest-sample (item 1.7); (3) wire σ into the solve
  as log-space measurement noise + render confidence rings / topology fallback (item 0.2). These are the
  remaining RSSI-half positioning changes.
- **@science:** fix your records — author is **Pereira et al.** (not Cabral); **drop the ITU-R P.833 cite** for
  the wildland n/σ and, if you want WILDLAND promoted from `indicative`, supply a real near-ground 2.4 GHz
  forest-RSSI *measurement* paper (Meng/Lee, Joshi, or a near-ground WSN study) with an actual log-distance n and
  σ. Open science items remaining: 1.3 (MPU6050 Allan variance — partially used already), 1.6, 1.7, plus Tier 2
  (fusion filter / flip-ambiguity) and the 0.4 radio-choice / prior-art sanity check.

---

## Pass 6 — revised wildland path-loss (Science's re-cite of 1.1) (2026-07-03)

Science re-issued 1.1 incorporating Pass 5's refutation: it withdrew the ITU-R P.833 wildland entry and proposed
near-ground values **n≈1.8–2.5** (lower than 2.7), **honestly self-flagging** the specific figures as unconfirmed
search snippets pending Code verification. Code reproduced the arithmetic and ran a 2-agent citation check.
**Outcome: Science's instinct (the old number was mis-sourced) was right, but the proposed direction (lower n)
is wrong for a single-slope preset — and the new citation is also a scenario mismatch.** Net: WILDLAND updated
to a *better-grounded* value, still `indicative`.

- **Wang 2012 — real paper, wrong scenario + wrong numbers (PLAUSIBLE):** it exists (Wang, Song, Kong & Zhang,
  "Near-Ground Path Loss… at 2.4 GHz") but is **IJDSN/SAGE, not IEEE** (DOI 10.1155/2012/969712), and it measured
  **open plaza / sidewalk / grassland LOS — no forest, no vegetation, no NLOS.** Using it as a *wildland*
  reference is a scenario over-reach. The specific figures Science quoted (1.86–2.48, "vegetation-NLOS 1.78",
  "obstructed 2.0–2.5") are **not in it** — 1.78 is cross-contaminated from a different (cassava-farm) paper.
  Science's own "unconfirmed snippet" caveat is **vindicated**; don't treat those digits as sourced.
- **The load-bearing correction (both agents):** near-ground 2.4 GHz is **two-slope** — n≈2 short-range (pre
  first-Fresnel-zone breakpoint, ~50–110 m for ~1.3 m antennas), rising to **n≈3.5–4 past it**. So a single **low**
  n≈2.0 would **under-predict loss and inflate distances** beyond the breakpoint. The physically-faithful answer
  is a two-slope model; the honest **single-slope planning value is n≈3.0, σ≈6–8 dB** (per near-ground vegetation
  campaigns — Olasupo 2016 IEEE TAP; Klaina 2018 Sensors — not Wang). The original 2.7 was a *reasonable single-
  slope average* that merely had a bogus citation; it should go slightly **up**, not down.
- **Code action (done):** `fusion.py` WILDLAND preset **2.7/8.7 → n=3.0, σ=7.0**, still `indicative`, re-noted to
  the near-ground vegetation literature + the two-slope caveat (Wang explicitly *not* used as the wildland
  source). Effect: fractional range error ±74% → **±54%**; a −80 dBm RSSI now resolves to 21.5 m (was 30.3 m) —
  more conservative. Python-verified; wire tests still 6/6. Shipped as repo **v0.3.1**.
- **@science:** your wildland re-cite is **not adopted as-is** — (a) Wang 2012 is open-grassland LOS, not a
  wildland/forest source; (b) lowering to n≈2.0 under-predicts loss past the Fresnel breakpoint. If you want a
  measured wildland preset (to promote it off `indicative`), cite a **frequency- and scenario-matched** near-
  ground vegetation campaign with an explicit n **and** σ — Olasupo 2016 (IEEE TAP, natural grass 2.4 GHz),
  Klaina 2018 (Sensors), or Alsayyari 2018 — ideally as a **two-slope {n1, n2, breakpoint, σ}** rather than one
  exponent.

---

## Pass 7 — convergence + Code audits its own citations (2026-07-04)

Science re-issued 1.1 a third time — this one **fully converges** with Pass 6: it adopted n≈3.0/σ≈7, explicitly
reversed its "lower n" framing ("the opposite of my earlier framing"), and withdrew the Wang 2012 / 1.78 numbers.
No new claims. **But Pass 6 had put the Olasupo 2016 / Klaina 2018 citations into the shipped code on the
recommendation of a verify agent that couldn't open the PDFs — so Code verified its own citations** (same
standard we hold Science to). 2-agent check:

- **Olasupo 2016 — correctly attributed, on-target (PLAUSIBLE).** Real: Olasupo, Otero, Olasupo & Kostanic,
  "Empirical Path Loss Models for WSN Deployments in Short and Tall Natural Grass Environments," **IEEE TAP 64(9),
  2016** (DOI 10.1109/TAP.2016.2583507). Genuinely a near-ground 2.4 GHz over-grass measurement campaign — *not*
  an ITU-P.833-style wrong-paper error. Its grass exponents (~2.9–4) support n≈3.0. σ tables paywalled/unread.
- **Klaina 2018 — correctly attributed, on-target, but does NOT support σ (PLAUSIBLE).** Real: Klaina, Vázquez
  Alejos, Aghzout & Falcone, "Narrowband Characterization of Near-Ground Radio Channel… at 5G-IoT Bands,"
  **Sensors 18(8):2428, 2018.** Near-ground 2.4 GHz over soil/short-grass/tall-grass, three-slope, Fresnel-
  motivated; its obstructed slopes bracket 3.0 (tall-grass slope literally 3.0), so n≈3.0 is defensible. **KEY:
  it reports NO lognormal shadowing σ** — it's a deterministic fit. So the preset's **σ≈7 dB is not sourced from
  it.**
- **Code action (done, pushed):** the n≈3.0 stays (well-supported); the **σ≈7 dB is now labelled in-code + in
  CHANGELOG as an ASSUMPTION** from the general near-ground shadowing band (~4–8 dB), *not* attributed to those
  papers — Klaina has no σ, Olasupo's σ unread. σ is the least-grounded value; bench-measure it first. Values
  unchanged (comment/provenance-only fix, no new tag).

### Path-loss thread scorecard (Passes 5–7)

Four citation issues surfaced in this one sub-topic: ITU-R P.833 **refuted**, "Cabral" → **Pereira** (author),
Wang 2012 **wrong scenario**, and σ≈7 **not sourced** from the papers it was attributed to. The *values* have
converged to a defensible `indicative` preset (WILDLAND n=3.0/σ=7, STRUCTURAL 4.5/8.1, INDUSTRIAL 5.85/4.0), but
the sourcing needed heavy correction throughout — **the honest bottom line is that the wildland {n, σ} is
bench-calibration territory, not a literature-settled fact.** Every wrong attribution was caught by an
independent pass, including Code's own.

---

## Pass 8 — primary sources read; wildland σ grounded from measurement (2026-07-04)

The user supplied the actual full-text PDFs the earlier agents couldn't reach (paywalled). Code read them and
finally set the wildland `{n, σ}` from **measured** data rather than assumption. Key source:

- **Schneider et al. 2026, "Explaining Seasonal 5G Path Loss in a Vineyard," Future Internet 18(5):237** — an LNS
  fit `PL = PL(d0) + 10α·log10(d/d0) + Xσ` over a **vegetation-density gradient** at **3.75 GHz, RX @1.5 m
  (= responder height)**. Table 10 (read directly):
  | foliage | α (=n) | σ (dB) |
  |---|---|---|
  | April (bare, NDVI 0.19) | 2.27 | 7.21 |
  | May (growing) | 3.28 | 8.21 |
  | June (dense canopy, NDVI 0.80) | 4.23 | 8.97 |
  This is the **first measured vegetation shadowing σ** in the whole thread — retiring the "σ is an assumption"
  caveat. σ runs **7.2–9.0 dB**, rising with foliage density; n runs **2.3 → 4.2**.
- **Corroboration** (from the same document set): Olasupo 2016 (IEEE TAP, 2.4 GHz natural grass, n≈2.9–4),
  Klaina 2018 (Sensors, 2.4 GHz near-ground, obstructed slopes bracket 3), Boonlom et al. 2026 (Sensors, 923 MHz
  LoRa — **forest n=3.22**, LOS 2.31, ~25 dB vegetation excess loss), Barrios-Ulloa et al. 2022 (Sensors, review
  of WSN propagation in vegetated environments — vegetated models carry high error).
- **Convergent grounded picture:** vegetation n ≈ 2.3 (light) → 3.3 (moderate) → 4.2 (dense canopy); σ ≈ 7–9 dB
  rising with density. The single-slope MODERATE point is **n≈3.0, σ≈8**.
- **Code action (done, v0.3.2):** WILDLAND **σ 7.0 → 8.0** (n=3.0 kept); provenance rewritten to cite the measured
  campaigns (σ no longer "assumption"), with the density gradient documented in-code so an operator/dev can pick
  light/moderate/dense. Fractional range error ±54% → **±61%** (measured veg σ is *higher* than the earlier
  assumption — more honest bars). Python-verified; wire tests still 6/6.
- **Still `indicative`, honestly:** the σ source is a **3.75 GHz vineyard** (not a 2.4 GHz forest), and it's a
  single-slope fit over 0.2–104 m. So bench-calibrate on the actual XIAO boards near the ground, and a
  **density-parameterized or two-slope {n1, n2, breakpoint, σ}** wildland model remains the eventual upgrade.

**Thread resolution:** after 8 passes, the path-loss numbers are now grounded in *read* primary sources
(measured vegetation LNS for σ; four corroborating campaigns for n), with every earlier mis-citation corrected.
The preset is still labelled `indicative` because the exact-match (2.4 GHz forest, on-hardware) measurement is
the bench test — but it is no longer resting on any unread or mis-attributed citation.

---

## Pass 9 — Science converges; items 1.1/1.2 CLOSED (2026-07-04)

Science's fourth revision of 1.1 is a **pure reconciliation** — it adopts the Pass 8 state verbatim (n=3.0,
σ=8, the Schneider vineyard density table, the corroborating sources, the density/two-slope upgrade note) and
introduces **no new claims**. Nothing to verify; nothing to change (v0.3.2 already reflects it). **Items 1.1 +
1.2 are settled by agreement.** WILDLAND n=3.0/σ=8.0 (`indicative`, measurement-backed σ), STRUCTURAL 4.5/8.1
and INDUSTRIAL 5.85/4.0 (`measured`) — all selectable via `set_environment()`.

### Remaining `@code` (the RSSI-half work 1.1/1.2 hands off, none blocked on more research)

1. **Per-unit + per-mounting `TX_POWER_AT_1M` offset (4.3)** — mechanism can be built now (a per-node dB offset
   applied upstream of `rssi_to_distance_m`); the *values* need an on-hardware 1 m calibration per board+mount.
2. **Orientation-averaged RSSI window (1.7)** — replace `_rssi_edges` latest-sample-wins with a short per-edge
   window. **Open design sub-question:** which estimator best recovers the *unshadowed* (LoS) distance — a
   median/mean pulls toward the middle of the 10–20 dB body-shadow swing, whereas the **max** (strongest RSSI in
   the window) is closest to the LoS value. Worth settling (possibly `@science`) before coding, since it changes
   what the window computes.
3. **σ into the solver + confidence rings / topology fallback (0.2)** — the larger fusion redesign; the presets
   now carry the σ it needs (`fractional_range_sigma()`).

These form one coherent "RSSI honesty" fusion pass; best designed together rather than piecemeal.

---

## Pass 12 — item 0.2 fusion redesign: design → implement → review (2026-07-04, v0.5.0)

The big fusion change that consumes items 1.1/1.7/0.2. Run as a proper multi-phase effort under ultracode:
a **design judge-panel** (3 architects with different priors — statistical-rigor / pragmatic / safety — →
synthesized spec), inline **implementation**, then an **adversarial review** (3 lenses → verify → fix).

- **Design panel earned its keep** by surfacing three non-obvious pitfalls before any code: the **pinv-zero-
  tangential trap** (reading bearing variance from the Jacobian gives ~0 for range-only geometry — a confident
  dot where bearing is *least* known), the **anchor soft-pin self-contradiction** (old code zeroed the anchor
  then added penalty rows on the zeroed values), and the **D_FLOOR NaN-poison** (two coincident nodes → log10(0)).
  Locked design: whitened dB-space residuals + hard-eliminated anchor + `soft_l1`; **closed-form polar
  covariance** (radial from preset σ+edge-count, tangential from GDOP `σ_t~σ_r/sin(sep)`, never the Jacobian);
  Procrustes flip detect/repair/align; per-node coordinate/topology gating that fails safe.
- **Implemented** in `fusion.py` (full rewrite of the solve/confidence/gating path) + snapshot/env fields +
  `static/map.html` rendering (ellipses, topology rings, grade coloring, TOPOLOGY-MODE banner) + server broadcast.
- **Adversarial review caught 5 REAL bugs, all fixed + regression-tested:**
  1. **CRITICAL** — an **off-air node lingered as a confident coordinate dot** (the staleness gate lived inside
     the per-frame grader, unreachable once the node left the edge set; the ~8 s edge-freshness < 15 s stale
     threshold meant it *never* aged out). This is the exact false-pinpoint the redesign exists to forbid — and
     my own geometry tests missed it. Fixed with an `_age_out` sweep over ALL nodes every cycle.
  2. **HIGH** — re-anchor didn't zero the new anchor's x/y → team lead drawn off-origin. Fixed in `set_anchor`.
  3. **HIGH** — `n_indep` counted anti-parallel (collinear) edges as 2 independent → a collinear node graded
     coordinate with an unconstrained cross-line axis. Fixed: cluster by **line direction (mod π)**.
  4. **HIGH** — the flip guard's collinearity check used two arbitrary nodes and **skipped all alignment** on
     that branch, blending the solver's arbitrary rotation and **shrinking range-from-anchor** (a false
     "closer/safer" reading). Fixed: whole-cloud singular-value chirality check; **always** apply the proper
     rotation, gate only the reflection decision.
  5. **LOW** — `pos_confidence` used a bearing proxy that disagreed with the ellipse. Fixed to use the same GDOP
     term. Also **tuned** the tangential model from the panel's mean-resultant to best-pair `sin(sep)` after a
     test showed corner nodes were over-graded topology (a real GDOP correction).
- **Verified:** `test_fusion.py` — 9 tests incl. all 5 review regressions (off-air→stale, unobserved→topology,
  re-anchor-zeroes, collinear→topology, flip preserves range, single-edge tangential smear, rigid-K4 recovers
  distances to 0.30 m + coordinate-grade) — plus wire tests 6/6, map data-contract + JS syntax. **Not verified:**
  the map render live (preview server wouldn't come up here); firmware unaffected (SBC-only change).
- **Honest state:** all gating thresholds are `indicative`/bench-tunable. At the current ~0.5 Hz per-link RSSI
  (Pass 11), most nodes will legitimately grade **topology** on real hardware until the team moves/turns enough
  to trilaterate — that is correct-by-design (the honest default), not a regression.

### Remaining after Pass 12

- `@code`: per-unit/per-mounting `TX_POWER_AT_1M` offset mechanism (4.3) is the last RSSI-half item; the
  on-node windowed-RSSI-reporting firmware follow-up (1.7b) to exploit the already-captured all-frame RSSI.
- **Broader project:** flash/compile the firmware on real hardware; bench-calibrate every `indicative` constant
  (PDR stride/stillness, path-loss presets, TX offsets, the 0.2 gating thresholds); build the phone app.
- `@science`: the straight-line-advance yaw-coverage falsification (1.7b); Tier 2 (fusion filter / flip
  ambiguity theory) and 0.4 (radio-choice / prior-art) remain open if the collaboration continues.

---

## Pass 10 — item 1.7 (RSSI filter/estimator/freshness): verified + implemented (2026-07-04)

Science answered Code's Pass-9 estimator question and delivered the full 1.7 treatment. Code reproduced the
load-bearing simulation, caught a cross-cutting architecture issue, and implemented the correct parts (v0.4.0).

- **Estimator (verified) — NOT raw MAX.** Code's MAX instinct was "half right, half dangerous." Science
  simulated two coexisting processes — one-sided body shadow (0–20 dB, slow) + symmetric fast fading (±4 dB,
  fast) — and found: mean/median biased low (distance over-estimate), **raw MAX biased HIGH (+7.8 dB → distance
  under-estimate → a downed responder looks closer/safer, the dangerous direction)**, a high percentile
  (75–90th) near-unbiased. **Code independently reproduced it** (different model params → different digits, but
  the *ranking and signs held*: MAX dangerously optimistic, mean/median pessimistic, a high percentile near
  zero). Recommendation adopted: **median pre-filter (reject impulsive multipath) → ~75th percentile**; exact
  percentile is model/bench-dependent (Science 75, Code ~90) so it's tunable.
- **Cross-cutting catch (Code):** the two-stage estimator assumes a fast (ms) sampling stage, but at the **~2 s
  beacon cadence** a node gets ~1 RSSI/neighbor/2 s — so a node-side median-of-5 spans ~10 s (orientation scale,
  injecting the −3 dB bias it was meant to avoid), and the SBC has too few samples for a real percentile in a
  4 s window. **1.7's estimator is sample-rate-limited and couples to the Tier-5 beacon-rate/power decision.**
- **Also verified (reproduced):** per-sample 6 dB swing → 1.58× distance error at n=3.0; speed→freshness (1.4 m/s
  → 2.1 s for a 3 m floor); window tension (45°/s → 4 s to span 180°; 1.4 m/s × 4 s = 5.6 m translation blur).
- **Implemented (v0.4.0, `fusion.py`, Python-verified):** (1) windowed high-percentile estimator (median-pre-
  filtered, default 75th) replacing latest-sample-wins; (2) speed-adaptive freshness (~2 s moving → ~8 s still,
  from PDR step-rate); (3) bidirectional edge averaging (per-direction windows, averaged when both exist).
- **NOT implemented (flagged @science):** the node-side median-of-N (would inject the −3 dB bias at 2 s cadence);
  the motion-gated window-length switch (leans on the same beacon-rate question). These wait on the beacon-rate
  decision.

**Handoff:** `@science` — the beacon rate is now the pivotal open variable for 1.7 (and it ties to Tier-5 power):
what RSSI sample rate does a robust orientation percentile actually need, and can the node measure RSSI on all
received packets (not just 2 s beacons) to get it without a power hit? `@code` — remaining RSSI-half:
per-unit/per-mounting `TX_POWER_AT_1M` offset mechanism (4.3), and σ-into-solver + confidence rings / topology
fallback (0.2, the larger fusion redesign).

---

## Pass 11 — item 1.7b (RSSI sample rate for the percentile) (2026-07-04)

Science answered Code's Pass-10 beacon-cadence catch with a quantitative rate analysis. Code reproduced the
arithmetic and checked the firmware. **This pass is analysis + a CONOPS resolution, not a discrete feature — no
standalone code shipped; its actions couple to 0.2 and a firmware follow-up.**

- **Rate requirement (reproduced exactly):** a robust orientation percentile needs the **max** of two rates —
  (1) an **orientation-coverage floor ~2.5 Hz** (10 looks across 180° at ~45°/s yaw), and (2) an **order-statistic
  tail** N·(1−p) ≳ 5 → **N≥20/window for the 75th**, N≥50 for the 90th. With motion-gated windows: **moving 2 s
  → ~10 Hz (75th) / ~25 Hz (90th); still 8 s → ~2.5 Hz** (coverage-limited). The 0.5 Hz beacon cadence is short
  by 5× (still) to 50× (moving-90th) — **Code's starvation diagnosis is quantitatively confirmed.** *(Minor:
  Science's "10–50×" undersells the still end, which is 5×; the moving end 20–50× is right.)*
- **Key reframe (correct):** RSSI rate is **per-LINK**, not aggregate-mesh — the A→B percentile is fed only when
  A transmits and B overhears, so a busy mesh doesn't help a quiet link.
- **Passive harvest is free — and already half-done.** ESP32 exposes `rx_ctrl.rssi` on *every* received frame;
  **the firmware already captures it on all frames** (`espnow_mesh.cpp` `staticOnRecvRssi` → `neighbor_rssi_`,
  line 196/210), not just beacons. **The gap is downstream:** it's stored latest-wins and only the latest is
  reported at 1 Hz telemetry, so the harvest is thrown away before it reaches the SBC. Exploiting it needs
  **on-node windowed RSSI aggregation + reporting** (a firmware + wire-format change, and traffic-dependent —
  helps only on chatty links). *Not built this pass.*
- **Resolution (elegant, sound):** the demand and supply curves run opposite, so the **motion-gate already in
  v0.4.0 also resolves the rate/power conflict** — STILL needs only ~2.5 Hz (passive-harvest-achievable, and
  zero translation blur = best window) so trust the RSSI coordinate; MOVING needs 10–25 Hz that a quiet link
  can't supply, but PDR carries position then and coordinate RSSI is least critical (0.2 topology). **Do NOT
  raise beacon cadence or go promiscuous** — both spend the Tier-5 budget the energy analysis said isn't there.
- **Honest bottom line:** at the current 1 Hz telemetry / ~0.5 Hz per-link fresh RSSI, the coordinate percentile
  is **fundamentally under-sampled** → **topology-grade is the honest default**, coordinates only when still with
  enough turning. This *is* the 0.2 conclusion, now quantified.

### Handoff

- `@code` (the pieces 1.7b hands off, both coupling to bigger work — not forced this pass):
  1. **0.2 redesign** should consume 1.7b: gate each edge on **achieved sample count + yaw-coverage** (a link
     with 50 samples all at one heading is still not a valid percentile — coverage, not count, is the real gate),
     and render coordinate-grade vs topology-grade per edge (confidence rings / link-graph fallback).
  2. **Firmware follow-up:** on-node windowed RSSI reporting to actually exploit the already-captured all-frame
     RSSI (traffic-dependent; needs a wire-format tweak).
  3. Still open: per-unit/per-mounting `TX_POWER_AT_1M` offset (4.3).
- `@science`: the **straight-line-advance falsification** is the sharp open risk — a responder who moves without
  turning gets no orientation diversity at *any* rate, so the coverage gate (gyro yaw-span over the window), not
  sample count, must catch it. Worth a bench trace (per-link RSSI + yaw, straight advance vs turning search).
  - When convenient, bench-measure the duty-cycled ESP-NOW-from-light-sleep average (the one open 5.0 number).
