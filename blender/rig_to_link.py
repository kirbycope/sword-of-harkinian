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
    "arms": ("LeftHand", "RightHand"),
    "sword": ("RightHand",),
}
# The cap is not in that table because it cannot be decided independently: it has to
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


# Which bone each appendage sits on, and how it splits left from right.
#
# The mittens sit exactly on Link's hand joints, with no offset, and that only works
# because the mod rewrites every animation so the hand joints stay beside Kirby's body
# at a stubby reach (see tools/carry_fk.py). Seated on the raw skeleton they would land
# on his upper flank, where they read as ears, and any offset from the joint is an arc
# that every arm rotation swings them through. With the animations posed, the joint
# itself is where the mitten belongs, so the mitten goes there and nowhere else, and it
# follows the hand into a sword swing or onto a carried pot.
APPENDAGES = (
    ("feet", "LeftFoot", "RightFoot", 0.0),
    ("arms", "LeftHand", "RightHand", 0.0),
)


# A hole worth closing is a short loop of boundary edges. Anything longer is a shape
# that is meant to be open, like the underside of the cap.
#
# The Kirby 64 body has two of these, one either side at mid height, which are the sockets
# the mittens plug into. They were invisible while the mittens sat in them, and moving the
# mittens down his flank uncovered them, so he walks around with a hole in each side.
# Raised to cover the cap's own rim as well. A hat is normally open underneath and the
# head fills it, but this cap is oversized for the ball it sits on, so the rim is left
# exposed and you can see up inside it from behind. Closing it costs two triangles and
# nothing is ever seen through it again.
HOLE_EDGES = 16


def close_small_holes(kirby: bpy.types.Object) -> str:
    """Fill short open loops, so the body is airtight wherever something was moved off it."""
    import bmesh

    mesh = kirby.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.active

    boundary = [e for e in bm.edges if len(e.link_faces) == 1]
    if not boundary:
        bm.free()
        return "no open edges; the body was already airtight"

    adjacency: dict[int, list] = {}
    for edge in boundary:
        for vert in edge.verts:
            adjacency.setdefault(vert.index, []).append(edge)

    known_uv: dict[int, tuple[float, float]] = {}
    if uv_layer is not None:
        for face in bm.faces:
            for loop in face.loops:
                known_uv.setdefault(loop.vert.index, tuple(loop[uv_layer].uv))

    visited, filled, skipped = set(), 0, 0
    for edge in boundary:
        if edge.index in visited:
            continue
        loop_edges, stack = [], [edge]
        visited.add(edge.index)
        while stack:
            current = stack.pop()
            loop_edges.append(current)
            for vert in current.verts:
                for other in adjacency.get(vert.index, []):
                    if other.index not in visited:
                        visited.add(other.index)
                        stack.append(other)
        if len(loop_edges) > HOLE_EDGES:
            skipped += 1
            continue
        try:
            made = bmesh.ops.holes_fill(bm, edges=loop_edges,
                                        sides=len(loop_edges)).get("faces") or []
        except (TypeError, RuntimeError):
            continue
        source = loop_edges[0].link_faces[0]
        for face in made:
            face.material_index = source.material_index
            face.smooth = source.smooth
            if uv_layer is not None:
                for loop in face.loops:
                    uv = known_uv.get(loop.vert.index)
                    if uv is not None:
                        loop[uv_layer].uv = uv
        filled += len(made)

    if filled:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return (f"closed {filled} face(s) over short open loops, "
            f"left {skipped} long loop(s) open as intended")


# Kirby's face, as a box in the body texture's own coordinates, and the name the split
# material takes. The box is measured, not chosen: eighteen of the nineteen Kirby 64 face
# textures put every feature inside x 34 to 93 and y 36 to 93 of a 128 by 128 image, and
# tools/face_textures.py --measure re-derives it and fails if the assets ever move it.
#
# Blender's V runs the other way from the image's rows, hence the subtractions.
FACE_BOX_PX = (34, 36, 94, 94)
FACE_MATERIAL = "kirby_face"


