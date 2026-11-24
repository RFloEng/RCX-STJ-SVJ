"""Convert LightWave LWOB (RCX .LWO) models to Wavefront OBJ + MTL.

Usage: python lwo2obj.py <out_dir> <file.lwo> [more.lwo ...]

LightWave is Y-up / left-handed; output is Z-up / right-handed (Blender):
  blender = (x, -z, y)  [a proper rotation, so winding is preserved as-is
  after reversing LW's clockwise order]
"""
import os
import struct
import sys


def read_chunks(d, off, end):
    while off + 8 <= end:
        tag = d[off:off + 4].decode('latin1')
        ln = struct.unpack('>I', d[off + 4:off + 8])[0]
        yield tag, off + 8, ln
        off += 8 + ln + (ln & 1)


def cstr(d, o):
    e = d.index(b'\0', o)
    return d[o:e].decode('latin1'), e + 1 + ((e + 1 - o) & 1)


def parse_lwob(path):
    d = open(path, 'rb').read()
    if d[:4] != b'FORM' or d[8:12] != b'LWOB':
        raise ValueError('not an LWOB file: ' + path)
    end = 8 + struct.unpack('>I', d[4:8])[0]
    surfs, pts, polys, mats = [], [], [], {}
    for tag, o, ln in read_chunks(d, 12, end):
        if tag == 'SRFS':
            surfs = [s.decode('latin1') for s in d[o:o + ln].split(b'\0') if s]
        elif tag == 'PNTS':
            pts = [struct.unpack('>fff', d[o + i * 12:o + i * 12 + 12]) for i in range(ln // 12)]
        elif tag == 'POLS':
            p = o
            while p < o + ln:
                nv = struct.unpack('>H', d[p:p + 2])[0]
                idx = struct.unpack('>%dH' % nv, d[p + 2:p + 2 + nv * 2])
                surf = struct.unpack('>h', d[p + 2 + nv * 2:p + 4 + nv * 2])[0]
                p += 4 + nv * 2
                if surf < 0:  # detail polygons follow
                    nd = struct.unpack('>H', d[p:p + 2])[0]
                    p += 2
                    for _ in range(nd):
                        m = struct.unpack('>H', d[p:p + 2])[0]
                        p += 2 + m * 2 + 2
                    surf = -surf
                polys.append((idx, surf - 1))
        elif tag == 'SURF':
            name, p = cstr(d, o)
            m = {'color': (0.78, 0.78, 0.78), 'double': False}
            while p + 6 <= o + ln:
                st = d[p:p + 4].decode('latin1')
                sl = struct.unpack('>H', d[p + 4:p + 6])[0]
                body = d[p + 6:p + 6 + sl]
                if st == 'COLR':
                    m['color'] = tuple(c / 255.0 for c in body[:3])
                elif st == 'FLAG':
                    m['double'] = bool(struct.unpack('>H', body[:2])[0] & 0x100)
                elif st == 'TRAN':
                    m['trans'] = struct.unpack('>H', body[:2])[0] / 256.0
                p += 6 + sl
            mats[name] = m
    return surfs, pts, polys, mats


def write_obj(path, out_dir):
    surfs, pts, polys, mats = parse_lwob(path)
    base = os.path.splitext(os.path.basename(path))[0]
    with open(os.path.join(out_dir, base + '.mtl'), 'w') as f:
        for name in surfs:
            m = mats.get(name, {'color': (0.78, 0.78, 0.78)})
            f.write('newmtl %s\nKd %.4f %.4f %.4f\n' % ((name,) + m['color']))
            if m.get('trans'):
                f.write('d %.3f\n' % (1.0 - m['trans']))
            f.write('\n')
    with open(os.path.join(out_dir, base + '.obj'), 'w') as f:
        f.write('mtllib %s.mtl\n' % base)
        for x, y, z in pts:
            f.write('v %.6f %.6f %.6f\n' % (x, -z, y))
        by_surf = {}
        for idx, s in polys:
            if len(idx) >= 3:
                by_surf.setdefault(s, []).append(idx)
        for s, plist in sorted(by_surf.items()):
            name = surfs[s] if 0 <= s < len(surfs) else 'Default'
            f.write('o %s\ng %s\nusemtl %s\n' % (name, name, name))
            for idx in plist:
                # LW is clockwise; the (x,-z,y) rotation keeps that, so reverse
                f.write('f ' + ' '.join(str(i + 1) for i in reversed(idx)) + '\n')
    return len(pts), len(polys)


if __name__ == '__main__':
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    for p in sys.argv[2:]:
        try:
            print(os.path.basename(p), *write_obj(p, out))
        except Exception as e:
            print('FAIL', p, e)
