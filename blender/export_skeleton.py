"""Export Sword Kirby over Adult Link with Fast64.

    blender --background --python blender/export_skeleton.py -- \
        --blend build/sword_kirby_rigged.blend --decomp decomp --version ntsc-1.2

Two things have to happen before the exporter will take the mesh.

The materials arrive from the OBJ import as Principled BSDF, and Fast64 cannot export
those: an exported mesh has to use F3D materials, the N64's own combiner model. Fast64
ships a converter for exactly this, so it runs first.

New materials on Link also need segment C enabled, which is what draws his reflection.
The Custom Link Process calls this out specifically, and a material without it renders
wrongly in the mirror rooms rather than failing loudly, so it is easy to miss.

The export writes C into the decompilation, which is the input the o2r packer reads.
"""

from __future__ import annotations

import argparse
import os
import sys

import bpy


# The N64 holds 4KB of texture memory at a time. Kirby 64's body textures are 128x128,
# which is 32KB as 16-bit colour, eight times over. They are also nearly flat: a pink
# field with eyes, blush and a mouth on it, no gradients worth keeping.
#
# So they are halved to 64x64 and exported as CI4, the 16-colour indexed format, which
# is 2KB plus a 32-byte palette. That keeps the face sharp where dropping to 32x32 in
# full colour would have blurred it, and indexed colour is what the game uses for Link's
# own textures anyway.
MAX_TEXTURE = 64
TEXTURE_FORMAT = "CI4"


PALETTE_SIZE = 16


def quantize(image: bpy.types.Image, colours: int = PALETTE_SIZE) -> int:
    """Reduce an image to a small palette, so it fits the indexed format.

    Fast64 will not quantize for you: it refuses the export if the image has more
    unique colours than the format holds. These textures are nearly flat already, and
    the extra colours are anti-aliasing around the eyes and mouth, so taking the most
    common colours and snapping everything to the nearest one is enough. Returns how
    many unique colours were present before.
    """
    pixels = list(image.pixels)
    count = len(pixels) // 4

    # Count colours on a coarse grid so near-identical anti-aliased shades group up.
    tally: dict[tuple[int, int, int, int], int] = {}
    quantised: list[tuple[int, int, int, int]] = []
    for i in range(count):
        r, g, b, a = pixels[i * 4:i * 4 + 4]
        key = (round(r * 31), round(g * 31), round(b * 31), 1 if a > 0.5 else 0)
        quantised.append(key)
        tally[key] = tally.get(key, 0) + 1

    if len(tally) <= colours:
        return len(tally)

    palette = [c for c, _n in sorted(tally.items(), key=lambda kv: -kv[1])[:colours]]

    cache: dict[tuple[int, int, int, int], tuple[int, int, int, int]] = {}
    for i, key in enumerate(quantised):
        nearest = cache.get(key)
        if nearest is None:
            if key[3] == 0:
                candidates = [p for p in palette if p[3] == 0] or palette
            else:
                candidates = [p for p in palette if p[3] == 1] or palette
            nearest = min(
                candidates,
                key=lambda p: (p[0] - key[0]) ** 2 + (p[1] - key[1]) ** 2 + (p[2] - key[2]) ** 2,
            )
            cache[key] = nearest
        pixels[i * 4] = nearest[0] / 31.0
        pixels[i * 4 + 1] = nearest[1] / 31.0
        pixels[i * 4 + 2] = nearest[2] / 31.0
        pixels[i * 4 + 3] = float(nearest[3])

    image.pixels = pixels
    return len(tally)


def prepare_textures(mesh: bpy.types.Object, work_dir: str) -> list[str]:
    """Fit every texture into N64 texture memory: resize, then reduce its palette."""
    os.makedirs(os.path.abspath(work_dir), exist_ok=True)
    notes: list[str] = []
    seen: set[str] = set()

    for material in mesh.data.materials:
        if material is None or not material.use_nodes:
            continue
        for node in material.node_tree.nodes:
            image = getattr(node, "image", None)
            if image is None or image.name in seen:
                continue
            seen.add(image.name)

            width, height = image.size
            note = []
            if width > MAX_TEXTURE or height > MAX_TEXTURE:
                target_w = min(width, MAX_TEXTURE)
                target_h = min(height, MAX_TEXTURE)
                image.scale(target_w, target_h)
                note.append(f"{width}x{height} -> {target_w}x{target_h}")

            before = quantize(image)
            if before > PALETTE_SIZE:
                note.append(f"{before} colours -> {PALETTE_SIZE}")

            if note:
                stem = os.path.splitext(os.path.basename(image.name))[0]
                out = os.path.join(os.path.abspath(work_dir), f"{stem}_n64.png")
                image.filepath_raw = out
                image.file_format = "PNG"
                image.save()
                notes.append(f"{image.name}: " + ", ".join(note))
    return notes


def set_texture_format(mesh: bpy.types.Object) -> int:
    """Put every F3D texture into the indexed format so it fits in texture memory."""
    changed = 0
    for material in mesh.data.materials:
        f3d = getattr(material, "f3d_mat", None)
        if f3d is None:
            continue
        for slot in ("tex0", "tex1"):
            tex = getattr(f3d, slot, None)
            if tex is None or not getattr(tex, "tex", None):
                continue
            try:
                tex.tex_format = TEXTURE_FORMAT
                changed += 1
            except (TypeError, ValueError) as error:
                print(f"  could not set {slot} format on {material.name}: {error}")
    return changed


