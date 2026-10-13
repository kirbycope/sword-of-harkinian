"""Assemble Sword Kirby for Ocarina of Time from three ripped N64 models.

    blender --background --python blender/build_sword_kirby.py -- \
        --assets .assets --out build/sword_kirby.blend

No single game ships the character this mod wants, so he is assembled from three:

  * the body, face and feet come from Kirby 64: The Crystal Shards, at 516 triangles,
    roughly double the resolution of the Smash 64 Kirby and with a far better face;
  * Link's green cap comes from Smash 64's "Kirby (Link)" copy-ability model, which is
    the only N64 Kirby wearing it. Only the cap is taken, picked out by the two
    materials using the green cloth texture and the one using the gold band;
  * the sword is Kirby 64's Burn-Cutter Sword, at 34 triangles. Smash 64's Link Kirby
    carries a boomerang, which is wrong for a character replacing the Master Sword.

All three are Nintendo's and stay out of git. This script is the committed part, so the
result is reproducible from your own copies. Fetch them from The Models Resource:
Kirby 64 assets 292078 and 533304, and Super Smash Bros. asset 283456.

The result is rigged to Adult Link's 21 limbs, rigidly, one bone per vertex, because
Link is a flex skeleton of LOD limbs that draws one display list per limb.
"""

from __future__ import annotations

import argparse
import os
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_skeleton import DEFORM  # noqa: E402
from sword_kirby import BONE_REST, build_reference_armature  # noqa: E402

# Smash 64 textures that make up Link's cap, checked by average colour rather than by
# guessing from the group names: the two green cloth materials and the gold band.
CAP_TEXTURES = ("2367eecf", "brim")

# Where Kirby sits against the reference rig. Link's head bone is near z 0.92, and
# Kirby standing 0.84 high reads correctly without overtopping it.
TARGET_HEIGHT = 0.84


def clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_obj(path: str) -> list[bpy.types.Object]:
    """Import an OBJ and bake its axis conversion into the mesh.

    The importer records the Y-up to Z-up conversion as a rotation on the object rather
    than in the mesh data. Anything that later assigns rotation_euler would wipe that
    conversion and leave the model lying on its side, which is exactly what sent the
    sword's blade off along the wrong axis. Applying it here means every transform
    afterwards starts from identity and behaves the way the maths says it should.
    """
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=os.path.abspath(path), forward_axis="NEGATIVE_Z", up_axis="Y")
    fresh = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]

    bpy.ops.object.select_all(action="DESELECT")
    for o in fresh:
        o.select_set(True)
    if fresh:
        bpy.context.view_layer.objects.active = fresh[0]
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return fresh


def join(objects: list[bpy.types.Object], name: str) -> bpy.types.Object:
    for o in bpy.data.objects:
        o.select_set(False)
    for o in objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    if len(objects) > 1:
        bpy.ops.object.join()
    result = bpy.context.view_layer.objects.active
    result.name = name
    result.data.name = name
    return result


def world_bounds(obj: bpy.types.Object) -> tuple[Vector, Vector]:
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ v.co for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def uses_cap_texture(material: bpy.types.Material | None) -> bool:
    if material is None or not material.use_nodes:
        return False
    for node in material.node_tree.nodes:
        image = getattr(node, "image", None)
        if image is None:
            continue
        name = os.path.basename(image.filepath or image.name).lower()
        if any(hint in name for hint in CAP_TEXTURES):
            return True
    return False


def keep_only_cap(obj: bpy.types.Object) -> int:
    """Delete every face of the Smash 64 model that is not part of Link's cap."""
    cap_slots = {i for i, m in enumerate(obj.data.materials) if uses_cap_texture(m)}
    if not cap_slots:
        raise ValueError("no cap materials found; the green cloth texture did not match")

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    doomed = [f for f in bm.faces if f.material_index not in cap_slots]
    bmesh.ops.delete(bm, geom=doomed, context="FACES")
    kept = len(bm.faces)
    bm.to_mesh(obj.data)
    bm.free()

    for v in [v for v in obj.data.vertices]:
        pass
    obj.data.update()
    return kept


