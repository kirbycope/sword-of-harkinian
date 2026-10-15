"""What does Link's own face material look like after the Harbour Masters import?

    blender --background --python blender/dump_face_material.py -- --blend build/hm_link_child.blend

His eyes and mouth are flipbooks the game swaps frame by frame, so the material carries a
texture reference rather than an image. This prints every material's texture settings so
Kirby's face can be set up the same way instead of guessed at.
"""
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
blend = argv[argv.index("--blend") + 1] if "--blend" in argv else "build/hm_link_child.blend"

for name in ("fast64-main", "fast64"):
    try:
        bpy.ops.preferences.addon_disable(module=name)
    except Exception:                              # noqa: BLE001
        pass
bpy.ops.preferences.addon_enable(module="fast64_hm")
bpy.ops.wm.open_mainfile(filepath=bpy.path.abspath(blend))

out = []
for mat in bpy.data.materials:
    if not getattr(mat, "is_f3d", False):
        continue
    f3d = mat.f3d_mat
    row = [f"material {mat.name}"]
    for slot in ("tex0", "tex1"):
        tex = getattr(f3d, slot, None)
        if tex is None:
            continue
        bits = [f"tex_set={tex.tex_set}"]
        if getattr(tex, "use_tex_reference", False):
            bits.append(f"reference={tex.tex_reference!r} size={tuple(tex.tex_reference_size)}")
            if getattr(tex, "tex_reference_palette", ""):
                bits.append(f"palette={tex.tex_reference_palette!r} "
                            f"palette_size={getattr(tex, 'tex_reference_palette_size', None)}")
        if getattr(tex, "tex", None) is not None:
            bits.append(f"image={tex.tex.name}")
        bits.append(f"fmt={tex.tex_format}")
        row.append(f"  {slot}: " + ", ".join(bits))
    flipbook = getattr(mat, "flipbookGroup", None)
    if flipbook is not None:
        for slot in ("flipbook0", "flipbook1"):
            book = getattr(flipbook, slot, None)
            if book is not None and getattr(book, "enable", False):
                names = [t.image.name if t.image else "(none)" for t in book.textures]
                row.append(f"  {slot}: enabled, exportName={getattr(book, 'exportName', '')!r}, "
                           f"{len(names)} frames: {names}")
    out.append(chr(10).join(row))
open("build/face_material.txt", "w", encoding="utf8").write(chr(10).join(out))
