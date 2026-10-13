"""Play one of Link's real animations on Sword Kirby and render it.

    blender --background --python blender/animate_test.py -- \
        --blend build/sword_kirby_rigged.blend --decomp decomp --version ntsc-1.2 \
        --anim gPlayerAnim_Link_m_wait --out docs/preview/motion

Posing bones by hand proves the weights are attached to something. Playing the game's
own animation data proves they are attached to the right things, in the proportions the
game will actually move them. The animation comes out of the decompilation, so it is the
same data Ocarina of Time plays.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flat_shading import flatten_all  # noqa: E402


def configure(decomp: str, version: str, anim: str) -> None:
    scene = bpy.context.scene
    for attr in ("gameEditorMode", "game_editor_mode"):
        if hasattr(scene, attr):
            try:
                setattr(scene, attr, "OOT")
            except (TypeError, ValueError):
                pass
    scene.ootDecompPath = decomp
    oot = scene.fast64.oot
    oot.oot_version = version

    settings = oot.animImportSettings
    settings.isLink = True
    settings.isCustom = False
    settings.animName = anim


def setup_scene() -> None:
    world = bpy.data.worlds.new("Motion")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.09, 0.10, 0.13, 1.0)
    bpy.context.scene.world = world

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = scene.render.resolution_y = 720
    scene.render.fps = 20
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 100


def place_camera(centre: Vector, radius: float, azimuth: float) -> None:
    az, el = math.radians(azimuth), math.radians(12.0)
    distance = radius * 4.2
    position = centre + Vector((
        math.sin(az) * math.cos(el) * distance,
        -math.cos(az) * math.cos(el) * distance,
        math.sin(el) * distance,
    ))
    data = bpy.data.cameras.new("Cam")
    data.lens = 58.0
    cam = bpy.data.objects.new("Cam", data)
    cam.location = position
    cam.rotation_euler = (centre - position).normalized().to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--decomp", default="decomp")
    parser.add_argument("--version", default="ntsc-1.2")
    parser.add_argument("--anim", default="gPlayerAnim_Link_m_wait")
    parser.add_argument("--out", default="docs/preview/motion")
    parser.add_argument("--frames", type=int, default=40)
    args = parser.parse_args(argv)

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    configure(os.path.abspath(args.decomp), args.version, args.anim)

    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    armature = armatures[0]
    kirby = max(meshes, key=lambda o: len(o.data.vertices))

    # Drop the far-distance rig so it does not sit in frame beside him.
    for other in armatures[1:]:
        for child in list(other.children):
            bpy.data.objects.remove(child, do_unlink=True)
        bpy.data.objects.remove(other, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature

    try:
        bpy.ops.object.oot_import_anim()
    except RuntimeError as error:
        print(f"FAIL: could not import {args.anim!r}: {error}")
        sys.exit(1)

    action = armature.animation_data.action if armature.animation_data else None
    if action is None:
        print("FAIL: no action after import")
        sys.exit(1)
    start, end = (int(v) for v in action.frame_range)
    print(f"imported {args.anim!r}: frames {start} to {end}")

    setup_scene()
    armature.hide_render = True
    flatten_all(kirby)

    scene = bpy.context.scene
    scene.frame_start = start
    scene.frame_end = min(end, start + args.frames - 1)

    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = kirby.evaluated_get(depsgraph).to_mesh()
    pts = [kirby.matrix_world @ v.co for v in evaluated.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    kirby.evaluated_get(depsgraph).to_mesh_clear()
    place_camera((lo + hi) * 0.5, max((hi - lo).x, (hi - lo).y, (hi - lo).z) * 0.5, 32.0)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    scene.render.filepath = os.path.join(out_dir, "frame_")
    bpy.ops.render.render(animation=True)
    written = sorted(f for f in os.listdir(out_dir) if f.startswith("frame_"))
    print(f"rendered {len(written)} frames into {out_dir}")


if __name__ == "__main__":
    main()
