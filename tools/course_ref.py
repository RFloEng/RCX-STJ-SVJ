"""Reference (Python) port of the course analysis in RAL.EXE 0x445501..0x445a7f.
Used to produce golden values for the Rust crate."""
import math, struct, sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from track import read_track

def fnv(b):
    h = 0xcbf29ce484222325
    for x in b: h = ((h ^ x) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h

def i32(x): return ((x + 2**31) % 2**32) - 2**31

def raw_pos(V, P, rec):
    v = V[P[rec][1]]
    return round(v[0] * 65536), round(v[2] * 65536)

def analyse(path, side):
    t = read_track(path); P = t['polys']; V = t['verts']
    slot = {0: 0x3e, 1: 0x3d}.get(side, 0)
    lst = [i for i, q in enumerate(P) if q[0] & 0xff == 0xff and ((q[0] >> 8) & 0xff) == slot]
    n = len(lst); m = 0
    if n >= 2:
        st = next((e for e, i in enumerate(lst) if ((P[i][9] >> 16) & 0xff) == 1), None)
        if st is not None:
            lst[0], lst[st] = lst[st], lst[0]; m = 1
            while m != n:
                nxt = P[lst[m - 1]][3]
                e = next((e for e, i in enumerate(lst) if i == nxt), None)
                if e is None: break
                lst[m], lst[e] = lst[e], lst[m]; m += 1
    chain = lst[:m] if m >= 2 else []
    out = {'records': n, 'chain': chain}
    if len(chain) < 2: return out
    cnt = len(chain)
    A = [0] * (cnt + 2)
    for i in range(1, cnt):
        x1, z1 = raw_pos(V, P, chain[i]); x0, z0 = raw_pos(V, P, chain[i - 1])
        A[i + 1] = i32(round(math.atan2(x1 - x0, z1 - z0) / math.pi * 0.5 * 65536 * 65536))
    A[cnt + 1] = A[cnt]; A[1] = A[2]; A[0] = A[2]
    out['heading'] = A
    seg = []; e = 0
    for r in chain:
        b = (P[r][9] >> 16) & 0xff
        if b not in (0, 1): e += 1
        seg.append(e)
    out['seg'] = seg
    cum = []; acc = 0
    for i in range(cnt - 1):
        x1, z1 = raw_pos(V, P, chain[i + 1]); x0, z0 = raw_pos(V, P, chain[i])
        cum.append(acc)
        acc = (acc + round(math.sqrt((x1 - x0) ** 2 + (z1 - z0) ** 2))) & 0xFFFFFFFF   # 32-bit register wraps
    cum.append(acc); out['cum'] = cum; out['total'] = acc
    for name, sl in (('near3c', 0x3c), ('near38', 0x38)):
        mk = [i for i, q in enumerate(P) if q[0] & 0xff == 0xff and ((q[0] >> 8) & 0xff) == sl][:0x400]
        res = []
        if mk:
            for r in chain:
                x0, z0 = raw_pos(V, P, r); best = 0xFFFFFFFF; bi = 0xFFFFFFFF
                for j, k in enumerate(mk):
                    x1, z1 = raw_pos(V, P, k)
                    d = round(math.sqrt((x1 - x0) ** 2 + (z1 - z0) ** 2))
                    if not d > best: best = d; bi = j
                res.append(bi)
        out[name] = res
    return out

def h(arr, signed=True):
    return fnv(b''.join(struct.pack('<i' if signed else '<I', x) for x in arr))

if __name__ == '__main__':
    for lv in sys.argv[1:]:
        for side in (0, 1):
            o = analyse('FILES/LEVELS/LEVEL%s/CDAT00C.SQ' % lv, side)
            c = o['chain']
            if not c: print(lv, side, o['records'], 0); continue
            print(lv, side, o['records'], len(c), o['total'], hex(h(o['heading'])), hex(h(o['seg'])), hex(h(o['cum'], False)),
                  hex(h(o['near3c'], False)) if o['near3c'] else '-', hex(h(o['near38'], False)) if o['near38'] else '-', hex(h(c, False)))
