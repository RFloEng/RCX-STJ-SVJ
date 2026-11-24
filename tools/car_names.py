"""Vehicle table recovered from RAL.EXE (per-car arrays at VA 0x5c5684..0x5c581c,
34 entries, index k = 0..33):
    texture base      = 1000 + 8*k          (k=6 -> 1232, k=24 -> 1048, ...)
    body model        = OBJ04xx  (table at 0x5c570c)
    second model (same size as body; purpose unverified) = OBJ04xx  (table at 0x5c5794)
    interior model    = OBJ05xx  (table at 0x5c581c)
Names come from ENGLISH.TXT (labels tokRenaultMegane ... tokLocalShop). The game code
only stores car indices, so the index->name link is reconstructed from the livery
decals plus the class-sorted RANK table below; each entry carries its evidence.
k=30/31 (black estate, Land Rover/Welsh sheep wagon/Lambaaghini?) and k=33 stay unnamed.
"""
BODY = [401, 403, 405, 407, 409, 411, 451, 415, 417, 419, 421, 423, 425, 427, 429, 431, 433,
        435, 437, 439, 441, 443, 445, 447, 413, 449, 453, 455, 457, 459, 463, 463, 465, 570]
ALT = [402, 404, 406, 408, 410, 412, 452, 416, 418, 420, 422, 424, 426, 428, 430, 432, 434,
       436, 438, 440, 442, 444, 446, 448, 414, 450, 454, 456, 458, 460, 464, 464, 465, 570]
INTERIOR = [501, 503, 505, 507, 509, 511, 551, 515, 517, 519, 521, 523, 525, 527, 529, 531, 533,
            535, 537, 539, 541, 543, 545, 547, 513, 549, 553, 555, 557, 559, 563, 563, 565, 571]

# Performance ranking table found in RAL.EXE (VA 0x605160, used by the AI opponent
# picker at 0x449907 through 0x4374b7): car indices sorted strongest -> weakest.
# It is ordered by the (A8..A5) class printed after each name in ENGLISH.TXT, which
# is what pins down the cars that have no readable decal.
RANK = [7, 14, 6, 4, 22, 2, 10, 0, 1, 3, 8, 9, 17, 20, 16, 15, 12, 11, 5, 18, 19, 21, 13]

# k -> (name, evidence)
#   decal  = readable text on the livery        class = fixed by RANK class block + body
#   livery = recognisable livery/shape          visual = judged from the model only
NAMES = {
    0: ('Renault Megane', 'decal'), 1: ('Vauxhall Astra', 'decal'),
    2: ('Seat Cordoba WRC', 'livery+class A8'), 3: ('Hyundai Coupe evo II', 'decal'),
    4: ('Subaru Impreza WRC', 'livery+class A8'), 5: ('Peugeot 106 Maxi', 'livery+class A6'),
    6: ('Max Power car', 'decal+class A8'), 7: ('Mitsubishi Lancer Evo V', 'livery+class A8'),
    8: ('Volkswagen Golf GTI mkIV', 'plate+class A7'), 9: ('Nissan Almera', 'decal'),
    10: ('Proton Wira', 'decal+class A8'), 11: ('Citroen Saxo', 'decal'),
    12: ('Honda Civic', 'decal'), 13: ('Nissan Micra Maxi', 'class A5 (weakest)'),
    14: ('Peugeot 206 WRC', 'decal'), 15: ('Ford Escort (RS2000 or Maxi)', 'decal+class A7'),
    16: ('Skoda Octavia', 'logo+class A7'), 17: ('Seat Ibiza', 'decal'),
    18: ('Skoda Felicia', 'logo+class A6'), 19: ('Proton Compact', 'decal+class A6'),
    20: ('Ford Escort (RS2000 or Maxi)', 'decal+class A7'), 21: ('Ford Puma', 'decal+class A5'),
    22: ('Mitsubishi Lancer Evo IV (OS Lancer)', 'livery+class A8'),
    23: ('MF Turbo', 'decal'), 24: ('MF Hothatch', 'decal'),
    25: ("Bernie's dicemobile", 'visual'), 26: ('Skip', 'decal'),
    27: ('Turbotater', 'visual'), 28: ('Moo 1.8bse turbo', 'visual'),
    29: ('Radio control car', 'visual'), 32: ('Local shop', 'visual'),
}


def model_names():
    out = {}
    for k, (name, conf) in NAMES.items():
        slug = name
        out[BODY[k]] = (slug, k, conf, 'body')
        if ALT[k] != BODY[k]:
            out[ALT[k]] = (slug, k, conf, 'alt')
        out[INTERIOR[k]] = (slug, k, conf, 'interior')
    return out
