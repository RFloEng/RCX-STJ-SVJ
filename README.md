# RCX to STJ / SVJ

Converters that turn the data of **Rally Championship Xtreme** (Magnetic Fields / Europress, 1999, European release,
`RAL.EXE` "Rally 5.29") into two open simulator formats:

* **STJ** - [Standard Track JSON](https://github.com/RFloEng/STJ-Standard-Track-Json): tracks as OpenUSD geometry plus a JSON document.
* **SVJ** - Standard Vehicle JSON (spec 0.112): cars with geometry, powertrain and chassis data.

The 48 stages and 32 cars of the game convert and pass the respective validators.

## What you get

**Per stage** (`export/stj/LEVELnn/`): `LEVELnn.usda` (Z up, metres; terrain as visual + collision mesh split per texture page and surface,
instanced placed objects, UsdPreviewSurface materials, PNG textures), `LEVELnn.stj.json` and the STJ document inside the USD.

| STJ block | Content |
|---|---|
| `surfaces` | named surface profiles (asphalt, gravel, mud, snow, ice, ...), estimated grip, per-face bindings |
| reference line, `timing` | road line, start, finish and split points; gates as `STJ__timing__*` prims in the USD |
| `race.pacenotes` | co-driver calls (corners, general calls, modifiers) from the game's speech table |
| `cameras` | replay camera sets with their active range along the route |
| `environment` | lamps, sky dome, raw light / fog values |
| `audio.emitters` | sound spots |
| collision | terrain and objects marked `stj:collision` |

**Per car** (`export/svj/<car>/`): `.svj.json` (ISO 8855 frame, wheel parts, mass, wheelbase, drive layout, gears, rev limit,
torque curve in N.m), a textured `.glb` and a `.usdz`.

What is exact, estimated or unknown: [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md). Formats of the game files: [`docs/FORMATS.md`](docs/FORMATS.md).

## Usage

You need your own copy of the game. Put its files (`FILES/`, `RAL.EXE`) in the repository root.

```bash
pip install pillow numpy usd-core

# stages -> export/stj/LEVELnn/
python tools/track_to_stj.py export/stj FILES/LEVELS/LEVEL*/CDAT00C.SQ

# cars -> export/svj/<car>/   (--svj-tools <SVJ repo>/tools adds the USDZ through its gltf2usd)
python tools/car_to_svj.py
```

Validate with the `tools/validate.py` of the [STJ](https://github.com/RFloEng/STJ-Standard-Track-Json) and SVJ repositories.
Details of every script: [`docs/TOOLS.md`](docs/TOOLS.md).

## Legal notice

**No game data and no game code is in this repository**: no executable, textures, models, sounds, tracks or converted files.
The tools read your own copy of the game, written from observation of the data files and the program's behaviour,
for interoperability and preservation. Rally Championship is the property of its owners; this project is not affiliated with or
endorsed by them. The code in this repository is released under the [MIT licence](LICENSE); it does not cover the game or its data.

## Credits

* Facts about the engine's data (replay cameras, surface table, car physics layout) were cross-checked against the
  [rc2k_decomp](https://github.com/Diatosta/rc2k_decomp) decompilation of Rally Championship 2000 (GPL-3.0). Only facts were used; no code was copied.
* Format specifications: STJ and SVJ by the same author.
