"""Rig Sword Kirby to Adult Link's real skeleton.

    blender --background --python blender/rig_to_link.py -- \
        --skeleton build/link_skeleton.blend --assets .assets --out build/sword_kirby_rigged.blend

Run `import_link_skeleton.py` first: this starts from the file that produced, which holds
the skeleton Fast64 imported from the decompilation, with the limb translations that only
exist in ROM data.

What this replaces: the first version weighted Kirby by where a face landed in his own
bounding box, with thresholds like "the outer fifth is an arm". That works until the
proportions change, and it has no idea where Link's bones actually are. Here every piece
is matched against real bone positions instead:

  * the feet, arms, ball, cap and sword are identified by what they are, carried from
    assembly as role tags rather than inferred from position;
  * within a role, the limb is the nearest real bone of the plausible set, measured to
    the bone segment rather than to its head;
  * the body ball is restricted to the spine bones. Pure nearest-bone would hand the
    underside of the ball to the thigh and shin bones, which sit inside it, and those
    rotate far enough when walking to tear the ball apart.

Weights stay rigid, one bone per vertex, because Link is a flex skeleton of LOD limbs
that draws one display list per limb.
"""

from __future__ import annotations

import argparse
import os
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_sword_kirby import assemble, assemble_lod, roles_of, world_bounds  # noqa: E402
from link_skeleton import (  # noqa: E402
    DEFORM,
    FAST64_BONE,
    limb_of_bone,
    verify_against_imported,
)

# Filled in once the armature is known, since adult and child name their bones apart.
BONE_NAME: dict[str, str] = {}

# Which limbs each role is allowed to claim by proximity to the real bone.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "feet": ("LeftFoot", "RightFoot", "LeftShin", "RightShin"),
    "arms": ("LeftHand", "RightHand", "LeftForearm", "RightForearm",
             "LeftShoulder", "RightShoulder"),
    "sword": ("RightHand",),
}
# The cap is not in that table because it cannot be decided independently. It has to
# ride whichever bone the ball ends up on, which is worked out at rig time.

# The body ball goes to exactly one bone, and it is worth being plain about why.
#
# Kirby has no neck. Splitting his ball across Link's spine, by distance or by height,
# tears him in half the moment those bones rotate relative to each other, because the
# weights are rigid and nothing blends across the seam. A 55 degree head turn separated
# the crown from the face entirely.
#
# So the whole ball rides the single spine bone whose pivot sits nearest the ball's
# own centre, which keeps him rotating about himself rather than swinging around a
# pivot up near his crown. The other spine bones get hidden filler.
#
# The cost is that Kirby does not turn his head independently during Z-targeting. For a
# character who is one sphere that is the right trade: a ball that swings off its own
# centre looks far worse than a ball that does not glance sideways.
SPINE = ("Waist", "Torso", "Collar", "Head")

# How tall Kirby stands relative to Link. He is a ball, so matching Link's full height
# would make him enormous; this keeps his crown near Link's, which is what the camera
# and the animations are built around.
# Kirby is a short round ball, not a humanoid, so he stands well below Link's
# height. This is a share of whichever Link he replaces: the child geometry is
# scaled when the mod is written, so the same number holds for both.
HEIGHT_OF_LINK = 0.62


def bone_names(armature: bpy.types.Object) -> dict[str, str]:
    """Map each limb to the bone that represents it in this particular armature.

    The adult and child skeletons name their bones differently, so the names are read
    off the armature in hand rather than taken from the adult table.
    """
    out: dict[str, str] = {}
    for bone in armature.data.bones:
        limb = limb_of_bone(bone.name)
        if limb is not None:
            out[limb] = bone.name
    return out


def bone_segments(armature: bpy.types.Object) -> dict[str, tuple[Vector, Vector]]:
    """Each limb's bone as a head-to-tail segment in world space."""
    out: dict[str, tuple[Vector, Vector]] = {}
    matrix = armature.matrix_world
    for bone in armature.data.bones:
        limb = limb_of_bone(bone.name)
        if limb is not None:
            out[limb] = (matrix @ bone.head_local, matrix @ bone.tail_local)
    return out


