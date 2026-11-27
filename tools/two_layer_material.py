"""The STJ material of a terrain face with two texture layers (spec §7.7, "materials stay as the source").

course_tiles.py explains what the game does.  Nothing is baked: the face keeps the game's own texture pages, and
  * its mesh carries the faceVarying primvars `st` (layer A, or B when there is no A), `stj_st_b` (layer B) and
    `stj_weight` (layer B's corner weight, 0..1);
  * its material `mix_<page A | none>_<page B>_<mode>` has a UsdPreviewSurface fallback, which shows ONE layer (A, or B
    when there is no A), and the mix as a MaterialX network on `outputs:mtlx:surface`;
  * the material's customData `x_rcx` says the same for a converter that does not read MaterialX:
    {shader: "RCX two-layer face, mode <mode>" (the last word is the mode), rule, page_a (-1 = no A), page_b}.
The mix is made on the stored (sRGB encoded) texels, as the game does, and only the result is turned into linear light.

Baked shadows (an INFERENCE, the game does not call them that): a `gloss` layer over a layer A only darkens it.  Counted
over every stage, all 17,098 such faces use texture tiles that are used for nothing else, with the shapes of tree crowns,
fences and bushes, nearer to standing panels and objects than other ground (docs/stj_baked_material_layers_proposal.md).
Declared as STJ 0.4 does it (§7.2): their materials get the interface input `inputs:stj_baked_strength` (1 = as the game
draws it, 0 = without the layer) and are listed in the mesh's `baked.material_layers`, with basis `inferred`.  Gloss
without a layer A is not covered.
"""
from pxr import Gf, Sdf, UsdShade

# w = stj_weight x B's alpha; aA = A's alpha; the face is cut out where alpha < 0.5
RULE = {'alpha': 'alpha = w + aA(1-w); colour = (B w + A aA (1-w)) / alpha',
        'add': 'alpha = w + aA(1-w); colour = (B w + A aA) / alpha',
        'gloss': 'alpha = aA; colour = A (1 - B w)',
        'fade': 'alpha = w; colour = B'}

# the interface input of a material with a baked shadow layer (the STJ 0.4 name)
STRENGTH = 'stj_baked_strength'
EVIDENCE = ('second layer that only darkens, on texture tiles used for nothing else, with the shapes of tree crowns, '
            'fences and bushes and nearer to standing panels and objects than other ground; the game does not call it a shadow')

F, C3, C4 = Sdf.ValueTypeNames.Float, Sdf.ValueTypeNames.Color3f, Sdf.ValueTypeNames.Color4f
V2, STR, FILE = Sdf.ValueTypeNames.Float2, Sdf.ValueTypeNames.String, Sdf.ValueTypeNames.Asset


def is_baked_shadow(page_a, mode):
    return page_a is not None and mode == 'gloss'


def baked_layer(name, rel_b):
    """The entry of a material in geometry.meshes[].baked.material_layers."""
    return {'material': name, 'kind': 'shadow', 'input': STRENGTH,
            'layer': {'uri': rel_b, 'texcoord': 'stj_st_b', 'weight_primvar': 'stj_weight'},
            'op': 'darken', 'space': 'encoded', 'basis': 'inferred', 'note': EVIDENCE}


def name_of(page_a, page_b, mode):
    """page_a None = the face has no layer A; its mode is then 'fade' (B with the weights as its opacity)."""
    return 'mix_%s_%04d_%s' % ('none' if page_a is None else '%04d' % page_a, page_b, 'fade' if page_a is None else mode)


