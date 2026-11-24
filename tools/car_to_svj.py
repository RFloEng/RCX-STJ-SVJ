"""Describe the game's cars as SVJ (Standard Vehicle JSON, https://github.com/RFloEng/SVJ-standard-vehicle-json).

usage: python tools/car_to_svj.py [out_dir]          (default export/svj; needs export/glb_named/cars from name_exports.py)

Per car (the 34 body models of car_names.BODY) it writes  <out_dir>/<name>/<name>.svj.json  and a copy of the car's GLB.
GEOMETRY ONLY: the physics of the cars (mass, centre of gravity, engine, suspension, tyres) is not decoded (B/CARSINFO does
not hold it; see docs/OPEN_QUESTIONS.md), so the file has no `chassis`, and SVJ's `chassis.mass_total` is not invented.
What is measured from the car's LWO model (metres): the four wheel parts (surfaces wheels1..4) -> wheel centres, radius
and width, hence wheelbase and track; the bounding box; the side of the driver.
Frame (SVJ ISO 8855: X forward, Y left, Z up, origin = middle of the front axle on the ground) from the game's frame
(x along the car with the bonnet at -x, y up, z across, left-handed):  X = -x, Y = -z, Z = y - ground.
Everything is under the extension key `x_rcx` (spec 17) because SVJ has no field for a bare measurement set;
`assets.meshes` points to the GLB of the same car (glTF frame as the GLBs of this project, not shifted).
"""
import math, subprocess, struct, collections, glob, json, os, re, shutil, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import car_names as C
import lwob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = 'car_to_svj.py'


SVJ_TOOLS = None


def measure(path):
    r = lwob.read_lwob(path)
    P = np.array(r['points'], float)
    names = [str(n) for n in r['surfaces']]
    by = collections.defaultdict(set)
    for verts, s in r['polys']:
        by[s].update(verts)
    part = {}
    for s, vs in by.items():
        q = P[sorted(vs)]
        part[names[s]] = ((q.min(0) + q.max(0)) / 2, q.max(0) - q.min(0))
    return P, part


def describe(body_model):
    path = os.path.join(ROOT, 'FILES', 'B', 'OBJ%04d.LWO' % body_model)
    if not os.path.exists(path):
        return None
    P, part = measure(path)
    # the wheel parts are named wheels1..4, wheel1..4 or wheel{Left,Right}{Front,Rear}; the spare (wheels5, wheelSpare) is not one of the four
    wheels = [part[n] for n in part if re.fullmatch(r'(?i)wheels?[1-4]|wheel(left|right)(front|rear)', n)]
    if len(wheels) != 4:
        return None
    front_sign = -1.0      # all 34 bodies carry the bonnet / front bumper at -x (checked on the first 14 models)
    ws = []
    for c, e in wheels:
        # wheel axis is z (lateral): extents are (diameter, diameter, width)
        ws.append({'x': front_sign * c[0], 'y': -c[2], 'z': c[1], 'radius': e[1] / 2, 'width': e[2]})
    ws.sort(key=lambda w: (-w['x'], -w['y']))          # FL, FR, RL, RR
    ground = min(w['z'] - w['radius'] for w in ws)
    x0 = (ws[0]['x'] + ws[1]['x']) / 2
    out = {}
    for sid, w in zip(('FL', 'FR', 'RL', 'RR'), ws):
        out[sid] = {'center': [round(w['x'] - x0, 4), round(w['y'], 4), round(w['z'] - ground, 4)],
                    'radius': round(w['radius'], 4), 'width': round(w['width'], 4)}
    wb = out['FL']['center'][0] - out['RL']['center'][0]
    lo, hi = P.min(0), P.max(0)
    drv = next((part[n] for n in part if n.lower() in ('driver', 'steeringwheel', 'stearingwheel')), None)
    xs = sorted([front_sign * lo[0] - x0, front_sign * hi[0] - x0])
    ys = sorted([-lo[2], -hi[2]])
    zs = [round(lo[1] - ground, 4), round(hi[1] - ground, 4)]
    return {'wheels': out, 'wheelbase': round(wb, 4), 'track_front': round(out['FL']['center'][1] - out['FR']['center'][1], 4),
            'track_rear': round(out['RL']['center'][1] - out['RR']['center'][1], 4),
            'bounding_box': {'x': [round(v, 4) for v in xs], 'y': [round(v, 4) for v in ys], 'z': zs},
            'driver_side': None if drv is None else ('left' if -drv[0][2] > 0 else 'right'),
            'spare_wheel': any(n.lower() in ('wheels5', 'wheelspare') for n in part)}


