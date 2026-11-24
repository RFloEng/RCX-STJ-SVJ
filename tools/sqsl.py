"""SQSL decompressor (RAL.EXE FUN_0041b9bb).

Header (16 bytes): 'SQSL', u32, u32, u32 count (output dwords). Then an MSB-first
bitstream over little-endian dwords. One output dword per token:
  0                -> 0
  1 0  + 8 bits    -> out[n-1-idx]
  1 1 0 0 + 12 b   -> out[n-1-idx]
  1 1 0 1 + 17 b   -> unsigned 17-bit literal
  1 1 1 0 + 28 b   -> signed 28-bit literal
  1 1 1 1 + 32 b   -> 32-bit literal
"""
import struct
import sys


def sqsl_decode(data):
    if data[:4] != b'SQSL':
        raise ValueError('not SQSL')
    count = struct.unpack_from('<I', data, 12)[0]
    nwords = (len(data) - 16) // 4
    words = struct.unpack_from('<%dI' % nwords, data, 16)
    pos = 0       # bit position
    total = nwords * 32

    def bits(n):
        nonlocal pos
        if pos + n > total:
            raise ValueError('bitstream overrun')
        v = 0
        while n:
            w = words[pos >> 5]
            off = pos & 31
            take = min(n, 32 - off)
            v = (v << take) | ((w >> (32 - off - take)) & ((1 << take) - 1))
            pos += take
            n -= take
        return v

    out = []
    for _ in range(count):
        if not bits(1):
            out.append(0)
        elif not bits(1):
            out.append(out[-1 - bits(8)])
        elif not bits(1):
            if not bits(1):
                out.append(out[-1 - bits(12)])
            else:
                out.append(bits(17))
        else:
            if not bits(1):
                v = bits(28)
                if v & 0x8000000:
                    v -= 1 << 28
                out.append(v & 0xFFFFFFFF)
            else:
                out.append(bits(32))
    return struct.pack('<%dI' % count, *out), pos


if __name__ == '__main__':
    for p in sys.argv[1:]:
        raw = open(p, 'rb').read()
        try:
            dec, used = sqsl_decode(raw)
            print(p, len(raw), '->', len(dec), 'bits used %d/%d' % (used, (len(raw) - 16) * 8))
        except Exception as e:
            print('FAIL', p, e)
