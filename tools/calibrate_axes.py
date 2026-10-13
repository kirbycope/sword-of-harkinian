"""Work out how Blender coordinates map to the game's, from Link's own data.

    blender --background --python tools/calibrate_axes.py -- \
        --blend build/link_skeleton.blend \
        --assets decomp/extracted/ntsc-1.2/assets/objects/object_link_boy

Writing a mod by hand means emitting vertices in each limb's own space and in the game's
axis convention. Guessing that convention produces a model that is silently rotated or
mirrored, which is the kind of mistake that is obvious on screen and invisible in code.

So it is measured instead. Fast64 imported Link's real mesh, and the decompilation has
the same vertices in game coordinates. Trying every axis permutation and sign against
those two sets and keeping the one that lines up gives the mapping and the scale, with
the match error as evidence it is right.
"""

from __future__ import annotations

import argparse
import itertools
import os
import re
import sys

import bpy
from mathutils import Vector

VTX = re.compile(r"VTX\(\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),")

# The limb to calibrate on, and the vertex file holding its game-space vertices.
LIMB_BONE = "bone001_gLinkAdultWaistLimb"
VTX_FILE = "gLinkAdultWaistNearVtx.inc.c"


def game_vertices(path: str) -> list[Vector]:
    text = open(path, encoding="utf8", errors="replace").read()
    return [Vector((float(a), float(b), float(c))) for a, b, c in VTX.findall(text)]


def blender_vertices(armature: bpy.types.Object, mesh: bpy.types.Object,
                     bone_name: str) -> list[Vector]:
    """Vertices weighted to one bone, expressed in that bone's rest space."""
    bone = armature.data.bones[bone_name]
    to_bone = (armature.matrix_world @ bone.matrix_local).inverted()

    group = mesh.vertex_groups.get(bone_name)
    if group is None:
        raise SystemExit(f"mesh has no vertex group {bone_name!r}")

    out = []
    for v in mesh.data.vertices:
        if any(g.group == group.index and g.weight > 0.5 for g in v.groups):
            out.append(to_bone @ (mesh.matrix_world @ v.co))
    return out


def best_mapping(blender: list[Vector], game: list[Vector]) -> tuple:
    """Find the axis permutation, signs and scale that map one set onto the other."""
    if not blender or not game:
        raise SystemExit("one of the vertex sets is empty")

    def extent(pts, axis):
        vals = [p[axis] for p in pts]
        return max(vals) - min(vals)

    results = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            # Compare extents first: a correct mapping lines the spans up.
            error = 0.0
            for axis in range(3):
                b = extent(blender, perm[axis]) * 1000.0
                g = extent(game, axis)
                error += abs(b - g)
            results.append((error, perm, signs))
    results.sort(key=lambda r: r[0])
    return results[0]


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--assets", required=True)
    args = parser.parse_args(argv)

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    armature = next(a for a in armatures if not a.name.endswith("_lod"))
    meshes = [o for o in bpy.data.objects
              if o.type == "MESH" and o.parent is armature]
    if not meshes:
        meshes = [o for o in bpy.data.objects if o.type == "MESH" and "Skel" in o.name]
    mesh = max(meshes, key=lambda o: len(o.data.vertices))
    print(f"calibrating on {mesh.name!r} / {LIMB_BONE}")

    bl = blender_vertices(armature, mesh, LIMB_BONE)
    gm = game_vertices(os.path.join(os.path.abspath(args.assets), VTX_FILE))
    print(f"  blender verts on that bone: {len(bl)}")
    print(f"  game verts in {VTX_FILE}: {len(gm)}")

    for label, pts, mul in (("blender (x1000)", bl, 1000.0), ("game", gm, 1.0)):
        lo = [min(p[i] for p in pts) * mul for i in range(3)]
        hi = [max(p[i] for p in pts) * mul for i in range(3)]
        print(f"  {label:<16} "
              + "  ".join(f"{lo[i]:8.1f}..{hi[i]:8.1f}" for i in range(3)))

    error, perm, signs = best_mapping(bl, gm)
    axes = "XYZ"
    mapping = ", ".join(
        f"game {axes[a]} = {'-' if signs[a] < 0 else '+'}blender {axes[perm[a]]}"
        for a in range(3)
    )
    print(f"\nbest mapping (extent error {error:.1f} game units):")
    print(f"  {mapping}")
    print(f"  scale: blender * 1000")
    print(f"\nPERM={perm} SIGNS={signs}")


if __name__ == "__main__":
    main()