def convert_materials(mesh: bpy.types.Object) -> int:
    """Turn the imported Principled BSDF materials into F3D materials."""
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.context.view_layer.objects.active = mesh

    scene = bpy.context.scene
    if hasattr(scene, "bsdf_conv_all"):
        scene.bsdf_conv_all = False
    if hasattr(scene, "rename_uv_maps"):
        scene.rename_uv_maps = True

    bpy.ops.object.convert_bsdf()
    return len(mesh.data.materials)


def enable_segment_c(mesh: bpy.types.Object) -> int:
    """Switch on segment C for every material, for Link's reflection."""
    touched = 0
    for material in mesh.data.materials:
        if material is None:
            continue
        for holder in (material, getattr(material, "f3d_mat", None)):
            if holder is None:
                continue
            props = getattr(holder, "draw_layer", None) or holder
            for attr in ("menu_upper", "set_fog"):
                pass
            dynamic = getattr(holder, "ootMaterial", None)
            if dynamic is None:
                continue
            for half in ("opaque", "transparent"):
                side = getattr(dynamic, half, None)
                if side is not None and hasattr(side, "segment12"):
                    # Segments are named 8..D in the UI; segment C is index 12.
                    setattr(side, "segment12", True)
                    touched += 1
    return touched


def ensure_lod_mesh(armature: bpy.types.Object, mesh: bpy.types.Object) -> str:
    """Give Link's far-distance skeleton a mesh too.

    Link is drawn from a second, lower-detail skeleton once he is far from the camera,
    and Fast64 exports both. Kirby is 706 triangles, far below even Link's near mesh, so
    there is nothing to gain from a reduced version: the same mesh is bound to the far
    skeleton as well. Leaving it empty makes the export fail outright, and clearing the
    reference instead would strip the vanilla far display lists and leave Link invisible
    at a distance.
    """
    skeleton = getattr(armature, "ootSkeleton", None)
    lod = getattr(skeleton, "LOD", None) if skeleton else None
    if lod is None:
        return "no far skeleton set"

    existing = [c for c in lod.children if c.type == "MESH"]
    if existing:
        return f"far skeleton already has {existing[0].name!r}"

    main_bones = {b.name for b in armature.data.bones}
    lod_bones = {b.name for b in lod.data.bones}
    missing = {g.name for g in mesh.vertex_groups} - lod_bones
    if missing:
        return f"far skeleton is missing bones the weights need: {sorted(missing)[:4]}"

    copy = mesh.copy()
    copy.data = mesh.data.copy()
    copy.name = f"{mesh.name}_lod"
    copy.data.name = copy.name
    bpy.context.scene.collection.objects.link(copy)

    copy.parent = lod
    copy.matrix_world = mesh.matrix_world.copy()
    for modifier in list(copy.modifiers):
        copy.modifiers.remove(modifier)
    armature_mod = copy.modifiers.new(name="Armature", type="ARMATURE")
    armature_mod.object = lod
    return (f"bound a copy to {lod.name!r} "
            f"({len(main_bones & lod_bones)} bones in common)")


def configure_export(decomp: str, version: str) -> None:
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

    settings = oot.skeletonExportSettings
    settings.mode = "Adult Link"
    settings.isCustom = False
    settings.removeVanillaData = True
    settings.optimize = False
    settings.folder = "object_link_boy"
    settings.customAssetIncludeDir = "assets/objects/object_link_boy"


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--decomp", default="decomp")
    parser.add_argument("--version", default="ntsc-1.2")
    args = parser.parse_args(argv)

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))

    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not armatures or not meshes:
        print("FAIL: the file needs an armature and a mesh")
        sys.exit(1)
    armature = armatures[0]
    mesh = max(meshes, key=lambda o: len(o.data.vertices))
    print(f"armature {armature.name!r}, mesh {mesh.name!r} "
          f"({len(mesh.data.vertices)} verts, {len(mesh.data.materials)} materials)")

    print(f"far skeleton: {ensure_lod_mesh(armature, mesh)}")

    # Both skeletons' meshes have to go through this. The far one is a different model,
    # the Smash 64 Kirby, with its own materials and its own oversized textures, and the
    # exporter rejects the whole export if either carries a non-F3D material.
    lod_armature = getattr(getattr(armature, "ootSkeleton", None), "LOD", None)
    targets = [mesh]
    if lod_armature is not None:
        targets += [c for c in lod_armature.children if c.type == "MESH"]

    for target in targets:
        print(f"\npreparing {target.name!r}:")
        resized = prepare_textures(target, os.path.join("build", "textures"))
        for note in resized:
            print(f"  resized {note}")
        if not resized:
            print("  no textures needed resizing")
        print(f"  converted {convert_materials(target)} material(s) to F3D")
        print(f"  set {set_texture_format(target)} texture slot(s) to {TEXTURE_FORMAT}")
        print(f"  segment C on {enable_segment_c(target)} material side(s)")
    print()

    configure_export(os.path.abspath(args.decomp), args.version)

    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature

    try:
        bpy.ops.object.oot_export_skeleton()
    except RuntimeError as error:
        print(f"FAIL: export refused: {error}")
        sys.exit(1)

    target = os.path.join(os.path.abspath(args.decomp), "assets", "objects", "object_link_boy")
    print(f"\nexported into {target}")
    if os.path.isdir(target):
        written = sorted(
            (f, os.path.getmtime(os.path.join(target, f)))
            for f in os.listdir(target)
        )
        recent = [f for f, m in written][-8:]
        print(f"  {len(written)} file(s) there; most recent: {', '.join(recent)}")
    print("OK: export finished")


if __name__ == "__main__":
    main()
