"""Import Link's skeleton straight out of oot.o2r with the Harbour Masters Fast64 fork.

    blender --background --python blender/hm_import_link.py -- \
        --o2r .toolchain/soh/app/oot.o2r --mode "Child Link" --out build/hm_link_child.blend

This is the supported route: their fork reads the port's own archive, so there is no
decompilation, no ROM build and no hand-written resource in the chain. What comes out is
the armature and mesh the exporter expects back, and the export writes the XML resources
the port actually reads.
"""
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name: str, fallback: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else fallback


o2r = bpy.path.abspath(arg("--o2r", ".toolchain/soh/app/oot.o2r"))
mode = arg("--mode", "Child Link")
out = arg("--out", "build/hm_link.blend")
log: list[str] = []

for name in ("fast64-main", "fast64"):
    try:
        bpy.ops.preferences.addon_disable(module=name)
    except Exception:                              # noqa: BLE001
        pass
bpy.ops.preferences.addon_enable(module="fast64_hm")

scene = bpy.context.scene
scene.gameEditorMode = "OOT"
scene.fast64.oot.feature_set = "hm64"              # the Harbour Masters resource format
scene.hm64_use_o2r_import = True
scene.hm64_o2r_path = o2r

settings = scene.fast64.oot.skeletonImportSettings
for field, value in (("mode", mode), ("drawLayer", "Opaque")):
    if hasattr(settings, field):
        try:
            setattr(settings, field, value)
            log.append(f"import setting {field} = {value}")
        except Exception as exc:                   # noqa: BLE001
            log.append(f"import setting {field} refused {value}: {exc}")

before = set(bpy.data.objects.keys())
try:
    bpy.ops.object.oot_import_skeleton()
    log.append("imported")
except Exception as exc:                           # noqa: BLE001
    log.append(f"import failed: {exc}")

for name in sorted(set(bpy.data.objects.keys()) - before):
    obj = bpy.data.objects[name]
    extra = ""
    if obj.type == "ARMATURE":
        extra = f", {len(obj.data.bones)} bones"
    elif obj.type == "MESH":
        extra = f", {len(obj.data.vertices)} vertices, {len(obj.data.polygons)} faces"
    log.append(f"new object: {name} ({obj.type}{extra})")

bpy.ops.wm.save_as_mainfile(filepath=bpy.path.abspath(out))
log.append(f"saved {out}")
open("build/hm_import.txt", "w", encoding="utf8").write(chr(10).join(log))
