"""How a terrain face of an RCX course is textured.

A face record (64 bytes) is one quad with up to TWO texture layers (the RC2000 decompilation, race/course_faces.rs:
face_materials, face_texture_coordinates, draw_face, and scene/draw_polygon.rs: the polygon modes):

    byte 0x00  kind of layer A: bits 0..2 = orientation, bit 3 = the face has a back side
    byte 0x01  tile slot of layer A (0 = no layer A)
    byte 0x14  four corner lights            (the baked shade, our vertex colour)
    byte 0x20  kind of layer B: bits 0..2 = orientation
    byte 0x21  tile slot of layer B (0 = no layer B)
    byte 0x22  four corner weights of layer B, 0..255

  * orientation: corner k takes the TEXTINFO corner word ORDER[orientation][k] of its tile.
  * only A: A.  only B: B, with the corner weights as its opacity.  A and B: A, then B over it, weighted by
    texture alpha x corner weight.  HOW it goes over is the tile of B: TEXTINFO byte 0x24 (RAL.EXE copies it to slot
    byte +0x2C; 0x4529fd looks it up in the table 3, 5, 4, 0 at 0x63d950; the draw states are set in 0x44b7ba):
        0  mode 3  alpha blend                            A*(1-w) + B*w
        1  mode 5  gloss    (SRC=ZERO, DEST=INVSRCCOLOR)  A*(1 - B*w): B darkens A by its own colour
        2  mode 4  additive (SRC=SRCALPHA, DEST=ONE)      A + B*w
    (kind 3 -> mode 0 does not occur in the shipped data).  The vertex shade multiplies the whole result.
    In mode 3 alone, when all four weights are 255 and TEXTINFO byte 0x26 of B's tile is 0, A is not drawn
    (RAL.EXE 0x44b6dd), so holes in B stay holes.
  * TEXTINFO byte 0x25, signed, > 0 (either layer; RAL.EXE 0x452ff5 and 0x45322d): the corner uv do not come from the
    tile but from the corner's position: page fraction = (x, z of the game) * 2^((byte & 3) + 2) / 256, less the whole
    pages of corner 0.  For the value 1 the picture repeats every 32 m.
  * the quad's outline is 0, 1, 3, 2; the game's triangles are (0, 1, 2) and (3, 2, 1): the diagonal is 1-2.

track_to_stj.py keeps the two pages and writes the mix in the material (two_layer_material.py).  Plain glTF cannot
blend two textures by corner weights, so for track_to_glb.py each distinct (layer A, layer B, weights, mode) is baked
once into an atlas page (Atlas); the bake interpolates per triangle, as the game does.  With A under it all three modes bake exactly (distance fog aside).
NOT REPRODUCED: B alone in mode 5 or 4 (water ripples, the coast's fading strip, some hedges and walls) darkens or
lights whatever is BEHIND it.  A material with a cut-out alpha cannot do that (tried: white or black patches), so
face_layers returns 'alpha' for those faces and they are drawn as B's own picture.
"""
import io
import math
import os
import struct

import numpy as np
from PIL import Image

from ro3_to_glb import pcx_png

ORDER = ((0, 1, 2, 3), (1, 0, 3, 2), (2, 3, 0, 1), (3, 2, 1, 0),
         (2, 0, 3, 1), (3, 1, 2, 0), (0, 2, 1, 3), (1, 3, 0, 2))
FULL_PAGE = (0x00000000, 0x0000FFFF, 0xFFFF0000, 0xFFFFFFFF)   # tile code without a TEXTINFO record
ATLAS, GUTTER = 1024, 2
MODES = {0: 'alpha', 1: 'gloss', 2: 'add'}     # TEXTINFO byte 0x24 of layer B's tile


def tile_corners(textinfo, code, order):
    """The four corners' (u, v) in pixels of the tile's page, for an orientation."""
    c = struct.unpack_from('<4I', textinfo, code * 128) if 0 <= code < 16000 else (0,) * 4
    if not any(c):
        c = FULL_PAGE
    return [((c[k] & 0xFFFF) / 256.0, (c[k] >> 16) / 256.0) for k in ORDER[order]]


def world_corners(code, mapping, points):
    """The corners' (u, v) in page pixels for a tile mapped by position (TEXTINFO byte 0x25 > 0). points = the four
    corners as the game stores them: (x, height, z) in metres."""
    size = 128 if 400 <= code >> 4 < 800 else 256
    k = (1 << ((mapping & 3) + 2)) / 256.0
    rows = [[points[c][axis] * k for c in range(4)] for axis in (0, 2)]
    return list(zip(*[[(f - math.floor(row[0])) * size for f in row] for row in rows]))


def _layer(code, kind, points, textinfo):
    """(tile code, [4 corner uv in page pixels], mapped by position)"""
    mapping = textinfo[code * 128 + 0x25] if 0 <= code < 16000 else 0
    if 0 < mapping < 128:                       # a signed byte in the game
        return code, world_corners(code, mapping, points), True
    return code, tile_corners(textinfo, code, kind & 7), False