def place_cap(
    cap: bpy.types.Object,
    donor_lo: Vector,
    donor_hi: Vector,
    body_lo: Vector,
    body_hi: Vector,
) -> None:
    """Fit the cap to the new body by matching the two whole Kirbys, not the cap.

    The cap must be scaled by how much smaller the Kirby 64 Kirby is than the Smash 64
    one it came off, which means comparing the two complete bodies. Scaling by the cap's
    own bounding box instead makes it enormous, because the cap is a narrow shape with a
    long drooping tail and nothing like the width of the head it sits on.

    Both models stand with their feet at z 0 and are centred on x, so once the cap is
    scaled it only has to keep the position it had on its donor.
    """
    donor_width = max(donor_hi.x - donor_lo.x, 1e-6)
    body_width = body_hi.x - body_lo.x
    # Kirby 64's head is rounder than the Smash 64 one the cap was cut from, so a
    # straight width match sits the brim across his eyes. Easing it in and lifting it
    # puts the brim on his forehead and lets the green show.
    scale = (body_width / donor_width) * 0.90
    lift = (body_hi.z - body_lo.z) * 0.10

    donor_centre = Vector(((donor_lo.x + donor_hi.x) * 0.5, (donor_lo.y + donor_hi.y) * 0.5, donor_lo.z))
    body_centre = Vector(((body_lo.x + body_hi.x) * 0.5, (body_lo.y + body_hi.y) * 0.5, body_lo.z))

    cap.scale = (scale, scale, scale)
    bpy.context.view_layer.update()

    # Where the cap sat on the donor, carried across at the new scale, then lifted.
    cap.location = body_centre - donor_centre * scale + Vector((0.0, 0.0, lift))

    bpy.ops.object.select_all(action="DESELECT")
    cap.select_set(True)
    bpy.context.view_layer.objects.active = cap
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def find_hand(body: bpy.types.Object, sign: float) -> Vector:
    """Locate the tip of one of Kirby's mittens from the body mesh itself.

    Guessing a grip position from the bounding box puts the sword in mid-air, because
    the widest point of the box is the mitten only at one particular height. This takes
    the vertices furthest out on that side, ignoring the feet, and averages them.
    """
    pts = [body.matrix_world @ v.co for v in body.data.vertices]
    lo_z = min(p.z for p in pts)
    hi_z = max(p.z for p in pts)
    above_feet = [p for p in pts if p.z > lo_z + (hi_z - lo_z) * 0.25]
    if not above_feet:
        above_feet = pts

    extreme = max(p.x * sign for p in above_feet)
    edge = (max(p.x for p in above_feet) - min(p.x for p in above_feet)) * 0.12
    mitten = [p for p in above_feet if p.x * sign >= extreme - edge]

    centre = Vector((0.0, 0.0, 0.0))
    for p in mitten:
        centre += p
    return centre / len(mitten)


def place_sword(sword: bpy.types.Object, body: bpy.types.Object,
                body_lo: Vector, body_hi: Vector) -> None:
    """Put the sword in Kirby's right hand, angled up and out.

    Both models are Kirby 64 rips, but the two rips were exported at different scales,
    so the sword is sized against Kirby's own height rather than trusted to arrive
    correct. Blade length is set to a share of his height that reads as a sword he can
    actually swing.
    """
    import math

    body_width = body_hi.x - body_lo.x
    body_height = body_hi.z - body_lo.z

    lo, hi = world_bounds(sword)
    length = max(hi.z - lo.z, hi.y - lo.y, hi.x - lo.x, 1e-6)
    sword.scale = [(body_height * 0.80) / length] * 3

    # Lean the blade out to Kirby's right and a little forward. Tilting it backward
    # instead hides the whole blade behind his body and cap, leaving only the hilt
    # showing, which reads as a stick rather than a sword.
    sword.rotation_euler = (math.radians(10.0), math.radians(-26.0), 0.0)
    bpy.context.view_layer.update()

    # Kirby's right is negative x here. Sink the grip slightly into the mitten so the
    # sword is held rather than balanced against it.
    hand = find_hand(body, -1.0)
    hand.x += body_width * 0.02
    hand.z -= body_height * 0.06

    lo, hi = world_bounds(sword)
    sword.location = (
        sword.location.x + (hand.x - (lo.x + hi.x) * 0.5),
        sword.location.y + (hand.y - (lo.y + hi.y) * 0.5),
        sword.location.z + (hand.z - lo.z),
    )
    bpy.ops.object.select_all(action="DESELECT")
    sword.select_set(True)
    bpy.context.view_layer.objects.active = sword
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    lo, hi = world_bounds(sword)
    print(f"      grip at z {hand.z:.3f}, blade reaches z {hi.z:.3f}, "
          f"head top z {body_hi.z:.3f}")


