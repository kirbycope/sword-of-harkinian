"""Build a low-poly Sword Kirby for Ocarina of Time's player skeleton.

Run inside Blender:

    blender --background --python blender/sword_kirby.py -- --out build/sword_kirby.blend

Why the model is one mesh with rigid per-bone vertex groups: Adult Link is a Flex
skeleton of LOD limbs, so the game draws a separate display list per limb. Rigid,
one-bone-per-vertex weighting is the format's native shape rather than a shortcut, and
soft weights spanning two limbs cannot survive the export.

Why every limb gets geometry, including ones buried inside the ball: Fast64's own OoT
guide is explicit that when applying weights there must be geometry weighted to every
bone set to deformable, "otherwise Link's skeleton may break in game, due to the way
Link's actor handles some of its drawing code". Kirby has no thighs, shins or shoulders,
so those three pairs get small wedges hidden inside the body. They satisfy the exporter
and the actor without showing.
"""

from __future__ import annotations

import os
import sys

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_skeleton import DEFORM, LIMBS, verify_against_imported  # noqa: E402

# Kirby's palette, flat on purpose. Fast64's guide caps exported textures at 64x64 and
# recommends 32x32, so flat colour per limb is both cheaper and closer to the original.
PINK = (0.98, 0.58, 0.72, 1.0)
BLUSH = (0.90, 0.34, 0.47, 1.0)
EYE_NAVY = (0.06, 0.07, 0.20, 1.0)
MOUTH_DARK = (0.30, 0.05, 0.10, 1.0)
FOOT_RED = (0.78, 0.13, 0.22, 1.0)
CAP_GREEN = (0.16, 0.48, 0.20, 1.0)
SHEATH_BROWN = (0.36, 0.24, 0.16, 1.0)
BLADE_GREY = (0.78, 0.80, 0.85, 1.0)

# Reference rest pose, Blender units, Z up, for a roughly one-unit-tall Link. These are
# placeholders for previewing and for proving the weighting: the real limb translations
# live in ROM data and arrive with a Fast64 "Import Skeleton: Adult Link". The finished
# mesh is then re-pointed at that genuine armature by bind_to_imported().
BONE_REST: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "Root":            ((0.00, 0.00, 0.00), (0.00, 0.00, 0.06)),
    "Waist":           ((0.00, 0.00, 0.46), (0.00, 0.00, 0.53)),
    "LowerControl":    ((0.00, 0.00, 0.46), (0.00, 0.00, 0.39)),
    "RightThigh":      ((-0.09, 0.00, 0.42), (-0.09, 0.00, 0.28)),
    "RightShin":       ((-0.09, 0.00, 0.28), (-0.09, 0.00, 0.14)),
    "RightFoot":       ((-0.09, 0.00, 0.14), (-0.09, 0.09, 0.05)),
    "LeftThigh":       ((0.09, 0.00, 0.42), (0.09, 0.00, 0.28)),
    "LeftShin":        ((0.09, 0.00, 0.28), (0.09, 0.00, 0.14)),
    "LeftFoot":        ((0.09, 0.00, 0.14), (0.09, 0.09, 0.05)),
    "UpperControl":    ((0.00, 0.00, 0.53), (0.00, 0.00, 0.58)),
    "Torso":           ((0.00, 0.00, 0.58), (0.00, 0.00, 0.70)),
    "Collar":          ((0.00, 0.00, 0.70), (0.00, 0.00, 0.78)),
    "Head":            ((0.00, 0.00, 0.78), (0.00, 0.00, 0.92)),
    "Hat":             ((0.00, 0.02, 0.88), (0.00, 0.30, 1.00)),
    "LeftShoulder":    ((0.12, 0.00, 0.74), (0.28, 0.00, 0.70)),
    "LeftForearm":     ((0.28, 0.00, 0.70), (0.40, 0.00, 0.66)),
    "LeftHand":        ((0.40, 0.00, 0.66), (0.50, 0.00, 0.63)),
    "RightShoulder":   ((-0.12, 0.00, 0.74), (-0.28, 0.00, 0.70)),
    "RightForearm":    ((-0.28, 0.00, 0.70), (-0.40, 0.00, 0.66)),
    "RightHand":       ((-0.40, 0.00, 0.66), (-0.50, 0.00, 0.63)),
    "Sheath":          ((0.00, 0.10, 0.66), (0.00, 0.16, 0.80)),
}

# Kirby's body is one ball spanning four limbs, bottom to top, following Link's own
# spine. The split is by share of the sphere's horizontal rings rather than by height,
# because a cutoff expressed as a height lands between rings and hands a limb a single
# ring of vertices, which pinches when that limb rotates.
#
# Head deliberately takes the largest share. Kirby's face sits around the middle of his
# ball and has to be owned by the head limb or he will not turn to look at anything:
# Link's head bone is what the game aims during Z-targeting and cutscenes.
BALL_CENTRE = Vector((0.0, 0.0, 0.50))
BALL_RADIUS = 0.42
BODY_BANDS: list[tuple[str, float]] = [
    ("Waist", 0.25),
    ("Torso", 0.20),
    ("Collar", 0.15),
    ("Head", 0.40),
]

