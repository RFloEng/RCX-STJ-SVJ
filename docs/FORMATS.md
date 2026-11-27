# File formats and export conventions

All numbers are little endian. "16.16" is a signed 32-bit fixed-point value (divide by 65536). The game is
**left-handed**; every exporter negates Z for glTF (right-handed, Y up). Everything below was derived from the files and
from the behaviour of `RAL.EXE`; where something is a guess it says so. The readers are `tools/sqsl.py`, `tools/track.py`,
`tools/ro3.py`, `tools/lwob.py` and the Rust crate `rcx-formats`.

## Folders of the game used here

| Folder | Content |
|---|---|
| `FILES/LEVELS/LEVELnn/` | one stage: `CDAT00C.SQ` (geometry, SQSL-compressed) and `LOCALDAT` (per-stage tables). 48 stages |
| `FILES/A/` | track-side objects `OBJnnnn.RO3`, textures `TXnnnn.PCX`, `TEXTINFO` (UV sets) |
| `FILES/B/` | cars: `OBJ0401…` bodies, `OBJ05xx` interiors (`.RO3` runtime model + `.LWO` LightWave source), textures, `CARSINFO` |
| `FILES/S/` | a small set of special objects |
| `FILES/LOCALE/ENGLISH.TXT` | text tokens (stage and rally names, car names) |
| `FILES/SPEECH/{F,M,CARS,RALLY}` | co-driver and menu speech (WAV) |
| `FILES/D/SND`, `FILES/MAPS`, `FILES/G`, `FILES/M` | sound effects, map screens (`.SQ`), menu graphics |

## SQSL (`*.SQ`)

Container that starts with the bytes `SQSL`; a custom LZ-style compressor. `tools/sqsl.py` is a port of the game's
decompressor (`FUN_0041b9bb`); `rcx-formats::sqsl` is the Rust port. The game also accepts an uncompressed development
copy (`srclvls\levelNN\cdat00c`) in front of the shipped one.

## Stage geometry: `CDAT00C.SQ` (after decompression)

```
dword  0..63   header: 'sout', ntable (71), npolys, nverts, ...   (other words unknown)
dword  64..    ntable x 8 dwords   lookup table (probably a spatial grid for collision — not decoded)
then           npolys x 16 dwords  polygon records
then           nverts x 3 x i32    vertices (x, y = height, z), 16.16
```

The game copies the header to `0x5ccc94`; records live at `[0x5ccc3c]` (64 bytes each) and vertices at `[0x5ccc40]`
(12 bytes each).

### Polygon record (16 dwords, index = dword number)

| dword | meaning |
|---|---|
| `p[0]` | byte 0 = kind (**`0xFF` = special record**: objects, road-line points; below `0x10` = terrain quad: bits 0..2 = texture orientation, bit 3 = has a back side); byte 1 = tile *slot*; bits 16..31 = face flags (`0x80` = not ground, `0x400`/`0x800` = wall bits) |
| `p[1..4]` | vertex indices of the quad in "Z" order: the outline is `p1, p2, p4, p3`; the game's triangles are `(p1, p2, p3)` and `(p4, p3, p2)` |
| `p[8]` bytes 0, 1 | second texture layer: orientation and tile slot (0 = no second layer) |
| bytes `0x22..0x25` | weight of the second layer at each corner, 0..255 |
| `p[5]` | baked shade, 4 bytes (one per corner, LSB first) → exported as vertex colour |
| `p[6]` | special records: object size, `16` = natural (scale p6/16); the road-line chains are the marker slots 0x3d/0x3e |
| `p[2]`,`p[3]`,`p[4]` | special objects: yaw (1/256 turn about +Y), pitch?/roll? (unapplied) |
| `p[7]` | object height offset, 16.16 |
| `p[9]` byte 2 | **`1` = first point of the road line, `2` = checkpoint** |
| face bytes 40..43, 25..27, 32 | **eight pace-note slots** of a road-line point (below) |
| other | runtime scratch (zero in the file) or undecoded |

### Texturing (terrain)

