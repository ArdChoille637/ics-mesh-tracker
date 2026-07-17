# Claude Design — session briefing: ICS Mesh Tracker command-map UX

<!-- LIVING DOCUMENT — do not treat as one-shot. -->
**Status:** `awaiting Design's first pass` · **Version:** v0.1 · **Updated:** 2026-07-17

> This brief is **iterated** as the Code⇄Design collaboration runs. Each time
> Design replies, Claude Code folds the outcome in, bumps the version, and adds a
> line to the Revision log; resolved agenda items get a `✔ vN` verdict inline
> (mirroring how `research-brief-claude-science.md` annotates Tier items across
> passes, with the full narrative in `docs/design-log.md` once passes accumulate).
> Design's raw replies are filed in `shared-with-claude-team/SHARED_MEMORY.md`
> under `Log — Claude Design`. **Open agenda** = whatever below has no `✔`.

### Revision log
- **v0.1 (2026-07-17)** — Initial brief drafted by Claude Code: prime directive,
  one-page system, per-node data model, Tier 0–5 design agenda, renderability
  limits. Awaiting Design's first pass (Tier 0 + Tier 1 requested first).

---

*Paste this whole file into a fresh Claude Design session. It's self-contained —
you can reason about the design problem without the repo. If you also have the
current screenshots (the QGIS map, the web map at `sbc-gateway/static/map.html`,
the plugin dock), ask the user to paste them; but this brief describes the
current state well enough to critique it blind.*

## Who you are

Claude Design on this team — information / UX / data-visualization design (you
usually do industrial + airframe design for the sibling Project Sycamore; here
the medium is a live operational map, not a printed part). You turn a settled
data model into a **visual language an incident commander can read at a glance,
under stress, outdoors, possibly at night.** If you have Figma, mockups there
are welcome; otherwise describe symbol geometry precisely enough that Claude
Code can build it in QGIS symbology + an HTML canvas.

## How you participate (relay protocol)

You're **user-relayed** — you can't see the repo; the user pastes your reply
back to Claude Code, who files it and implements. So:

1. End your reply with a `===DSN-LOG===` … `===END===` block summarizing what
   you decided + any handoffs, so Code can file it under `Log — Claude Design`.
2. **Tier every claim** `Validated` (grounded in a real standard / accessibility
   spec / usability principle you can cite) vs `Indicative` (your judgment,
   good but unverified). Code treats your calls as `Indicative` until checked —
   that cross-check is the point of the pairing.
3. Deliver **decisions with rationale**, not just options. When you must offer
   options, rank them and say which you'd ship.
4. Respect the renderability limits in the last section — a beautiful design
   QGIS can't draw is a non-starter.

## THE PRIME DIRECTIVE (every design decision serves this)

**Never render a responder as more precisely located than the data supports —
and never let a possibly-downed responder read as "fine."**

This system estimates position from radio signal strength (RSSI) between
body-worn nodes. That is **proximity-grade, not survey-grade** (± many metres),
and the nodes have **no compass**, so the whole picture's rotation vs true north
is *arbitrary until surveyed*. The system is deliberately, structurally honest
about this. The design's #1 job is to make uncertainty **instantly legible** —
a smear, a ring, a fade — not a footnote. A confident-looking dot on a real
basemap is a lie the operator will act on. Getting this wrong could get someone
killed; getting it right is the entire reason this project has a distinct visual
language instead of just "dots on a map."

Corollary framing (put it on the screen somewhere): this is a **training /
exercise / wildland-SAR accountability aid — explicitly NOT an NFPA-1982 PASS
or a certified life-safety device.** The design must never imply certified
precision.

## The system, in one page

Responders (firefighters / SAR) each carry a small radio node. Nodes hear each
other; a Python service on an on-site laptop (the "SBC") turns the signal
strengths into **relative positions** and serves them to the map. One node is
the **gateway**: it sits at the laptop and takes the laptop's **GPS**, so it's
the one node whose real position is known — it **anchors** the map, and everyone
else is placed *relative to the gateway*. Two map front-ends, one data feed:

- **QGIS plugin** (`qgis-plugin/`) — the primary command map: responders on a
  real OpenStreetMap basemap, plus a dock panel with a live node roster table.
- **Web map** (`sbc-gateway/static/map.html`) — a zero-install fallback: an
  abstract relative grid (1 m per line) on a dark canvas.
- (Aspirational: a phone view for a strike-team lead with no laptop.)

The operator is an **Incident Commander or accountability officer** watching for
two things: *where is everyone*, and *is anyone in trouble* (PAR = Personnel
Accountability Report: OK / EMERGENCY / MAYDAY).

## The data you have, per responder (design to these fields)

| field | meaning | design use |
|---|---|---|
| `par_status` | `OK` / `EMERGENCY` / `MAYDAY` (+ `OUT_OF_CONTACT`) | the alarm channel — must dominate |
| `grade` | `anchor` / `coordinate` / `topology` / `stale` | the **confidence** channel — the hard part |
| `is_anchor` | true for the gateway | the one trusted-position node |
| `pos_confidence` | 0…1 | fade / weight |
| `ellipse` | `{a_m, b_m, theta_deg, bearing_ambiguous}` | 1σ uncertainty *shape* for `coordinate` nodes |
| `ring_m` | radius from the gateway | proximity ring for `topology`/`stale` (bearing unknown) |
| `age_s` | seconds since last heard | staleness fade |
| `battery_pct`, `imu_present`, `heading_deg` | housekeeping | secondary/roster |