class _Graph:
    def __init__(self, stage, root):
        self.stage, self.root, self.count = stage, root, 0

    def shader(self, nid, inputs):
        """inputs: name -> (value type, a constant or an output)."""
        self.count += 1
        sh = UsdShade.Shader.Define(self.stage, '%s/%s_%d' % (self.root, nid[3:], self.count))
        sh.CreateIdAttr(nid)
        for key, (typ, val) in inputs.items():
            inp = sh.CreateInput(key, typ)
            if isinstance(val, (UsdShade.Output, UsdShade.Input)):
                inp.ConnectToSource(val)
            else:
                inp.Set(val)
        return sh

    def node(self, nid, out_type, inputs):
        return self.shader(nid, inputs).CreateOutput('out', out_type)

    def two(self, op, kind, x, y):
        typ = F if kind == 'float' else C3
        return self.node('ND_%s_%s' % (op, kind), typ, {'in1': (typ, x), 'in2': (typ, y)})

    def times(self, colour, k):
        return self.node('ND_multiply_color3FA', C3, {'in1': (C3, colour), 'in2': (F, k)})

    def image(self, rel, primvar):
        """(rgb, alpha) of a page: the stored values (raw), nearest texel like the game, repeating."""
        uv = self.node('ND_geompropvalue_vector2', V2, {'geomprop': (STR, primvar)})
        tex = self.shader('ND_image_color4', {'file': (FILE, Sdf.AssetPath(rel)), 'texcoord': (V2, uv),
                                              'uaddressmode': (STR, 'periodic'), 'vaddressmode': (STR, 'periodic'),
                                              'filtertype': (STR, 'closest')})
        tex.GetInput('file').GetAttr().SetColorSpace('raw')
        out = tex.CreateOutput('out', C4)
        split = self.shader('ND_separate4_color4', {'in': (C4, out)})
        r, g, b, a = [split.CreateOutput(n, F) for n in ('outr', 'outg', 'outb', 'outa')]
        return self.node('ND_combine3_color3', C3, {'in1': (F, r), 'in2': (F, g), 'in3': (F, b)}), a


def add_mix(stage, mat, page_a, page_b, rel_a, rel_b, mode):
    """Add the mix to a material that already has its fallback. rel_* are texture paths relative to the layer."""
    mode = 'fade' if page_a is None else mode
    g = _Graph(stage, str(mat.GetPath()) + '/mtlx')
    cb, ab = g.image(rel_b, 'stj_st_b')
    weight = g.node('ND_geompropvalue_float', F, {'geomprop': (STR, 'stj_weight')})
    wb = g.two('multiply', 'float', weight, ab)
    shadow = is_baked_shadow(page_a, mode)
    if shadow:
        strength = mat.CreateInput(STRENGTH, F)
        strength.Set(1.0)
        wb = g.two('multiply', 'float', wb, strength)
    if page_a is None:
        colour, alpha = cb, wb
    else:
        ca, aa = g.image(rel_a, 'st')
        if mode == 'gloss':
            alpha = aa
            colour = g.two('subtract', 'color3', ca, g.times(g.two('multiply', 'color3', ca, cb), wb))
        else:
            rest = g.two('multiply', 'float', aa, g.two('subtract', 'float', 1.0, wb))
            alpha = g.two('add', 'float', wb, rest)
            top = g.two('add', 'color3', g.times(cb, wb), g.times(ca, aa if mode == 'add' else rest))
            colour = g.times(top, g.two('divide', 'float', 1.0, g.two('max', 'float', alpha, 1e-6)))
            colour = g.node('ND_clamp_color3', C3, {'in': (C3, colour), 'low': (C3, Gf.Vec3f(0, 0, 0)),
                                                    'high': (C3, Gf.Vec3f(1, 1, 1))})
    linear = g.node('ND_srgb_texture_to_lin_rec709_color3', C3, {'in': (C3, colour)})
    cut = g.node('ND_ifgreater_float', F, {'value1': (F, alpha), 'value2': (F, 0.4999), 'in1': (F, 1.0), 'in2': (F, 0.0)})
    surface = g.node('ND_open_pbr_surface_surfaceshader', Sdf.ValueTypeNames.Token,
                     {'base_color': (C3, linear), 'specular_roughness': (F, 1.0), 'geometry_opacity': (F, cut)})
    mat.CreateSurfaceOutput('mtlx').ConnectToSource(surface)
    data = {'shader': 'RCX two-layer face, mode ' + mode, 'rule': RULE[mode],
            'page_a': -1 if page_a is None else page_a, 'page_b': page_b}
    if shadow:          # for a converter that reads only this: without the layer the face is layer A, alpha included
        data.update({'baked_kind': 'shadow', 'baked_basis': 'inferred', 'baked_input': STRENGTH})
    mat.GetPrim().SetCustomDataByKey('x_rcx', data)
