"""Give Kirby the eye flipbook the game drives, the way Link's own face material does.

    blender --background --python blender/hm_face.py -- \
        --blend build/hm_kirby_child.blend --age child --out build/hm_kirby_child_face.blend

Link's face material does not hold an image: it holds a texture reference to segment
0x08000000, 64 by 32, CI8, plus a flipbook of eight eye frames. The player code writes
whichever frame he should be showing into that segment, which is what makes him blink,
dart his eyes and wince. Kirby's face material is set up the same way with his own eight
frames, named as the game names Link's so they replace them.

Read out of the imported Link in `blender/dump_face_material.py`, not guessed.
"""
import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name: str, fallback: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else fallback


blend = arg("--blend", "build/hm_kirby_child.blend")
age = arg("--age", "child")
out = arg("--out", "build/hm_kirby_child_face.blend")
frames_dir = arg("--frames", "build/face")

PREFIX = {"child": "gLinkChild", "adult": "gLinkAdult"}[age]
EYE_NAMES = [f"{PREFIX}Eyes{part}Tex" for part in
             ("Open", "Half", "Closedf", "RollLeft", "RollRight", "Shock", "Unk1", "Unk2")]
EYE_FILES = ["eye_0_Open.png", "eye_1_Half.png", "eye_2_Closedf.png", "eye_3_RollLeft.png",
             "eye_4_RollRight.png", "eye_5_Shock.png", "eye_6_Unk1.png", "eye_7_Unk2.png"]
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

# F3D materials first, so the face material is one of theirs before it is set up.
if any(not getattr(m, "is_f3d", False) for m in bpy.data.materials):
    scene.bsdf_conv_all = True
    scene.rename_uv_maps = True
    bpy.ops.object.convert_bsdf()
    log.append("converted the plain materials to F3D")

# The converter turns a plain material into a shaded solid, whose combiner names no
# texture at all, so the exporter writes none and he comes out faceless. Link's own eye
# material is the thing that works, so it is appended from the imported skeleton and
# used as it is, with his frames swapped for Kirby's.
donor = arg("--donor", "build/hm_link_child.blend")
template = None
with bpy.data.libraries.load(os.path.abspath(donor), link=False) as (src, dst):
    dst.materials = list(src.materials)
for mat in dst.materials:
    if mat is None or not getattr(mat, "is_f3d", False):
        continue
    book = getattr(mat.flipbookGroup, "flipbook0", None)
    if book is not None and book.enable and mat.f3d_mat.tex0.tex_reference == "0x08000000":
        template = mat
        break
log.append(f"eye material template from {donor}: {template.name if template else 'not found'}")

face = None
for mesh in (o for o in bpy.data.objects if o.type == "MESH"):
    for index, slot in enumerate(mesh.material_slots):
        if slot.material is not None and "face" in slot.material.name.lower():
            if template is not None:
                # The donor is one of Link's own, named as the importer named it; the
                # resource names come from the material name, so it takes Kirby's.
                old_name = slot.material.name
                slot.material.name = old_name + "_unused"
                template.name = old_name
                slot.material = template
            face = slot.material
            log.append(f"face slot {index} on {mesh.name} is now {face.name}")
if face is None:
    log.append("no face material found")
else:
    # Link's eyes are CI8 through his skin palette, 256 colours shared by his whole
    # head. Kirby's are his own art and do not fit that palette: exported as CI8 they
    # came out as a blue smear across his face. Full colour instead, which is what the
    # shipped mod's own eye overrides are, and the format costs nothing here.
    tex = face.f3d_mat.tex0
    tex.tex_format = "RGBA16"
    tex.tex_reference_size = (64, 32)
    log.append(f"face texture: reference {tex.tex_reference} {tuple(tex.tex_reference_size)} "
               f"{tex.tex_format}")

    book = face.flipbookGroup.flipbook0
    book.enable = True
    book.exportName = f"{PREFIX}Eyes"
    while len(book.textures):
        book.textures.remove(0)
    for frame_name, file_name in zip(EYE_NAMES, EYE_FILES):
        path = os.path.abspath(os.path.join(frames_dir, file_name))
        if not os.path.exists(path):
            log.append(f"missing frame {path}")
            continue
        # Link's own eye frames came in with the skeleton and hold these names already;
        # Blender would push Kirby's to "name.001", and the export writes the name, so
        # his frames would no longer replace the game's. Link's go aside first.
        existing = bpy.data.images.get(frame_name)
        if existing is not None:
            existing.name = frame_name + "_link"
        image = bpy.data.images.load(path, check_existing=True)
        image.name = frame_name                     # the name the game knows it by
        entry = book.textures.add()
        entry.image = image
    log.append(f"eye flipbook: {len(book.textures)} frames, "
               f"{[t.image.name for t in book.textures]}")

bpy.ops.wm.save_as_mainfile(filepath=bpy.path.abspath(out))
log.append(f"saved {out}")
open("build/hm_face.txt", "w", encoding="utf8").write(chr(10).join(log))
