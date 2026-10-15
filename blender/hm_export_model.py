"""Export the rigged Kirby through the Harbour Masters Fast64 exporter.

    blender --background --python blender/hm_export_model.py -- \
        --blend build/hm_kirby_child.blend --out build/hm_export_child --name gLinkChildSkel

Their exporter writes the XML resources the port actually reads (SkeletonLimb,
DisplayList, Vertex, materials, textures), which is what this project used to write by
hand and got wrong. Everything about the format therefore comes from their code, not
from this repository's guesses.
"""
import os
import re
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name: str, fallback: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else fallback


blend = arg("--blend", "build/hm_kirby_child.blend")
out = os.path.abspath(arg("--out", "build/hm_export_child"))
folder = arg("--folder", "object_link_child")
log: list[str] = []

for name in ("fast64-main", "fast64"):
    try:
        bpy.ops.preferences.addon_disable(module=name)
    except Exception:                              # noqa: BLE001
        pass
bpy.ops.preferences.addon_enable(module="fast64_hm")

bpy.ops.wm.open_mainfile(filepath=bpy.path.abspath(blend))
scene = bpy.context.scene
scene.gameEditorMode = "OOT"
scene.fast64.oot.feature_set = "hm64"
os.makedirs(out, exist_ok=True)

settings = scene.fast64.oot.skeletonExportSettings
for field, value in (("customPath", out), ("isCustom", True), ("isCustomFilename", False),
                     ("folder", folder), ("name", folder), ("removeVanillaData", False),
                     ("optimize", False)):
    if hasattr(settings, field):
        try:
            setattr(settings, field, value)
            log.append(f"export setting {field} = {value}")
        except Exception as exc:                   # noqa: BLE001
            log.append(f"export setting {field} refused {value!r}: {exc}")
log.append("export settings available: " + ", ".join(
    p.identifier for p in settings.bl_rna.properties if not p.is_readonly))

# Kirby's materials are plain Blender ones; the exporter only takes F3D materials, so
# Fast64's own converter makes them, texture and all, rather than this repository
# hand-writing material resources as it used to.
plain = [m.name for m in bpy.data.materials if not getattr(m, "is_f3d", False)]
if plain:
    scene.bsdf_conv_all = True
    scene.rename_uv_maps = True
    bpy.ops.object.convert_bsdf()
    still = [m.name for m in bpy.data.materials if not getattr(m, "is_f3d", False)]
    log.append(f"converted {len(plain)} material(s) to F3D: {plain}")
    if still:
        log.append(f"still not F3D: {still}")

# The rig carries a tiny box on every bone, marked with the "role_filler" vertex group,
# left over from weighting the model. The old build dropped them and nobody saw them; the
# exporter draws everything it is given, so they came out as red specks over the cap and
# around the shoulders. Deleting them is worse than it sounds: a limb then has nothing to
# draw, the exporter points it at "gEmptyDL", a name out of the decompilation that is in
# no archive, and the port loses the whole scene to it. So each box is collapsed onto its
# own centre instead. Every limb keeps a real display list, every triangle in these ones
# has no area, and the rasteriser throws them away.
for obj in [o for o in bpy.data.objects if o.type == "MESH"]:
    group = obj.vertex_groups.get("role_filler")
    if group is None:
        continue
    marked = [v for v in obj.data.vertices
              if any(g.group == group.index and g.weight > 0.0 for g in v.groups)]
    if not marked:
        continue
    bone_names = {g.index: g.name for g in obj.vertex_groups}
    islands: dict[str, list] = {}
    for vertex in marked:
        owner = max((g for g in vertex.groups if bone_names.get(g.group) != "role_filler"),
                    key=lambda g: g.weight, default=None)
        islands.setdefault(bone_names.get(owner.group) if owner else "", []).append(vertex)
    for bone, members in islands.items():
        centre = sum((v.co for v in members), Vector((0.0, 0.0, 0.0))) / len(members)
        for vertex in members:
            vertex.co = centre
    log.append(f"collapsed {len(marked)} filler vertices on {obj.name} "
               f"across {len(islands)} limb(s), so they cover no area")

armature = max((o for o in bpy.data.objects if o.type == "ARMATURE"),
               key=lambda o: len(o.data.bones))
for obj in bpy.data.objects:
    obj.select_set(False)
armature.select_set(True)
bpy.context.view_layer.objects.active = armature
log.append(f"exporting {armature.name}, {len(armature.data.bones)} bones")

def run_export() -> None:
    try:
        bpy.ops.object.oot_export_skeleton()
        log.append("exported")
    except Exception as exc:                       # noqa: BLE001
        log.append(f"export failed: {exc}")


def palette_counts() -> dict:
    """For each material, how many colours its palette actually holds.

    Every paletted texture is written with "Color Count" at its default of 255, which
    their own panel says is right for a vanilla TLUT. Kirby's textures are not vanilla:
    his boot palette holds four colours. The port then loads 256 of them out of an eight
    byte resource and dies in gfx_load_tlut_handler_rdp, which is the crash that took
    file 2 out in Link's House. So the first export is read back, each material's own
    display list says which palette it loads, and the count is set from that file's size.
    """
    header, entry = 92, 2                          # resource header, then RGBA16 entries
    counts = {}
    for root, _dirs, files in os.walk(out):
        for name in files:
            if not name.startswith("mat_") or "_layer" not in name:
                continue
            body = open(os.path.join(root, name), "rb").read().decode("utf8", "replace")
            if "LoadTLUTCmd" not in body:
                continue
            images = re.findall(r'<SetTextureImage Path="([^"]+)"', body)
            if not images:
                continue
            palette = os.path.join(root, images[-1].split("/")[-1])
            if not os.path.exists(palette):
                continue
            entries = max(1, (os.path.getsize(palette) - header) // entry)
            material = name[len("mat_"):].rsplit("_layer", 1)[0]
            # The count is what the hardware loads minus one, which is why a vanilla
            # 256-colour palette is written as 255.
            counts[material] = max(1, min(entries - 1, 255))
    return counts


run_export()
by_material = palette_counts()
if by_material:
    fixed = []
    for material in bpy.data.materials:
        if not getattr(material, "is_f3d", False):
            continue
        key = next((k for k in by_material if k.endswith(material.name)), "")
        if not key:
            continue
        for slot in ("tex0", "tex1"):
            tex = getattr(material.f3d_mat, slot, None)
            if tex is None or not tex.tex_set or not tex.tex_format.startswith("CI"):
                continue
            if tex.palette_color_count != by_material[key]:
                tex.palette_color_count = by_material[key]
                fixed.append(f"{material.name}.{slot}={by_material[key]}")
    if fixed:
        log.append("palette colour counts from the first export: " + ", ".join(fixed))
        run_export()

for root, _dirs, files in os.walk(out):
    for f in sorted(files):
        path = os.path.join(root, f)
        log.append(f"  {os.path.relpath(path, out)}  {os.path.getsize(path)} bytes")
open("build/hm_export.txt", "w", encoding="utf8").write(chr(10).join(log))
