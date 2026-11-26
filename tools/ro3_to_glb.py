"""Convert RCX .RO3 (+ optional .LWO for part names) to textured GLB.

Usage: python ro3_to_glb.py <out_dir> <file.RO3> [more.RO3 ...]
Textures TXnnnn.PCX are looked up next to the RO3 file.
The game is left-handed: Z is negated for glTF (right-handed, Y-up) and the
polygon order is reversed so faces are counter-clockwise (front-facing).

Pipeline overview (read this first when extending the exporter):
  1. ro3.read_ro3()          -> vertices, polygons (n-gons with per-corner pixel UVs + normal index), normals.
  2. lwo2obj.parse_lwob()    -> surface name of every polygon (same polygon order as the RO3), used to split
                                the model into named parts (body, wheelLeftFront, driver, RollCage, ...).
  3. orient_polys()          -> per-polygon flip flags so back-face culling shows the outside of the shell.
  4. add_model()             -> groups triangles by (part, texture page) and appends meshes/materials to a Glb.
  5. Glb.save()              -> writes the binary glTF 2.0 container (no external libraries needed).
track_to_glb.py reuses Glb and add_model() to place the objects of a track, so keep their signatures stable.

Known gaps / ideas for later:
  * Wheels are exported in place (no pivot at the axle) -> to animate them, re-centre each wheelXxx part
    on its own centroid and move the node translation there.
  * Textures are PCX pages with a magenta colour key; mip-mapping/filtering flags of the game are not
    represented (the sampler is NEAREST on purpose, like the original software/Direct3D look).
  * Poly flags 'f1 >> 16' and 'f2' are only partly understood (see ro3.py); transparency/additive
    flags may exist that are currently ignored.
  * Per-vertex colour is not stored in RO3; lighting comes from the normals table only.
"""
import io
import json
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ro3 import read_ro3
from lwo2obj import parse_lwob
from car_names import model_names


def pcx_png(path):
    """PCX -> RGBA PNG. (255,0,255) (+ near-magenta fringe) is the colour key.
    TX1826 is the car glass sheet: its pure blue (0,0,255) half is the glass
    tint -> translucent dark blue. Transparent pixels get the colour of the
    nearest opaque pixels, so viewers that ignore alpha or filter linearly do
    not show black/pink boxes."""
    import numpy as np
    im = Image.open(path).convert('RGBA')
    a = np.array(im)
    r, g_, b = a[..., 0].astype(int), a[..., 1].astype(int), a[..., 2].astype(int)
    key = (r >= 225) & (b >= 225) & (g_ <= 40)
    glass = np.zeros_like(key)
    if os.path.basename(path).upper() == 'TX1826.PCX':
        glass = (r <= 10) & (g_ <= 10) & (b >= 245)
        a[glass] = (12, 20, 40, 110)
    a[key, 3] = 0
    # bleed opaque colours into fully transparent pixels (8 passes of 3x3 mean)
    filled = a[..., 3] > 0
    rgb = a[..., :3].astype(float)
    for _ in range(8):
        if filled.all():
            break
        w = filled.astype(float)
        acc = np.zeros_like(rgb); cnt = np.zeros(w.shape)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                acc += np.roll(np.roll(rgb * w[..., None], dy, 0), dx, 1)
                cnt += np.roll(np.roll(w, dy, 0), dx, 1)
        new = (~filled) & (cnt > 0)
        rgb[new] = acc[new] / cnt[new][:, None]
        filled = filled | new
    out = a.copy()
    tr = out[..., 3] == 0
    out[tr, :3] = rgb[tr].astype(np.uint8)
    b_ = io.BytesIO()
    Image.fromarray(out).save(b_, 'PNG')
    return b_.getvalue(), im.size


