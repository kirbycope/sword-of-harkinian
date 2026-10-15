"""Build the mod archive: the Fast64 export plus the re-posed player animations.

    python tools/pack_mod.py --export build/hm_export_child --age child \
        --anims dist/kirby_child.o2r --out dist/kirby_child.o2r.new

Two halves, both in the layout Harbour Masters' own mods use:

* the model, as Fast64 wrote it, under `alt/objects/<object>/...`, which is how their
  3DS Link mod ships a replacement skeleton;
* the animations as a pair beside the header, `objects/gameplay_keep/<name>_Data` with
  the header's segment string pointing at it, which is how their 3DS animation pack
  ships animations. Overriding `misc/link_animetion/...` in place instead mis-draws the
  player, and no mod of theirs does it.

The posed frames come from `carry_fk`, cached by `make_mod`; this script only moves
bytes and rewrites the 70-byte header that names them.
"""
import argparse
import os
import re
import struct
import sys
import zipfile

AGE_OBJECT = {"child": "object_link_child", "adult": "object_link_boy"}
# How far back along the hand bone the mitten hangs, per age. Measured off the shipped
# lists, which every posed animation was tuned against: the bone is aimed a hang past him
# so that the mitten lands at his side, and geometry left on the bone draws level with his
# cap. The adult is the taller rig and hangs further.
HAND_HANG = {"child": -1272, "adult": -1990}
# A limb with nothing to draw cannot ship an empty list. Nothing but EndDisplayList is
# what one renderer backend tolerates and another runs off the end of: pointing sixteen
# limbs at one took the whole world off the screen and left the HUD drawing over black.
# The published 3DS Link mod draws a degenerate triangle instead of an empty list, and so
# does this: three vertices on the limb's origin, no area, discarded by the rasteriser,
# but a real list with real commands in it.
NL = chr(10)
TAB = chr(9)
STUB_VTX = ('<Vertex Version="0">' + NL
            + (TAB + '<Vtx X="0" Y="0" Z="0" S="0" T="0" R="0" G="0" B="127" A="255"/>' + NL) * 3
            + '</Vertex>' + NL).encode()


def stub_dl(folder: str, material: str) -> bytes:
    """One triangle of zero area, which is to say nothing at all, but a real list."""
    return ('<DisplayList Version="0">' + NL
            + TAB + f'<CallDisplayList Path="{material}"/>' + NL
            + TAB + f'<LoadVertices Path="{folder}/gKirbyStubVtx" VertexBufferIndex="0" '
            + 'VertexOffset="0" Count="3"/>' + NL
            + TAB + '<Triangle1 V00="0" V01="1" V02="2" Flag0="0"/>' + NL
            + TAB + '<EndDisplayList/>' + NL
            + '</DisplayList>' + NL).encode()


RIDE_PREFIX = "gPlayerAnim_link_uma"
MOVING = re.compile(r"walk|run|jump")
SHORTS_PER_FRAME = 67


def seated(data: bytes, vanilla: bytes, name: str, args) -> bytes:
    """Put the rider where he belongs on the horse.

    A ball does not sit where Link's hips do and does not lean into a gallop the way he
    does, so the root translation of every riding animation is moved: up, across, and
    forward again on the moving set. The numbers were found on the screen, a build at a
    time, and they are nothing like the ones the hand-built model needed, which is why
    they are applied here rather than carried over. They have to live in the build, or a
    rebuild quietly ships him on the horse's rump again.
    """
    out = bytearray(data)
    frames = struct.unpack_from("<I", out, 64)[0] // SHORTS_PER_FRAME
    moving = bool(MOVING.search(name))
    lift = args.ride_lift + (args.ride_moving_lift if moving else 0)
    forward = args.ride_forward + (args.ride_moving if moving else 0)
    for frame in range(frames):
        at = 68 + frame * SHORTS_PER_FRAME * 2
        x, y, z = struct.unpack_from("<3h", vanilla, at)
        struct.pack_into("<3h", out, at,
                         max(-32768, min(32767, x + args.ride_side)),
                         max(-32768, min(32767, y + lift)),
                         max(-32768, min(32767, z + forward)))
    return bytes(out)


