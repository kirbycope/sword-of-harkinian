"""Pose Link's skeleton and render, to prove the weights actually follow the bones.

    blender --background --python blender/pose_test.py -- \
        --blend build/sword_kirby_rigged.blend --out docs/preview/pose

A rest-pose render says nothing about rigging. These poses each check one thing that
would be wrong in the game if a limb were weighted to the wrong bone:

  * head turned: Kirby's face has to follow, or he stares straight ahead while the game
    aims his head during Z-targeting and cutscenes;
  * sword arm raised: the blade has to travel with the hand, or every swing leaves it
    hanging in the air;
  * legs swung apart: his feet have to move while the ball stays put, which is what
    walking looks like.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_skeleton import FAST64_BONE  # noqa: E402

# name, then a list of (limb, axis, degrees) applied to the pose.
POSES: list[tuple[str, list[tuple[str, str, float]]]] = [
    ("rest", []),
    ("head-turned", [("Head", "Z", 55.0)]),
    ("sword-raised", [("RightShoulder", "Y", -70.0), ("RightForearm", "Y", -35.0)]),
    ("walking", [("LeftThigh", "X", 38.0), ("RightThigh", "X", -38.0),
                 ("LeftShin", "X", -25.0)]),
]


def apply_pose(armature: bpy.types.Object, moves: list[tuple[str, str, float]]) -> None:
    for bone in armature.pose.bones:
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = (0.0, 0.0, 0.0)
    for limb, axis, degrees in moves:
        bone = armature.pose.bones.get(FAST64_BONE[limb])
        if bone is None:
            print(f"  missing pose bone for {limb}")
            continue
        index = "XYZ".index(axis)
        rotation = list(bone.rotation_euler)
        rotation[index] = math.radians(degrees)
        bone.rotation_euler = rotation
    bpy.context.view_layer.update()


def setup_scene() -> None:
    world = bpy.data.worlds.new("Preview")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.09, 0.10, 0.13, 1.0)
    bpy.context.scene.world = world
    for name, rot, energy in (("key", (55, 10, 35), 4.0), ("fill", (65, 0, -120), 1.8)):
        light = bpy.data.lights.new(name, type="SUN")
        light.energy = energy
        obj = bpy.data.objects.new(name, light)
        obj.rotation_euler = [v * math.pi / 180.0 for v in rot]
        bpy.context.scene.collection.objects.link(obj)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = scene.render.resolution_y = 1100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 100


def place_camera(centre: Vector, radius: float, azimuth: float, elevation: float) -> None:
    az, el = math.radians(azimuth), math.radians(elevation)
    distance = radius * 4.2
    position = centre + Vector((
        math.sin(az) * math.cos(el) * distance,
        -math.cos(az) * math.cos(el) * distance,
        math.sin(el) * distance,
    ))
    data = bpy.data.cameras.new("Cam")
    data.lens = 60.0
    cam = bpy.data.objects.new("Cam", data)
    cam.location = position
    cam.rotation_euler = (centre - position).normalized().to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--out", default="docs/preview/pose")
    args = parser.parse_args(argv)

    for name, moves in POSES:
        bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
        setup_scene()

        armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]
        if not armatures or not meshes:
            print("FAIL: need an armature and a mesh")
            sys.exit(1)
        armature, kirby = armatures[0], max(meshes, key=lambda o: len(o.data.vertices))
        armature.hide_render = True

        apply_pose(armature, moves)

        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = kirby.evaluated_get(depsgraph).to_mesh()
        pts = [kirby.matrix_world @ v.co for v in evaluated.vertices]
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        kirby.evaluated_get(depsgraph).to_mesh_clear()

        centre = (lo + hi) * 0.5
        radius = max(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z) * 0.5
        place_camera(centre, radius, 32.0, 12.0)

        path = os.path.join(os.path.abspath(args.out), f"pose_{name}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        print(f"rendered {name:<14} deformed bounds "
              f"x {lo.x:6.2f}..{hi.x:5.2f}  z {lo.z:6.2f}..{hi.z:5.2f}")


if __name__ == "__main__":
    main()