class Glb:
    """Tiny glTF 2.0 writer (single .glb file, one binary buffer).

    Usage: build meshes with acc()/material()/texture(), append {'mesh': i} dicts to `nodes`
    (children are allowed through the 'children' key) and call save().  Everything that is not
    referenced as somebody's child becomes a scene root (roots()).
    Caches: textures by file path (tex_by_id) and materials by (name, texture, alpha, cull)
    (mat_by_key) so a texture page used by many parts/objects is embedded only once."""

    def __init__(self):
        self.bin = bytearray()
        self.views, self.accs, self.images, self.textures = [], [], [], []
        self.materials, self.meshes, self.nodes = [], [], []
        self.mat_by_key, self.tex_by_id = {}, {}
        self.extras = None
        self.hidden_meshes = set()

    def view(self, data, target=None):
        """Append `data` to the binary buffer (4-byte aligned) and return the new bufferView index.
        target: 34962 = ARRAY_BUFFER (vertex attributes), 34963 = ELEMENT_ARRAY_BUFFER, None = images."""
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {'buffer': 0, 'byteOffset': len(self.bin), 'byteLength': len(data)}
        if target:
            v['target'] = target
        self.bin += data
        self.views.append(v)
        return len(self.views) - 1

    def acc(self, data, ctype, count, ctypename, target, mn=None, mx=None):
        """Add an accessor.  ctype: 5126 = float32, 5123 = uint16, 5125 = uint32;
        ctypename: 'SCALAR' | 'VEC2' | 'VEC3'.  POSITION accessors MUST pass min/max (glTF rule).
        Meshes here are non-indexed triangle lists (one accessor entry per triangle corner),
        which keeps per-corner UVs/normals simple at the cost of file size."""
        a = {'bufferView': self.view(data, target), 'componentType': ctype,
             'count': count, 'type': ctypename}
        if mn is not None:
            a['min'], a['max'] = mn, mx
        self.accs.append(a)
        return len(self.accs) - 1

    def texture(self, path):
        if path in self.tex_by_id:
            return self.tex_by_id[path]
        png, size = pcx_png(path)
        return self.texture_png(path, png, size)

    def texture_png(self, path, png, size):
        """The same for a picture already in memory; `path` is only the cache key."""
        if path in self.tex_by_id:
            return self.tex_by_id[path]
        self.images.append({'bufferView': self.view(png), 'mimeType': 'image/png'})
        self.textures.append({'source': len(self.images) - 1, 'sampler': 0})
        self.tex_by_id[path] = (len(self.textures) - 1, size)
        return self.tex_by_id[path]

    def material(self, name, tex=None, color=(0.78, 0.78, 0.78), alpha='MASK', cull=False):
        """Return a material index (cached).  alpha: 'MASK' for the magenta colour-key textures,
        'BLEND' for the car glass; cull=True means single-sided (only safe when every polygon's
        winding is consistent, i.e. after orient_polys); cull=False is double-sided."""
        key = (name, tex, alpha, cull)
        if key not in self.mat_by_key:
            pbr = {'metallicFactor': 0.0, 'roughnessFactor': 0.9}
            if tex is not None:
                pbr['baseColorTexture'] = {'index': tex}
            else:
                pbr['baseColorFactor'] = list(color) + [1.0]
            self.materials.append({'name': name, 'pbrMetallicRoughness': pbr, 'doubleSided': not cull,
                              'alphaMode': alpha if tex is not None else 'OPAQUE'})
            self.mat_by_key[key] = len(self.materials) - 1
        return self.mat_by_key[key]

    def mesh_node(self, name, mi, **extra):
        n = {'name': name, 'mesh': mi}
        n.update(extra)
        if mi in self.hidden_meshes:   # damage overlays start hidden
            n['extensions'] = {'KHR_node_visibility': {'visible': False}}
            self.uses_visibility = True
        return n

    def roots(self):
        kids = {c for n in self.nodes for c in n.get('children', [])}
        return [i for i in range(len(self.nodes)) if i not in kids]

    def save(self, path):
        """Serialise to a .glb: 12-byte header, JSON chunk (0x4E4F534A), BIN chunk (0x004E4942).
        NEAREST sampler (9728) is deliberate: the source textures are low-resolution pixel art."""
        doc = {'asset': {'version': '2.0', 'generator': 'rcx-tools'},
               'scene': 0, 'scenes': [{'nodes': self.roots()}],
               'nodes': self.nodes, 'meshes': self.meshes, 'materials': self.materials,
               'accessors': self.accs, 'bufferViews': self.views,
               'buffers': [{'byteLength': len(self.bin)}],
               'samplers': [{'magFilter': 9728, 'minFilter': 9728, 'wrapS': 10497, 'wrapT': 10497}]}
        if getattr(self, 'uses_visibility', False):
            doc['extensionsUsed'] = ['KHR_node_visibility']
        if self.extras:
            doc['scenes'][0]['name'] = self.extras['name']
            doc['scenes'][0]['extras'] = self.extras
        if self.images:
            doc['images'], doc['textures'] = self.images, self.textures
        js = json.dumps(doc, separators=(',', ':')).encode()
        js += b' ' * (-len(js) % 4)
        while len(self.bin) % 4:
            self.bin.append(0)
        out = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(self.bin))
        out += struct.pack('<II', len(js), 0x4E4F534A) + js
        out += struct.pack('<II', len(self.bin), 0x004E4942) + bytes(self.bin)
        open(path, 'wb').write(out)