def distance_to_segment(p: Vector, a: Vector, b: Vector) -> float:
    ab = b - a
    length_sq = ab.dot(ab)
    if length_sq <= 1e-12:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / length_sq))
    return (p - (a + ab * t)).length


def nearest_limb(p: Vector, segments: dict[str, tuple[Vector, Vector]],
                 allowed: tuple[str, ...]) -> str:
    best = None
    best_d = float("inf")
    for limb in allowed:
        seg = segments.get(limb)
        if seg is None:
            continue
        d = distance_to_segment(p, seg[0], seg[1])
        if d < best_d:
            best_d, best = d, limb
    if best is None:
        raise ValueError(f"none of {allowed} is present in the skeleton")
    return best


# Which bone each appendage should sit on, and how it splits left from right.
APPENDAGES = (
    ("arms", "LeftHand", "RightHand"),
    ("feet", "LeftFoot", "RightFoot"),
)


def seat_appendages(kirby: bpy.types.Object,
                    segments: dict[str, tuple[Vector, Vector]]) -> list[str]:
    """Move the mittens and feet onto the bones that drive them.

    A part that follows a bone has to sit on that bone, or every rotation of the limb
    swings it through an arc the size of the offset. Kirby's mittens sat against his
    body while Link's hand bones are further out and lower, so the arms in his idle
    animation threw the mittens up beside his head. The same applies to the feet.

    Positions are corrected here rather than when the model is assembled, because this
    is the first point at which the real bones are known.
    """
    roles = roles_of(kirby)
    moved: list[str] = []

    for role, left_limb, right_limb in APPENDAGES:
        members = roles.get(role) or set()
        if not members:
            continue
        points = {i: kirby.matrix_world @ kirby.data.vertices[i].co for i in members}
        centre_x = sum(p.x for p in points.values()) / len(points)

        for limb, side in ((left_limb, 1.0), (right_limb, -1.0)):
            seg = segments.get(limb)
            if seg is None:
                continue
            group = [i for i, p in points.items() if (p.x - centre_x) * side > 0.0]
            if not group:
                continue

            centroid = Vector((0.0, 0.0, 0.0))
            for i in group:
                centroid += points[i]
            centroid /= len(group)

            target = (seg[0] + seg[1]) * 0.5
            shift = target - centroid
            for i in group:
                kirby.data.vertices[i].co += shift
            moved.append(f"{limb} by {shift.length:.2f}")

    kirby.data.update()
    return moved


def fit_to_skeleton(kirby: bpy.types.Object, segments: dict[str, tuple[Vector, Vector]]) -> float:
    """Scale Kirby against the real skeleton and stand him on Link's feet."""
    points = [v for seg in segments.values() for v in seg]
    link_lo_z = min(p.z for p in points)
    link_hi_z = max(p.z for p in points)
    link_height = link_hi_z - link_lo_z
    # Centre on the skeleton, not on the world origin. Fast64 parks the far-distance
    # armature ten units to the side so the two rigs do not overlap in the viewport, and
    # a mesh centred on the origin would sit that far from the bones meant to drive it.
    link_centre_x = (min(p.x for p in points) + max(p.x for p in points)) * 0.5
    link_centre_y = (min(p.y for p in points) + max(p.y for p in points)) * 0.5

    lo, hi = world_bounds(kirby)
    scale = (link_height * HEIGHT_OF_LINK) / max(hi.z - lo.z, 1e-9)
    kirby.scale = (scale, scale, scale)
    bpy.context.view_layer.update()

    lo, hi = world_bounds(kirby)
    centre = (lo + hi) * 0.5
    kirby.location = (
        kirby.location.x + (link_centre_x - centre.x),
        kirby.location.y + (link_centre_y - centre.y),
        kirby.location.z + (link_lo_z - lo.z),
    )
    bpy.ops.object.select_all(action="DESELECT")
    kirby.select_set(True)
    bpy.context.view_layer.objects.active = kirby
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return scale