# Limbs Kirby's silhouette has no room for. Each gets a hidden wedge pulled in toward
# the middle of the ball, where the body surface conceals it.
HIDDEN_FILLER = (
    "RightThigh", "RightShin", "LeftThigh", "LeftShin",
    "RightShoulder", "LeftShoulder",
)


def on_ball(azimuth_deg: float, elevation_deg: float, sink: float = 0.0) -> Vector:
    """A point on the body ball's surface.

    Azimuth runs from Kirby's face (-Y) around to his left; elevation runs up from the
    equator. Placing the face this way keeps every feature on the surface no matter how
    the ball is resized, which absolute coordinates did not.
    """
    import math

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    radius = BALL_RADIUS - sink
    return BALL_CENTRE + Vector((
        math.sin(az) * math.cos(el) * radius,
        -math.cos(az) * math.cos(el) * radius,
        math.sin(el) * radius,
    ))


def assign_body_bands(builder: "MeshBuilder", ball_verts: list) -> dict[str, int]:
    """Hand the body ball's horizontal rings out to the four spine limbs.

    Rings are allocated by share, bottom to top, so each limb owns whole rings and no
    limb is left with a single one. Returns the ring count each limb received, which
    the caller can assert on.
    """
    rings: dict[int, list] = {}
    for v in ball_verts:
        rings.setdefault(round(v.co.z * 10000.0), []).append(v)
    order = sorted(rings)

    total = len(order)
    shares = [max(1, round(total * share)) for _name, share in BODY_BANDS]
    while sum(shares) > total:
        shares[shares.index(max(shares))] -= 1
    while sum(shares) < total:
        shares[shares.index(max(shares))] += 1

    received: dict[str, int] = {}
    cursor = 0
    for (bone, _share), count in zip(BODY_BANDS, shares):
        for key in order[cursor:cursor + count]:
            for v in rings[key]:
                builder.owner[v] = bone
        received[bone] = count
        cursor += count
    return received


def _material(name: str, colour: tuple[float, float, float, float]) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = colour
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.55
    mat.diffuse_color = colour
    return mat


