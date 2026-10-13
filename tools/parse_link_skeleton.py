"""Derive Adult Link's real limb hierarchy from extracted decompilation data.

The limb names and ordering are in source control, but the parent of each limb is not:
the skeleton stores a first-child index and a next-sibling index per limb, and those
live in ROM data. This reads the extracted limb files and turns that pair of indices
back into a parent for each limb, along with its joint position.

    python tools/parse_link_skeleton.py decomp/extracted/ntsc-1.2/assets/objects/object_link_boy

Run it with -I so nothing in the extracted directory can shadow a standard module.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LIMB_DONE = 255

# Array order, from object_link_boy.xml. Index i here is PLAYER_LIMB_* minus one.
LIMB_FILES: list[tuple[str, str]] = [
    ("Root", "gLinkAdultRootLimb"),
    ("Waist", "gLinkAdultWaistLimb"),
    ("LowerControl", "gLinkAdultLowerControlLimb"),
    ("RightThigh", "gLinkAdultRightThighLimb"),
    ("RightShin", "gLinkAdultRightLegLimb"),
    ("RightFoot", "gLinkAdultRightFootLimb"),
    ("LeftThigh", "gLinkAdultLeftThighLimb"),
    ("LeftShin", "gLinkAdultLeftLegLimb"),
    ("LeftFoot", "gLinkAdultLeftFootLimb"),
    ("UpperControl", "gLinkAdultUpperControlLimb"),
    ("Head", "gLinkAdultHeadLimb"),
    ("Hat", "gLinkAdultHatLimb"),
    ("Collar", "gLinkAdultCollarLimb"),
    ("LeftShoulder", "gLinkAdultLeftShoulderLimb"),
    ("LeftForearm", "gLinkAdultLeftArmLimb"),
    ("LeftHand", "gLinkAdultLeftHandLimb"),
    ("RightShoulder", "gLinkAdultRightShoulderLimb"),
    ("RightForearm", "gLinkAdultRightArmLimb"),
    ("RightHand", "gLinkAdultRightHandLimb"),
    ("Sheath", "gLinkAdultSwordAndSheathLimb"),
    ("Torso", "gLinkTorsoLimb"),
]

NUMBER = re.compile(r"^\s*(-?\d+),\s*//\s*([xyz])\s*$", re.MULTILINE)
# The resolved index is in the leading comment, because the value itself is written as
# an expression such as "PLAYER_LIMB_WAIST - 1" or the constant LIMB_DONE.
INDEX = re.compile(r"/\*\s*(\d+)\s*\*/\s*.+?,\s*//\s*(child|sibling)")
DLIST = re.compile(r"^\s*(\w+),\s*//\s*[01]\s*$", re.MULTILINE)


class Limb:
    def __init__(self, name: str, pos: tuple[int, int, int], child: int, sibling: int,
                 dlists: list[str]) -> None:
        self.name = name
        self.pos = pos
        self.child = child
        self.sibling = sibling
        self.dlists = dlists
        self.parent: str | None = None

    @property
    def draws(self) -> bool:
        return bool(self.dlists)


def parse_limb(name: str, path: Path) -> Limb:
    text = path.read_text(encoding="utf8", errors="replace")

    axes = {axis: int(value) for value, axis in NUMBER.findall(text)}
    if set(axes) != {"x", "y", "z"}:
        raise ValueError(f"{path.name}: expected x, y and z joint position, got {sorted(axes)}")

    indices = {kind: int(value) for value, kind in INDEX.findall(text)}
    for kind in ("child", "sibling"):
        if kind not in indices:
            raise ValueError(f"{path.name}: no {kind} index found")

    dlists = [n for n in DLIST.findall(text) if n != "NULL"]
    return Limb(name, (axes["x"], axes["y"], axes["z"]),
                indices["child"], indices["sibling"], dlists)


def resolve_parents(limbs: list[Limb]) -> None:
    """Turn each limb's first-child and next-sibling indices into a parent."""
    for index, limb in enumerate(limbs):
        child = limb.child
        while child != LIMB_DONE:
            if child >= len(limbs):
                raise ValueError(f"{limb.name}: child index {child} is out of range")
            if limbs[child].parent is not None:
                raise ValueError(f"{limbs[child].name} already has parent {limbs[child].parent}")
            limbs[child].parent = limb.name
            child = limbs[child].sibling


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("object_dir", type=Path,
                        help="extracted assets/objects/object_link_boy directory")
    args = parser.parse_args()

    limbs: list[Limb] = []
    for name, stem in LIMB_FILES:
        path = args.object_dir / f"{stem}.inc.c"
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            return 1
        limbs.append(parse_limb(name, path))

    resolve_parents(limbs)

    rootless = [limb.name for limb in limbs if limb.parent is None]
    if rootless != ["Root"]:
        print(f"expected Root alone to have no parent, got {rootless}", file=sys.stderr)
        return 1

    drawing = sum(1 for limb in limbs if limb.draws)
    print(f"# {len(limbs)} limbs, {drawing} of which draw a display list")
    print("# name, parent, draws, joint position (N64 x, y, z), limb struct, near DL, far DL")
    for limb, (_name, stem) in zip(limbs, LIMB_FILES):
        parent = f'"{limb.parent}"' if limb.parent else "None"
        near = f'"{limb.dlists[0]}"' if len(limb.dlists) > 0 else "None"
        far = f'"{limb.dlists[1]}"' if len(limb.dlists) > 1 else "None"
        print(f'    ("{limb.name}", {parent}, {limb.draws}, {limb.pos}, '
              f'"{stem}", {near}, {far}),')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