def weight(kirby: bpy.types.Object, segments: dict[str, tuple[Vector, Vector]]) -> dict[str, int]:
    roles = roles_of(kirby)
    untagged = set(range(len(kirby.data.vertices)))
    for members in roles.values():
        untagged -= members
    if untagged:
        # Anything without a role is treated as body, which is the safe default: the
        # spine bones barely move relative to each other.
        roles["ball"] |= untagged
        print(f"  note: {len(untagged)} vertex/vertices had no role tag, treated as ball")

    owner: dict[int, str] = {}
    verts = [kirby.matrix_world @ v.co for v in kirby.data.vertices]

    for role, members in roles.items():
        allowed = CANDIDATES.get(role)
        if not allowed or not members:
            continue
        for i in members:
            owner[i] = nearest_limb(verts[i], segments, allowed)

    ball = roles.get("ball") or set()
    if ball:
        centroid = Vector((0.0, 0.0, 0.0))
        for i in ball:
            centroid += verts[i]
        centroid /= len(ball)

        best, best_d = None, float("inf")
        for limb in SPINE:
            seg = segments.get(limb)
            if seg is None:
                continue
            d = ((seg[0] + seg[1]) * 0.5 - centroid).length
            if d < best_d:
                best_d, best = d, limb
        print(f"  ball rides {best!r}, whose pivot is {best_d:.2f} from the ball's centre")
        for i in ball:
            owner[i] = best

        # The cap joins the ball. Link's hat bone is a child of his head, so leaving the
        # cap there would have it swing away from a ball that no longer follows the head.
        # It is a shame: the hat limb was the neatest part of the idea. But a cap welded
        # to the skull beats a cap that detaches every time the game aims his head.
        cap = roles.get("cap") or set()
        for i in cap:
            owner[i] = best
        if cap:
            print(f"  cap rides {best!r} too, so it cannot separate from the head")

    for group in list(kirby.vertex_groups):
        kirby.vertex_groups.remove(group)
    groups = {name: kirby.vertex_groups.new(name=BONE_NAME[name]) for name in DEFORM}
    for index, limb in owner.items():
        groups[limb].add([index], 1.0, "REPLACE")

    counts = {name: 0 for name in DEFORM}
    for limb in owner.values():
        counts[limb] += 1
    return counts


