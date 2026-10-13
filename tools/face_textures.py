"""Kirby's faces, cut to fit the eye flipbook Ocarina of Time already drives.

    python tools/face_textures.py --preview build/face

Ocarina of Time swaps Link's eyes every frame. `Player_Draw` binds segment 8 to one of
eight eye textures and segment 9 to one of four mouth textures, chosen by the game from
what Link is doing: blinking on a timer, glancing left and right while idle, going wide
when startled, wincing when hurt. The head display list then samples segment 8 rather
than naming a texture, so whatever sits at that address is what his face does.

That is a flipbook, and Kirby 64 shipped nineteen complete faces for the same character.
Pointing one at the other gives Kirby the game's own expression logic for nothing: he
blinks on Link's blink timer and winces when Link takes damage, with no code and no
animation work. It is the one piece of real animation that transfers between these two
games, because it is sprite swapping rather than skeletal motion.

Two facts make the cut exact rather than guessed. Eighteen of the nineteen faces put
their features inside the same box, x 34 to 93 and y 36 to 93 of a 128 by 128 image,
measured by differencing them; the nineteenth is an outlier with a black band across the
top and is not used. And the game's own eye texture is 64 by 32, which at 16-bit colour
is 4096 bytes, exactly the N64's texture memory. A published Ship of Harkinian mod ships
replacements at precisely that size and format, so it is the proven shape to hand back.

The face box is roughly square and the tile is 2:1, so the crop is squashed on the way in
and stretched again by the texture coordinates on the model. That spends every texel on
the face instead of padding the crop out to the tile's aspect with blank cheek.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

# The face box, measured by differencing the nineteen faces rather than eyeballed.
# tools/face_textures.py --measure re-derives it and fails if it has moved.
FACE_BOX = (34, 36, 94, 94)          # left, top, right, bottom; right and bottom exclusive

# The game's own eye tile. 64 x 32 x 2 bytes is 4096, which is the whole of texture
# memory, and is what a shipped mod uses. Do not raise it.
TILE = (64, 32)
FORMAT_RGBA16 = 2

# Ship of Harkinian's names for the eight eye states, in the order the game's own
# sEyeTextures table holds them. They are not the decompilation's names: the port kept an
# older set, so "Closedf" has a stray f, and the last three are Shock, Unk1 and Unk2
# where the decompilation says Wide, Down and Wincing. Taken from the game's own
# oot.o2r, not guessed.
EYE_STATES = (
    "Open", "Half", "Closedf", "RollLeft", "RollRight", "Shock", "Unk1", "Unk2",
)

# Which Kirby 64 face plays each state, and whether it is mirrored.
#
# The pairing is by what the game does in that state, not by file order. Open and Closedf
# are the same face with the eyes shut, so a blink keeps his smile instead of changing his
# whole expression. RollRight is RollLeft flipped: 681B21D3 is the only face of the
# nineteen with any left-to-right asymmetry at all, measured, so it is the only one that
# can read as looking somewhere, and the flip gives the other direction for free.
FACES = {
    "Open":      ("79A9B9CD", False),   # eyes open, smiling. His default.
    "Half":      ("48D38B30", False),   # lids down, the middle of a blink
    "Closedf":   ("63B885EC", False),   # eyes shut, same smile
    "RollLeft":  ("681B21D3", False),   # pupils off centre
    "RollRight": ("681B21D3", True),    # the same face, flipped
    "Shock":     ("4F732E68", False),   # eyes wide, mouth open
    "Unk1":      ("160B5516", False),   # pupils low, looking down
    "Unk2":      ("64B53CAF", False),   # hurt, with a tear
}

PREFIX = {"adult": "gLinkAdultEyes", "child": "gLinkChildEyes"}


def _pillow():
    """Pillow lives in the user site packages, which -I drops. Add it back by path."""
    for base in sys.path:
        if "site-packages" in base:
            break
    else:
        import site
        sys.path.extend(site.getusersitepackages() if isinstance(
            site.getusersitepackages(), list) else [site.getusersitepackages()])
    from PIL import Image
    return Image


def face_paths(assets_root: str) -> dict[str, str]:
    folder = os.path.join(assets_root, "kirby64", "k64", "Kirby", "faces")
    out = {}
    for name in os.listdir(folder):
        if name.lower().endswith(".png"):
            out[name.split("_")[0].upper()] = os.path.join(folder, name)
    return out


def measure(assets_root: str) -> tuple[int, int, int, int]:
    """Re-derive the face box by differencing every face against the others.

    The point of keeping this is that FACE_BOX is a measurement, not a preference. If the
    ripped assets are ever replaced the box can be checked rather than trusted.
    """
    Image = _pillow()
    from collections import Counter

    boxes = []
    for path in face_paths(assets_root).values():
        im = Image.open(path).convert("RGB")
        pixels = list(im.getdata())
        background = Counter(pixels).most_common(1)[0][0]
        xs, ys = [], []
        for i, (r, g, b) in enumerate(pixels):
            if abs(r - background[0]) + abs(g - background[1]) + abs(b - background[2]) > 12:
                xs.append(i % im.width)
                ys.append(i // im.width)
        if not xs:
            continue
        box = (min(xs), min(ys), max(xs), max(ys))
        # One face carries a black band across the whole top and is not a face layout at
        # all. It is excluded here and never chosen in FACES.
        if box[0] == 0 and box[2] == im.width - 1:
            continue
        boxes.append(box)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes) + 1, max(b[3] for b in boxes) + 1)


def tile_image(path: str, mirror: bool):
    """One face, cropped to the shared box and squashed into the game's tile."""
    Image = _pillow()
    im = Image.open(path).convert("RGBA").crop(FACE_BOX)
    if mirror:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    return im.resize(TILE, Image.LANCZOS)


