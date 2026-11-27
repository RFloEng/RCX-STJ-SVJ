# Tools (`tools/`)

Run from the repository root, with your copy of the game in it. Python 3.10+, `pip install pillow numpy usd-core`.

| Script | Role |
|---|---|
| `track_to_stj.py` | **main**: stages -> STJ (OpenUSD + `.stj.json`) |
| `car_to_svj.py` | **main**: cars -> SVJ 0.112 (+ GLB, USDZ) |
| `course_tiles.py` | terrain texturing: the game's two texture layers and blend modes (and the baked atlas of the GLB) |
| `two_layer_material.py` | STJ material of a two-layer face: original pages, MaterialX mix, one-layer fallback (nothing baked) |
| `object_contacts.py` | which placed objects are solid, and their STJ `scenery` prototypes |
| `track.py`, `sqsl.py` | read a stage `CDAT00C.SQ` (SQSL decompressor, polygon records) |
| `ro3.py`, `lwob.py`, `lwo2obj.py` | read RO3 runtime models and LightWave LWOB sources |
| `car_names.py` | car tables recovered from the exe (model per car, names) |
| `course_ref.py` | course analysis (road line, headings, distances) |
| `ro3_to_glb.py`, `track_to_glb.py` | helpers (textures, GLB writer, model lookup) used by both converters |

Note on the car USDZ: Blender's USD importer turns cut-out materials into blended ones (`Set Material Blend`, on by default),
which sorts faces wrongly (parts look missing, wheels black). Import with that option off, or use the GLB.
