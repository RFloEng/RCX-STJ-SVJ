"""Convert an RCX track (LEVELS/LEVELxx/CDAT00C.SQ + LOCALDAT) to STJ (Standard Track JSON, v0.3) in OpenUSD.
https://github.com/RFloEng/STJ-Standard-Track-Json

usage: python track_to_stj.py <out_dir> <CDAT00C.SQ> [...]       (needs `pip install usd-core`)

Output, per level:  <out_dir>/LEVELnn/LEVELnn.usda      root layer: geometry + the STJ document in customLayerData["stj"]
                    <out_dir>/LEVELnn/textures/*.png    PCX pages converted to PNG (magenta colour key -> alpha)
The geometry is the same as track_to_glb.py writes (same triangles, UVs, baked shade, object placement); only the
container, the frame and the document are new.  Read track_to_glb.py first for how the game data is decoded.

Frame (spec 4.3):  the game is left-handed with Y up; STJ is right-handed with Z up.
    STJ = (x, z, y) of the game      (a mirror of the game's frame, so triangle winding is the glTF one)
  A rotation about the game's +Y by `a` in track_to_glb.py's glTF frame is a rotation about STJ +Z by `a`.
  Units: one game unit is taken as one metre (LEVEL75 comes out 3.1 km, LEVEL26 22 km: stage-like lengths).

What is written:
  * geometry   /track/ground/TXnnnn     terrain, one Mesh per texture page; visual AND collision (`stj:collision`
                                         on /track/ground, doubleSided = true because the game collides both sides)
               /track/prototypes/OBJnnnn  object models (placed through USD native instancing)
               /track/objects/OBJnnnn_k   placed objects (signs, trees, buildings...) with their yaw
               materials: UsdPreviewSurface + UsdUVTexture (nearest filter like the game); the baked light is the
               `displayColor` primvar and is declared `baked.vertex_colour` (role light), not multiplied into the albedo
  * reference  centerline = the road line (slot 0x3e chain, see course_ref.py; slot 0x3d is the same line walked the
               other way, so there is no width in the data); `s` is the plan length along it (levels 21..76; levels
               01-06 have a 3-record line and 11-16 none, they get no reference)
  * timing     start = first check record, finish = last check record, `split_points` = the checks between (byte 0x26 != 0, line records 1..last-1; RC2000 decomp)
  * x_rcx      everything the document has no field for: the pace-note slot bytes with their `s` and direction (forward
               chain, and the reverse chain converted to forward s; code -> speech not decoded), zone open/close
               markers, the object table and the raw record indices
  * provenance converted_from with the `lost` list; licence.redistributable = false (commercial game content)

Ideas / TODO:
  * Surfaces: each terrain mesh is split by (texture page, surface profile) -> /track/ground/TXnnnn_S<k>, bound to
    surface `rcx_s<k>` through `surfaces.bindings[].prim`.  k = low byte of TEXTINFO+0x20 of the tile (copied to slot
    byte +0x10 by 0x439137, used as profile index by the collision code 0x43d3f8); polygons with flag 0x1000 use
    profile 5.  The 14 profiles (48 bytes at 0x6138f8 of RAL.EXE: grip-table pointer + floats) are read from your
    RAL.EXE into x_rcx; the class stays 'other' because the profiles carry no name.  Profile 0 and 13 have a grip
    table of 1 (not a driving surface?), 1 has the highest grip (tarmac-like), 9 the lowest.
  * Pace notes: when the code -> call mapping is decoded, write `race.pacenotes.notes[]` (call, kind, direction,
    severity) instead of `x_rcx.pace_notes`.
  * Object size p6 (16 = natural) is applied as scale p6/16; pitch/roll (p3/p4) are in x_rcx but not applied.
  * `scenery.prototypes/instances` could describe the objects in the document; for now they only exist as USD prims.
  * A .usdz package: `python tools/stj_usd.py package` from the STJ repository.
"""
import json
import math
import os
import struct
import sys

from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from track import read_track
import course_ref
from ro3 import read_ro3
from ro3_to_glb import pcx_png, orient_polys
from track_to_glb import TILE_SWAP, FILES, FALLBACK, model_path, is_placed_object, object_scale, page_size

TOOL, TOOL_VERSION = 'track_to_stj.py', '0.1'


def exe_surface_profiles():
    """The game's 14 surface profiles (RAL.EXE, 48 bytes each from 0x6138f8): a pointer to a 6-value grip table plus
    floats.  Read from the user's own RAL.EXE; returns {index: {'grip': [6 ints], 'floats': [11 floats]}} or {}."""
    path = os.path.join(os.path.dirname(os.path.abspath(FILES)), 'RAL.EXE')
    if not os.path.exists(path):
        return {}
    d = open(path, 'rb').read()
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    n, ohs = struct.unpack_from('<H', d, pe + 6)[0], struct.unpack_from('<H', d, pe + 20)[0]
    base = struct.unpack_from('<I', d, pe + 24 + 28)[0]
    secs = []
    for i in range(n):
        o = pe + 24 + ohs + i * 40
        vs, va, rs, ro = struct.unpack_from('<IIII', d, o + 8)
        secs.append((base + va, max(vs, rs), ro))

    def rd(a, ln):
        for va, sz, ro in secs:
            if va <= a < va + sz:
                return d[ro + a - va: ro + a - va + ln]
    out = {}
    for i in range(14):
        rec = rd(0x6138f8 + i * 48, 48)
        ptr = struct.unpack_from('<I', rec, 0)[0]
        tb = struct.unpack('<13i', rd(ptr, 52))
        out[i] = {'grip_table': list(tb),      # [0] default, [1..6] set A, [7..12] set B (percent), see grip note
                  'floats': [round(x, 4) for x in struct.unpack_from('<11f', rec, 4)]}
    return out


