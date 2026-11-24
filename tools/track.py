"""RCX track (FILES/LEVELS/LEVELnn/CDAT00C.SQ, after SQSL decompression) reader.

The game loads it in 0x44538a ("locacurrcourse"): first the raw dev copy srclvls\\levelNN\\cdat00c,
else files\\levels\\levelNN\\cdat00c.sq, which starts with 'SQSL' and goes through the decompressor
(0x41b9bb, ported in sqsl.py).  After loading, the 64-dword header is copied to 0x5ccc94, the
polygon records live at [0x5ccc3c] (64 bytes each) and the vertices at [0x5ccc40] (12 bytes each).

Layout (dwords, little endian):
  [0..63]   header: 'sout', ntable(71), npolys, nverts, ...
  [64..]    ntable x 8 dwords   (lookup table, meaning TBD)
  then      npolys x 16 dwords  { flags, v0, v1, v2, v3, colour, p6, p7, p8, p9, p10, p11..p15 }
  then      nverts x 3 x i32    (16.16 fixed; x, y(height), z)

Polygon record fields (index = dword number inside the 16-dword record):
  p[0]  flags: low byte 0xFF = SPECIAL record (see below), otherwise a normal terrain quad.
        bits 8..15 = slot: terrain -> LOCALDAT[64 + slot] = tile code (texture TX{code>>4},
        UV record TEXTINFO[code]); special object records -> LOCALDAT[320 + slot] = model id.
  p[1..4]  vertex indices of the quad, in 'Z' order: the outline is p1, p2, p4, p3.
  p[5]  per-corner shade, 4 bytes (one per corner), used as vertex colour.
  p[6]  for special records: 16 = linked point of a chain (road line / road edge), else object size.
  p[2], p[3], p[4], p[7]  for object records: yaw (1/256 turn), pitch?/roll?, height offset 16.16.
  p[9]  byte 2 (bits 16..23, = record byte 0x26): 1 = first point of the road line, 2 = checkpoint.
  p[10] bytes 0..3 (= record bytes 0x28..0x2b): four PACE-NOTE slots: 99 = zone open, 100 = zone
        close, 98 = other marker, 1..97 = note code (code -> speech file mapping NOT decoded).
        The game reads them in 0x4417fd ("init pacenotes") via the offset table at 0x614c94
        (28 29 2a 2b 19 1a 1b 20); bytes 0x19..0x1b and 0x20 were always 0 in the levels checked.
  p[8], p[11..15]  runtime scratch in the shipped file (zero) or not yet decoded.
  Special slots on the road line: 0x3e / 0x3d = left / right line chain, 0x3c / 0x38 = markers
  whose purpose is unknown (only their nearest-point links are computed, see course_ref.py).

TODO (for later work):
  * The 71 x 8 dword 'table' is not decoded (probably a spatial lookup used by collision).
  * Header words other than the counts are unknown.
  * Decode what slots 0x3c / 0x38 mean (checkpoints? timing lines?) and how pace-note codes map to
    FILES/SPEECH/<F|M>/{INSTRUCT,GENERAL,DISTANCE,SUFFIX}.
"""
import struct
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqsl import sqsl_decode


def s32(x):
    """Reinterpret an unsigned 32-bit word as signed."""
    return x - (1 << 32) if x >> 31 else x


def read_track(sq_path):
    """Return {'header', 'table', 'polys', 'verts'}; polys are tuples of 16 unsigned ints,
    verts are (x, y, z) floats in the game's left-handed frame (callers negate z for glTF)."""
    dec, _ = sqsl_decode(open(sq_path, 'rb').read())
    w = struct.unpack('<%dI' % (len(dec) // 4), dec)
    ntab, npoly, nvert = w[1], w[2], w[3]
    table = [w[64 + i * 8:72 + i * 8] for i in range(ntab)]
    po = 64 + ntab * 8
    polys = [w[po + i * 16:po + i * 16 + 16] for i in range(npoly)]
    vo = po + npoly * 16
    # the file is consumed exactly; a failure here means the header counts or layout assumption is wrong
    assert vo + nvert * 3 == len(w), (vo + nvert * 3, len(w))
    verts = [tuple(s32(x) / 65536.0 for x in w[vo + i * 3:vo + i * 3 + 3]) for i in range(nvert)]
    return {'header': w[:64], 'table': table, 'polys': polys, 'verts': verts}
