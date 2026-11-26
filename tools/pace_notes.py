#!/usr/bin/env python3
"""The co-driver's calls of a stage, made the way the game makes them (RAL.EXE 0x441941..0x441b98; the same logic as
race/pace_notes.rs and race/pace.rs of the RC2000 decompilation).

A byte on the road line is NOT a speech file number: it is a *cue*.  RAL.EXE 0x5124f8 holds 113 pointers to lists of sound records
(ended by -1); for the cues 0..83 they are identical to the RC2000 decompilation's CueLists.  Record (r - 0xED) // 2 is a phrase of
the speech file list (male/female pairs), which gives the CUES texts below.

  * Each line point (face) has eight code slots, at face bytes 40, 41, 42, 43, 25, 26, 27, 32 (table at 0x614c94).  A place on the
    line is point * 8 + slot.
  * Codes 98, 99, 100 are not cues: 99 = "this corner, worked out from the road" , 98 = end of a note not joined to the next one,
    100 = end of a note.  A note runs from a code in 1..99 other than 98 to the next 98 or 100; its calls are read from its start
    until a 0 or a 100.
  * The notes start two points after the first check (the start line).
  * A call group is said when the car reaches the end of the previous note: first the distance word for the gap to the next note
    (table 0x614cb8, by tens of metres), then the note, plus the following ones while the gap is 30 m or less.
  * Computed corner: length = line distance over the note, turn = heading at its end - heading before its start; grade 1..5 from
    |turn| * 60 / max(length, 60 m); +8 when the note is 120 m or longer ("long"); +16 when the heading grows (right-hander).
"""
import math

SLOTS = (40, 41, 42, 43, 25, 26, 27, 32)
END_OPEN, CORNER, END = 0x62, 0x63, 0x64
BAD_CUE = 0x54                      # the game stops a group's text here ("invalid pace data")
JOIN_GAP = 0x1E0000                 # 30 m
DISTANCE_CUES = ([0] + [32] * 4 + [33] * 2 + [34] * 3 + [35] * 5 + [36] * 5 + [37] * 5 + [38] * 5 + [39] * 10 + [40])
DISTANCE_M = {32: 30, 33: 50, 34: 70, 35: 100, 36: 150, 37: 200, 38: 250, 39: 300, 40: 400}

SHAPES = ['1', '2', '3', '4', '5', 'square', 'hairpin', 'tight hairpin']
CUES = {}
for _k, _n in enumerate(SHAPES):
    CUES[1 + _k] = 'left ' + _n
    CUES[17 + _k] = 'right ' + _n
for _k in range(5):
    CUES[9 + _k] = 'long left %d' % (_k + 1)
    CUES[25 + _k] = 'long right %d' % (_k + 1)
CUES.update({30: 'long', 31: 'straight', 41: 'into', 47: 'over finish to stop', 48: 'opens', 49: 'narrows', 50: 'tightens',
             51: "don't cut", 52: 'over', 53: 'under', 54: 'through', 55: 'past', 56: 'caution', 57: 'ice', 58: 'logs',
             59: 'loose gravel', 60: 'road dips', 61: 'rocks', 62: 'rough surface', 63: 'slippery surface', 64: 'chicane',
             65: 'crest', 66: 'jump', 67: 'gate', 68: 'junction', 69: 'water', 70: 'bank', 71: 'bridge', 72: 'ditch',
             73: 'haybales', 80: 'turn around', 81: 'where are you going', 82: 'wrong way', 83: 'let me drive'})
CUES.update({c: '%d' % m for c, m in DISTANCE_M.items()})

# first matching cue of a note without a corner decides its STJ kind
FEATURE_KIND = {47: 'finish', 31: 'straight', 66: 'jump', 65: 'crest', 68: 'junction', 69: 'water', 71: 'bridge', 60: 'dip',
                64: 'twisty', 62: 'surface_change', 63: 'surface_change', 59: 'surface_change', 49: 'narrows', 56: 'caution'}
MODIFIERS = {30: 'long', 48: 'opens', 50: 'tightens', 49: 'narrows', 51: 'dont_cut', 56: 'caution', 57: 'slippy',
             63: 'slippy', 59: 'loose', 62: 'rough', 41: 'into'}