def pack_rgba16(im) -> bytes:
    """RGBA5551, top row first, which is the order the N64 reads."""
    out = bytearray()
    for r, g, b, a in im.convert("RGBA").getdata():
        out += struct.pack(">H", ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1)
                           | (1 if a > 127 else 0))
    return bytes(out)


def resource(im) -> bytes:
    """The game's texture container: 64 bytes of header, then format, size and pixels."""
    data = pack_rgba16(im)
    header = bytearray(64)
    header[4:8] = b"XETO"                       # OTEX, little-endian
    struct.pack_into("<I", header, 8, 1)        # version
    struct.pack_into("<II", header, 12, 0xDEADBEEF, 0xDEADBEEF)
    body = struct.pack("<4I2fI", FORMAT_RGBA16, TILE[0], TILE[1], 0, 1.0, 1.0, len(data))
    return bytes(header) + body + data


def build(assets_root: str, age: str) -> dict[str, bytes]:
    """Every eye resource for one age, keyed by the name the game looks it up under."""
    paths = face_paths(assets_root)
    out: dict[str, bytes] = {}
    for state in EYE_STATES:
        stem, mirror = FACES[state]
        if stem not in paths:
            raise FileNotFoundError(f"no ripped face named {stem} for the {state!r} state")
        out[PREFIX[age] + state + "Tex"] = resource(tile_image(paths[stem], mirror))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", default=".assets")
    parser.add_argument("--preview", help="write the eight tiles as PNGs here")
    parser.add_argument("--measure", action="store_true",
                        help="re-derive the face box and check it against FACE_BOX")
    parser.add_argument("--out", help="write the eye resources under here, one folder "
                                      "per age, for make_mod.py to pack")
    args = parser.parse_args()
    root = os.path.abspath(args.assets)

    if args.measure:
        found = measure(root)
        print(f"measured face box {found}, FACE_BOX is {FACE_BOX}")
        if found != FACE_BOX:
            print("FAIL: the measured box has moved. Update FACE_BOX and re-check the "
                  "texture coordinates in make_mod.py, which depend on it.")
            raise SystemExit(1)
        print("OK")
        return

    if args.preview:
        Image = _pillow()
        os.makedirs(args.preview, exist_ok=True)
        sheet = Image.new("RGBA", (TILE[0] * 4 * 3, TILE[1] * 2 * 3), (30, 30, 30, 255))
        paths = face_paths(root)
        for i, state in enumerate(EYE_STATES):
            stem, mirror = FACES[state]
            tile = tile_image(paths[stem], mirror)
            tile.resize((TILE[0] * 3, TILE[1] * 3), Image.NEAREST)
            sheet.paste(tile.resize((TILE[0] * 3, TILE[1] * 3), Image.NEAREST),
                        ((i % 4) * TILE[0] * 3, (i // 4) * TILE[1] * 3))
            tile.save(os.path.join(args.preview, f"eye_{i}_{state}.png"))
            print(f"  {state:<10} {stem}{' mirrored' if mirror else ''}")
        sheet.save(os.path.join(args.preview, "eyes_sheet.png"))
        print(f"wrote {args.preview}")

    for age in ("adult", "child"):
        built = build(root, age)
        print(f"{age}: {len(built)} eye resource(s), "
              f"{len(next(iter(built.values())))} bytes each")
        if args.out:
            # Written to disk rather than built inside the packer, because the packer
            # runs inside Blender and Blender ships no imaging library.
            folder = os.path.join(args.out, age)
            os.makedirs(folder, exist_ok=True)
            for name, payload in built.items():
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(payload)
            print(f"  wrote {folder}")


if __name__ == "__main__":
    main()