def face_layers(p, tiles, textinfo, verts):
    """p = the 16 dwords of a face, verts = the course's vertices as read_track returns them. Returns
    (a, b, weights, mode): a and b are (tile code, [4 corner uv], mapped by position) or None, weights four values
    0..255, mode how b goes over a ('alpha', 'gloss' or 'add'); weights and mode only mean something with b. Layers
    that cannot be seen are dropped; a = b = None means the game draws nothing."""
    raw = struct.pack('<16I', *p)
    points = [verts[i] for i in p[1:5]]
    a = b = None
    mode = 'alpha'
    if raw[0x01]:
        a = _layer(tiles[raw[0x01]], raw[0x00], points, textinfo)
    if raw[0x21]:
        code = tiles[raw[0x21]]
        b = _layer(code, raw[0x20], points, textinfo)
        if 0 <= code < 16000:
            mode = MODES.get(textinfo[code * 128 + 0x24], 'alpha')
    w = tuple(raw[0x22:0x26])
    if b and not any(w):
        b = None
    if a and b and a[0] >> 4 == 0:      # texture group 0 is "no texture": nothing to bake under B
        a = None
    if not a:                           # NOT REPRODUCED (see the module text): gloss / add over what is behind
        mode = 'alpha'
        return a, b, w, mode
    # RAL.EXE 0x44b6dd, alpha blend only: all four weights full and byte 0x26 of B's tile zero -> A is not drawn at all
    if a and b and mode == 'alpha' and w == (255,) * 4 and 0 <= b[0] < 16000 and not textinfo[b[0] * 128 + 0x26]:
        a = None
    return a, b, w, mode


def needs_bake(a, b, weights, mode):
    """One layer drawn as it is needs no new picture."""
    return bool(b and (a or mode != 'alpha' or weights != (255,) * 4))


class Pages:
    """The game's texture pages TXnnnn.PCX as RGBA arrays (magenta colour key -> alpha 0)."""
    def __init__(self, folder):
        self.folder = folder
        self.done = {}

    def get(self, tid):
        if tid not in self.done:
            path = os.path.join(self.folder, 'TX%04d.PCX' % tid)
            img = None
            if tid and os.path.exists(path):
                img = np.array(Image.open(io.BytesIO(pcx_png(path)[0])).convert('RGBA'), float)
            self.done[tid] = img
        return self.done[tid]


def _corner_mix(values, n):
    """Four corner values over an n x n grid, corners 0, 1, 2, 3 at (0,0), (1,0), (0,1), (1,1): linear inside each of
    the game's two triangles, (0, 1, 2) and (3, 2, 1)."""
    t = (np.arange(n) + 0.5) / n
    s, r = np.meshgrid(t, t)            # s along x, r along y
    v = np.asarray(values, float)
    if v.ndim == 1:
        v = v[:, None]
    s, r = s[..., None], r[..., None]
    return np.where(s + r <= 1.0, v[0] + s * (v[1] - v[0]) + r * (v[2] - v[0]),
                    v[3] + (1 - s) * (v[2] - v[3]) + (1 - r) * (v[1] - v[3]))


def _sample(img, layer, n):
    """An n x n picture of the part of a page that a layer's corner uv span. A layer mapped by position repeats the
    page; a tile's own uv stay inside the rectangle of its corners."""
    corners = layer[1]
    xy = _corner_mix(corners, n) - 0.5
    h, w = img.shape[:2]
    x, y = xy[..., 0], xy[..., 1]
    if not layer[2]:
        c = np.asarray(corners, float)
        x = np.clip(x, c[:, 0].min(), min(w - 1, c[:, 0].max() - 0.5))
        y = np.clip(y, c[:, 1].min(), min(h - 1, c[:, 1].max() - 0.5))
        x, y = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
    fx, fy = (x - np.floor(x))[..., None], (y - np.floor(y))[..., None]
    x0, y0 = np.floor(x).astype(int) % w, np.floor(y).astype(int) % h
    if layer[2]:
        x1, y1 = (x0 + 1) % w, (y0 + 1) % h
    else:
        x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x1] * fx * (1 - fy) +
            img[y1, x0] * (1 - fx) * fy + img[y1, x1] * fx * fy)


def _outline(p):
    return p[1], p[2], p[4], p[3]


def ground_border(polys, verts):
    """The edges where the ground ends. The car only stands on faces without flag 0x80 (RC2000 decomp:
    physics/probe_ground.rs searches with mask 0x80; physics/ground_search.rs face_under_point), and leaving that
    area is what stops it: the game never tests the car against the other faces' geometry."""
    count = {}
    for p in polys:
        if p[0] & 0xFF >= 0x10 or (p[0] >> 16) & 0x80 or max(p[1:5]) >= len(verts):
            continue
        ring = _outline(p)
        for i in range(4):
            a, b = verts[ring[i]], verts[ring[i - 3]]
            if tuple(a) != tuple(b):
                e = frozenset((tuple(a), tuple(b)))
                count[e] = count.get(e, 0) + 1
    return {e for e, n in count.items() if n == 1}