def fill_empty(kirby: bpy.types.Object, counts: dict[str, int],
               segments: dict[str, tuple[Vector, Vector]]) -> list[str]:
    """Hidden wedges for limbs Kirby's shape has nothing to give.

    Link's actor misbehaves if a deformable bone has nothing weighted to it. Unlike the
    first version, these sit at the real bone, so they stay inside the body where the
    bone actually is rather than at a guessed coordinate.
    """
    empty = [name for name, n in counts.items() if n == 0]
    if not empty:
        return []

    lo, hi = world_bounds(kirby)
    size = min(hi.x - lo.x, hi.z - lo.z) * 0.012

    centroid = Vector((0.0, 0.0, 0.0))
    for v in kirby.data.vertices:
        centroid += kirby.matrix_world @ v.co
    centroid /= max(1, len(kirby.data.vertices))

    bm = bmesh.new()
    bm.from_mesh(kirby.data)
    added: dict[str, list] = {}
    for name in empty:
        head, tail = segments[name]
        midpoint = (head + tail) * 0.5
        # Sitting the wedge exactly on the bone leaves it poking out for the bones near
        # the top of Kirby's silhouette, where Link's head and shoulders sit above the
        # ball's surface. Pulling it well in toward the centre keeps it buried while it
        # still travels with the right bone.
        midpoint = midpoint + (centroid - midpoint) * 0.55
        before = set(bm.verts)
        bmesh.ops.create_cube(bm, size=size)
        new = [v for v in bm.verts if v not in before]
        for v in new:
            v.co = v.co + midpoint
        added[name] = new

    bm.verts.index_update()
    indices = {name: [v.index for v in vs] for name, vs in added.items()}
    bm.to_mesh(kirby.data)
    bm.free()

    # Tag the wedges so the mod can leave them out. They exist to satisfy Fast64's
    # export, which breaks if a deformable bone has nothing weighted to it. A mod only
    # replaces display lists, so a limb with nothing to draw can simply draw nothing,
    # and shipping the wedges puts stray red geometry through the hat.
    filler_group = kirby.vertex_groups.get("role_filler") or kirby.vertex_groups.new(
        name="role_filler"
    )
    for name, idxs in indices.items():
        group = kirby.vertex_groups.get(BONE_NAME[name]) or kirby.vertex_groups.new(
            name=BONE_NAME[name]
        )
        group.add(idxs, 1.0, "REPLACE")
        filler_group.add(idxs, 1.0, "REPLACE")
    return empty


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--skeleton", required=True)
    parser.add_argument("--assets", default=".assets")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.skeleton))

    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        print("FAIL: no armature in the skeleton file")
        sys.exit(1)
    armature = armatures[0]

    problems = [] if "Child" in armature.name else verify_against_imported(armature)
    if problems:
        for p in problems:
            print(f"  MISMATCH  {p}")
        print("FAIL: the skeleton does not match the limb table")
        sys.exit(1)
    BONE_NAME.update(bone_names(armature))
    print(f"skeleton {armature.name!r}: {len(armature.data.bones)} bones, "
          f"{len(BONE_NAME)} recognised")

    # Link's own mesh is only here as a reference for the import; drop it.
    for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
        bpy.data.objects.remove(obj, do_unlink=True)

    print("\nassembling Kirby:")
    kirby = assemble(os.path.abspath(args.assets))

    segments = bone_segments(armature)
    scale = fit_to_skeleton(kirby, segments)
    lo, hi = world_bounds(kirby)
    link_z = [v.z for seg in segments.values() for v in seg]
    print(f"\nscaled Kirby by {scale:.3f}: {hi.x - lo.x:.2f} wide, {hi.z - lo.z:.2f} tall, "
          f"against Link's {max(link_z) - min(link_z):.2f}")

    for note in seat_appendages(kirby, segments):
        print(f"  seated {note}")

    counts = weight(kirby, segments)
    filled = fill_empty(kirby, counts, segments)

    kirby.parent = armature
    modifier = kirby.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature

    print("\nlimb coverage, by nearest real bone:")
    for name in DEFORM:
        note = "  (hidden filler at the real bone)" if name in filled else ""
        print(f"  {name:<15} {counts.get(name, 0):>4} verts{note}")

    missing = [n for n in DEFORM if counts.get(n, 0) == 0 and n not in filled]
    if missing:
        print(f"FAIL: limbs still empty: {missing}")
        sys.exit(1)
    tris = sum(len(p.vertices) - 2 for p in kirby.data.polygons)
    print(f"\nnear mesh: {len(kirby.data.vertices)} verts, {tris} tris, "
          f"all {len(DEFORM)} deformable limbs covered")

    # The far-distance skeleton gets the Smash 64 Kirby, which is less than half the
    # triangles and already wears the cap.
    lod_armature = getattr(getattr(armature, "ootSkeleton", None), "LOD", None)
    if lod_armature is None:
        print("\nno far skeleton on this rig, skipping the LOD mesh")
    else:
        print("\nassembling the far-distance Kirby:")
        lod = assemble_lod(os.path.abspath(args.assets))
        lod_segments = bone_segments(lod_armature)
        lod_scale = fit_to_skeleton(lod, lod_segments)
        seat_appendages(lod, lod_segments)
        lod_counts = weight(lod, lod_segments)
        lod_filled = fill_empty(lod, lod_counts, lod_segments)

        lod.parent = lod_armature
        lod_mod = lod.modifiers.new(name="Armature", type="ARMATURE")
        lod_mod.object = lod_armature

        lod_tris = sum(len(p.vertices) - 2 for p in lod.data.polygons)
        print(f"  scaled by {lod_scale:.3f}; {len(lod.data.vertices)} verts, "
              f"{lod_tris} tris, {len(lod_filled)} limb(s) filled")
        empty = [n for n in DEFORM if lod_counts.get(n, 0) == 0 and n not in lod_filled]
        if empty:
            print(f"FAIL: far mesh has empty limbs: {empty}")
            sys.exit(1)
        print(f"  far mesh covers all {len(DEFORM)} deformable limbs, "
              f"{100 * lod_tris // max(tris, 1)}% of the near mesh's triangles")

    if args.out:
        path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
