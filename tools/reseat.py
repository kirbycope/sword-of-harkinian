"""Re-shift the riding animations in a packed adult archive without repacking.

    python tools/reseat.py dist/kirby_adult.o2r --lift 1790 --forward -400

The root translation of every gPlayerAnim_link_uma_* blob is rewritten to the vanilla
value plus (0, lift, forward) in game units, so a saddle tune can be looked at in the
attract demo in a minute instead of waiting on a full animation pass. Once a pair of
numbers is right, put them in make_mod.py as RIDE_LIFT and RIDE_FORWARD (note the sign:
make_mod subtracts RIDE_FORWARD from z, so --forward here is minus RIDE_FORWARD).
"""
import argparse
import os
import re
import shutil
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carry_fk as fk  # noqa: E402

SHORTS = fk.SHORTS_PER_FRAME


def main() -> None:
    a = argparse.ArgumentParser()
    a.add_argument("archive")
    a.add_argument("--game", default=".toolchain/soh/app/oot.o2r")
    a.add_argument("--lift", type=int, required=True)
    a.add_argument("--forward", type=int, required=True, help="added to z; +z is toward the horse's head")
    a.add_argument("--moving-lift", type=int, default=0,
                   help="added again to y on the moving animations")
    a.add_argument("--moving", type=int, default=0,
                   help="added again on the moving animations (walk, run, jump): Link leans into "
                        "the gallop and a ball cannot, so without this he reads as sitting on the rump")
    args = a.parse_args()
    game = zipfile.ZipFile(args.game)
    heads = [n.rsplit("/", 1)[1] for n in game.namelist()
             if n.startswith("objects/gameplay_keep/" + fk.RIDE_PREFIX)]
    replaced: dict[str, bytes] = {}
    moving = re.compile(r"walk|run|jump")
    for h in heads:
        extra = args.moving if moving.search(h) else 0
        extra_y = args.moving_lift if moving.search(h) else 0
        frames, blob = fk.animation_blob(game, h)
        vanilla = game.read(blob)
        name = "misc/link_animetion/kirby/" + blob.split("/")[-1]
        with zipfile.ZipFile(args.archive) as mod:
            data = bytearray(mod.read(name))
        count = struct.unpack_from("<I", data, 64)[0] // SHORTS
        for f in range(count):
            at = 68 + f * SHORTS * 2
            x, y, z = struct.unpack_from("<3h", vanilla, at)
            struct.pack_into("<3h", data, at, x, max(-32768, min(32767, y + args.lift + extra_y)),
                             max(-32768, min(32767, z + args.forward + extra)))
        replaced[name] = bytes(data)
    tmp = args.archive + ".tmp"
    with zipfile.ZipFile(args.archive) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, replaced.get(info.filename, src.read(info.filename)))
    shutil.move(tmp, args.archive)
    print(f"{len(replaced)} riding blobs reseated: lift {args.lift}, forward {args.forward}, "
          f"moving +{args.moving} forward +{args.moving_lift} up, in {args.archive}")


if __name__ == "__main__":
    main()