# Stage number (= LEVELnn folder) -> stage name. Hardcoded from the game's own data: the track pack in RAL.EXE (36 rows of 0x40 bytes at
# VA 0x734b7a; dword 0 = name text id 0x3e8 + i = tokSS_StageNNN in LOCALE/ENGLISH.TXT, dword 13 = stage number) and the capitalisation
# of the RC2000 decompilation (menu/launch.rs).  Checked against the exe + ENGLISH.TXT: 36/36 equal ignoring case.
STAGE_NAMES = {
    21: 'Port Soderick', 22: 'Ballagyr', 23: 'Curraghs', 24: 'Tholt-y-Will', 25: 'Injerbreck', 26: 'Cringle',
    31: 'Twiglees', 32: 'Yair', 33: 'Cardrona', 34: 'Black Loch', 35: 'Glentrool', 36: 'Ae',
    41: 'Clocaenog Mid', 42: 'Penmachno South', 43: 'Myherin', 44: 'Hafren', 45: 'Dyfi', 46: 'Gartheiniog',
    51: "Hamilton's Folly", 52: "Tyrone's Ditches", 53: 'Feeney', 54: 'Parkanaur', 55: 'Lisnamuck', 56: 'Tardree',
    61: 'Chirdonhead', 62: 'Falstone', 63: 'Kershope', 64: 'Pundershaw', 65: 'Riccarton', 66: 'Newcastleton',
    71: 'Moon and Star', 72: 'Bothwell', 73: 'Whitchester', 74: 'Eccles', 75: 'Langton', 76: 'Fogo',
}


def stj(v):
    """Game (x, y up, z) -> STJ (x, y, z up)."""
    return (v[0], v[2], v[1])


class Textures:
    """PCX -> PNG on first use; returns the path relative to the root layer, or None when the PCX is missing."""
    def __init__(self, out_dir):
        self.dir = os.path.join(out_dir, 'textures')
        self.done = {}
        self.sizes = {}

    def get(self, pcx_path):
        if pcx_path in self.done:
            return self.done[pcx_path]
        rel = None
        if os.path.exists(pcx_path):
            os.makedirs(self.dir, exist_ok=True)
            tag = os.path.basename(os.path.dirname(pcx_path))
            name = '%s_%s.png' % (tag, os.path.splitext(os.path.basename(pcx_path))[0])
            png, size = pcx_png(pcx_path)
            open(os.path.join(self.dir, name), 'wb').write(png)
            rel = 'textures/' + name
            self.sizes[rel] = size
        self.done[pcx_path] = rel
        return rel


class Mats:
    """UsdPreviewSurface materials, one per name; textured ones use a nearest-filtered UsdUVTexture on `st`."""
    def __init__(self, stage):
        self.stage = stage
        self.cache = {}
        UsdGeom.Scope.Define(stage, '/track/materials')

    def get(self, name, tex_rel, alpha, colour=FALLBACK):
        if name in self.cache:
            return self.cache[name]
        mat = UsdShade.Material.Define(self.stage, '/track/materials/' + name)
        sh = UsdShade.Shader.Define(self.stage, '/track/materials/%s/pbr' % name)
        sh.CreateIdAttr('UsdPreviewSurface')
        sh.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(1.0)
        sh.CreateInput('metallic', Sdf.ValueTypeNames.Float).Set(0.0)
        if tex_rel:
            rd = UsdShade.Shader.Define(self.stage, '/track/materials/%s/st' % name)
            rd.CreateIdAttr('UsdPrimvarReader_float2')
            rd.CreateInput('varname', Sdf.ValueTypeNames.Token).Set('st')
            rd.CreateOutput('result', Sdf.ValueTypeNames.Float2)
            tx = UsdShade.Shader.Define(self.stage, '/track/materials/%s/tex' % name)
            tx.CreateIdAttr('UsdUVTexture')
            tx.CreateInput('file', Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(tex_rel))
            tx.CreateInput('st', Sdf.ValueTypeNames.Float2).ConnectToSource(rd.GetOutput('result'))
            tx.CreateInput('sourceColorSpace', Sdf.ValueTypeNames.Token).Set('sRGB')
            tx.CreateInput('wrapS', Sdf.ValueTypeNames.Token).Set('repeat')
            tx.CreateInput('wrapT', Sdf.ValueTypeNames.Token).Set('repeat')
            tx.CreateInput('minFilter', Sdf.ValueTypeNames.Token).Set('nearest')
            tx.CreateInput('magFilter', Sdf.ValueTypeNames.Token).Set('nearest')
            tx.CreateOutput('rgb', Sdf.ValueTypeNames.Float3)
            tx.CreateOutput('a', Sdf.ValueTypeNames.Float)
            sh.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).ConnectToSource(tx.GetOutput('rgb'))
            if alpha:                       # colour-keyed cutout (magenta -> alpha 0)
                sh.CreateInput('opacity', Sdf.ValueTypeNames.Float).ConnectToSource(tx.GetOutput('a'))
                sh.CreateInput('opacityThreshold', Sdf.ValueTypeNames.Float).Set(0.5)
        else:
            sh.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*colour))
        sh.CreateOutput('surface', Sdf.ValueTypeNames.Token)
        mat.CreateSurfaceOutput().ConnectToSource(sh.GetOutput('surface'))
        self.cache[name] = mat
        return mat