def two_sided_copy(src, dst):
    """Copy a GLB with every material double sided: the original engine draws the faces of these models from both sides and the
    winding fixed for the single-sided export is not reliable on every part (bonnet, hubs), so a viewer that culls back faces
    would show holes."""
    b = open(src, 'rb').read()
    n = struct.unpack_from('<I', b, 12)[0]
    j = json.loads(b[20:20 + n])
    for m in j.get('materials', []):
        m['doubleSided'] = True
    js = json.dumps(j, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    rest = b[20 + n:]
    out = b[:12] + struct.pack('<I', len(js)) + b'JSON' + js + rest
    out = out[:8] + struct.pack('<I', len(out)) + out[12:]
    open(dst, 'wb').write(out)


def make_usdz_two_sided(usdz, glb):
    """gltf2usd drops the glTF doubleSided flag: set doubleSided on every Mesh of the package and repack it (see two_sided_copy)."""
    import tempfile, zipfile
    from pxr import Usd, UsdGeom, UsdUtils
    tmp = tempfile.mkdtemp()
    with zipfile.ZipFile(usdz) as z:
        z.extractall(tmp)
        names = z.namelist()
    root = os.path.join(tmp, names[0])
    st = Usd.Stage.Open(root)
    for pr in st.Traverse():
        if pr.IsA(UsdGeom.Mesh):
            UsdGeom.Mesh(pr).CreateDoubleSidedAttr(True)
            # the stored normals do not agree with the winding fixed on export (same as the STJ objects): let the viewer derive them
            for nm in ('normals', 'primvars:normals', 'primvars:normals:indices'):
                if pr.HasAttribute(nm):
                    pr.RemoveProperty(nm)
    # gltf2usd also drops alphaMode MASK (cut-out): without a threshold viewers blend the texture alpha and the parts turn
    # semi-transparent and sort wrongly. Give every MASK material its glTF cutoff as opacityThreshold.
    b = open(glb, 'rb').read()
    j = json.loads(b[20:20 + struct.unpack_from('<I', b, 12)[0]])
    from pxr import UsdShade, Sdf
    for i, m in enumerate(j.get('materials', [])):
        if m.get('alphaMode') == 'MASK':
            for pr in st.Traverse():
                if pr.GetName() == 'mat_%d' % i:
                    for ch in Usd.PrimRange(pr):
                        if ch.GetTypeName() == 'Shader' and ch.GetAttribute('info:id').Get() == 'UsdPreviewSurface':
                            UsdShade.Shader(ch).CreateInput('opacityThreshold', Sdf.ValueTypeNames.Float).Set(float(m.get('alphaCutoff', 0.5)))
    st.GetRootLayer().Save()
    os.remove(usdz)
    UsdUtils.CreateNewUsdzPackage(root, usdz)
    shutil.rmtree(tmp, ignore_errors=True)


def spec_record(k):
    """Car spec record k of RAL.EXE (34 x 0xB0 bytes at file offset 0x1c5a8c; the names inside match car_names).
    Checked on the numbers: int19 = mass (WRC cars 1230 kg), float20 = wheelbase (Cordoba 2.443 m), int6 = 1 only on the
    4WD cars, int29 = rev limit, int30 = top speed km/h, floats 31..39 = torque at i/8 of the rev limit (peak 1.0)."""
    b = open(os.path.join(ROOT, 'RAL.EXE'), 'rb').read()
    r = b[0x1c5a8c + k * 0xB0: 0x1c5a8c + (k + 1) * 0xB0]
    if len(r) < 0xB0:
        return None
    I, F = struct.unpack('<44i', r), struct.unpack('<44f', r)
    return {'name': r[:24].split(bytes(1))[0].decode('latin1').strip(), 'drive': I[6], 'gear_field': I[7], 'mass': I[19],
            'wheelbase': F[20], 'rev_limit': I[29], 'peak_torque_lbft': I[30], 'gear_mph': list(I[25:29]), 'torque': [round(F[i], 4) for i in range(31, 40)],
            'raw_ints_16_18': list(I[16:19]), 'raw_float_21': round(F[21], 4),
            'raw_floats_40_43': [round(F[i], 4) for i in range(40, 44)], 'raw_floats_9_15': [round(F[i], 4) for i in range(9, 16)]}


def powertrain_of(sp):
    rev = float(sp['rev_limit'])
    peak = sp['peak_torque_lbft'] * 1.3558179          # N m
    curve = [[round(rev * i / 8, 1), round(t * peak, 1)] for i, t in enumerate(sp['torque'])]
    pmax = max(x * 2 * math.pi / 60 * y for x, y in curve) / 1000
    pw = {'layout': '4WD' if sp['drive'] == 1 else 'FF',
          'engine': {'max_rpm': rev, 'torque_curve': curve,
                     '_note': 'torque = normalised curve (exe table, 9 points at i/8 of the rev limit) x peak torque; the peak is '
                              'the record field read by the game as the drive torque figure (RAL.EXE 0x4212b3), taken as lb ft. '
                              'Evidence: Megane peak power comes out above the "more than 280bhp" of the game text; the game acceleration '
                              'model with the real mass gives the same power within about +-25 percent over 7 cars. '
                              'Peak power of this curve: %.0f kW (%.0f bhp).' % (pmax, pmax * 1.341)}}
    return pw


def main(out_dir):
    glbs = glob.glob(os.path.join(ROOT, 'export', 'glb_named', 'cars', '*.glb'))
    n = 0
    used = set()
    for k, model in enumerate(C.BODY):
        g = describe(model)
        if not g:
            print('skip', model)
            continue
        name, evidence = C.NAMES.get(k, ('OBJ%04d' % model, 'unnamed'))
        safe = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in name).strip()
        if safe in used:                      # two cars share a name (the two Ford Escorts): keep both
            safe += ' (car %d)' % k
        used.add(safe)
        make, _, mdl = name.partition(' ')
        folder = os.path.join(out_dir, safe)
        os.makedirs(folder, exist_ok=True)
        doc = {
            '_metadata': {
                'specification': 'SVJ', 'version': '0.112', 'coordinate_system': 'ISO_8855', 'units': 'SI',
                'description': 'Rally Championship Xtreme car %s (body model OBJ%04d): geometry only' % (name, model),
                'author': TOOL, 'source_format': 'RCX B/OBJ%04d.LWO' % model,
                'data_origin': {'type': 'calculated', 'confidence': 'low',
                                'detail': 'Dimensions measured from the 3D model; no physics (mass, engine, suspension, tyres) '
                                          'was decoded from the game, so there is no chassis block.'},
                'licence': {'redistributable': False, 'notes': 'Converted from commercial game files; do not redistribute.'}},
            'vehicle_info': {'make': make, 'model': mdl or make, 'vehicle_class': 'race_car'},
            'x_rcx': {'body_model': model, 'alt_model': C.ALT[k], 'interior_model': C.INTERIOR[k], 'car_index': k,
                      'name_evidence': evidence, 'geometry': g,
                      'lost': ['mass, centre of gravity, inertia', 'engine, gearbox, drivetrain', 'suspension, tyres, brakes']}}
        sp = spec_record(k)
        if sp:
            doc['chassis'] = {'mass_total': float(sp['mass']), 'wheelbase': round(sp['wheelbase'], 3),
                              'center_of_gravity': [-round(sp['wheelbase'] / 2, 3), 0.0, 0.45],
                              '_center_of_gravity_note': 'ESTIMATE (schema requires one): midway between the axles, 0.45 m high; the game record holds a 0.5-0.6 value (float 21) whose meaning is not confirmed',
                              'track_front': abs(g['track_front']), 'track_rear': abs(g['track_rear'])}
            doc['powertrain'] = powertrain_of(sp)
            R = (g['wheels']['FL']['radius'] + g['wheels']['RL']['radius']) / 2
            ng = min(max(sp['gear_field'], 3), 7)
            m1, mt = sp['gear_mph'][0], sp['gear_mph'][1]       # first / top gear speed at the rev limit (mph, setting 0)
            v1, vt = m1 * 0.44704, mt * 0.44704
            ratio = lambda v: sp['rev_limit'] * 2 * math.pi * R / (60 * v)
            r1, rt = ratio(v1), ratio(vt)
            doc['powertrain']['gearbox'] = {'type': 'sequential', 'ratios': [round(r1 + (rt - r1) * k / (ng - 1), 3) for k in range(ng)],
                                            '_note': 'OVERALL ratios (final drive included): speed at the rev limit in first and top gear are the record ints '
                                                     '(mph, RAL.EXE 0x42177d, gear setting 0), the gears between are evenly spaced like the game does; '
                                                     'wheel radius from the model; the gear count is the record field clamped to 3..7'}
            doc['x_rcx']['spec_record'] = sp
            doc['x_rcx']['lost'] = ['centre of gravity (estimated), inertia', 'final drive and differentials (gear ratios are overall, from the redline speeds)',
                                    'suspension, tyres, brakes']
            doc['_metadata']['data_origin']['detail'] = ('Dimensions measured from the 3D model; mass, wheelbase, drive, rev limit, '
                                                         'top speed and a normalised torque curve from the car spec table of RAL.EXE.')
            doc['_metadata']['description'] = doc['_metadata']['description'].replace('geometry only', 'geometry and basic spec')
        hit = [p for p in glbs if '(OBJ%04d)' % model in os.path.basename(p)]
        if hit:
            two_sided_copy(hit[0], os.path.join(folder, safe + '.glb'))
            doc['assets'] = {'meshes': [{'id': 'body', 'uri': safe + '.glb', 'description': 'visual model of the car (textured glTF)'}]}
            if SVJ_TOOLS:      # OpenUSD asset with the textures packed inside, glTF kept as its preview (spec 22.10)
                usdz = os.path.join(folder, safe + '.usdz')
                r = subprocess.run([sys.executable, os.path.join(SVJ_TOOLS, 'gltf2usd.py'), os.path.join(folder, safe + '.glb'), usdz],
                                   capture_output=True, text=True)
                if r.returncode == 0 and os.path.exists(usdz):
                    make_usdz_two_sided(usdz, os.path.join(folder, safe + '.glb'))
                    doc['assets']['meshes'] = [{'id': 'body', 'uri': safe + '.usdz', 'format': 'usd', 'preview': safe + '.glb',
                                                'description': 'visual model of the car, textures packed in the USDZ'}]
                else:
                    print('gltf2usd failed for', safe, r.stderr[-200:])
        json.dump(doc, open(os.path.join(folder, safe + '.svj.json'), 'w'), indent=1)
        n += 1
    print(n, 'cars ->', out_dir)


if __name__ == '__main__':
    # usage: car_to_svj.py [out_dir] [--svj-tools <folder of the SVJ standard's tools/>]  (the folder gives gltf2usd.py; needs usd-core)
    a = sys.argv[1:]
    if '--svj-tools' in a:
        i = a.index('--svj-tools')
        SVJ_TOOLS = a[i + 1]
        del a[i:i + 2]
    main(a[0] if a else os.path.join(ROOT, 'export', 'svj'))