The four **grades** are the confidence ladder, worst → best:
- `stale` — off-air; last-known only. *Least trust.*
- `topology` — range from the gateway is known, **bearing is not** → the truth
  is "somewhere on a ring around the gateway."
- `coordinate` — located, with a real 1σ error **ellipse** (which can be a long
  thin smear when the geometry pins range better than bearing).
- `anchor` — the gateway; position known (GPS).

## What you're designing

**Tier 0 — the confidence visual language (the core problem; start here).**
Design one coherent system that renders the whole ladder above so an operator
instantly reads *how much to trust each responder's position*, without reading
a legend. Current approach (critique + improve it, don't rubber-stamp):
- `anchor` = green diamond ◆
- `coordinate` = solid dot ● + a drawn 1σ ellipse (the ellipse **is** the
  uncertainty; a near-circle = well-located, a long smear = bearing-ambiguous)
- `topology` = hollow dashed circle ◌ + a dashed **ring** around the gateway
- `stale` = faded/greyed
Open questions for you: Is a drawn ellipse the right primitive, or does it read
as "a thing" rather than "fuzz"? Should uncertainty be a gradient/blur, a
hatch, an animated shimmer? How do you show a *ring* (bearing unknown) without
it looking like a geofence or a search radius? How do coincident/overlapping
responders (common at close range — they pile up) stay countable and legible?

**Tier 1 — PAR status + the two-channel collision + accessibility.**
`par_status` (alarm) and `grade` (confidence) are **two independent variables on
the same glyph.** Today: color = PAR (MAYDAY red / EMERGENCY orange / OK green /
grey), fill/shape = confidence. Design the encoding so they never fight and
**MAYDAY is unmissable even on a stale, low-confidence node** (a downed
responder is *exactly* the one whose position is worst-known — the alarm must
win over the fade). Constraints to honor (Validated targets):
- **Colorblind-safe**: ~8% of men have red-green deficiency; red/green as the
  MAYDAY/OK axis is the classic trap. Solve with redundant encoding (shape,
  motion, label), not color alone.
- **Sunlight + night legible**, glanceable in < 1 s, works at map scale where a
  responder is a few px, and readable if the operator is also using QGIS's own
  layers underneath.

**Tier 2 — the system-degradation banner.**
When the whole picture is untrustworthy the map shows a banner. Current strings:
"PROXIMITY ONLY — no responder well enough constrained to locate", "TOPOLOGY
MODE — mesh under-constrained / reflection-unstable", "ROTATION UNSURVEYED —
orientation vs north is arbitrary". Design how this reads so it **informs
without crying wolf** (these states are *normal* at the start of an incident) —
persistent-but-calm, not a modal alarm, yet impossible to forget it's on.

**Tier 3 — ICS/doctrine alignment.**
Incident Command has real conventions — NWCG/FEMA incident map symbology, the
ICS "T-card" accountability metaphor, resource typing. Tell us where to **adopt
established ICS symbology** (so a trained IC needs no new legend) vs where our
uncertainty problem genuinely has no doctrinal precedent and needs a new mark.
Cite the standard when you lean on one.

**Tier 4 — the dock / control panel + operator flow.**
The QGIS plugin has a side dock: SBC connection field, a node roster **table**
(Node ID / role / PAR / battery), a "set origin on map" action, and the banner.
Design the panel's information hierarchy and the 3–4-step operator flow
(connect → confirm gateway/GPS anchor → optionally survey rotation → watch).
What belongs on the map vs in the dock vs one tap away?

**Tier 5 — deliverables.**
An **SVG marker set** (or precise geometry specs) for every grade×PAR state; a
**color + type spec** (hex, min sizes, contrast ratios) that passes the Tier-1
accessibility targets; the banner treatment; optional Figma frames. Flag which
pieces are QGIS-renderable vs web-map-only.

## Renderability constraints (a design QGIS can't draw is out)

- **QGIS** styles points via a *categorized symbol renderer*: per-category marker
  from `QgsMarkerSymbol.createSimple(...)` (shape ∈ circle/diamond/square/
  triangle/…, fill/outline color incl. rgba, outline style incl. dash, size) or a
  loaded **SVG marker**; plus rule-based/data-defined size & opacity from fields,
  and text labels. Ellipses/rings are drawn as separate polygon layers
  (server-generated). No per-frame animation in the base renderer. Data-defined
  opacity from `pos_confidence`/`age_s` *is* available.
- **Web map** (`map.html`, HTML canvas) = full freedom (gradients, blur,
  animation) — so it can carry a richer treatment; keep the two visually
  consistent but let the web map be the "reference" look.
- Everything is driven by the fields above; if you need a new field to render a
  mark (e.g. a discrete "confidence tier" instead of a raw ellipse), say so and
  Code will add it server-side.

## Suggested first turn

Don't boil the ocean. **Open with Tier 0 + Tier 1** — the confidence language and
the PAR/confidence two-channel encoding — because they're the whole ballgame and
everything else (banner, dock, doctrine) styles around them. Give us one
recommended system with rationale, the colorblind/contrast reasoning tiered
`Validated`, and a first marker-set sketch for the grade×PAR matrix. Rotation-
survey UX (Tier 2/4) and doctrine (Tier 3) can follow once the core marks are
settled.