def write_mesh(stage, path, corners, mat, colours=None, normals=None, collision=False):
    """corners: list of (STJ point, (u, v) with v down like the PCX, optional shade). Three corners per triangle,
    not shared: the points are merged by position, UVs/shade/normals stay per face corner (faceVarying)."""
    # drop degenerate triangles (two corners at the same point, left by quads with repeated vertices): Blender reports
    # them as 'invalid face data' and they have no area anyway
    keep = [i for i in range(0, len(corners), 3) if len({c[0] for c in corners[i:i + 3]}) == 3]
    if len(keep) * 3 != len(corners):
        pick = [j for i in keep for j in (i, i + 1, i + 2)]
        corners = [corners[j] for j in pick]
        colours = [colours[j] for j in pick] if colours else colours
        normals = [normals[j] for j in pick] if normals else normals
    index, pts, ids = {}, [], []
    for c in corners:
        k = c[0]
        if k not in index:
            index[k] = len(pts)
            pts.append(Gf.Vec3f(*k))
        ids.append(index[k])
    m = UsdGeom.Mesh.Define(stage, path)
    m.CreatePointsAttr(Vt.Vec3fArray(pts))
    m.CreateFaceVertexCountsAttr(Vt.IntArray([3] * (len(ids) // 3)))
    m.CreateFaceVertexIndicesAttr(Vt.IntArray(ids))
    m.CreateDoubleSidedAttr(True)
    m.CreateSubdivisionSchemeAttr('none')
    pv = UsdGeom.PrimvarsAPI(m)
    st = pv.CreatePrimvar('st', Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying)
    st.Set(Vt.Vec2fArray([Gf.Vec2f(c[1][0], 1.0 - c[1][1]) for c in corners]))      # USD V points up
    if colours:
        dc = pv.CreatePrimvar('displayColor', Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.faceVarying)
        dc.Set(Vt.Vec3fArray([Gf.Vec3f(c, c, c) for c in colours]))
    if normals:
        m.CreateNormalsAttr(Vt.Vec3fArray([Gf.Vec3f(*n) for n in normals]))
        m.SetNormalsInterpolation(UsdGeom.Tokens.faceVarying)
    UsdShade.MaterialBindingAPI.Apply(m.GetPrim()).Bind(mat)
    return m


def add_prototype(stage, tex, mats, ro3_path, name):
    """One Xform with a Mesh per texture page of an RO3 model (the same triangles as ro3_to_glb.add_model)."""
    verts, polys, normals = read_ro3(ro3_path)
    nrm = [(x, y, -z) for x, y, z in normals]        # same convention as add_model, needed by orient_polys
    has_n = bool(normals) and all(l[3] < len(normals) for q in polys for l in q['loops'])
    flips = orient_polys(verts, polys, nrm) if has_n else None
    folder = os.path.dirname(ro3_path)
    groups = {}
    for i, p in enumerate(polys):
        if p['n'] < 3:
            continue
        loops = p['loops'][::-1] if flips and flips[i] else p['loops']
        g = groups.setdefault(p['f1'] & 0xFFFF, [])
        for k in range(1, len(loops) - 1):
            g += [loops[0], loops[k + 1], loops[k]]
    root = UsdGeom.Xform.Define(stage, '/track/prototypes/' + name)
    for tid, tris in sorted(groups.items()):
        rel = tex.get(os.path.join(folder, 'TX%04d.PCX' % tid))
        size = tex.sizes.get(rel, (256, 256))
        mat = mats.get('%s_TX%04d' % (os.path.basename(folder), tid), rel, alpha=True)
        corners = [(stj(verts[t[0]]), (t[1] / size[0], t[2] / size[1])) for t in tris]
        normals_out = None      # stored normals disagree with the winding fixed by orient_polys: let the viewer derive them
        write_mesh(stage, '/track/prototypes/%s/TX%04d' % (name, tid), corners, mat, normals=normals_out)
    return root


def chain_s(pts):
    """Plan distance (x/y) along a point list, starting at 0."""
    s = [0.0]
    for i in range(1, len(pts)):
        s.append(s[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    return s


def build_road(t, sq_path):
    """The road line. Returns None when the level has no usable line.
    Slots 0x3e and 0x3d hold the SAME polyline, one chain per direction (checked on LEVEL75 and LEVEL44: every
    point of one is a point of the other, and B is A reversed), so there is no left/right pair and no road width
    in this data. A (slot 0x3e) is the forward line and becomes the reference centerline; B (0x3d) is the reverse
    direction and only contributes its own marker bytes (pace notes of the reversed stage), converted to forward s."""
    P, V = t['polys'], t['verts']
    ca = course_ref.analyse(sq_path, 0).get('chain', [])
    cb = course_ref.analyse(sq_path, 1).get('chain', [])
    if len(ca) < 2:
        return None
    pts = [stj(V[P[r][1]]) for r in ca]
    keep = [0]                                  # drop points that do not advance in plan (< 1 cm)
    for i in range(1, len(pts)):
        if math.hypot(pts[i][0] - pts[keep[-1]][0], pts[i][1] - pts[keep[-1]][1]) > 0.01:
            keep.append(i)
    s_all = chain_s(pts)
    road = {'pts': [pts[i] for i in keep], 's': [s_all[i] for i in keep], 'src': keep, 'chain_a': ca, 'chain_b': cb}
    road['length'] = road['s'][-1]
    ptsb = [stj(V[P[r][1]]) for r in cb]
    road['s_b'] = chain_s(ptsb) if len(cb) >= 2 else []
    return road


def markers(P, chain, s_of, direction):
    """Checkpoint / pace-note bytes of a chain. `s_of(i)` gives the forward s of chain index i (or None)."""
    splits, notes, zones = [], [], []
    for i, r in enumerate(chain):
        si = s_of(i)
        if si is None:
            continue
        p = P[r]
        kind = (p[9] >> 16) & 0xFF
        if kind == 2 and direction == 'forward':
            splits.append({'id': 'cp%d' % (len(splits) + 1), 'name': 'checkpoint %d' % (len(splits) + 1),
                           's': round(si, 3)})
        for slot, v in enumerate((p[10] >> (8 * k)) & 0xFF for k in range(4)):
            if v:
                e = {'s': round(si, 3), 'direction': direction, 'record': r, 'slot': slot, 'code': v}
                (zones if v in (98, 99, 100) else notes).append(e)
    return splits, notes, zones


# Pace-note codes = index into the exe's speech table (0x105e1a / 0x106b14: count..go, distance, suffix, general,
# instruct, comment).  Checked: 47 left4, 50 lefthp, 52 right1, 55 right4 (the offset is the 6 `count` files).
GENERAL_KIND = {20: 'bank', 21: 'bridge', 22: 'caution', 23: 'twisty', 24: 'crest', 25: 'ditch', 26: 'finish', 27: 'other',
                28: 'other', 29: 'water', 30: 'jump', 31: 'junction', 32: 'other', 33: 'other', 34: 'other',
                35: 'dip', 36: 'other', 37: 'surface_change', 38: 'other', 39: 'other', 40: 'other', 41: 'other',
                42: 'other', 43: 'water'}
GENERAL_NAME = ['bank', 'bridge', 'caution', 'chicane', 'crest', 'ditch', 'finish', 'gate', 'haybales', 'ice', 'jump',
                'junction', 'logs', 'loose gravel', 'over', 'past', 'road dips', 'rocks', 'rough surface',
                'slippery surface', 'through', 'to stop', 'under', 'water']


def pacenote_of(code):
    """(kind, direction, severity, call) for a corner / general code, or None (counts, distances, suffixes, comments)."""
    if 44 <= code <= 59:
        side = 'left' if code < 52 else 'right'
        k = code - (44 if code < 52 else 52)           # 0..4 = 1..5, 5 square, 6 hairpin, 7 tight hairpin
        if k < 5:
            return 'turn', side, k + 1, '%s %d' % (side, k + 1)
        return ('square', side, None, side + ' square') if k == 5 else                ('hairpin', side, None, side + (' hairpin' if k == 6 else ' tight hairpin'))
    if code == 61:
        return 'straight', None, None, 'straight'
    if code == 68:      # speech table entry 68 = comment\ohno.wav; stage data: 45 times in LEVEL61, mostly right after "right 4"
        return 'other', None, None, 'oh no'
    if 20 <= code <= 43:
        name = GENERAL_NAME[code - 20]
        kind = {'bridge': 'bridge', 'caution': 'caution', 'chicane': 'twisty', 'crest': 'crest', 'jump': 'jump',
                'junction': 'junction', 'finish': 'finish', 'water': 'water', 'road dips': 'dip', 'ditch': 'other'}.get(name, 'other')
        return kind, None, None, name
    return None


def pacenotes_block(notes):
    """race.pacenotes from the forward chain's notes; everything not decoded stays in x_rcx.pace_notes."""
    by_rec = {}
    for n in notes:
        if n['direction'] == 'forward':
            by_rec.setdefault((n['s'], n['record']), []).append(n['code'])
    out = []
    for (s_, rec), codes in sorted(by_rec.items()):
        mods = []
        if 60 in codes:
            mods.append('long')
        if 16 in codes:
            mods.append('dont_cut')
        if 18 in codes:
            mods.append('opens')
        if 19 in codes:
            mods.append('tightens')
        for c in codes:
            r = pacenote_of(c)
            if r:
                e = {'s': s_, 'kind': r[0], 'call': r[3]}
                if r[1]:
                    e['direction'] = r[1]
                if r[2]:
                    e['severity'] = r[2]
                if mods:
                    e['modifiers'] = mods
                out.append(e)
    return {'scale': 'ascending_1_6', 'language': 'en', 'written_for': 'RCX co-driver (severity 1..5 as the game words it, _est)',
            'notes': out}


# profile index -> (name, STJ class); names of the profiles as the game's own list gives them (found through the RC2000
# decompilation, github.com/Diatosta/rc2k_decomp: same engine, same table layout)
SURFACE_NAMES = {1: ('tarmac', 'asphalt'), 2: ('gravel', 'gravel'), 3: ('mud', 'mud'), 4: ('snow', 'snow'),
                 5: ('water', 'water'), 6: ('dust', 'dirt'), 7: ('grass', 'grass'), 8: ('rough', 'other'),
                 9: ('ice', 'ice'), 10: ('cattlegrid', 'metal'), 11: ('thick snow', 'snow'),
                 12: ('snow gravel', 'snow'), 0: ('none', 'other'), 13: ('none', 'other')}


def surfaces_block(surf_used, keys):
    """One surface per game profile in use, bound to the terrain meshes `/track/ground/TXnnnn_S<profile>`."""
    prof = exe_surface_profiles()
    lib, bind = {}, []
    for k in sorted(surf_used):
        nm, cls = SURFACE_NAMES.get(k, ('profile %d' % k, 'other'))
        e = {'class': cls, 'name': 'RCX %s (profile %d)' % (nm, k),
             'quality': {'basis': 'derived', 'confidence': 0.6,
                         'method': 'profile index read from TEXTINFO+0x20 of each tile (RAL.EXE 0x43d3f8); name from the engine profile list'},
             'x_rcx': {'profile': k, 'polygons': surf_used[k],
                       'grip_note': 'percent = A[i] + (B[i]-A[i])*f, i = per-car index 0..5 (0x719f84), f = level value 0..1 (0x71a1d8); '
                                    'floats[0..2] scale the three force terms (0x43d5c4..) and are multiplied by percent/100'}}
        if k in prof:
            e['x_rcx'].update(prof[k])
            g = prof[k]['grip_table'][1]
            if g > 1:      # profiles 0 and 13 have a table of 1s: not a driving surface, no factor
                e['mf_scaling'] = {'LMUX': round(g / 120.0, 3), 'LMUY': round(g / 120.0, 3),
                                   '_est': 'grip_table[1] (set A, index 0) / 120 (profile 1, the highest); the game picks the entry by a per-car index 0..5 and blends sets A and B by a level-wide 0..1 value (0x43ba9e)'}
        lib['rcx_s%d' % k] = e
    for tid, sf in sorted(keys):
        bind.append({'mesh_ref': 'stage', 'prim': '/track/ground/TX%04d_S%d' % (tid, sf), 'surface': 'rcx_s%d' % sf})
    return {'default': 'rcx_s%d' % max(surf_used, key=surf_used.get) if surf_used else 'rcx_s0', 'library': lib,
            'bindings': bind}


def level_markers(t, vs):
    """Marker polygons with slot 0x38 / 0x3C (replay cameras), 0x3B (lamps), 0x3F (sound spots); the position is the
    polygon's first vertex.  Layout checked against the RC2000 decompilation (same engine) and on LEVEL75 / LEVEL61."""
    out = {0x38: [], 0x3B: [], 0x3C: [], 0x3F: []}
    for i, p in enumerate(t['polys']):
        slot = (p[0] >> 8) & 0xFF
        if p[0] & 0xFF == 0xFF and slot in out and p[1] < len(vs):
            out[slot].append({'poly': i, 'pos': [round(c, 3) for c in vs[p[1]]], 'p2': p[2], 'p7': p[7], 'p8': p[8]})
    return out


def camera_runs(cams, pts, sv):
    """Active range of each replay camera: along the road line a camera is the one in use while it is the nearest of
    its set in plan (race::best_line `replay_cameras`/`nearest_camera` in the RC2000 decomp: each route face gets the
    nearest camera of kind 0x3C and of kind 0x38).  Returns [(camera index, s_start, s_end)]."""
    runs = []
    cur, start = None, 0.0
    for k, p in enumerate(pts):
        best = min(range(len(cams)), key=lambda i: math.hypot(cams[i]['pos'][0] - p[0], cams[i]['pos'][1] - p[1]))
        if best != cur:
            if cur is not None:
                runs.append((cur, start, sv[k]))
            cur, start = best, sv[k]
    if cur is not None:
        runs.append((cur, start, sv[-1]))
    return runs


def markers_blocks(mk, road=None):
    block = {}
    sets = []
    for slot, sid, nm in ((0x3C, 'replay_a', 'replay cameras (kind 0x3C)'), (0x38, 'replay_b', 'replay cameras (kind 0x38)')):
        cams = mk[slot]
        if not cams:
            continue
        out, n = [], 0
        if road and road.get('pts') and len(road['pts']) > 1:
            for ci, s0, s1 in camera_runs(cams, road['pts'], road['s']):
                n += 1
                c = cams[ci]
                out.append({'id': '%s_%d' % (sid, n), 'kind': 'tracking', 'position': c['pos'],
                            'active': {'s_start': round(s0, 2), 's_end': round(s1, 2)},
                            'x_rcx': {'poly': c['poly'], 'slot': hex(slot), 'p7': c['p7'], 'p8': c['p8'],
                                      '_note': 'range = where this camera is the nearest of its set along the road line'}})
        else:
            out = [{'id': '%s_%d' % (sid, k + 1), 'kind': 'tracking', 'position': c['pos'], 'x_rcx': {'poly': c['poly']}}
                   for k, c in enumerate(cams)]
        sets.append({'id': sid, 'name': nm, 'cameras': out})
    if sets:
        block['cameras'] = {'sets': sets}
    lights = [{'id': 'lamp%d' % (k + 1), 'kind': 'other', 'type': 'point', 'position': m['pos'], 'schedule': 'always',
               'x_rcx': {'poly': m['poly']}} for k, m in enumerate(mk[0x3B])]
    if lights:
        block['lights'] = lights
    emit = [{'id': 'snd%d' % (k + 1), 'kind': 'ambient', 'position': m['pos'],
             'x_rcx': {'poly': m['poly'], 'sound_id': m['p2']}} for k, m in enumerate(mk[0x3F])]
    if emit:
        block['emitters'] = emit
    return block


def environment_raw(ld, sky=None):
    """Level parameters of the sun / light, from LOCALDAT (see FORMATS.md): the sun exists when dwords 407 or 427 are
    non-zero (0x4553a0); its direction is two angles in 1/256 turn: (d416 + d417) & 255 and d418 (0x4553b1); dwords
    384..389 are the min / max RGB of the dynamic light colour (0x451085).  The game has no sky geometry in the level."""
    return {'sun_present': bool(ld[407] or ld[427]), 'sun_angle_a': (ld[416] + ld[417]) & 255, 'sun_angle_b': ld[418],
            'light_rgb_min': [ld[384], ld[385], ld[386]], 'light_rgb_max': [ld[387], ld[388], ld[389]],
            'sky_dome': sky,
            '_note': 'raw values; angle conventions and the exact use of the colours are not verified'}


def build_document(name, t, road, placed, textures, n_polys, surf_used, keys, env=None, mk=None):
    doc = {
        '_metadata': {
            'specification': 'STJ', 'version': '0.3', 'coordinate_system': 'OpenDRIVE', 'units': 'SI',
            'description': 'Rally Championship Xtreme stage %s, converted from the game files by the user' % name,
            'source_format': 'RCX LEVELS/%s (CDAT00C.SQ + LOCALDAT)' % name,
            'licence': {'redistributable': False, 'holder': 'original game content',
                        'notes': 'Converted from commercial game files; do not redistribute.'},
            'provenance': {
                'type': 'converted', 'confidence': 'medium',
                'detail': 'Geometry, textures, road line and markers decoded from the game files',
                'converted_from': [{
                    'format': 'RCX level (RAL.EXE)', 'tool': TOOL, 'tool_version': TOOL_VERSION, 'source': name,
                    'lost': ['road width (the road line has no width in the level files)',
                                                          'pace-note codes outside corners/general calls (counts, distances, comments; kept in x_rcx.pace_notes)',
                             'object pitch and roll (kept in x_rcx.objects)',
                             'replay camera zoom and any fixed aim (cameras are listed as tracking; ranges = nearest camera along the road line)',
                             'water (LEVEL03/13 holes, LEVEL05/15 edges: drawn by the game, not in the data)']}]}},
        'track_info': {'name': name, 'type': 'point_to_point', 'primary_surface': 'other'},
        # the stage's own name (levels 01-06 and 11-16 are not in the table)
        'geometry': {'meshes': [{'id': 'stage', 'uri': '%s.usda' % name, 'roles': ['visual', 'collision'],
                                 'baked': {'vertex_colour': {'primvar': 'displayColor', 'role': 'light'}}}]},
        'surfaces': surfaces_block(surf_used, keys),
        'x_rcx': {'game_unit_is_metre': True, 'object_collision': 'all placed objects marked as collision (estimate: the game decides per object material kind)', 'polygons': n_polys, 'objects': placed, 'environment': env},
    }
    if int(name[5:]) in STAGE_NAMES:
        doc['track_info']['name'] = STAGE_NAMES[int(name[5:])]
    if env and env.get('sky_dome'):
        doc.setdefault('environment', {})['sky'] = {'model': 'engine_default', 'x_rcx': env['sky_dome']}
    if mk:
        b = markers_blocks(mk, road)
        if 'cameras' in b:
            doc['cameras'] = b['cameras']
        if 'lights' in b:
            doc.setdefault('environment', {}).setdefault('lighting', {})['lights'] = b['lights']
        if 'emitters' in b:
            doc['audio'] = {'emitters': b['emitters']}
    if road:
        pts, s, length = road['pts'], road['s'], road['length']
        doc['reference'] = {'centerline': {'points': [[round(c, 4) for c in p] for p in pts]},
                            'length': round(length, 3)}
        P = t['polys']
        src_to_s = dict(zip(road['src'], s))
        splits, notes, zones = markers(P, road['chain_a'], src_to_s.get, 'forward')
        # Start, finish and splits as the game does it (RC2000 decomp race/checkpoints.rs, level_init.rs listed_checkpoints,
        # car_starts.rs start_car): the checks are the line records 1 .. last-1 whose byte 0x26 (p9 byte 2) is not 0; the car
        # is placed on the first check, the stage ends on the last one and the ones between are the splits.  The chain's
        # first record (flag 1) and last record are not checks, so the line runs on before the start and after the finish.
        chain_a = road['chain_a']
        checks = [(src_to_s[i], i) for i in range(1, len(chain_a) - 1) if i in src_to_s and (P[chain_a[i]][9] >> 16) & 0xFF]
        if len(checks) >= 2:
            start_s, finish_s = checks[0][0], checks[-1][0]
            splits = [{'id': 'cp%d' % (k + 1), 'name': 'checkpoint %d' % (k + 1), 's': round(si, 3)}
                      for k, (si, _i) in enumerate(checks[1:-1])]
        else:           # fewer than two checks (LEVEL01-06): the game lists none; the line ends stand in
            start_s, finish_s, splits = 0.0, length, []
        doc['track_info']['length'] = round(finish_s - start_s, 1)
        sb = road['s_b']
        if sb and sb[-1] > 0:      # reverse direction: s_forward = L - s along the reversed chain
            _sp, notes_r, zones_r = markers(P, road['chain_b'], lambda i: max(0.0, length - sb[i] * length / sb[-1]),
                                            'reverse')
            notes, zones = notes + notes_r, zones + zones_r
        doc['timing'] = {'start': {'s': round(start_s, 3)}, 'finish': {'s': round(finish_s, 3)}}
        if splits:
            doc['timing']['split_points'] = splits
        pn = pacenotes_block(notes)
        if pn['notes']:
            doc.setdefault('race', {})['pacenotes'] = pn
        doc['x_rcx']['pace_notes'] = notes
        doc['x_rcx']['zones'] = zones        # 99 = corner measured from the route, 100 = end, 98 = end marker (RC2000 decomp)
        doc['x_rcx']['road_line'] = {'records_forward': len(road['chain_a']), 'records_reverse': len(road['chain_b'])}
    return doc


def point_at(road, s):
    """(x, y, z, heading degrees) of the road line at plan distance s (linear between line points)."""
    pts, sv = road['pts'], road['s']
    s = min(max(s, 0.0), sv[-1])
    i = max(1, next((k for k in range(1, len(sv)) if sv[k] >= s), len(sv) - 1))
    a, b = pts[i - 1], pts[i]
    t = 0.0 if sv[i] == sv[i - 1] else (s - sv[i - 1]) / (sv[i] - sv[i - 1])
    p = [a[k] + (b[k] - a[k]) * t for k in range(3)]
    return p[0], p[1], p[2], math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def add_timing_markers(stage, road, timing):
    """Start, finish and split gates as thin boxes across the road (prims STJ__timing__<id>; visual only, no collision).
    The road has no width in the data, so the gate width (12 m) is a placeholder."""
    UsdGeom.Scope.Define(stage, '/track/timing')
    items = [('start', timing['start']['s']), ('finish', timing['finish']['s'])] +             [(sp['id'], sp['s']) for sp in timing.get('split_points', [])]
    for k, s_ in items:
        x, y, z, h = point_at(road, s_)
        g = UsdGeom.Xform.Define(stage, '/track/timing/STJ__timing__%s' % k)
        g.AddTranslateOp().Set(Gf.Vec3d(x, y, z + 2.5))
        g.AddRotateZOp().Set(h)
        g.AddScaleOp().Set(Gf.Vec3f(0.3, 12.0, 5.0))        # thin across the road (local y), 5 m tall
        g.GetPrim().CreateAttribute('stj:id', Sdf.ValueTypeNames.String, custom=True).Set(k)
        g.GetPrim().CreateAttribute('stj:s', Sdf.ValueTypeNames.Double, custom=True).Set(float(s_))
        c = UsdGeom.Cube.Define(stage, '/track/timing/STJ__timing__%s/gate' % k)
        c.CreateSizeAttr(1.0)
        c.CreateDisplayColorAttr([Gf.Vec3f(0.1, 0.8, 0.2) if k == 'start' else Gf.Vec3f(0.9, 0.1, 0.1) if k == 'finish' else Gf.Vec3f(1.0, 0.8, 0.0)])


def convert(sq_path, out_dir):
    name = os.path.basename(os.path.dirname(sq_path))
    out = os.path.join(out_dir, name)
    os.makedirs(out, exist_ok=True)
    t = read_track(sq_path)
    vs = [stj(v) for v in t['verts']]
    textinfo = open(os.path.join(FILES, 'A', 'TEXTINFO'), 'rb').read()
    local = open(os.path.join(os.path.dirname(sq_path), 'LOCALDAT'), 'rb').read()
    tiles = list(struct.unpack_from('<256i', local, 64 * 4))
    stage_no = int(name[5:])
    swap = TILE_SWAP.get(stage_no)
    if swap and swap[0] in tiles[:255]:
        tiles[tiles.index(swap[0])] = swap[1]

    stage = Usd.Stage.CreateNew(os.path.join(out, name + '.usda'))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, '/track')
    stage.SetDefaultPrim(root.GetPrim())
    ground = UsdGeom.Scope.Define(stage, '/track/ground')
    ground.GetPrim().CreateAttribute('stj:collision', Sdf.ValueTypeNames.Bool, custom=True).Set(True)
    tex, mats = Textures(out), Mats(stage)

    # --- terrain: same grouping and triangulation as track_to_glb.convert -----------------------------------
    groups = {}
    for p in t['polys']:
        if p[0] & 0xFF == 0xFF or p[6] == 16 or max(p[1:5]) >= len(vs):
            continue
        code = tiles[(p[0] >> 8) & 0xFF]
        c = struct.unpack_from('<4I', textinfo, code * 128) if 0 <= code < 16000 else (0,) * 4
        if not any(c):
            c = (0x00000000, 0x0000FFFF, 0xFFFF0000, 0xFFFFFFFF)
        uv = [((w & 0xFFFF) / 256.0, (w >> 16) / 256.0) for w in c]   # pixels; divided by the page size below
        shade = [((p[5] >> (8 * k)) & 255) / 255.0 for k in range(4)]
        # surface profile: low byte of TEXTINFO+0x20 (slot byte +0x10); polygons flagged 0x1000 use profile 5
        surf = textinfo[code * 128 + 0x20] if 0 <= code < 16000 else 0
        if (p[0] >> 16) & 0x1000:
            surf = 5
        groups.setdefault((code >> 4, min(surf, 13)), []).append((p[1:5], uv, shade))
    surf_used = {}
    for (tid, sf), quads in sorted(groups.items()):
        surf_used[sf] = surf_used.get(sf, 0) + len(quads)
        rel = tex.get(os.path.join(FILES, 'A', 'TX%04d.PCX' % tid))
        corners, colours = [], []
        pw, ph = page_size(tid)
        for idx, uv, sh in quads:
            uv = [(u / pw, v / ph) for u, v in uv]
            for tri in ((0, 3, 1), (0, 2, 3)):      # counter-clockwise seen from +Z: the normal (right-handed) points up
                for k in tri:
                    corners.append((vs[idx[k]], uv[k]))
                    colours.append(sh[k])
        if tid == 0:
            # texture group 0 is never a file (there is no TX0000.PCX): the game draws these polygons with no texture, the
            # vertex colour only (RC2000 decomp: scene::choose_texture -> NO_TEXTURE); in LEVEL61 they are the power cables
            mat = mats.get('untextured', None, alpha=False, colour=(1.0, 1.0, 1.0))
        else:
            mat = mats.get('TX%04d' % tid if rel else 'missing_TX%04d' % tid, rel, alpha=True)
        write_mesh(stage, '/track/ground/TX%04d_S%d' % (tid, sf), corners, mat, colours=colours)

    # --- placed objects (see track_to_glb.place_objects) -------------------------------------------------------
    ld = struct.unpack_from('<%di' % (len(local) // 4), local, 0)
    UsdGeom.Scope.Define(stage, '/track/prototypes')
    objs = UsdGeom.Scope.Define(stage, '/track/objects')
    # the game tests the car against placed objects too (physics::face_contact -> object_hits_car, RC2000 decomp), so the
    # instances are collision surface; which objects are actually solid depends on a per-model material kind not decoded here
    objs.GetPrim().CreateAttribute('stj:collision', Sdf.ValueTypeNames.Bool, custom=True).Set(True)
    protos, placed, counts = {}, [], {}
    for p in t['polys']:
        if not is_placed_object(p) or p[1] >= len(vs):
            continue
        slot = (p[0] >> 8) & 0xFF
        if slot >= 64 or (slot >= 0x38 and p[6] == 16):
            continue
        m = ld[320 + slot]
        path = model_path(m)
        if not path:
            continue
        pname = 'OBJ%04d' % m
        if pname not in protos:
            add_prototype(stage, tex, mats, path, pname)
            protos[pname] = True
        k = counts[pname] = counts.get(pname, 0) + 1
        x, y, z = vs[p[1]]
        dz = (p[7] - (1 << 32) if p[7] >> 31 else p[7]) / 65536.0
        inst = UsdGeom.Xform.Define(stage, '/track/objects/%s_%d' % (pname, k))
        inst.GetPrim().GetReferences().AddInternalReference('/track/prototypes/' + pname)
        inst.GetPrim().SetInstanceable(True)
        inst.AddTranslateOp().Set(Gf.Vec3d(x, y, z + dz))
        inst.AddRotateZOp().Set(p[2] * 360.0 / 256.0)
        sc = object_scale(p)
        inst.AddScaleOp().Set(Gf.Vec3f(sc, sc, sc))
        placed.append({'prim': '/track/objects/%s_%d' % (pname, k), 'model': m, 'p2': p[2], 'p3': p[3], 'p4': p[4],
                       'size': p[6]})
    # --- sky: scene object 1 = the model in LOCALDAT[321] (a 3 m dome with 7 sky textures), drawn by the game around the
    # camera with no far clip, turned by sky_turn and raised by sky_height (race::draw_sky / sky_matrix, RC2000 decomp).
    # Here it is a fixed dome centred on the stage and scaled to cover it (viewer decoration, never collision).
    sky = None
    sm = ld[321]
    if model_path(sm):
        pname = 'OBJ%04d' % sm
        if pname not in protos:
            add_prototype(stage, tex, mats, model_path(sm), pname)
            protos[pname] = True
        xs_, ys_ = [v[0] for v in vs], [v[1] for v in vs]
        cx, cy = (min(xs_) + max(xs_)) / 2, (min(ys_) + max(ys_)) / 2
        scale = max(max(xs_) - min(xs_), max(ys_) - min(ys_), 1000.0) * 1.5
        zlow = min(v[2] for v in vs)
        UsdGeom.Scope.Define(stage, '/track/sky')
        dome = UsdGeom.Xform.Define(stage, '/track/sky/dome')
        dome.GetPrim().GetReferences().AddInternalReference('/track/prototypes/' + pname)
        dome.AddTranslateOp().Set(Gf.Vec3d(cx, cy, zlow + (ld[409] << 8) / 65536.0 * scale))
        dome.AddRotateZOp().Set(ld[416] * 360.0 / 256.0)
        dome.AddScaleOp().Set(Gf.Vec3f(scale, scale, scale))
        sky = {'model': sm, 'sky_height': ld[409], 'sky_turn': ld[416], 'scale_m': round(scale, 1),
               'prim': '/track/sky/dome'}
    # prototypes are not geometry of their own: keep them out of the drawn scene
    UsdGeom.Imageable(stage.GetPrimAtPath('/track/prototypes')).CreateVisibilityAttr(UsdGeom.Tokens.invisible)

    road = build_road(t, sq_path)
    doc = build_document(name, t, road, placed, tex, len(t['polys']), surf_used, list(groups), environment_raw(ld, sky), level_markers(t, vs))
    if road and doc.get('timing'):
        add_timing_markers(stage, road, doc['timing'])
    stage.GetRootLayer().customLayerData = {'stj': json.dumps(doc, separators=(',', ':'))}
    stage.GetRootLayer().Save()
    json.dump(doc, open(os.path.join(out, name + '.stj.json'), 'w'), indent=1)      # readable copy for diffing
    return (os.path.join(out, name + '.usda'), 'polygons', len(t['polys']), 'terrain pages', len(groups),
            'objects', len(placed), 'models', len(protos), 'road', round(road['length']) if road else None)


if __name__ == '__main__':
    os.makedirs(sys.argv[1], exist_ok=True)
    for p in sys.argv[2:]:
        print(*convert(p, sys.argv[1]))