def winding_flipped(lw_verts, loops, normals):
    """True when the stored vertex order disagrees with the stored vertex normals.

    Some polygons were authored with the opposite winding; the per-vertex
    normals (loop field w) are the reference for which side is the front.

    NOTE: superseded by orient_polys() (a single-polygon vote is unreliable for nearly flat faces);
    kept only as a simpler reference implementation, nothing calls it any more."""
    # Newell's method: (fx, fy, fz) is the (unnormalised) face normal of the polygon in LW space
    fx = fy = fz = 0.0
    for k in range(len(loops)):
        a = lw_verts[loops[k][0]]
        b = lw_verts[loops[(k + 1) % len(loops)][0]]
        fx += (a[1] - b[1]) * (a[2] + b[2])
        fy += (a[2] - b[2]) * (a[0] + b[0])
        fz += (a[0] - b[0]) * (a[1] + b[1])
    score = 0
    for l in loops:
        n = normals[l[3]]  # z already negated -> undo for the LW-space test
        d = fx * n[0] + fy * n[1] - fz * n[2]
        score += (d > 1e-12) - (d < -1e-12)
    return score < 0


def orient_polys(lw_verts, polys, normals):
    """Per-polygon flip decisions (True = reverse the stored vertex order).

    Each polygon votes with its vertex normals (strength = mean normal . unit face normal);
    the votes are then made consistent over the mesh: polygons that share an edge must walk
    it in opposite directions, so a whole connected shell is flipped or kept together, by the
    sum of its votes. This is what back-face culling needs; a weak or wrong vote on a single
    polygon (nearly flat faces, smoothed normals) no longer hides it.

    Steps: (1) vote[i] = mean(vertex normal . unit Newell face normal) -> sign says which way the stored
    order faces; (2) build an edge table (edge -> [(poly, direction)]); (3) walk each connected component
    starting from its most confident polygon, giving neighbours the orientation that makes the shared
    edge run in opposite directions; (4) flip the whole component if the weighted votes disagree.
    Edges used by more or fewer than two polygons (open borders, T-junctions) stop the propagation,
    which is why separate shells of one model can end up with different orientation.
    Tuning ideas: weight votes by polygon area; treat f2 == 0x700100 surfaces (inner/headliner) as
    their own component so they never fight the outer body."""
    n = len(polys)
    vote = [0.0] * n
    for i, q in enumerate(polys):
        L = q['loops']
        if len(L) < 3:
            continue
        fx = fy = fz = 0.0
        for k in range(len(L)):
            a = lw_verts[L[k][0]]
            b = lw_verts[L[(k + 1) % len(L)][0]]
            fx += (a[1] - b[1]) * (a[2] + b[2])
            fy += (a[2] - b[2]) * (a[0] + b[0])
            fz += (a[0] - b[0]) * (a[1] + b[1])
        ln = (fx * fx + fy * fy + fz * fz) ** 0.5
        if ln < 1e-9:
            continue        # degenerate polygon (zero area): no vote, it follows its neighbours
        t = 0.0
        for l in L:
            nn = normals[l[3]]
            t += (fx * nn[0] + fy * nn[1] - fz * nn[2]) / ln
        vote[i] = t / len(L)
    edges = {}
    for i, q in enumerate(polys):
        L = q['loops']
        if len(L) < 3:
            continue
        for k in range(len(L)):
            a, b = L[k][0], L[(k + 1) % len(L)][0]
            if a != b:
                edges.setdefault((min(a, b), max(a, b)), []).append((i, 1 if a < b else -1))
    orient = [0] * n
    flip = [False] * n
    for seed in sorted(range(n), key=lambda i: -abs(vote[i])):
        if orient[seed] or len(polys[seed]['loops']) < 3:
            continue
        orient[seed] = 1
        comp = [seed]
        stack = [seed]
        while stack:
            i = stack.pop()
            L = polys[i]['loops']
            for k in range(len(L)):
                a, b = L[k][0], L[(k + 1) % len(L)][0]
                if a == b:
                    continue
                users = edges[(min(a, b), max(a, b))]
                if len(users) != 2:
                    continue
                d_i = (1 if a < b else -1) * orient[i]
                for j, d_j in users:
                    if j == i or orient[j]:
                        continue
                    orient[j] = 1 if d_j == -d_i else -1
                    comp.append(j)
                    stack.append(j)
        if sum(vote[i] * orient[i] for i in comp) < 0:
            for i in comp:
                orient[i] = -orient[i]
        for i in comp:
            flip[i] = orient[i] < 0
    return flip


