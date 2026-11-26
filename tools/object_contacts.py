"""What the car feels when it touches a placed object, and how STJ says it (spec §7.2, §8.2).

From the RC2000 decomp (physics/face_contact.rs, rc2k_core physics/face_contacts.rs, drive/lit_faces.rs,
drive/animate_lit_faces.rs, scene/draw_described_object.rs); not re-checked in RAL.EXE.

Each model has a 128-byte record: FILES/A/OBJSINFO (models 0..399), FILES/B/CARSINFO (400..599).  Byte 0x1C is the
contact type:
    1            solid, only the object's own point is tested (a trunk); the object wobbles for 24 steps
    10           solid, only the object's own point is tested
    5, 6, 9      solid, the object's point AND its mesh points are tested (9 also wobbles)
    2            the car passes through; the object leans over to 67.5 degrees
    3, 8         the car passes through; the object turns and falls flat (8 also tests the mesh points)
    4            the car passes through; the object slides away along the ground
    7            the car passes through; sound only
    other (0)    nothing
A placement can switch it off: bit 0 of byte 0x25 of its record, an object slot of 0x38 or more other than 0x3A, or
bits 1..2 of byte 0x25 (not 0: tried in one direction only, value - 1; 0 is the forward line, slot 0x3E).

In STJ the prototype's `collision` says what a vehicle hits, and in the stage file only the prims marked
`stj:collision` are collision surface.  So: 'mesh' prototypes have their instances marked; 'trunk' ones do not (the
whole mesh would stop the car on leaves); pass-through objects are 'none' and unmarked.  `physics` is not written:
STJ requires a mass for anything that moves and no mass was found in the game data.  The game's reaction is kept in
the prototype's x_rcx.
"""
import os

TRUNK_RADIUS = 0.25     # m, ESTIMATE: the game tests a point against the car, so it has no trunk radius

# contact type -> (STJ collision, what the game does)
TYPES = {
    1: ('trunk', 'solid at its own point; wobbles'),
    10: ('trunk', 'solid at its own point'),
    5: ('mesh', 'solid (its point and mesh points are tested)'),
    6: ('mesh', 'solid (its point and mesh points are tested)'),
    9: ('mesh', 'solid (its point and mesh points are tested); wobbles'),
    2: ('none', 'the car passes through; leans to 67.5 degrees and stays'),
    3: ('none', 'the car passes through; turns, falls flat and stays'),
    8: ('none', 'the car passes through; turns, falls flat and stays'),
    4: ('none', 'the car passes through; slides away along the ground and stays there'),
    7: ('none', 'the car passes through; sound only'),
}
# contact type -> STJ prototype class, ONLY where every model of the type was looked at in a render and fits
CLASSES = {1: 'tree', 2: 'bush', 7: 'bush', 3: 'sign', 8: 'barrier', 4: 'prop', 10: 'pole'}
_records = {}


def contact_type(files, model):
    """Byte 0x1C of the model's record, or None when the file has no record for it."""
    name, first = (('A', 'OBJSINFO'), 0) if model < 400 else (('B', 'CARSINFO'), 400)
    path = os.path.join(files, *name)
    if path not in _records:
        _records[path] = open(path, 'rb').read()
    at = (model - first) * 128 + 0x1C
    return _records[path][at] if 0 <= at < len(_records[path]) else None


def placement_state(poly, slot):
    """'' = collides on the forward line; 'off' = never; 'rev' = only in the reverse direction."""
    flags = (poly[9] >> 8) & 0xFF
    if flags & 1 or (slot >= 0x38 and slot != 0x3A):
        return 'off'
    way = (flags >> 1) & 3
    return '' if way in (0, 1) else 'rev' if way == 2 else 'off'


def prototype(files, model, poly, slot):
    """(prototype id, scenery.prototypes entry without `mesh` and `height`) for one placement."""
    kind = contact_type(files, model)
    collision, reaction = TYPES.get(kind, ('none', 'nothing'))
    state = placement_state(poly, slot) if kind in TYPES else ''
    pid = 'obj%04d' % model + ('_' + state if state else '')        # STJ ids are lower case
    entry = {'class': CLASSES.get(kind, 'other'), 'collision': 'none' if state else collision,
             'x_rcx': {'model': model, 'contact_type': kind, 'game': reaction}}
    if state:
        entry['x_rcx']['switched_off'] = {'off': 'these placements are never tried by the game',
                                          'rev': 'these placements are only tried in the reverse direction'}[state]
    if entry['collision'] == 'trunk':
        entry['collision_radius'] = TRUNK_RADIUS
        entry['x_rcx']['_estimate'] = 'collision_radius: the game tests a point, the radius is not game data'
    return pid, entry