class MeshBuilder:
    """Accumulates parts into one bmesh, recording which limb owns each vertex.

    Ownership is keyed by the BMVert itself rather than by its index. Every bmesh
    primitive renumbers the whole vertex table, so an index captured while building the
    body is meaningless by the time the cap is added. Indices are resolved exactly once,
    in finish(), against the final table.
    """

    def __init__(self) -> None:
        self.bm = bmesh.new()
        self.owner: dict[bmesh.types.BMVert, str] = {}
        self.materials: list[bpy.types.Material] = []
        self._slots: dict[str, int] = {}

    def _slot(self, mat: bpy.types.Material) -> int:
        if mat.name not in self._slots:
            self._slots[mat.name] = len(self.materials)
            self.materials.append(mat)
        return self._slots[mat.name]

    def _claim(self, verts, faces, bone: str, mat: bpy.types.Material) -> None:
        slot = self._slot(mat)
        for v in verts:
            self.owner[v] = bone
        for f in faces:
            f.material_index = slot

    def _added(self, before_verts, before_faces):
        self.bm.verts.index_update()
        self.bm.faces.index_update()
        verts = [v for v in self.bm.verts if v not in before_verts]
        faces = [f for f in self.bm.faces if f not in before_faces]
        return verts, faces

    def ball(
        self,
        centre: Vector,
        radius: float,
        bone: str,
        mat: bpy.types.Material,
        u: int = 12,
        v: int = 8,
        squash: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> list:
        bv, bf = set(self.bm.verts), set(self.bm.faces)
        bmesh.ops.create_uvsphere(self.bm, u_segments=u, v_segments=v, radius=radius)
        verts, faces = self._added(bv, bf)
        for x in verts:
            x.co = Vector((
                x.co.x * squash[0],
                x.co.y * squash[1],
                x.co.z * squash[2],
            )) + centre
        self._claim(verts, faces, bone, mat)
        return verts

    def box(
        self,
        centre: Vector,
        size: tuple[float, float, float],
        bone: str,
        mat: bpy.types.Material,
    ) -> list:
        bv, bf = set(self.bm.verts), set(self.bm.faces)
        bmesh.ops.create_cube(self.bm, size=1.0)
        verts, faces = self._added(bv, bf)
        for x in verts:
            x.co = Vector((
                x.co.x * size[0],
                x.co.y * size[1],
                x.co.z * size[2],
            )) + centre
        self._claim(verts, faces, bone, mat)
        return verts

    def cone(
        self,
        base: Vector,
        tip: Vector,
        radius: float,
        bone: str,
        mat: bpy.types.Material,
        segments: int = 8,
    ) -> list:
        bv, bf = set(self.bm.verts), set(self.bm.faces)
        bmesh.ops.create_cone(
            self.bm,
            cap_ends=True,
            cap_tris=True,
            segments=segments,
            radius1=radius,
            radius2=0.0,
            depth=1.0,
        )
        verts, faces = self._added(bv, bf)
        axis = tip - base
        length = axis.length
        rot = Vector((0.0, 0.0, 1.0)).rotation_difference(axis.normalized()).to_matrix()
        for x in verts:
            x.co = rot @ Vector((x.co.x, x.co.y, (x.co.z + 0.5) * length)) + base
        self._claim(verts, faces, bone, mat)
        return verts

    def finish(self, name: str) -> tuple[bpy.types.Object, dict[int, str]]:
        """Write the mesh and resolve ownership against the final vertex table."""
        self.bm.verts.index_update()
        by_index = {v.index: bone for v, bone in self.owner.items()}

        mesh = bpy.data.meshes.new(name)
        self.bm.normal_update()
        self.bm.to_mesh(mesh)
        self.bm.free()
        for mat in self.materials:
            mesh.materials.append(mat)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        return obj, by_index


def build_reference_armature(name: str = "SwordKirbySkel") -> bpy.types.Object:
    """Create an armature with Link's exact limb names, parents and deform flags."""
    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    bpy.context.scene.collection.objects.link(arm_obj)

    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")

    created: dict[str, bpy.types.EditBone] = {}
    for limb_name, _parent, draws, _pos, _struct in LIMBS:
        head, tail = BONE_REST[limb_name]
        bone = arm_data.edit_bones.new(limb_name)
        bone.head = Vector(head)
        bone.tail = Vector(tail)
        bone.use_deform = draws
        created[limb_name] = bone

    for limb_name, parent, _draws, _pos, _struct in LIMBS:
        if parent is not None:
            created[limb_name].parent = created[parent]

    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


def build_kirby_mesh() -> tuple[bpy.types.Object, dict[int, str]]:
    """Build body, hidden filler, feet, mittens, cap, face and sword.

    Returns the object and the vertex-index to limb-name map that binds it.
    """
    pink = _material("KirbyPink", PINK)
    blush = _material("KirbyBlush", BLUSH)
    # Named for the flipbook slots they take later: eyes at 0x08000000, mouth 0x09000000.
    eye_navy = _material("KirbyEyes", EYE_NAVY)
    mouth_dark = _material("KirbyMouth", MOUTH_DARK)
    red = _material("KirbyFootRed", FOOT_RED)
    green = _material("LinkCapGreen", CAP_GREEN)
    brown = _material("SheathBrown", SHEATH_BROWN)
    steel = _material("MasterSwordBlade", BLADE_GREY)

    b = MeshBuilder()

    # The body: one ball split across waist, torso, collar and head by height, so it
    # bends with Link's spine the way his own torso does.
    ball = b.ball(BALL_CENTRE, BALL_RADIUS, "Torso", pink, u=14, v=12)
    assign_body_bands(b, ball)

    # Hidden filler for the limbs Kirby does not have, pulled in toward the ball centre.
    for bone in HIDDEN_FILLER:
        head, tail = BONE_REST[bone]
        midpoint = (Vector(head) + Vector(tail)) * 0.5
        inward = BALL_CENTRE - midpoint
        if inward.length > 0.0:
            midpoint = midpoint + inward.normalized() * min(0.10, inward.length * 0.6)
        b.box(midpoint, (0.035, 0.035, 0.035), bone, pink)

    # Feet: flattened red ovals peeking out from under the ball. Kirby's whole stance.
    for bone, side in (("LeftFoot", 1.0), ("RightFoot", -1.0)):
        b.ball(
            Vector((side * 0.18, -0.03, 0.07)), 0.16, bone, red,
            u=10, v=6, squash=(0.82, 1.22, 0.50),
        )

    # Mittens. Link's hand limb is where his held sword display list lives, so Kirby's
    # mitten inherits the grip for free.
    for bone, side in (("LeftHand", 1.0), ("RightHand", -1.0)):
        b.ball(Vector((side * 0.46, -0.02, 0.56)), 0.12, bone, pink, u=8, v=6)

    # Forearm nubs, just enough to join mitten to ball.
    for bone, side in (("LeftForearm", 1.0), ("RightForearm", -1.0)):
        b.ball(
            Vector((side * 0.38, -0.01, 0.58)), 0.078, bone, pink,
            u=6, v=4, squash=(1.3, 1.0, 1.0),
        )

    # Link's cap on Link's own hat limb: the bone that makes this joke cheap to build.
    # It is a long cone sweeping back and down, not a party hat, because the hat limb
    # trails behind the head and the silhouette is most of what sells the costume.
    cap_base = BALL_CENTRE + Vector((0.0, 0.03, 0.30))
    b.cone(cap_base, Vector((0.00, 0.62, 0.58)), 0.25, "Hat", green, segments=10)
    b.ball(
        BALL_CENTRE + Vector((0.0, 0.01, 0.245)), 0.295, "Hat", green,
        u=12, v=6, squash=(1.0, 1.0, 0.40),
    )

    # Face, on the head limb, placed on the ball's surface rather than by absolute
    # height. The eye and mouth materials are the ones that later take the flipbook
    # texture references 0x08000000 and 0x09000000 from the Custom Link Process.
    for side in (1.0, -1.0):
        b.ball(
            on_ball(side * 12.0, 17.0, sink=0.035), 0.066, "Head", eye_navy,
            u=6, v=5, squash=(0.62, 0.52, 1.75),
        )
    b.ball(
        on_ball(0.0, 6.0, sink=-0.012), 0.055, "Head", mouth_dark,
        u=6, v=4, squash=(1.05, 0.70, 0.80),
    )
    for side in (1.0, -1.0):
        b.ball(
            on_ball(side * 36.0, 6.0, sink=0.035), 0.075, "Head", blush,
            u=6, v=4, squash=(1.20, 0.38, 0.72),
        )

    # Sword and sheath on Link's sheath limb, so they ride Kirby's back as shipped.
    # Slung diagonally across the lower back, clear of both the body ball and the cap,
    # which otherwise swallow it entirely. A placeholder: a real mod can keep Link's own
    # sheath display list on this limb instead.
    scabbard_low = Vector((-0.14, 0.40, 0.17))
    scabbard_high = Vector((0.30, 0.31, 0.63))
    b.cone(scabbard_low, scabbard_high, 0.062, "Sheath", brown, segments=6)
    b.cone(scabbard_high, scabbard_high + Vector((0.09, -0.02, 0.10)), 0.042, "Sheath", steel, segments=6)
    b.box(scabbard_high + Vector((0.035, -0.01, 0.035)), (0.17, 0.035, 0.035), "Sheath", steel)

    return b.finish("SwordKirby")


def bind(
    mesh_obj: bpy.types.Object,
    arm_obj: bpy.types.Object,
    owner: dict[int, str],
) -> None:
    """Attach the mesh to the armature with rigid, one-bone-per-vertex weights."""
    groups = {name: mesh_obj.vertex_groups.new(name=name) for name in DEFORM}
    for index, bone in owner.items():
        if bone in groups:
            groups[bone].add([index], 1.0, "REPLACE")

    mesh_obj.parent = arm_obj
    modifier = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = arm_obj


def bind_to_imported(
    mesh_obj: bpy.types.Object,
    imported_arm: bpy.types.Object,
) -> list[str]:
    """Re-target a finished Kirby onto a genuine Fast64 "Adult Link" import.

    The vertex groups already carry Link's limb names, so this repoints the armature
    modifier and then checks the import agrees with the limb table. A non-empty return
    means do not export yet.
    """
    mesh_obj.parent = imported_arm
    modifier = mesh_obj.modifiers.get("Armature")
    if modifier is None:
        modifier = mesh_obj.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = imported_arm
    return verify_against_imported(imported_arm)


def audit(mesh_obj: bpy.types.Object) -> dict[str, int]:
    """Count vertices per limb, so a limb with no geometry is caught before export."""
    counts = {name: 0 for name in DEFORM}
    for v in mesh_obj.data.vertices:
        for g in v.groups:
            name = mesh_obj.vertex_groups[g.group].name
            if g.weight > 0.0 and name in counts:
                counts[name] += 1
    return counts


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = argv[argv.index("--out") + 1] if "--out" in argv else None

    bpy.ops.wm.read_factory_settings(use_empty=True)

    arm = build_reference_armature()
    mesh, owner = build_kirby_mesh()
    bind(mesh, arm, owner)

    counts = audit(mesh)
    tris = sum(len(p.vertices) - 2 for p in mesh.data.polygons)
    print("=== Sword Kirby built ===")
    print(f"vertices {len(mesh.data.vertices)}  triangles {tris}")
    for name in DEFORM:
        print(f"  {name:<15} {counts[name]:>4} verts")

    missing = [name for name, n in counts.items() if n == 0]
    if missing:
        print(f"FAIL: deformable limbs with no geometry: {missing}")
        sys.exit(1)
    print(f"OK: all {len(DEFORM)} deformable limbs carry geometry")

    if out:
        path = os.path.abspath(out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