def split_face(kirby: bpy.types.Object) -> str:
    """Move the face triangles onto their own material, keyed to the game's eye tile.

    The face used to share the body's texture, and the body's texture has to fit the
    whole of him into 32 by 32, so the face got about fifteen pixels across and looked
    like it. Ocarina of Time already keeps the eyes on a tile of their own, 64 by 32,
    swapped every frame through segment 8 so Link can blink: giving Kirby's face that
    tile is four times the width and twice the height for free, and it comes with the
    game's own expression logic attached.

    Nothing is added to the mesh. The triangles that already carry the face are simply
    reassigned, so they keep the ball's curvature and its normals and cannot seam or
    z-fight the way a flat decal laid over the top would.

    Three tests pick them, and the second and third are not optional. The texture box
    alone catches 164 triangles when the face is 86, because the Kirby 64 ball is
    unwrapped so that the back of his head samples the same patch of the texture as the
    front, and because some of the cap's triangles happen to land in the same box of
    their own image. Reassigning those gave him a second face on the back of his head
    and a smear of eye across the hat, which is what a hard line between face and body
    looks like from the front.
    """
    mesh = kirby.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return "no UV map, so the face cannot be split"

    roles = roles_of(kirby)
    ball = roles.get("ball") or set()
    cap = roles.get("cap") or set()
    if not ball:
        return "no ball role, so the face cannot be told from the hat"

    # Which way he faces, measured rather than assumed. Link's cap has a long tail, and
    # the tail hangs down the back, so whichever side the cap reaches further than the
    # ball does is behind him.
    def reach(members, sign):
        pts = [(kirby.matrix_world @ mesh.vertices[i].co).y for i in members]
        return (max(pts) if sign > 0 else -min(pts)) if pts else 0.0

    behind = 1.0
    if cap:
        behind = 1.0 if reach(cap, 1) - reach(ball, 1) > reach(cap, -1) - reach(ball, -1) else -1.0

    x0, y0, x1, y1 = FACE_BOX_PX
    u0, u1 = x0 / 128.0, x1 / 128.0
    v0, v1 = 1.0 - y1 / 128.0, 1.0 - y0 / 128.0

    material = bpy.data.materials.get(FACE_MATERIAL) or bpy.data.materials.new(FACE_MATERIAL)
    if material.name not in [m.name for m in mesh.materials if m]:
        mesh.materials.append(material)
    index = next(i for i, m in enumerate(mesh.materials) if m and m.name == FACE_MATERIAL)

    chosen = []
    for poly in mesh.polygons:
        if poly.material_index == index:
            continue
        if not all(v in ball for v in poly.vertices):      # the ball, not the hat or feet
            continue
        if poly.normal.y * behind >= 0.0:                  # facing forward, not backward
            continue
        u = sum(uv_layer.data[l].uv[0] for l in poly.loop_indices) / poly.loop_total
        v = sum(uv_layer.data[l].uv[1] for l in poly.loop_indices) / poly.loop_total
        if u0 <= u <= u1 and v0 <= v <= v1:
            chosen.append(poly)

    # Remember which image the face came off, so the packer can paint the face out of
    # it. Left in, the face is averaged into the body texture when that is squeezed down
    # to 32 by 32, which darkens the pink for a few texels all round the face box; the
    # body triangles next to the face then sample those darkened texels and draw a dim
    # ring around the patch. That ring is the hard line between face and body.
    sources = {mesh.materials[i].name for i in {p.material_index for p in chosen}
               if i < len(mesh.materials) and mesh.materials[i]}
    mesh["face_source_material"] = sorted(sources)[0] if sources else ""
    mesh["face_box_px"] = list(FACE_BOX_PX)

    for poly in chosen:
        poly.material_index = index
        for loop in poly.loop_indices:
            u, v = uv_layer.data[loop].uv
            uv_layer.data[loop].uv = ((u - u0) / (u1 - u0), (v - v0) / (v1 - v0))

    mesh.update()
    return (f"face split onto {FACE_MATERIAL!r}: {len(chosen)} front triangle(s) of the "
            f"ball moved onto the game's own 64 by 32 eye tile "
            f"(back is {'+Y' if behind > 0 else '-Y'})")


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

    lo, hi = world_bounds(kirby)
    body_height = max(hi.z - lo.z, 1e-9)

    for role, left_limb, right_limb, drop in APPENDAGES:
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

            # A hand bone is a leaf, so its tail is wherever the importer put it; the
            # joint is the head, and that is where the game puts the hand's origin.
            target = seg[0].copy() if role == "arms" else (seg[0] + seg[1]) * 0.5
            target.z -= body_height * drop
            shift = target - centroid
            for i in group:
                kirby.data.vertices[i].co += shift
            moved.append(f"{limb} by {shift.length:.2f}"
                         + (f", dropped {body_height * drop:.2f}" if drop else ""))

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

        # The cap joins the ball. Link's hat bone is a child of his head, so a cap left
        # there would swing away from a ball that no longer follows the head. It is a
        # shame, because the hat limb was the neatest part of the idea, but a cap welded
        # to the skull beats one that detaches every time the game aims his head.
        #
        # The mittens do not: they ride the hand joints, see APPENDAGES, so they hold
        # the sword and the pot, and the mod poses every animation to keep them home.
        for role in ("cap",):
            members = roles.get(role) or set()
            for i in members:
                owner[i] = best
            if members:
                print(f"  {role} rides {best!r} too, so it cannot separate from the body")

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
    print(f"  {close_small_holes(kirby)}")
    print(f"  {split_face(kirby)}")

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