def add_model(g, ro3_path, include_damage=False):
    """Append the meshes of one RO3 model to Glb `g`; returns [(part_name, mesh_index)].
    Cached per path so a model used many times is stored once."""
    cache = g.__dict__.setdefault('model_cache', {})
    if ro3_path in cache:
        return cache[ro3_path]
    # Returned list is [(mesh_name, mesh_index)]: one mesh per named part (LWO surface); a single 'mesh'
    # part is used when no LWO exists or its polygon count differs from the RO3.
    # include_damage=True would add the crack overlay of TX1826 as a separate (hidden-flagged) mesh;
    # Blender ignores KHR_node_visibility, so the default exports clean glass only.
    base = os.path.splitext(ro3_path)[0]
    verts, polys, normals = read_ro3(ro3_path)
    lw_verts = verts                                  # untouched game-space vertices, used for winding tests
    verts = [(x, y, -z) for x, y, z in verts]         # glTF space: mirror Z (left-handed -> right-handed)
    normals = [(x, y, -z) for x, y, z in normals]
    # loop field w indexes the normal table in car models; in some objects it
    # is something else (out of range) -> fall back to flat shading there.
    normals_lw = normals
    has_normals = bool(normals) and all(l[3] < len(normals) for q in polys for l in q['loops'])
    names = None   # per-polygon part name from the LightWave original (only trusted when counts match)
    if os.path.exists(base + '.LWO'):
        surfs, _pts, lp, _m = parse_lwob(base + '.LWO')
        if len(lp) == len(polys):
            names = [surfs[s] if 0 <= s < len(surfs) else 'Default' for _i, s in lp]
    flips = orient_polys(lw_verts, polys, normals_lw) if has_normals else None
    folder = os.path.dirname(ro3_path)
    groups = {}  # (part, texid) -> list of (idx, u, v) triangles
    for i, p in enumerate(polys):
        if p['n'] < 3:
            continue
        part = names[i] if names else 'mesh'
        tid = p['f1'] & 0xFFFF          # texture page number -> TXnnnn.PCX next to the RO3
        loops = [(l[0], l[1], l[2], l[3]) for l in p['loops']]
        if flips and flips[i]:
            loops = loops[::-1]
        keys = [(part, tid, None)]
        if tid == 1826:   # shared car glass sheet: glass tint (top half) + crack overlay
            keys = [(part, tid, 'glass')] + ([(part, tid, 'cracks')] if include_damage else [])
        for key in keys:
            tris = groups.setdefault(key, [])
            lp = loops if key[2] != 'glass' else [(a, u, max(0.0, min(v - 128.0, 126.0)), w) for a, u, v, w in loops]
            # fan triangulation; the (0, k+1, k) order reverses the stored winding because of the Z mirror
            for k in range(1, len(lp) - 1):
                tris += [lp[0], lp[k + 1], lp[k]]
    by_part = {}
    for (part, tid, variant), tris in groups.items():
        by_part.setdefault((part, variant == 'cracks'), []).append((tid, variant, tris))
    result = []
    for (part, overlay), prims in sorted(by_part.items()):
        gprims = []
        for tid, variant, tris in prims:
            tpath = os.path.join(folder, 'TX%04d.PCX' % tid)
            tex, size = (g.texture(tpath) if os.path.exists(tpath) else (None, (256, 256)))
            pos = b''.join(struct.pack('<3f', *verts[t[0]]) for t in tris)
            # UVs are stored in pixels -> normalise by the PCX size.  glTF's V axis points down like the
            # PCX rows, so no flip is needed.  (Textures missing on disk fall back to a 256x256 guess.)
            uvs = b''.join(struct.pack('<2f', t[1] / size[0], t[2] / size[1]) for t in tris)
            nrm = b''.join(struct.pack('<3f', *normals[t[3]]) for t in tris) if has_normals else None
            xs = [verts[t[0]] for t in tris]
            mn = [min(v[k] for v in xs) for k in range(3)]
            mx = [max(v[k] for v in xs) for k in range(3)]
            a_pos = g.acc(pos, 5126, len(tris), 'VEC3', 34962, mn, mx)
            a_uv = g.acc(uvs, 5126, len(tris), 'VEC2', 34962)
            attrs = {'POSITION': a_pos, 'TEXCOORD_0': a_uv}
            if nrm:
                attrs['NORMAL'] = g.acc(nrm, 5126, len(tris), 'VEC3', 34962)
            mname = '%s_TX%d%s' % (part, tid, '_' + variant if variant else '')
            gprims.append({'attributes': attrs,
                           'material': g.material(mname, tex, alpha='BLEND' if variant == 'glass' else 'MASK', cull=bool(flips is not None))})
        mname = part + ('_damage_cracks' if overlay else '')
        g.meshes.append({'name': mname, 'primitives': gprims})
        if overlay:
            g.hidden_meshes.add(len(g.meshes) - 1)
        result.append((mname, len(g.meshes) - 1))
    cache[ro3_path] = result
    return result


def convert(ro3_path, out_dir):
    base = os.path.splitext(ro3_path)[0]
    folder = os.path.dirname(ro3_path)
    g = Glb()
    for part, mi in add_model(g, ro3_path):
        g.nodes.append(g.mesh_node(part, mi))
    npolys = len(read_ro3(ro3_path)[1])
    out = os.path.join(out_dir, os.path.basename(base) + '.glb')
    mid = int(os.path.basename(base)[3:7]) if os.path.basename(base)[:3] == 'OBJ' else None
    info = model_names().get(mid) if os.path.basename(folder) == 'B' else None
    if info:
        g.extras = {'name': info[0], 'carIndex': info[1], 'nameSource': info[2], 'role': info[3]}
    g.save(out)
    return out, npolys, len(g.textures)


if __name__ == '__main__':
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    for p in sys.argv[2:]:
        try:
            print(*convert(p, out_dir))
        except Exception as e:
            print('FAIL', p, repr(e))
