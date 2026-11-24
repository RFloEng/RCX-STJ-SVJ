"""Convert an RCX track (LEVELS/LEVELxx/CDAT00C.SQ + LOCALDAT) to a textured GLB.

Texture lookup, as done by loadlevel_init / initgametexinfbuf in RAL.EXE:
  slot  = (flags >> 8) & 0xFF                      polygon flags (track.py)
  code  = LOCALDAT dword[64 + slot]                the level's 255-entry tile list
  file  = A/TX{code >> 4:04d}.PCX
  UVs   = A/TEXTINFO record `code` (128 bytes): dwords 0..3 are the corner UVs
          c0..c3, lo16 = u, hi16 = v over the 256x256 texture
Quad vertices p1..p4 are in 'Z' order, so the outline is p1,p2,p4,p3.
Corner shading (baked light) = the four bytes of the colour dword, LSB first,
one per vertex p1..p4; exported as COLOR_0.
Usage: python track_to_glb.py <out_dir> <CDAT00C.SQ> [...]

What ends up in the GLB (node names are stable, so Blender scripts can select by prefix):
  * TXnnnn            terrain, one mesh/material per texture page (double-sided, vertex-coloured by the baked shade)
  * OBJnnnn           placed objects (signs, trees, buildings...) from the special polygon records (place_objects)
  * ROADLINE_L/R      line strips along the road line, left/right chain (place_markers)
  * START_/FINISH_/CHECKPOINT_/ZONE_OPEN_/ZONE_CLOSE_/NOTEnn_ <L|R>_<index>
                      empty nodes on the road line; 'extras' carry the raw bytes (place_markers)
Coordinates: game space is left-handed, so Z is negated for glTF everywhere (the `vs` list below).

Ideas / TODO for later work:
  * Object pitch/roll (p3/p4) and scale (p6) are kept in node 'extras' but NOT applied yet; find out their
    meaning from the object draw routine before applying them.
  * Water: LEVEL03/13 have holes and LEVEL05/15 jagged edges where the game draws water/sea; no water
    plane is generated on purpose (not found in the data, probably a separate effect).
  * Terrain is split per texture page; to get one mesh per surface type or per road section, group by
    other keys in `groups` inside convert().
  * Pace-note code -> speech file mapping is unknown (see track.py); once decoded, name NOTEnn nodes after
    the spoken instruction (left4, right1 ...).
  * Replay camera positions are the marker polygons with slot 0x38 / 0x3C (see track_to_stj.py).
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from track import read_track
import math
from ro3_to_glb import Glb, add_model

FILES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'FILES')
FALLBACK = (0.42, 0.36, 0.30)   # brown used for texture pages whose PCX is missing on disk


def model_path(m):
    """Path of OBJ<m>.RO3 for a placed object: track objects live in FILES/A, car-style ids (0x190..) in B.
    Returns None when the model does not exist (the placement is then skipped silently)."""
    for d in ('A', 'B'):
        p = os.path.join(FILES, d, 'OBJ%04d.RO3' % m)
        if os.path.exists(p):
            return p
    return None


def place_objects(g, t, vs, local):
    """Special polygon records (flags low byte 0xFF, p6 != 16) are object instances:
    slot = (flags >> 8) & 0xFF (< 64) -> model id = LOCALDAT dword[320 + slot]
    (copied by the game into its object table and loaded as OBJxxxx.RO3);
    position = vertex p1 (+ p7 as a 16.16 height offset); p2 = yaw in 1/256 turns.
    Yaw sign/scale verified statistically: flat signs/banners (models 52, 91, 92, 94,
    149) end up perpendicular to the road line in 582 samples (circular R=0.64 for
    this convention, none for the others). p3/p4 (pitch/roll?) and p6 (size?) are
    kept in node extras but not applied.
    Records with p6 == 16 are linked point chains (road edge lines), not objects."""
    ld = struct.unpack_from('<%di' % (len(local) // 4), local, 0)
    placed = 0
    for p in t['polys']:
        if p[0] & 0xFF != 0xFF or p[6] == 16 or p[1] >= len(vs):
            continue
        slot = (p[0] >> 8) & 0xFF
        if slot >= 64:
            continue
        m = ld[320 + slot]
        path = model_path(m)
        if not path:
            continue
        parts = add_model(g, path)
        if not parts:
            continue
        x, y, z = vs[p[1]]
        dy = (p[7] - (1 << 32) if p[7] >> 31 else p[7]) / 65536.0
        ang = math.radians(p[2] * 360.0 / 256.0)   # +Y rotation in glTF (game is left-handed, z negated)
        node = {'name': 'OBJ%04d' % m, 'translation': [x, y + dy, z],
                'rotation': [0.0, math.sin(ang / 2), 0.0, math.cos(ang / 2)],
                'extras': {'model': m, 'p2': p[2], 'p3': p[3], 'p4': p[4], 'size': p[6]}}
        if len(parts) == 1:
            node['mesh'] = parts[0][1]
            g.nodes.append(node)
        else:
            kids = []
            for _name, mi in parts:
                g.nodes.append(g.mesh_node(_name, mi))
                kids.append(len(g.nodes) - 1)
            node['children'] = kids
            g.nodes.append(node)
        placed += 1
    return placed


def place_markers(g, sq_path, t, vs):
    """Empty nodes on the road line (slots 0x3e / 0x3d, one chain per side, see course_ref.py).
    Byte 0x26 of a line record (p[9] >> 16): 1 = start of the line, 2 = checkpoint.
    Bytes 0x28..0x2b (p[10]) are pace-note slots (read by 0x4417fd): 99 / 100 open / close a
    note zone, 98 is a third marker, other values are note codes (meaning not decoded)."""
    import course_ref
    n = 0
    for side, tag in ((0, 'L'), (1, 'R')):
        chain = course_ref.analyse(sq_path, side).get('chain', [])
        pts = [vs[t['polys'][r][1]] for r in chain if t['polys'][r][1] < len(vs)]
        if len(pts) >= 2:   # glTF has no curve type: a line strip (Blender: Object > Convert > Curve)
            pos = b''.join(struct.pack('<3f', *q) for q in pts)
            mn = [min(q[k] for q in pts) for k in range(3)]
            mx = [max(q[k] for q in pts) for k in range(3)]
            g.meshes.append({'name': 'ROADLINE_' + tag, 'primitives': [{
                'attributes': {'POSITION': g.acc(pos, 5126, len(pts), 'VEC3', 34962, mn, mx)}, 'mode': 3}]})
            g.nodes.append({'name': 'ROADLINE_' + tag, 'mesh': len(g.meshes) - 1})
        for i, r in enumerate(chain):
            p = t['polys'][r]
            if p[1] >= len(vs):
                continue
            kind = (p[9] >> 16) & 0xFF
            notes = [(p[10] >> (8 * k)) & 0xFF for k in range(4)]
            names = []
            if kind == 1:
                names.append('START')
            elif kind == 2:
                names.append('CHECKPOINT')
            if i == len(chain) - 1:
                names.append('FINISH')
            for v in notes:
                if v:
                    names.append({99: 'ZONE_OPEN', 100: 'ZONE_CLOSE', 98: 'MARK98'}.get(v, 'NOTE%d' % v))
            for nm in names:
                g.nodes.append({'name': '%s_%s_%04d' % (nm, tag, i), 'translation': list(vs[p[1]]),
                                'extras': {'kind': nm, 'side': tag, 'line_index': i, 'record': r,
                                           'byte26': kind, 'note_bytes': notes}})
                n += 1
    return n


def convert(sq_path, out_dir):
    """Convert one level directory's CDAT00C.SQ into <out_dir>/LEVELnn.glb.
    Returns (path, polygon count, texture count, 'objects', n, 'markers', n)."""
    t = read_track(sq_path)
    vs = [(x, y, -z) for x, y, z in t['verts']]      # mirror Z: game is left-handed, glTF right-handed
    textinfo = open(os.path.join(FILES, 'A', 'TEXTINFO'), 'rb').read()
    local = open(os.path.join(os.path.dirname(sq_path), 'LOCALDAT'), 'rb').read()
    tiles = list(struct.unpack_from('<256i', local, 64 * 4))
    # 0x4398d0: stage-specific tile substitution done by the game after loading
    stage = int(os.path.basename(os.path.dirname(sq_path))[5:])
    swap = {54: (0x2f72, 0x2fd2), 56: (0x2f72, 0x2fd2), 61: (0x2f72, 0x2fd2), 63: (0x2f72, 0x2fd2),
            65: (0x2f72, 0x2fd2), 62: (0x2f71, 0x2fd1), 64: (0x2f71, 0x2fd1), 66: (0x2f71, 0x2fd1)}.get(stage)
    if swap and swap[0] in tiles[:255]:
        tiles[tiles.index(swap[0])] = swap[1]
    g = Glb()
    groups = {}   # texture page -> list of (corner vertex indices, corner UVs, corner shades)
    for p in t['polys']:
        # skip special records (0xFF low byte = objects / road-line points) and degenerate vertex refs
        if p[0] & 0xFF == 0xFF or p[6] == 16 or max(p[1:5]) >= len(vs):
            continue
        code = tiles[(p[0] >> 8) & 0xFF]
        c = struct.unpack_from('<4I', textinfo, code * 128) if 0 <= code < 16000 else (0,) * 4
        if not any(c):    # tile code without a TEXTINFO record: use the full texture as a unit quad
            c = (0x00000000, 0x0000FFFF, 0xFFFF0000, 0xFFFFFFFF)
        uv = [((w & 0xFFFF) / 65535.0, (w >> 16) / 65535.0) for w in c]
        shade = [((p[5] >> (8 * k)) & 255) / 255.0 for k in range(4)]
        groups.setdefault(code >> 4, []).append((p[1:5], uv, shade))
    for tid, quads in sorted(groups.items()):
        path = os.path.join(FILES, 'A', 'TX%04d.PCX' % tid)
        tex = g.texture(path)[0] if os.path.exists(path) else None
        pos = bytearray(); uvb = bytearray(); col = bytearray(); n = 0
        for idx, uv, sh in quads:
            # quad corners are in 'Z' order (p1,p2,p3,p4 -> outline p1,p2,p4,p3): two triangles on that outline
            for tri in ((0, 1, 3), (0, 3, 2)):          # outline a,b,d,c
                for k in tri:
                    pos += struct.pack('<3f', *vs[idx[k]])
                    uvb += struct.pack('<2f', *uv[k])
                    col += struct.pack('<3f', sh[k], sh[k], sh[k])
                    n += 1
        xs = [struct.unpack_from('<3f', pos, i * 12) for i in range(n)]
        mn = [min(q[k] for q in xs) for k in range(3)]
        mx = [max(q[k] for q in xs) for k in range(3)]
        attrs = {'POSITION': g.acc(bytes(pos), 5126, n, 'VEC3', 34962, mn, mx),
                 'COLOR_0': g.acc(bytes(col), 5126, n, 'VEC3', 34962)}
        if tex is not None:
            attrs['TEXCOORD_0'] = g.acc(bytes(uvb), 5126, n, 'VEC2', 34962)
        name = 'TX%04d' % tid if tex is not None else 'missing_TX%04d' % tid
        g.meshes.append({'name': name, 'primitives': [{
            'attributes': attrs, 'material': g.material(name, tex, FALLBACK)}]})
        g.nodes.append({'name': name, 'mesh': len(g.meshes) - 1})
    placed = place_objects(g, t, vs, local)      # OBJnnnn nodes (instanced geometry, cached per model)
    markers = place_markers(g, sq_path, t, vs)
    out = os.path.join(out_dir, os.path.basename(os.path.dirname(sq_path)) + '.glb')
    g.save(out)
    return out, len(t['polys']), len(g.textures), 'objects', placed, 'markers', markers


if __name__ == '__main__':
    os.makedirs(sys.argv[1], exist_ok=True)
    for p in sys.argv[2:]:
        print(*convert(p, sys.argv[1]))