def face_codes(p):
    """The eight code bytes of a line record (16 dwords)."""
    return [(p[o // 4] >> (8 * (o % 4))) & 0xFF for o in SLOTS]


def line_tables(points):
    """points: (x, z) in 16.16 integers.  Distance and heading (32-bit binary angle, atan2(dx, dz)) per line point."""
    n = len(points)
    dist = [0]
    head = []
    for a, b in zip(points, points[1:]):
        dx, dz = b[0] - a[0], b[1] - a[1]
        dist.append(dist[-1] + int(round(math.hypot(dx, dz))))
        head.append(int(round(math.atan2(dx, dz) / math.pi * 2147483648.0)) & 0xFFFFFFFF)
    # heading at point k = direction of the link k-2 -> k-1; points 0 and 1 take point 2's
    angle = [head[min(max(k - 2, 0), len(head) - 1)] for k in range(n + 2)]
    return dist, angle


def corner_cue(dist, angle, start, end):
    """(cue, turn in degrees, length in 16.16) of a computed corner whose note runs from place `start` to place `end`."""
    length = dist[end >> 3] - dist[start >> 3]
    turn = (angle[end >> 3] - angle[max(start - 1, 0) >> 3]) & 0xFFFFFFFF
    signed = turn - (1 << 32) if turn >> 31 else turn
    flags = (8 if length >= 0x780000 else 0) + (16 if signed >= 0 else 0)
    q = abs(signed) * 60 // max(length, 0x3C0000)
    grade = 5 if q > 0x3330 else 4 if q > 0x2664 else 3 if q > 0x1998 else 2 if q > 0xCCC else 1
    return grade + flags, signed * 360.0 / 2 ** 32, length


def make_notes(codes, points, kinds):
    """codes: eight bytes per line point; points: (x, z) 16.16; kinds: face byte 0x26 per point.
    Returns the notes: {'start', 'end' (places), 'end_code', 'cues', 'computed', 'gap_before' (16.16 or None), 'joined'}."""
    count = len(codes)
    if count < 2:
        return []
    first = next((k for k in range(count) if kinds[k] == 2), None)
    if first is None or first + 2 >= count:
        return []
    dist, angle = line_tables(points)
    code_at = lambda place: codes[place >> 3][place & 7]
    last = count * 8
    notes = []
    place = ((((first + 2) << 3) + 1) >> 3) << 3

    def find(place, wanted):
        while place < last:
            if wanted(code_at(place)):
                return place
            place += 1
        return None

    previous_end = ((first + 2) << 3) + 1
    previous_code = 0
    while True:
        s = find(place, lambda c: c != 0 and c != END_OPEN and c < END)
        if s is None:
            break
        e = find(s + 1, lambda c: c in (END_OPEN, END))
        if e is None:
            break
        cues, computed, p = [], None, s
        while p < last:
            c = code_at(p)
            if c in (0, END):
                break
            if c == CORNER:
                c, turn, length = corner_cue(dist, angle, s, e)
                computed = {'cue': c, 'turn_deg': round(turn, 1), 'length_m': round(length / 65536.0, 1)}
            if c != END_OPEN:
                if c >= BAD_CUE:
                    break
                cues.append(c)
            p += 1
        gap = max(0, dist[s >> 3] - dist[previous_end >> 3])
        joined = bool(notes) and gap <= JOIN_GAP and previous_code != END_OPEN
        notes.append({'start': s, 'end': e, 'end_code': code_at(e), 'cues': cues, 'computed': computed,
                      'gap_before': gap, 'called_at': previous_end, 'joined': joined})
        previous_end, previous_code = e, code_at(e)
        place = e + 1
    return notes


def distance_word(gap):
    """The cue said for a gap (16.16) before a call group, 0 for none."""
    return DISTANCE_CUES[min((gap >> 16) // 10, 40)]


def stj_note(cues, s):
    """The STJ race.pacenotes entry of a note's cues, or None when no cue has a sound."""
    heard = [c for c in cues if c in CUES]
    if not heard:
        return None
    corner = next((c for c in heard if 1 <= c <= 29), None)
    e = {'s': s}
    mods = []
    if corner is not None:
        k = corner - (1 if corner < 17 else 17)             # 0..7 shapes, 8..12 long 1..5
        if k >= 8:
            k -= 8
            mods.append('long')
        e['direction'] = 'left' if corner < 17 else 'right'
        if k < 5:
            e['kind'] = 'turn'
            e['severity'] = 5 - k           # game 1 = gentlest .. 5 = tightest; STJ ascending: 1 = slowest .. fastest
        else:
            e['kind'] = 'square' if k == 5 else 'hairpin'
    else:
        e['kind'] = next((FEATURE_KIND[c] for c in heard if c in FEATURE_KIND), 'other')
    mods += [MODIFIERS[c] for c in heard if c in MODIFIERS]
    mods = [m for i, m in enumerate(mods) if m not in mods[:i] and m != e['kind']]
    if mods:
        e['modifiers'] = mods
    e['call'] = ' '.join(CUES[c] for c in heard)
    return e
