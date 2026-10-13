"""Import Adult Link's real skeleton with Fast64 and check it against the limb table.

    blender --background --python blender/import_link_skeleton.py -- \
        --decomp decomp --version ntsc-1.2 --out build/link_skeleton.blend

This needs Fast64 installed as a Blender add-on and the decompilation's assets already
extracted from your own ROM, because the skeleton's limb translations live in ROM data
rather than in source control.

The import is the only way to find out whether the limb table in link_skeleton.py is
right. That table's parents were reconstructed from each limb's first-child and
next-sibling indices, and its deform flags from which limbs carry a display list, so a
mismatch here means the model is weighted against a skeleton the game does not have.
Nothing should be exported until this reports no complaints.
"""

from __future__ import annotations

import argparse
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_skeleton import LIMBS, verify_against_imported  # noqa: E402


def configure(decomp_path: str, version: str, mode: str = "Adult Link") -> None:
    scene = bpy.context.scene

    # Fast64 serves both Ocarina of Time and Majora's Mask from one z64 module, so the
    # editor has to be told which game before the OoT settings mean anything.
    for attr, value in (("gameEditorMode", "OOT"), ("game_editor_mode", "OOT")):
        if hasattr(scene, attr):
            try:
                setattr(scene, attr, value)
            except (TypeError, ValueError):
                pass

    scene.ootDecompPath = decomp_path
    oot = scene.fast64.oot
    oot.oot_version = version

    settings = oot.skeletonImportSettings
    settings.mode = mode
    settings.applyRestPose = True
    # Doubles removal welds vertices across limb boundaries, which is exactly what must
    # not happen: a vertex shared between two limbs has nowhere to go in a skeleton that
    # draws one display list per limb.
    settings.removeDoubles = False
    settings.importNormals = True
    settings.import_animations = False


def describe(armature: bpy.types.Object) -> None:
    print(f"\nimported armature {armature.name!r} with {len(armature.data.bones)} bones")
    print(f"{'bone':<16} {'parent':<16} deform  head (x, y, z)")
    print("-" * 72)
    for bone in armature.data.bones:
        parent = bone.parent.name if bone.parent else "-"
        h = bone.head_local
        print(f"{bone.name:<16} {parent:<16} {str(bone.use_deform):<6}  "
              f"{h.x:7.3f} {h.y:7.3f} {h.z:7.3f}")


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--decomp", default="decomp")
    parser.add_argument("--version", default="ntsc-1.2")
    parser.add_argument("--mode", default="Adult Link",
                        choices=["Adult Link", "Child Link"],
                        help="which player skeleton to import")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    decomp = os.path.abspath(args.decomp)
    folder = "object_link_boy" if args.mode == "Adult Link" else "object_link_child"
    objects_dir = os.path.join(decomp, "extracted", args.version, "assets", "objects", folder)
    if not os.path.isdir(objects_dir):
        print(f"FAIL: no extracted assets at {objects_dir}")
        print("Extract them from your own ROM first; see README.md.")
        sys.exit(1)

    configure(decomp, args.version, args.mode)
    print(f"decomp   {decomp}")
    print(f"version  {args.version}")
    print(f"mode     {args.mode}")

    before = set(bpy.data.objects)
    bpy.ops.object.oot_import_skeleton()
    fresh = [o for o in bpy.data.objects if o not in before]
    armatures = [o for o in fresh if o.type == "ARMATURE"]
    if not armatures:
        print("FAIL: import produced no armature")
        sys.exit(1)

    armature = armatures[0]
    describe(armature)

    meshes = [o for o in fresh if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in m.data.polygons) for m in meshes)
    print(f"\nLink's own mesh came in as {len(meshes)} object(s), {tris} triangles")

    problems = verify_against_imported(armature) if args.mode == "Adult Link" else []
    print(f"\nchecking the limb table ({len(LIMBS)} limbs) against the import:")
    if problems:
        for p in problems:
            print(f"  MISMATCH  {p}")
        print(f"\nFAIL: {len(problems)} mismatch(es). Do not export until these are resolved.")
        sys.exit(1)
    print("  OK: parents and deform flags all agree")

    if args.out:
        path = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        print(f"saved {path}")


if __name__ == "__main__":
    main()