def fit_to_rig(obj: bpy.types.Object) -> float:
    """Scale the assembled character to Link's height and stand it on the floor."""
    lo, hi = world_bounds(obj)
    height = max(hi.z - lo.z, 1e-6)
    scale = TARGET_HEIGHT / height
    obj.scale = (scale, scale, scale)
    bpy.context.view_layer.update()

    lo, hi = world_bounds(obj)
    centre = (lo + hi) * 0.5
    obj.location = (
        obj.location.x - centre.x,
        obj.location.y - centre.y,
        obj.location.z - lo.z,
    )
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return scale


def limb_for(p: Vector, lo: Vector, span: Vector, is_cap: bool, is_sword: bool) -> str:
    """Pick the limb that owns a face, from where it sits in the character."""
    if is_cap:
        return "Hat"
    if is_sword:
        return "RightHand"

    fx = (p.x - lo.x) / span.x if span.x else 0.5
    fz = (p.z - lo.z) / span.z if span.z else 0.5

    # Feet, the bottom fifth.
    if fz < 0.20:
        return "LeftFoot" if fx > 0.5 else "RightFoot"
    if fz < 0.30 and (fx > 0.70 or fx < 0.30):
        return "LeftShin" if fx > 0.5 else "RightShin"

    # Arms, the outer fifth.
    if fx > 0.80:
        return "LeftHand" if fz > 0.40 else "LeftForearm"
    if fx < 0.20:
        return "RightHand" if fz > 0.40 else "RightForearm"

    # Body, bottom to top along Link's spine.
    if fz < 0.28:
        return "Waist"
    if fz < 0.48:
        return "Torso"
    if fz < 0.60:
        return "Collar"
    return "Head"


def assign_limbs(obj: bpy.types.Object, cap_slots: set[int], sword_slots: set[int]) -> dict[str, int]:
    mesh = obj.data
    verts = [v.co for v in mesh.vertices]

    # Normalise against the body alone. Including the sword and cap stretches the box,
    # which drags every threshold off and sends body faces to the wrong limb.
    body_verts = set()
    for poly in mesh.polygons:
        if poly.material_index not in cap_slots and poly.material_index not in sword_slots:
            body_verts.update(poly.vertices)
    sample = [verts[i] for i in body_verts] or verts

    lo = Vector((min(v.x for v in sample), min(v.y for v in sample), min(v.z for v in sample)))
    hi = Vector((max(v.x for v in sample), max(v.y for v in sample), max(v.z for v in sample)))
    span = hi - lo

    owner: dict[int, str] = {}
    for poly in mesh.polygons:
        centre = Vector((0.0, 0.0, 0.0))
        for vi in poly.vertices:
            centre += verts[vi]
        centre /= len(poly.vertices)
        limb = limb_for(centre, lo, span,
                        poly.material_index in cap_slots,
                        poly.material_index in sword_slots)
        for vi in poly.vertices:
            owner[vi] = limb

    groups = {name: obj.vertex_groups.new(name=name) for name in DEFORM}
    for index, limb in owner.items():
        groups[limb].add([index], 1.0, "REPLACE")

    counts = {name: 0 for name in DEFORM}
    for limb in owner.values():
        counts[limb] += 1
    return counts