A face has up to **two texture layers** (A: bytes 0, 1; B: bytes `0x20`, `0x21`). Each layer has its own tile slot and
orientation `o = kind & 7`: corner `k` takes TEXTINFO corner word `ORDER[o][k]`, with
`ORDER = 0123, 1032, 2301, 3210, 2031, 3120, 0213, 1302` (RC2000 decomp `view_store.rs`, `course_faces.rs`; checked on
the "MARINE DRIVE" gate of LEVEL21). Only A: A. Only B: B, with the corner weights as its opacity. Both: A, then B over
it, weighted by B's texture alpha x corner weight.

**How B goes over** is chosen by B's tile: TEXTINFO byte `0x24` indexes the table `3, 5, 4, 0` (`0x63d950`; read at
`0x4529fd`); the D3D states of each mode are set in `0x44b7ba`:

| byte `0x24` | mode | D3D blend | result (A = lit first layer) | faces (all level files) |
|---|---|---|---|---|
| 0 | 3 alpha | SRCALPHA / INVSRCALPHA | `A*(1-w) + B*w` | most (road patches, verges, kerbs) |
| 1 | 5 gloss | ZERO / INVSRCCOLOR | `A*(1 - B*w)`: B darkens by its own colour (snow shadows, TX588) | 17098 over A, 1334 alone (36 stages) |
| 2 | 4 additive | SRCALPHA / ONE | `A + B*w` (water, TX440) | 203 over A, 3473 alone (36 stages) |

In mode 3 only, when all four weights are 255 and TEXTINFO byte `0x26` of B's tile is 0, A is not drawn at all
(`0x44b6dd`). Bit 0 of byte `0x26` also puts a face in the see-through draw bucket (`0x454fec`).

**Mapping by position**: TEXTINFO byte `0x25`, signed, greater than 0 (either layer; `0x452ff5`, `0x45322d`; same in the
RC2000 decomp): the corner UVs do not come from the tile but from the corner's position, page fraction =
`(x, z) * 2^((byte & 3) + 2) / 256`, less the whole pages of corner 0. With the value 1 (all grass tiles of TX363/364/377)
the picture repeats every 32 m.

`tools/course_tiles.py` reads the two layers for both exporters. What they write differs:

* **STJ** (`track_to_stj.py`, STJ spec 7.7: a converter does not bake materials): the face keeps the game's own pages.
  Mesh primvars (faceVarying) `st` (A, or B when there is no A), `stj_st_b` (B) and `stj_weight` (B's corner weight,
  0..1); one material `mix_<page A | none>_<page B>_<mode>` per combination, with the mix as a MaterialX network and
  customData `x_rcx {shader, rule, page_a (-1 = none), page_b}`; the mode (`alpha`, `add`, `gloss`, `fade` = no A) is
  the last word of `shader`. With `w = stj_weight * B's alpha` and `aA` = A's alpha, on the stored (sRGB) texels:
  alpha: `alpha = w + aA(1-w)`, `colour = (B w + A aA(1-w)) / alpha`; add: same alpha,
  `colour = (B w + A aA) / alpha`; gloss: `alpha = aA`, `colour = A(1 - B w)`; fade: `alpha = w`, `colour = B`. The face
  is cut out below alpha 0.5. **The UsdPreviewSurface fallback shows one layer only** (A, or B without A): a reader that
  follows neither MaterialX nor `x_rcx` shows hard edges. No viewer has been seen rendering the MaterialX network.
* **GLB** (`track_to_glb.py`; glTF cannot say the mix): each distinct blended tile is baked into an atlas page
  (`blend64_*`, `blend128_*`, `blend256_*`), interpolating per game triangle.

**Not reproduced**: B alone in mode 5 or 4 darkens or lights whatever is behind it, which a material with a cut-out
alpha cannot do; those faces are drawn as B's own picture. None of this has been compared with a screenshot of the
running game.

