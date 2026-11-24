"""Minimal LightWave LWOB reader (FILES/B/OBJxxxx.LWO, original source models of the cars).
Chunks: SRFS (surface names), PNTS (big-endian float xyz), POLS (nverts, indices, signed surface id,
negative id = followed by detail polygon count), SURF (per-surface sub-chunks, kept raw)."""
import struct


def read_lwob(path):
    d = open(path, 'rb').read()
    if d[:4] != b'FORM' or d[8:12] != b'LWOB':
        raise ValueError('not LWOB')
    end = 8 + struct.unpack_from('>I', d, 4)[0]
    pos = 12
    out = {'surfaces': [], 'points': [], 'polys': [], 'surf': {}}
    while pos + 8 <= min(end, len(d)):
        tag = d[pos:pos + 4]
        n = struct.unpack_from('>I', d, pos + 4)[0]
        body = d[pos + 8:pos + 8 + n]
        if tag == b'SRFS':
            out['surfaces'] = [s.decode('latin1') for s in body.split(b'\0') if s]
        elif tag == b'PNTS':
            out['points'] = [struct.unpack_from('>3f', body, i * 12) for i in range(n // 12)]
        elif tag == b'POLS':
            i = 0
            while i + 2 <= n:
                nv = struct.unpack_from('>H', body, i)[0]
                idx = struct.unpack_from('>%dH' % nv, body, i + 2)
                sid = struct.unpack_from('>h', body, i + 2 + 2 * nv)[0]
                i += 4 + 2 * nv
                if sid < 0:                      # detail polygons follow
                    sid = -sid
                    i += 2 + 0                   # detail count word
                out['polys'].append((idx, sid - 1))
        elif tag == b'SURF':
            name = body[:body.index(b'\0')].decode('latin1')
            out['surf'][name] = body
        pos += 8 + n + (n & 1)
    return out


if __name__ == '__main__':
    import sys, collections
    o = read_lwob(sys.argv[1])
    print(len(o['points']), 'points', len(o['polys']), 'polys')
    c = collections.Counter(s for _, s in o['polys'])
    for i, s in enumerate(o['surfaces']):
        print(i, s, c.get(i, 0))