def fill_empty(obj: bpy.types.Object, counts: dict[str, int]) -> list[str]:
    """Hidden wedges for limbs Kirby's shape has no room for.

    Link's actor misbehaves if a deformable bone has nothing weighted to it, so the
    limbs Kirby lacks get a small cube tucked toward the middle of the body.
    """
    empty = [name for name, n in counts.items() if n == 0]
    if not empty:
        return []

    verts = [v.co for v in obj.data.vertices]
    centre = Vector((0.0, 0.0, 0.0))
    for v in verts:
        centre += v
    centre /= max(1, len(verts))
    lo = Vector((min(v.x for v in verts), min(v.y for v in verts), min(v.z for v in verts)))
    hi = Vector((max(v.x for v in verts), max(v.y for v in verts), max(v.z for v in verts)))

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    added: dict[str, list[int]] = {}
    for name in empty:
        head, tail = BONE_REST[name]
        midpoint = (Vector(head) + Vector(tail)) * 0.5
        midpoint.x = min(max(midpoint.x, lo.x), hi.x)
        midpoint.z = min(max(midpoint.z, lo.z), hi.z)
        inward = centre - midpoint
        if inward.length > 0.0:
            midpoint = midpoint + inward.normalized() * min(0.07, inward.length * 0.7)
        before = set(bm.verts)
        bmesh.ops.create_cube(bm, size=0.025)
        new = [v for v in bm.verts if v not in before]
        for v in new:
            v.co = v.co + midpoint
        added[name] = new

    bm.verts.index_update()
    added_idx = {name: [v.index for v in vs] for name, vs in added.items()}
    bm.to_mesh(obj.data)
    bm.free()

    for name, indices in added_idx.items():
        group = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
        group.add(indices, 1.0, "REPLACE")
    return empty


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", default=".assets")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.assets)
    body_obj = os.path.join(root, "kirby64", "k64", "Kirby", "kirby.obj")
    cap_obj = os.path.join(root, "ssb64", "extracted", "kirbylink.obj")
    sword_obj = os.path.join(root, "kirby64", "sword", "burncuttersword.obj")
    for p in (body_obj, cap_obj, sword_obj):
        if not os.path.isfile(p):
            print(f"FAIL: missing {p}")
            sys.exit(1)

    clear()

    body = join(import_obj(body_obj), "Body")
    body_tris = sum(len(p.vertices) - 2 for p in body.data.polygons)
    body_lo, body_hi = world_bounds(body)
    print(f"body  {len(body.data.vertices):>4} verts  {body_tris:>4} tris   Kirby 64")

    cap = join(import_obj(cap_obj), "Cap")
    # Measure the whole donor Kirby before culling: the cap's scale comes from how the
    # two complete bodies compare, not from the cap's own extents.
    donor_lo, donor_hi = world_bounds(cap)
    kept = keep_only_cap(cap)
    print(f"cap   {len(cap.data.vertices):>4} verts  {kept:>4} tris   Smash 64, cap only")
    place_cap(cap, donor_lo, donor_hi, body_lo, body_hi)

    sword = join(import_obj(sword_obj), "Sword")
    sword_tris = sum(len(p.vertices) - 2 for p in sword.data.polygons)
    print(f"sword {len(sword.data.vertices):>4} verts  {sword_tris:>4} tris   Kirby 64 Burn-Cutter")
    place_sword(sword, body, body_lo, body_hi)

    cap_names = {m.name for m in cap.data.materials if m}
    sword_names = {m.name for m in sword.data.materials if m}

    kirby = join([body, cap, sword], "SwordKirby")
    cap_slots = {i for i, m in enumerate(kirby.data.materials) if m and m.name in cap_names}
    sword_slots = {i for i, m in enumerate(kirby.data.materials) if m and m.name in sword_names}

    scale = fit_to_rig(kirby)
    tris = sum(len(p.vertices) - 2 for p in kirby.data.polygons)
    lo, hi = world_bounds(kirby)
    print(f"\nassembled: {len(kirby.data.vertices)} verts, {tris} tris, "
          f"{len(kirby.data.materials)} materials, scaled {scale:.2f}")
    print(f"size {hi.x - lo.x:.2f} wide x {hi.y - lo.y:.2f} deep x {hi.z - lo.z:.2f} tall")

    arm = build_reference_armature()
    counts = assign_limbs(kirby, cap_slots, sword_slots)
    filled = fill_empty(kirby, counts)

    kirby.parent = arm
    modifier = kirby.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = arm

    print("\nlimb coverage:")
    for name in DEFORM:
        note = "  (hidden filler)" if name in filled else ""
        print(f"  {name:<15} {counts.get(name, 0):>4} verts{note}")
    print(f"OK: all {len(DEFORM)} deformable limbs carry geometry")

    if args.out:
        path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