```
slot = byte 1 (layer A) or byte 0x21 (layer B)
code = LOCALDAT dword[64 + slot]            tile code of the stage (255 entries)
file = FILES/A/TX{code >> 4 :04d}.PCX
UVs  = FILES/A/TEXTINFO record `code` (128 bytes): dwords 0..3 are the corner UVs, lo16 = u, hi16 = v, 16.8 fixed point in
       PIXELS of the page itself (value >> 8 = pixel): fraction = value / 256 / page size. Pages are 128x128 (320 of 605) or
       256x256, so 0x7FFF is the edge of a 128 page; values past it repeat (RC2000 decomp: texture_coordinates).
       Group 0 (code < 16) has no file: the game draws those polygons with no texture, vertex colour only.

Surface per tile (found statically in `RAL.EXE`): the same TEXTINFO record carries physics attributes. `0x439137` copies
them into a 256-slot table (`0x979eb0`, 0x40 bytes per slot; slot byte `+0x10` = TEXTINFO byte `+0x20`, `+0x14` = `+0x10`,
`+0x18` = `+0x14`, `+0x2c..` = bytes `+0x24..0x26`). The collision code (`0x43d3f8`) reads slot `+0x10` as the **surface profile
index** (0..13) into 14 profiles of 48 bytes at `0x6138f8` (a pointer to a 6-value grip table, then floats); a polygon with
flag `0x1000` uses profile 5, and a second slot's profile is blended in by the polygon's vertex weights. LEVEL75 uses
profiles 0, 1, 2, 3, 7, 8. Profile names/classes are not in the data (tarmac-like = 1, grip 120; lowest grip = 9).

```

### What the car collides with

