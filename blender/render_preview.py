"""Render preview sheets of Sword Kirby: four angles, plus a limb-ownership view.

    blender --background --python blender/render_preview.py -- --out docs/preview

The limb view recolours every triangle by which of Link's 21 limbs owns it. That is the
picture worth checking before any export, because a limb with no geometry, or a body
band split in the wrong place, is obvious there and invisible in a beauty render.
"""

from __future__ import annotations

import colorsys
import os
import sys

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_skeleton import DEFORM  # noqa: E402
from sword_kirby import (  # noqa: E402
    BALL_CENTRE,
    bind,
    build_kirby_mesh,
    build_reference_armature,
)

ANGLES: list[tuple[str, float, float]] = [
    ("front", 0.0, 12.0),
    ("three-quarter", 38.0, 16.0),
    ("side", 90.0, 10.0),
    ("back", 180.0, 14.0),
]


def _clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _sun(name: str, rotation: tuple[float, float, float], energy: float) -> None:
    light = bpy.data.lights.new(name, type="SUN")
    light.energy = energy
    obj = bpy.data.objects.new(name, light)
    obj.rotation_euler = [v * 3.14159265 / 180.0 for v in rotation]
    bpy.context.scene.collection.objects.link(obj)


def _camera(azimuth_deg: float, elevation_deg: float, distance: float) -> bpy.types.Object:
    import math

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    target = Vector((0.0, 0.0, BALL_CENTRE.z))
    position = target + Vector((
        math.sin(az) * math.cos(el) * distance,
        -math.cos(az) * math.cos(el) * distance,
        math.sin(el) * distance,
    ))

    cam_data = bpy.data.cameras.new("Preview")
    cam_data.lens = 70.0
    cam = bpy.data.objects.new("Preview", cam_data)
    cam.location = position
    direction = (target - position).normalized()
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    return cam


def _scene(width: int = 1280, height: int = 1280) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in [
        item.identifier for item in
        bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    ] else "BLENDER_WORKBENCH"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    # Blender defaults PNG compression to 15 percent, which leaves these around 1.6 MB
    # each. 100 is still lossless and cuts them by roughly three quarters.
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 100
    world = bpy.data.worlds.new("Preview")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.09, 0.10, 0.13, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    scene.world = world
    _sun("KeyLight", (55.0, 10.0, 35.0), 4.0)
    _sun("FillLight", (65.0, 0.0, -120.0), 1.6)


def _recolour_by_limb(mesh_obj: bpy.types.Object) -> None:
    """Give every limb its own hue, so the per-limb split is visible at a glance."""
    mesh = mesh_obj.data
    mesh.materials.clear()

    slot_of: dict[str, int] = {}
    for i, name in enumerate(DEFORM):
        hue = i / float(len(DEFORM))
        r, g, b = colorsys.hsv_to_rgb(hue, 0.62, 0.95)
        mat = bpy.data.materials.new(f"limb_{name}")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
        mat.diffuse_color = (r, g, b, 1.0)
        slot_of[name] = i
        mesh.materials.append(mat)

    group_name = {g.index: g.name for g in mesh_obj.vertex_groups}
    for poly in mesh.polygons:
        tally: dict[str, int] = {}
        for vi in poly.vertices:
            for g in mesh.vertices[vi].groups:
                if g.weight > 0.0:
                    name = group_name.get(g.group)
                    if name in slot_of:
                        tally[name] = tally.get(name, 0) + 1
        if tally:
            winner = max(tally.items(), key=lambda kv: kv[1])[0]
            poly.material_index = slot_of[winner]


def _load_blend(path: str) -> bpy.types.Object:
    """Open a saved file and return its Kirby mesh, for previewing an imported model."""
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(path))
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"{path} has no mesh")
    for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        arm.hide_render = True
    return max(meshes, key=lambda o: len(o.data.vertices))


def render_sheet(out_dir: str, limb_view: bool, blend: str | None = None,
                 prefix: str = "kirby") -> list[str]:
    written: list[str] = []
    for name, azimuth, elevation in ANGLES:
        if blend:
            mesh = _load_blend(blend)
            _scene()
        else:
            _clear()
            _scene()
            arm = build_reference_armature()
            mesh, owner = build_kirby_mesh()
            bind(mesh, arm, owner)
            arm.hide_render = True
        if limb_view:
            _recolour_by_limb(mesh)
        _camera(azimuth, elevation, 2.6)

        suffix = "limbs" if limb_view else "shaded"
        path = os.path.join(os.path.abspath(out_dir), f"{prefix}_{name}_{suffix}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.context.scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        written.append(path)
        print(f"rendered {path}")
    return written


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out_dir = argv[argv.index("--out") + 1] if "--out" in argv else "docs/preview"
    blend = argv[argv.index("--blend") + 1] if "--blend" in argv else None
    prefix = argv[argv.index("--prefix") + 1] if "--prefix" in argv else "kirby"

    written = render_sheet(out_dir, limb_view=False, blend=blend, prefix=prefix)
    written += render_sheet(out_dir, limb_view=True, blend=blend, prefix=prefix)
    print(f"=== {len(written)} previews written to {os.path.abspath(out_dir)} ===")


if __name__ == "__main__":
    main()
