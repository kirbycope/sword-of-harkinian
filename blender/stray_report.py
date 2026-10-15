"""What does the exporter draw that the old pipeline dropped?

    blender --background --python blender/stray_report.py -- --blend build/hm_kirby_child.blend

The hand-written build dropped every triangle whose vertices carried no limb weight.
Fast64 draws all of them, which is where the red specks over the cap come from. This
lists each material's triangles by the bone they are weighted to, with the stray ones
first, so the ones to take out of the mesh can be named rather than guessed at.
"""
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
blend = argv[argv.index("--blend") + 1] if "--blend" in argv else "build/hm_kirby_child.blend"

bpy.ops.wm.open_mainfile(filepath=bpy.path.abspath(blend))
armature = next(o for o in bpy.data.objects if o.type == "ARMATURE" and not o.name.endswith("_lod"))
mesh_obj = max((o for o in bpy.data.objects if o.type == "MESH" and o.parent is armature),
               key=lambda o: len(o.data.vertices))
mesh = mesh_obj.data
bones = {b.name for b in armature.data.bones}
groups = {g.index: g.name for g in mesh_obj.vertex_groups}

lines = [f"{mesh_obj.name}: {len(mesh.vertices)} verts, {len(mesh.polygons)} faces"]
owner = {}
for v in mesh.vertices:
    best, weight = None, 0.0
    for g in v.groups:
        name = groups.get(g.group, "")
        if name in bones and g.weight > weight:
            best, weight = name, g.weight
    owner[v.index] = best

tally: dict[tuple[str, str], list[int]] = {}
for poly in mesh.polygons:
    material = mesh.materials[poly.material_index].name if mesh.materials else "(none)"
    owners = {owner[v] for v in poly.vertices}
    bone = owners.pop() if len(owners) == 1 else ("MIXED" if None not in owners else "UNWEIGHTED")
    tally.setdefault((material, bone or "UNWEIGHTED"), []).append(poly.index)

world = mesh_obj.matrix_world
for (material, bone), faces in sorted(tally.items(), key=lambda kv: (kv[0][1] != "UNWEIGHTED", kv[0])):
    centre = Vector((0, 0, 0))
    for index in faces:
        centre += world @ mesh.polygons[index].center
    centre /= len(faces)
    top = max((world @ mesh.polygons[i].center).z for i in faces)
    lines.append(f"  {material:28s} {bone:16s} {len(faces):4d} faces  centre "
                 f"({centre.x:6.2f},{centre.y:6.2f},{centre.z:6.2f}) top z {top:6.2f}")
open("build/stray_report.txt", "w", encoding="utf8").write(chr(10).join(lines))