* The wheels and body points look for ground only on faces with kind `< 0x10` and **flag `0x80` clear** (RC2000 decomp
  `physics/probe_ground.rs`, `ground_search.rs`); where that ground ends, the car is stopped. Walls inside it are
  faces whose `0x400` / `0x800` bits meet (`physics/wall_contacts.rs`). Terrain geometry is never tested as geometry;
  placed objects are (`face_contact.rs`, by the object's material kind).
* 99% of the standing faces are flagged `0x80`. For engines that collide with every triangle, `track_to_stj.py` moves to
  a visual-only file (`LEVELnn_cards.usda`) the faces that are flagged `0x80`, stand steeper than 80 degrees, have no
  edge on the border of the ground and are less than 85% solid (the last two limits are the tool's choice): overhanging
  leaves and bushes that were invisible barriers. LEVEL21: see-through terrain triangles within 3 m of the road line at
  car height drop from 626 to 2.

After loading, the game swaps one tile code on some stages (`FUN_0043989a`, which reads the stage number): `0x2f82→0x2fd0` on stages 32/34/36,
`0x2f91→0x2fe0` on 52/54/56, `0x2f72→0x2fd2` on 61/63/65 and `0x2f71→0x2fd1` on 62/64/66 (first match only, in the 255-entry tile list). The exporter does the same (`TILE_SWAP`).

### Placed objects

A special record (low byte `0xFF`) of an object slot (byte 1 below `0x38`) is an object instance: model id = `LOCALDAT dword[320 + slot]`
(`FILES/A/OBJxxxx.RO3`), position = vertex `p[1]` (+ `p[7]` height), yaw = `p[2]` · 360/256 about +Y. Flat signs end up
perpendicular to the road with this convention (checked statistically on 582 samples). `p[6]` is the size (16 = natural): scale = `p[6]` / 16 (the game scales the object matrix by size/16, RC2000 decomp).
Every such record is an object, whatever its `p[6]` or `p[9]`: the RC2000 decompilation draws all of them the same way (`p[9]` low byte = lit by the stage sun, size byte = `p[6]`).
Slots `0x38` and up are **not** placed by either export: the RC2000 decompilation wiki (`Course-files.md`) lists them as markers (replay cameras, lamps, sound spots, and the route's checkpoint chains `0x3D` backwards / `0x3E` forwards, which link through `p[3]`: 9818 of 9821 records in LEVEL61; the 843 `p[6]` = 16 records of object slots never link, 0 of 843). A checkpoint keeps pace-note slots in bytes `0x19..0x1B`, the upper bytes of the `p[6]` dword, so testing the whole dword against 16 is wrong: until 2026-11-27 this placed 24 checkpoints (10 stages, models 201/202) as huge pyramids, plus 3 checkpoints with a size byte of 14 or 21.
`p[3]`, `p[4]` are kept in the node's `extras` but not applied.

#### What the car feels when it touches an object

From the RC2000 decomp (`physics/face_contact.rs`, `physics/face_contacts.rs`, `drive/lit_faces.rs`,
`drive/animate_lit_faces.rs`, `scene/draw_described_object.rs`); not re-checked in RAL.EXE. Each model has a 128-byte
record (`FILES/A/OBJSINFO`, models 0..399; `FILES/B/CARSINFO`, 400..599) whose byte `0x1C` is the contact type:

| type | models in these (looked at in renders) | tested | effect | STJ `collision` |
|---|---|---|---|---|
| 1 | trees (all 21) | the object's own point | solid; wobbles ±4° for 24 steps | `trunk` |
| 10 | poles, a lamp post | own point | solid | `trunk` |
| 5, 6, 9 | mixed (photographers, signs, ...; not all looked at) | own point and mesh points | solid (9 also wobbles) | `mesh` |
| 2 | bushes and small shrubs | own point | passes through; leans to 67.5° and stays | `none` |
| 3, 8 | flags and Mobil 1 boards; a barrier | own point (8 also mesh) | passes through; turns, falls flat and stays | `none` |
| 4 | cone, bale, barrel, bench | own point | passes through; slides away along the ground | `none` |
| 7 | low scrub | own point | passes through; sound | `none` |
| 0 | a light flare, two others | | nothing | `none` |

A placement is never tried when bit 0 of its record's byte `0x25` is set or its slot is ≥ `0x38` and not `0x3A`; when
bits 1..2 are not 0 it is tried in one direction only (value − 1; direction 0 is the forward line, slot `0x3E`).
`track_to_stj.py` (`object_contacts.py`) writes `scenery.prototypes` (`obj0015`, with `_off` / `_rev` for switched-off
placements) and one USD `instances` set per prototype, and puts `stj:collision` only on the instances of `mesh`
prototypes. No `physics` is written: STJ requires a mass for moving props and none was found; the reaction is in the
prototype's `x_rcx`. The trunk radius (0.25 m) is an estimate, the game tests a point. The classes come from looking at
the renders; the mixed types are `other`.

### Road line, start, finish, checkpoints, pace notes

The road line is a chain of special records in slots `0x3e` and `0x3d`, ordered by the link in `p[3]` starting from the record
whose `p[9]` byte 2 is 1. **The two chains are the same polyline walked in opposite directions** (checked on stages 75 and 44:
every point of one is a point of the other, the second is the first reversed), not a left and a right edge, so the data holds
no road width. `0x3e` is the forward direction, `0x3d` the reverse one, each with its own marker bytes. Stage lengths computed
from it are 3–41 km (one game unit taken as one metre) and agree between both to a few metres. Levels 01–06 have a 3-record
line (15–28 m) and 11–16 none. From the code (`0x45a718` "init checkpoints", `0x4417fd` "init pacenotes"):

* byte `0x26` (`p[9] >> 16`): `1` start of the line, `2` checkpoint (stage 75: 5 checkpoints, stages 35 and 44: 15).
* **Pace notes** (`RAL.EXE` `0x441941..0x441b98`; same logic as the RC2000 decompilation `race/pace_notes.rs`; port in
  `tools/pace_notes.py`). Each point has eight code slots at face bytes 40, 41, 42, 43, 25, 26, 27, 32 (table `0x614c94`); a place
  is point × 8 + slot. A note runs from a code in 1..99 other than 98 to the next 98 or 100; its calls are read from the start
  until a 0 or 100. `99` = corner worked out from the road: length = line distance over the note, turn = heading (`atan2(dx, dz)`)
  at its end minus before its start, grade 1..5 = |turn|·60 / max(length, 60 m) cut at 0xCCC/0x1998/0x2664/0x3330 (5 = tightest),
  +8 for 120 m or longer, +16 for a right-hander. Notes start two points after the first check. A group is said at the end of the
  previous note: a distance word for the gap (table `0x614cb8`, 30..400 m), then the note and the ones within 30 m of it.
* **A code is a cue, not a speech file number.** 113 pointers at `0x5124f8` give each cue a list of sound records (-1 ends it);
  cues 0..83 are identical to the RC2000 `CueLists`; record `(r - 0xED) // 2` = phrase of the exe's speech file list. 1..8 =
  left 1..5, square, hairpin, tight hairpin; 9..13 long left 1..5; 17..24 and 25..29 the same to the right; 30 long,
  31 straight, 32..41 distances and "into", 47..73 suffixes and features (52 over, 55 past, 65 crest, 68 junction),
  80..83 comments; 84 or more ends the group ("invalid pace data"). Check: 165 of the 167 hand-placed corner cues on a bend of
  30° or more turn the same way as the road.
* **On-screen arrow.** Each called cue below 32 is also looked up in the table at `0x614bfc` (code `0x4416e4`; identical to the
  RC2000 decompilation's listed events): 17..24 → symbols 0..7 (right 1..5, square, hairpin, tight hairpin), 1..8 → 8..15 (the same
  to the left), long 9..13 / 25..29 → the arrows of 1..5; the other cues have none. The HUD draws the symbol with rectangle
  `0x933b10 + 16·symbol` of `FILES/B/TX1800.PCX`; 8..15 have a negative width (mirrored). Arrows go from green (1) to red (tight
  hairpin). All 4332 converted notes that have an arrow match it in side and grade; the 577 without one have no direction.
* Slots `0x3c` and `0x38` hold markers whose purpose is unknown.
* Replay cameras: marker polygons with slot 0x38 / 0x3C (position = first vertex; orientation and range not decoded). Lamps are slot 0x3B, sound spots 0x3F (`p[2]` = sound id). Layout cross-checked with the RC2000 decompilation (sibling game, same engine; github.com/Diatosta/rc2k_decomp, GPL-3.0 — only format facts were used, no code).end markers.

## `LOCALDAT` (8300 bytes per stage)

Read by `0x445427` into a `0x206c`-byte buffer at `0x608c34`. Known parts: dwords `64..` tile codes (above), dwords
`320..` object model ids per slot. The rest is unexamined.

## RO3 — runtime model (`OBJxxxx.RO3`)

```
header 44 bytes: u32 version (1), nverts, npolys, nnormals, 7 x 0
verts   nverts x (i32 x, y, z)                16.16
polys   npolys x { u32 n, u32 flags, u32 f2, u32 id, n x { u32 vertex, i32 u, i32 v, u32 w } }
normals nnormals x (f32 x, y, z)
```

`flags & 0xFFFF` = texture page `TXnnnn.PCX`; `u`,`v` are **pixel** coordinates (16.16) in that page; `w` indexes the
normals table in car models. The high half of `flags` and `f2` are render flags only partly understood
(`f2 = 0x700100` marks inner/headliner surfaces). Car models: `OBJ0401…` bodies and `OBJ05xx` interiors, loaded by the object
loader `0x425b1e`.

## LWO — LightWave source models (cars)

`FILES/B/OBJxxxx.LWO` are plain LWOB files (`FORM … LWOB`, chunks `SRFS`, `PNTS`, `POLS`, `SURF`). They contain the same
polygons in the same order as the RO3 (checked: 100 of 131 identical vertices and polygons, 28 more with identical polygon
lists), so **the surface names give the part names**: `body`, `Bonnet`, `BumperFront/Rear`, `Wing`, `lights`, `windows`,
`underneath`, `RollCage`, `Dash`, `interior`, `Seat`, `StearingWheel`, `driver`, `COdriver`, `wheelLeftFront`,
`wheelRightFront`, `wheelLeftRear`, `wheelRightRear`, `wheelSpare`, `mirrorLeft/Right`, `wiperLeft/Right/New`.

## PCX textures

8-bit PCX pages, 256x256 typically. `(255, 0, 255)` and a near-magenta fringe are the colour key (transparent). `TX1826`
is the shared car-glass sheet: its pure blue half is the glass tint, the rest is a crack overlay (not exported). The
exporter bleeds opaque colours into transparent pixels so viewers that filter or ignore alpha show no pink boxes.

## Cars

Per-car arrays in the exe (34 entries, index `k`): body model `BODY[k]`, alternate model `ALT[k]`, interior `INTERIOR[k]`,
texture base `1000 + 8k`. The code stores only the index, so names are reconstructed in `tools/car_names.py` from livery
decals, the class text in `ENGLISH.TXT` (`A5..A8`) and the AI performance ranking table at `0x605160`; each entry records
its evidence. Unresolved: `k=15/20` (the two Escorts), `k=30/31/33`.

`FILES/B/CARSINFO` (100 x 256 bytes, 79 non-empty) is **not decoded**: values look like fixed-point numbers with a zero low
byte, they are not wheel positions.

## Stage and rally names

`ENGLISH.TXT` has `tokSS_Stage000…035` (36 stages, six per rally in the order of `tokRT_ChampOne…Six`: Wales, Pirelli /
Kielder, RSAC Scottish, Seat Jim Clark Memorial, Stena Line Ulster, Sony Manx). The mapping **stage directory → rally** was
inferred from stage lengths, snow, and the descriptions (tens digit 4 → Wales, 6 → Kielder, 3 → Scottish, 7 → Jim Clark,
5 → Ulster, 2 → Manx); levels `0x`/`1x` are the small arcade levels. It is not read from code.

## GLB conventions of the exporters

* Z negated; one scene; textures embedded as PNG; NEAREST sampling (the sources are pixel art).
* Cars/objects: one mesh per LWO surface, material per (part, texture page); back-face culling on, with polygon winding made
  consistent per connected shell (`orient_polys`).
* Tracks: terrain is one node per texture page (`TXnnnn`, double sided, vertex colour = baked shade); placed objects are
  nodes `OBJnnnn` (geometry shared per model); `ROADLINE_L/R` are line strips; `START_*`, `FINISH_*`, `CHECKPOINT_*`,
  `ZONE_OPEN_*`, `ZONE_CLOSE_*`, `MARK98_*`, `NOTEnn_*` are empty nodes on the road line (suffix `_L|_R_<index>`), with the
  raw bytes in `extras`.
* Holes in stages 03/13 and ragged edges in 05/15 are real gaps in the data; no water plane is generated there.
* Sea: no sea geometry exists; it is painted on the lower half of the sky dome (pages 373..375, LEVEL21 and LEVEL22).
  `track_to_stj.py` gives Port Soderick (LEVEL21) a `water.bodies` sea with its own surface file: the coast is the
  fading foot of the Marine Drive cliff (page 520), the side is the dome's open water; **its level (1 m under the lowest
  vertex) and depth are estimates**. Ballagyr (LEVEL22) has no coast in its geometry and gets none.
* `tools/name_exports.py` copies everything to `export/glb_named/{tracks,cars,objects_A,objects_S}` with readable names and
  writes `NAMES.csv` (source → named file).

* Sky: scene object 1 is the model in LOCALDAT dword 321 (OBJ0101..0127: a 3 m dome with 7 sky textures). The game draws it around the camera with no far clip, turned by sky_turn (dword 416) and raised by sky_height (dword 409, `<<8`), then the sun (RC2000 decomp `race::draw_sky`; GPL-3.0, facts only). `track_to_stj.py` places it as a fixed scaled dome at `/track/sky/dome`.

## Car spec records (RAL.EXE file offset 0x1c5a8c, 34 x 0xB0 bytes, VA 0x5c788c)
Loaded by `0x421219`. Ints: 6 drive (1 = 4WD), 7 gear count (clamped 3..7), 16..18 unknown, 19 mass (kg), 25..28 speed at the rev limit in mph (first gear low/high setting, top gear low/high setting), 29 rev limit (rpm), 30 peak torque (the game stores it as the drive torque figure `x 8500 / rev limit`; read as lb ft), floats 20 wheelbase (m), 31..39 torque shape at i/8 of the rev limit (`0x43c952` interpolates it). The game's acceleration test (`0x421634`, 5000 steps of 0.01 s) is `v += (torque x figure / gear ratio / 10000 - (5 v^2 / 10000 + 0.75)) x 0.01` with `v x 2.25` in mph. Cross-checked with the sibling game's decompilation (Diatosta/rc2k_decomp, GPL-3.0, facts only): the 0-60 mph times it gives (4.3 s WRC, 4.5 s Megane) and, with the real mass, powers within about 25 percent of peak torque x rpm.
