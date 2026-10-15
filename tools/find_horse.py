"""Crop the frames of a title-demo capture around Epona, to judge how Kirby sits.

    python tools/find_horse.py --frames <dir> --out docs/preview/ride --keep 4

The rider is hard to pick out by eye in a contact sheet and the logo sits over the
middle of every frame, so the horse is found by its own brown instead: saturated,
mid-dark, and nothing else on Hyrule Field at dusk looks like it.
"""
import argparse
import glob
import os
import sys

from PIL import Image


def horse_pixels(image: Image.Image, step: int = 4):
    px = image.load()
    out = []
    for y in range(0, image.height, step):
        for x in range(0, image.width, step):
            r, g, b = px[x, y][:3]
            if 110 < r < 205 and 55 < g < 125 and 25 < b < 95 and r - g > 45 and g - b > 12:
                out.append((x, y))
    return out


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--frames", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--keep", type=int, default=4)
    a.add_argument("--pad", type=int, default=150)
    a.add_argument("--only", default="", help="one frame name, instead of the best few")
    args = a.parse_args()

    os.makedirs(args.out, exist_ok=True)
    found = []
    for path in sorted(glob.glob(os.path.join(args.frames, "*.png"))):
        if args.only and os.path.basename(path) != args.only:
            continue
        image = Image.open(path).convert("RGB")
        hits = horse_pixels(image)
        if len(hits) < 40:
            continue
        xs = sorted(x for x, _ in hits)
        ys = sorted(y for _, y in hits)
        mid = (xs[len(xs) // 2], ys[len(ys) // 2])
        near = [p for p in hits if abs(p[0] - mid[0]) < 300 and abs(p[1] - mid[1]) < 300]
        found.append((len(near), path, mid))
    found.sort(reverse=True)
    for count, path, (cx, cy) in found[:args.keep]:
        image = Image.open(path).convert("RGB")
        box = (max(0, cx - args.pad * 2), max(0, cy - args.pad * 2),
               min(image.width, cx + args.pad * 2), min(image.height, cy + args.pad))
        crop = image.crop(box)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        name = os.path.join(args.out, os.path.basename(path))
        crop.save(name)
        print(f"{name}  horse at {cx},{cy}, {count} pixels")
    if not found:
        print("no horse in those frames")
    return 0


if __name__ == "__main__":
    sys.exit(main())