def header_for(raw: bytes, data_path: str) -> bytes:
    """The animation header, pointing at `data_path` instead of its own blob."""
    length = struct.unpack_from("<I", raw, 70)[0]
    segment = ("__OTR__" + data_path).encode()
    return bytes(bytearray(raw[:70]) + struct.pack("<I", len(segment)) + segment + raw[74 + length:])


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--export", required=True, help="folder Fast64 exported into")
    a.add_argument("--age", choices=("child", "adult"), required=True)
    a.add_argument("--anims", default="", help="archive holding posed blobs under misc/link_animetion/kirby/")
    a.add_argument("--game", default=".toolchain/soh/app/oot.o2r")
    a.add_argument("--assets", default="", help="archive to carry asset overrides from (sheath, hands, eyes)")
    a.add_argument("--no-hands", dest="hands", action="store_false",
                   help="leave the game's bare-hand lists alone, so Link's own hands draw")
    a.add_argument("--ride-lift", type=int, default=800,
                   help="how far up the saddle he is moved; measured on the screen")
    a.add_argument("--ride-forward", type=int, default=0,
                   help="fore and aft on the standing horse, where vanilla is already right")
    a.add_argument("--ride-side", type=int, default=-250,
                   help="across the saddle; he sits a little left of the spine without it")
    a.add_argument("--ride-moving", type=int, default=2400,
                   help="forward again at a walk, run or jump, where Link leans and a ball cannot")
    a.add_argument("--ride-moving-lift", type=int, default=0)
    a.add_argument("--hand-hang", type=int, default=0,
                   help="how far back along the hand bone the mitten hangs; by default the "
                        "measurement for this age, taken off the shipped lists that the "
                        "animation posing was tuned against")
    a.add_argument("--carry", default="sheath,hands,eyes,tlut",
                   help="which asset groups --assets carries: sheath, hands, eyes, tlut")
    a.add_argument("--out", required=True)
    args = a.parse_args()

    game = zipfile.ZipFile(args.game)
    headers = {}
    for path in game.namelist():
        if not path.startswith("objects/gameplay_keep/gPlayerAnim_"):
            continue
        raw = game.read(path)
        if struct.unpack_from("<I", raw, 64)[0] != 1:
            continue
        length = struct.unpack_from("<I", raw, 70)[0]
        blob = raw[74:74 + length].decode("utf8", "replace").replace("__OTR__", "")
        headers.setdefault(blob.split("/")[-1], []).append((path, raw))

    # The game keeps the animation data under its own path, which the header names; the
    # seating is measured against those vanilla values so it does not stack up on a rebuild.
    vanilla_path = {n.split("/")[-1]: n for n in game.namelist()}

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    folder = f"objects/{AGE_OBJECT[args.age]}"
    empty_name = f"{folder}/gKirbyStubDL"
    material = ""
    for root, _dirs, files in os.walk(args.export):
        for name in sorted(files):
            if name.startswith("mat_") and not material:
                material = f"{folder}/{name}"
    model = animations = carried = emptied = handed = seated_count = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(args.export):
            for name in sorted(files):
                full = os.path.join(root, name)
                inside = os.path.relpath(full, args.export).replace(os.sep, "/")
                payload = open(full, "rb").read()
                z.writestr("alt/" + inside, payload)
                model += 1
        if emptied:
            raise SystemExit(f"{emptied} limb(s) exported with no display list. Collapse the "
                             "filler boxes instead of deleting them; see blender/hm_export_model.py")
        if args.hands:
            # The player code does not draw the hand limb's own list for long: it swaps in
            # one of the game's bare-hand lists for whatever he is doing, and those draw
            # Link's hand. With his hand textures replaced by Kirby's skin that comes out
            # as Link's hands in pink, which is exactly what it looks like. So every
            # bare-hand list is pointed at the hand the exporter wrote. Lists holding
            # something are left alone: the item's geometry lives inside them.
            # The mitten does not sit on the hand bone. Every animation is posed so that a
            # mitten hung back along the arm lands at his side, which means the bone itself
            # is aimed a mitten's hang past him: geometry left on the bone draws level with
            # the top of his cap. The shipped lists hang it 1272 units back along the bone's
            # x and the posed animations are measured against that, so the copy does too.
            hang = args.hand_hang or HAND_HANG[args.age]
            exported = {}
            for root, _dirs, names in os.walk(args.export):
                for name in names:
                    for side in ("Left", "Right"):
                        if f"{side}HandLimb" not in name:
                            continue
                        body = open(os.path.join(root, name), "rb").read()
                        if name.endswith("_vtx"):
                            text = body.decode("utf8")
                            text = re.sub(r'X="(-?\d+)"',
                                          lambda m: 'X="%d"' % (int(m.group(1)) + hang),
                                          text)
                            hung_vtx = f"{folder}/gKirbyHungHand{side}Vtx"
                            z.writestr("alt/" + hung_vtx, text.encode())
                            exported[side + "_vtx"] = hung_vtx
                        else:
                            exported[side] = body
            for side in ("Left", "Right"):
                if side in exported and side + "_vtx" in exported:
                    text = exported[side].decode("utf8")
                    text = re.sub(r'(<LoadVertices Path=")[^"]+(")',
                                  lambda m: m.group(1) + exported[side + "_vtx"] + m.group(2), text)
                    exported[side] = text.encode()
            # Only the plain open-hand lists. The closed, raised and held-out variants are
            # the ones the player code uses when he is carrying, climbing or has just put
            # the sword away, and the game's own geometry for them is already balled into a
            # mitten by the vertex overrides. Giving those his whole hand as well draws a
            # second mitten over the first, which reads as a lump hanging off his side.
            bare = re.compile(r"(Left|Right)Hand(Near|Far)DL$")
            for path in game.namelist():
                if not path.startswith(f"{folder}/"):
                    continue
                name = path.split("/")[-1]
                match = bare.search(name)
                if not match or "Holding" in name or "And" in name or "Fist" in name:
                    continue
                payload = exported.get(match.group(1))
                if payload is None:
                    continue
                z.writestr("alt/" + path, payload)
                handed += 1

        if args.assets:
            # The sheathed sword and shield, the hands and the blinking eyes are drawn by
            # the game's own display lists, which the player code picks by what he has
            # equipped, so they are not part of the skeleton the exporter writes. They
            # are overridden as what they are: the vertex lists and textures those lists
            # name, at the archive's own paths.
            groups = {
                "sheath": r"Sheath.*Vtx|Vtx_[0-9A-F]{6}",
                "hands": r"(Fist|Holding|HandClosed|HandUp|HandOut).*Vtx|HandTex|HandTLUT",
                "eyes": r"Eyes\w*Tex",
                "tlut": r"TLUT_[0-9A-F]{6}",
            }
            wanted = [g.strip() for g in args.carry.split(",") if g.strip()]
            unknown = [g for g in wanted if g not in groups]
            if unknown:
                raise SystemExit(f"unknown --carry group(s): {unknown}, know {sorted(groups)}")
            carry = re.compile("(" + "|".join(groups[g] for g in wanted) + ")$")
            source = zipfile.ZipFile(args.assets)
            for path in source.namelist():
                if path.startswith("alt/") and carry.search(path.split("/")[-1]):
                    z.writestr(path, source.read(path))
                    carried += 1

        if args.anims:
            posed = zipfile.ZipFile(args.anims)
            for path in posed.namelist():
                # Either layout is read: the posed blobs as make_mod writes them, and the
                # data-beside-the-header layout this script ships, so a packed archive can
                # be repacked without the animation pass being run again.
                if "/link_animetion/kirby/" in path:
                    blob = path.split("/")[-1]
                elif path.startswith("objects/gameplay_keep/") and path.endswith("_Data"):
                    header_name = path.split("/")[-1][:-len("_Data")]
                    blob = next((b for b, hs in headers.items()
                                 if any(h.split("/")[-1] == header_name for h, _ in hs)), "")
                    if not blob:
                        continue
                else:
                    continue
                for header_path, raw in headers.get(blob, ()):
                    anim = header_path.split("/")[-1]
                    data_path = f"objects/gameplay_keep/{anim}_Data"
                    payload = posed.read(path)
                    if args.age == "adult" and anim.startswith(RIDE_PREFIX):
                        payload = seated(payload, game.read(vanilla_path[blob]), anim, args)
                        seated_count += 1
                    z.writestr(data_path, payload)
                    z.writestr(header_path, header_for(raw, data_path))
                    animations += 1
    size = os.path.getsize(args.out) / 1024
    print(f"{args.out}: {model} model resources ({emptied} empty limb(s) stubbed), {carried} carried asset overrides, {handed} bare-hand "
          f"list(s) given his own hand, {animations} animations "
          f"({seated_count} seated on the horse), {size:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
