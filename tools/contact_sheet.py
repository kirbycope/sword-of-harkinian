"""A contact sheet of a folder of frames, to find the shots worth looking at.

    python tools/contact_sheet.py --frames <dir> --out docs/preview/sheet.png --every 3
"""
import argparse
import glob
import os
import sys

from PIL import Image, ImageDraw


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("--frames", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--every", type=int, default=1)
    a.add_argument("--cols", type=int, default=6)
    a.add_argument("--width", type=int, default=300)
    a.add_argument("--label", action="store_true", help="write each frame's name on it")
    args = a.parse_args()

    files = sorted(glob.glob(os.path.join(args.frames, "*.png")))[::args.every]
    if not files:
        print(f"no frames in {args.frames}")
        return 1
    first = Image.open(files[0])
    height = round(args.width * first.height / first.width)
    rows = (len(files) + args.cols - 1) // args.cols
    sheet = Image.new("RGB", (args.cols * args.width, rows * height), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(files):
        tile = Image.open(path).convert("RGB").resize((args.width, height), Image.LANCZOS)
        x, y = (index % args.cols) * args.width, (index // args.cols) * height
        sheet.paste(tile, (x, y))
        if args.label:
            # Picking a frame off a sheet by counting rows and columns has been wrong every
            # time; the name goes on the tile instead.
            name = os.path.splitext(os.path.basename(path))[0]
            draw.rectangle((x + 2, y + 2, x + 8 + 6 * len(name), y + 14), fill=(0, 0, 0))
            draw.text((x + 5, y + 3), name, fill=(255, 220, 0))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    sheet.save(args.out)
    print(f"{args.out}: {len(files)} frames, {args.cols} across, "
          f"first {os.path.basename(files[0])}, last {os.path.basename(files[-1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
