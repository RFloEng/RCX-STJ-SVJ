"""What a see-through standing panel ("card") of the terrain shows: trees, branches, fences, signs...

INFERRED, not game data: the game has no card type.  The table was made on 2026-11-27 by looking at all 144 texture
tiles that stand see-through on any stage.  TEXTINFO byte 0x18 agrees with it in most (2 grass and hedges, 5 tree
walls, 6 fences, 7 signs) but not in all (0 is a mix, and some grass tiles carry 6), so the byte is not used.
The type says nothing about collision: that is decided by course_tiles.is_loose_card.

A tile is `page * 16 + place on the sheet`.  The type of a face is that of its first layer's tile (B when there is no A).
"""
import numpy as np

import course_tiles

# type -> {page: places}
_TILES = {
    'treeline':    {126: (0, 1, 4, 5), 130: (0, 1), 194: (0, 1, 2, 3, 8, 9, 10, 11), 350: (8, 9, 10, 11),
                    366: (0, 1, 12, 13), 367: (0, 1, 12, 13), 432: (0,), 433: (1,)},          # walls of trees and bushes
    'tree':        {103: (4, 5, 8), 104: (0,), 108: (2,), 128: (0, 1, 2, 5, 6)},              # one whole tree or bush
    'branch':      {127: (8, 9, 12, 13), 185: (1, 3, 5, 7, 8, 10, 11, 13),                    # pieces that stick out
                    209: (0, 1, 2, 4, 5, 6, 12, 13, 14, 15), 724: (7,)},                      # sideways or hang
    'undergrowth': {12: (2,), 81: (4, 5), 180: (0, 1, 3), 184: (0, 1), 185: (0, 2), 187: (14, 15),
                    384: (0, 1, 3), 389: (4, 5)},                                             # grass, hedge, scrub
    'fence':       {12: (0, 1), 35: (2, 3), 53: (0, 1), 76: (4,), 101: (0, 1), 106: (0, 1), 131: (0, 1),
                    181: (0, 1, 2), 207: (11, 15), 379: (3, 7), 389: (2, 3)},                 # fences, gates, railings
    'barrier':     {35: (0,), 180: (15,), 183: (12,), 392: (12,)},                            # rail, striped boards
    'sign':        {728: (8,), 729: (9,), **{page: (1,) for page in range(740, 758)}},
    'banner':      {999: (0, 4)},                                                             # advertising
    'cable':       {183: (3,), 184: (3,), 392: (3,)},           # a sagging line; the game only darkens what is behind it
    'structure':   {13: (0,), 115: (0,), 182: (0,), 379: (0, 1, 2, 5, 9, 13, 14)},            # stone arches and walls
    'other':       {692: (5,), 736: (0, 2), 766: (0,), 999: (5,)},                            # not recognised
}
TYPES = tuple(_TILES)
BY_TILE = {page * 16 + place: name for name, pages in _TILES.items() for page, places in pages.items() for place in places}
assert len(BY_TILE) == sum(len(places) for pages in _TILES.values() for places in pages.values()), 'a tile listed twice'

_share = {}


def card_type(p, verts, pages, a, b, weights, mode):
    """The type of terrain face p if it is a card (steeper than 70 degrees, Z up, with holes in its picture), else None.
    A card whose tile is not in the table is 'other'."""
    q = np.array([verts[i] for i in p[1:5]], float)
    n = np.cross(q[1] - q[0], q[2] - q[0]) + np.cross(q[2] - q[3], q[1] - q[3])
    length = np.linalg.norm(n)
    if not length or abs(n[2]) / length > 0.35:
        return None
    key = tuple((x[0], tuple(map(tuple, x[1]))) if x else None for x in (a, b)) + (tuple(weights or ()), mode)
    if key not in _share:
        _share[key] = course_tiles.opaque_share(pages, a, b, weights, mode)
    if _share[key] >= 0.95:
        return None
    return BY_TILE.get((a or b)[0], 'other')