def is_loose_card(p, verts, border, pages, a, b, weights, mode):
    """A cut-out panel the game's car can never hit: flagged as not ground, standing (steeper than 80 degrees, Z up),
    with no edge on the line where the ground ends, and with holes in its picture (overhanging leaves, bushes). Kept
    solid, such a panel is an invisible barrier in an engine that collides with every triangle."""
    if not (p[0] >> 16) & 0x80:
        return False
    q = np.array([verts[i] for i in p[1:5]], float)
    n = np.cross(q[1] - q[0], q[2] - q[0]) + np.cross(q[2] - q[3], q[1] - q[3])
    length = np.linalg.norm(n)
    if not length or abs(n[2]) / length > 0.17:
        return False
    ring = [tuple(verts[i]) for i in _outline(p)]
    if any(frozenset((ring[i], ring[i - 3])) in border for i in range(4)):
        return False
    return opaque_share(pages, a, b, weights, mode) < 0.85


def opaque_share(pages, a, b, weights, mode, n=32):
    """The share of a face's picture that is drawn solid (alpha >= 0.5)."""
    imgs = [pages.get(x[0] >> 4) for x in (a, b) if x]
    if any(i is None for i in imgs):
        return 1.0
    return float(np.mean(_compose(imgs, a, b, weights, mode, n)[..., 3] >= 127.5))


def _compose(imgs, a, b, weights, mode, n):
    """The n x n RGBA picture of a face's layers (imgs = their pages, A first)."""
    base = _sample(imgs[0], a, n) if a else None
    if not b:
        return base
    top = _sample(imgs[-1], b, n)
    wb = (_corner_mix(weights, n) / 255.0 if a or weights != (255,) * 4 else 1.0) * top[..., 3:] / 255.0
    colour = top[..., :3]
    if a:
        ab = base[..., 3:] / 255.0
        if mode == 'gloss':                    # the game: what is there x (1 - B's colour)
            alpha, rgb = ab, base[..., :3] * (1 - colour / 255.0 * wb)
        else:
            alpha = wb + ab * (1 - wb)
            over = colour * wb + base[..., :3] * ab * (1 if mode == 'add' else 1 - wb)
            rgb = np.where(alpha > 1e-6, over / np.maximum(alpha, 1e-6), base[..., :3])
    else:
        alpha, rgb = wb * np.ones_like(top[..., 3:]), colour
    return np.dstack([rgb, alpha * 255.0]).clip(0, 255)


class Atlas:
    """Baked tiles. add() returns (page name, [4 corner uv, 0..1 with v down]); save() writes the pages."""
    def __init__(self, pages):
        self.pages = pages
        self.done = {}
        self.sheets = {}       # tile size -> list of RGBA uint8 arrays
        self.used = {}         # tile size -> cells used
        self.cells = {}        # page name -> what each cell was baked from, in cell order

    def add(self, a, b, weights, mode):
        """a may be None (B alone with the weights as opacity). Returns None when a needed page is missing."""
        key = (a and (a[0], tuple(a[1]), a[2]), (b[0], tuple(b[1]), b[2]), weights, mode)
        if key not in self.done:
            self.done[key] = self._bake(a, b, weights, mode)
        return self.done[key]

    def _bake(self, a, b, weights, mode):
        layers = [x for x in (a, b) if x]
        imgs = [self.pages.get(x[0] >> 4) for x in layers]
        if any(i is None for i in imgs):
            return None
        spans = [(float(np.ptp(np.asarray(x[1]), axis=0).max()), x[2]) for x in layers]
        span = max(v for v, _world in spans)
        n = 256 if any(world and v > 192 for v, world in spans) else 128 if span > 80 else 64
        tile = _compose(imgs, a, b, weights, mode, n).round().astype(np.uint8)
        cell = n + 2 * GUTTER
        side = ATLAS // cell
        k = self.used.get(n, 0)
        self.used[n] = k + 1
        sheet, k = divmod(k, side * side)
        sheets = self.sheets.setdefault(n, [])
        if sheet == len(sheets):
            sheets.append(np.zeros((ATLAS, ATLAS, 4), np.uint8))
        x, y = (k % side) * cell, (k // side) * cell
        sheets[sheet][y:y + cell, x:x + cell] = np.pad(tile, ((GUTTER, GUTTER), (GUTTER, GUTTER), (0, 0)), 'edge')
        u0, v0 = (x + GUTTER) / ATLAS, (y + GUTTER) / ATLAS
        u1, v1 = u0 + n / ATLAS, v0 + n / ATLAS
        page = 'blend%d_%02d' % (n, sheet)
        self.cells.setdefault(page, []).append([
            a and [a[0], [[round(c, 3) for c in uv] for uv in a[1]]],
            [b[0], [[round(c, 3) for c in uv] for uv in b[1]]], list(weights), mode])
        return page, [(u0, v0), (u1, v0), (u0, v1), (u1, v1)]

    def images(self):
        """[(page name, PIL image)]"""
        return [('blend%d_%02d' % (n, k), Image.fromarray(s, 'RGBA'))
                for n, sheets in sorted(self.sheets.items()) for k, s in enumerate(sheets)]
