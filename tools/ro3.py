"""RO3 (RCX runtime model) reader.

Used for every model in FILES/A, FILES/B (cars) and FILES/S: OBJxxxx.RO3.  The game loads these
with the object loader at 0x425b1e (called through 0x4258c6 "reloadobjects"); ids 0x190..0x257
(OBJ0400..OBJ0599, the cars) get extra car-specific path handling.

File layout (little endian):
  header 44 bytes: u32 version(1), nverts, npolys, nnormals, 7 x 0
  verts   nverts x 3 x i32 (16.16 fixed)
  polys   npolys x { u32 n, u32 flags(lo16 = TXnnnn id), u32 f2, u32 id,
                     n x { u32 vidx, i32 u(16.16 px), i32 v(16.16 px), u32 w } }
  normals nnormals x 3 x f32

Field notes (what is known, what is not):
  * Coordinates are in the game's left-handed frame: callers flip z (see ro3_to_glb.py) for glTF.
  * poly 'f1' low 16 bits = texture page (FILES/<dir>/TXnnnn.PCX); the high 16 bits are render flags.
    Seen in the high half: bit 0x0100 / 'f2 == 0x700100' mark inner/headliner surfaces that should
    not be culled the same way as the outside body (see orient_polys in ro3_to_glb.py).
  * loop 'u','v' are PIXEL coordinates in the texture page, not 0..1 (divide by the PCX size).
  * loop 'w' indexes the normals table in car models (nnormals > 0).  In some non-car objects it
    holds something else (it can exceed nnormals) -> callers must range-check it before use.
  * poly 'id' is a running index (0..npolys-1) in every file checked; not used by the converters.
  * The FILES/B/*.LWO LightWave originals have the same polygon order, which is how part names
    (wheelLeftFront, body, driver, ...) are attached to polygons (see lwob.py, lwo_check.py).

TODO (for later work):
  * 'f2' meaning is only partly known (see above); decode it fully from the renderer (3D draw
    routines around 0x4cb995 and the poly sorter) before relying on it.
  * Damage meshes: the crack overlay uses texture TX1826; the damage model code (dam_*) is not
    decoded, so no deformation data is exported.
"""
import struct


def read_ro3(path):
    """Return (verts, polys, normals).

    verts   : list of (x, y, z) floats (16.16 fixed converted to float)
    polys   : list of dicts {'n', 'f1', 'f2', 'id', 'loops': [(vidx, u_px, v_px, w), ...]}
    normals : list of (x, y, z) floats (empty for models without a normals table)
    """
    d = open(path, 'rb').read()
    ver, nv, npol, nn = struct.unpack_from('<4I', d, 0)
    o = 44                                   # header is 11 dwords; the last 7 are zero in every file seen
    verts = [tuple(c / 65536.0 for c in struct.unpack_from('<3i', d, o + i * 12)) for i in range(nv)]
    o += nv * 12
    polys = []
    for _ in range(npol):
        n, f1, f2, pid = struct.unpack_from('<4I', d, o)
        o += 16
        loops = []
        for _k in range(n):
            idx, u, v, w = struct.unpack_from('<IiiI', d, o)
            o += 16
            loops.append((idx, u / 65536.0, v / 65536.0, w))
        polys.append({'n': n, 'f1': f1, 'f2': f2, 'id': pid, 'loops': loops})
    normals = [struct.unpack_from('<3f', d, o + i * 12) for i in range(nn)]
    # the file must be consumed exactly; if this ever fails the layout above has a variant we missed
    assert o + nn * 12 == len(d), (o + nn * 12, len(d))
    return verts, polys, normals
