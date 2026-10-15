"""Does the Harbour Masters import give the same armature the old one did?

    blender --background --python blender/compare_skeletons.py -- \
        --a build/link_child_skeleton.blend --b build/hm_link_child.blend

Prints both bone lists with their head positions and parents, and the differences, so the
rig can be moved onto the supported import knowing exactly what changed.
"""
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name: str, fallback: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv else fallback


def read(path: str) -> dict:
    bpy.ops.wm.open_mainfile(filepath=bpy.path.abspath(path))
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    armature = max(armatures, key=lambda o: len(o.data.bones)) if armatures else None
    if armature is None:
        return {"name": "(none)", "bones": {}}
    world = armature.matrix_world
    return {
        "name": armature.name,
        "scale": tuple(round(v, 4) for v in armature.scale),
        "bones": {
            b.name: {
                "head": tuple(round(v, 4) for v in (world @ b.head_local)),
                "parent": b.parent.name if b.parent else "",
                "deform": b.use_deform,
            }
            for b in armature.data.bones
        },
        "meshes": {o.name: len(o.data.vertices) for o in bpy.data.objects
                   if o.type == "MESH" and o.parent is armature},
    }


a = read(arg("--a"))
b = read(arg("--b"))
lines = [f"A {arg('--a')}: armature {a['name']}, {len(a['bones'])} bones, meshes {a.get('meshes')}",
         f"B {arg('--b')}: armature {b['name']}, {len(b['bones'])} bones, meshes {b.get('meshes')}",
         f"A scale {a.get('scale')}  B scale {b.get('scale')}", ""]
only_a = sorted(set(a["bones"]) - set(b["bones"]))
only_b = sorted(set(b["bones"]) - set(a["bones"]))
lines.append(f"only in A ({len(only_a)}): {only_a}")
lines.append(f"only in B ({len(only_b)}): {only_b}")
lines.append("")
lines.append(f"{'bone':28} {'A head':34} {'B head':34} parent A / B")
for name in sorted(set(a["bones"]) | set(b["bones"])):
    ba, bb = a["bones"].get(name), b["bones"].get(name)
    lines.append(f"{name:28} {str(ba['head']) if ba else '-':34} {str(bb['head']) if bb else '-':34} "
                 f"{ba['parent'] if ba else '-'} / {bb['parent'] if bb else '-'}")
open("build/skeleton_compare.txt", "w", encoding="utf8").write(chr(10).join(lines))
