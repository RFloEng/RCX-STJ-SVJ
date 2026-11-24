# Open questions and approximations

What is decoded exactly, what is estimated, and what is still unknown in the STJ / SVJ exports.

## Tracks (STJ)

* **Replay cameras**: positions and the active range along the route follow the game's rule (nearest camera of its set).
  The kind is exported as `tracking`; orientation and zoom are not decoded.
* **Object collision**: every placed object is marked `stj:collision`; the game's per-object collision flags are not decoded.
* **Sky dome**: model, turn and height come from `LOCALDAT`; the scale and vertical placement are estimated.
  The sun is placed separately by the game and was not found in any level.
* **Fog / light values** (`LOCALDAT` dwords 384..392): units unknown; kept raw in `x_rcx.environment`, not mapped to STJ lighting.
* **Timing gates**: start, finish and split gates are written as `STJ__timing__*` prims; the width (12 m) is a placeholder and
  the split positions are not checked against the game's own split timing.
* **Surfaces**: names and grip come from the surface profile table (13 entries, formula in `docs/FORMATS.md`); grip is an
  estimate (`mf_scaling`). Per-corner blending is not done.
* **Pace notes**: code to phrase mapping comes from the exe's speech table order; code 99 is a corner measured from the route.
* **Stage -> rally mapping** is inferred, not read from code.
* Deliberately left empty: the holes of LEVEL03 / LEVEL13 and the jagged edges of LEVEL05 / LEVEL15 (not in the game's data).

## Cars (SVJ)

* **From the exe, exact**: drive layout, number of gears, mass, wheelbase, rev limit, peak torque and the torque curve shape
  (stored as fractions of the rev limit).
* **Estimates**: centre of gravity, track width, gear ratios (setting 0, evenly spaced) and the torque curve in N.m
  (shape x peak torque x 1.3558).
* **Not decoded**: differentials, suspension, tyres, `B/CARSINFO`, damage model.
* **Car names**: `k = 15, 20` (the two Escorts), `k = 30, 31, 33` unresolved; the two Escorts get a " (car k)" suffix.
