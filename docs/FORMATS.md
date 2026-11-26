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
| `p[0]` | flags. **Low byte `0xFF` = special record** (objects, road-line points); otherwise a terrain quad. Bits 8..15 = *slot* |
| `p[1..4]` | vertex indices of the quad in "Z" order: the outline is `p1, p2, p4, p3` |
| `p[5]` | baked shade, 4 bytes (one per corner, LSB first) → exported as vertex colour |
| `p[6]` | special records: object size, `16` = natural (scale p6/16); the road-line chains are the marker slots 0x3d/0x3e |
| `p[2]`,`p[3]`,`p[4]` | special objects: yaw (1/256 turn about +Y), pitch?/roll? (unapplied) |
| `p[7]` | object height offset, 16.16 |
| `p[9]` byte 2 | **`1` = first point of the road line, `2` = checkpoint** |
| face bytes 40..43, 25..27, 32 | **eight pace-note slots** of a road-line point (below) |
| other | runtime scratch (zero in the file) or undecoded |

### Texturing (terrain)

```
slot = (flags >> 8) & 0xFF
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

After loading, the game swaps one tile code on some stages (`FUN_0043989a`, which reads the stage number): `0x2f82→0x2fd0` on stages 32/34/36,
`0x2f91→0x2fe0` on 52/54/56, `0x2f72→0x2fd2` on 61/63/65 and `0x2f71→0x2fd1` on 62/64/66 (first match only, in the 255-entry tile list). The exporter does the same (`TILE_SWAP`).

### Placed objects

A special record (low byte `0xFF`, `p[6] != 16`) is an object instance: model id = `LOCALDAT dword[320 + slot]`
(`FILES/A/OBJxxxx.RO3`), position = vertex `p[1]` (+ `p[7]` height), yaw = `p[2]` · 360/256 about +Y. Flat signs end up
perpendicular to the road with this convention (checked statistically on 582 samples). `p[6]` is the size (16 = natural): scale = `p[6]` / 16 (the game scales the object matrix by size/16, RC2000 decomp).
Every special record of an object slot (< 0x38) is an object, whatever its `p[6]` or `p[9]`: the RC2000 decompilation draws all kind-0xFF records the same way (`p[9]` low byte = lit by the stage sun, size byte = `p[6]`). The chains are the marker slots 0x3d/0x3e, which link through `p[3]` (9818 of 9821 records in LEVEL61); the 843 `p[6]` = 16 records of object slots never link (0 of 843).
`p[3]`, `p[4]` are kept in the node's `extras` but not applied.

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
* Holes in stages 03/13 and ragged edges in 05/15 are real gaps in the data; no water plane is generated.
* `tools/name_exports.py` copies everything to `export/glb_named/{tracks,cars,objects_A,objects_S}` with readable names and
  writes `NAMES.csv` (source → named file).

* Sky: scene object 1 is the model in LOCALDAT dword 321 (OBJ0101..0127: a 3 m dome with 7 sky textures). The game draws it around the camera with no far clip, turned by sky_turn (dword 416) and raised by sky_height (dword 409, `<<8`), then the sun (RC2000 decomp `race::draw_sky`; GPL-3.0, facts only). `track_to_stj.py` places it as a fixed scaled dome at `/track/sky/dome`.

## Car spec records (RAL.EXE file offset 0x1c5a8c, 34 x 0xB0 bytes, VA 0x5c788c)
Loaded by `0x421219`. Ints: 6 drive (1 = 4WD), 7 gear count (clamped 3..7), 16..18 unknown, 19 mass (kg), 25..28 speed at the rev limit in mph (first gear low/high setting, top gear low/high setting), 29 rev limit (rpm), 30 peak torque (the game stores it as the drive torque figure `x 8500 / rev limit`; read as lb ft), floats 20 wheelbase (m), 31..39 torque shape at i/8 of the rev limit (`0x43c952` interpolates it). The game's acceleration test (`0x421634`, 5000 steps of 0.01 s) is `v += (torque x figure / gear ratio / 10000 - (5 v^2 / 10000 + 0.75)) x 0.01` with `v x 2.25` in mph. Cross-checked with the sibling game's decompilation (Diatosta/rc2k_decomp, GPL-3.0, facts only): the 0-60 mph times it gives (4.3 s WRC, 4.5 s Megane) and, with the real mass, powers within about 25 percent of peak torque x rpm.
