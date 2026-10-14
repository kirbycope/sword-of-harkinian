"""Does the game read everything this mod ships?

    python tools/check_archive.py dist/kirby_child.o2r [.toolchain/soh/app/oot.o2r]

An `alt/<path>` entry replaces the game's `<path>`, or is a new resource one of the mod's
own XML lists names by path. Anything else is dead weight: nothing refers to it and
nothing says so. That is how the sheath lists, shipped under the decompilation's names
while the archive names vertex arrays by offset, drew nothing for a day. Run this after
every pack, and treat a line here as a bug unless the README says the entry is
intentionally inert (today: the decomp-named hand and sheath lists, and unused materials).

Animation data under `misc/link_animetion/kirby/` is the one deliberate exception: those
are new resources the overridden headers point at by name, and the game archive is not
meant to have them.
"""
import re
import sys
import zipfile

DELIBERATE = ("misc/link_animetion/kirby/",)


def main() -> int:
    mod = sys.argv[1]
    game = sys.argv[2] if len(sys.argv) > 2 else ".toolchain/soh/app/oot.o2r"
    base = set(zipfile.ZipFile(game).namelist())
    archive = zipfile.ZipFile(mod)
    # The mod's own XML display lists and materials refer to the mod's own vertex
    # lists, textures and materials by path; those are new resources and are read.
    referred = set()
    for name in archive.namelist():
        data = archive.read(name)
        if data[:1] == b"<":
            referred.update(re.findall(r'Path="([^"]+)"', data.decode("utf8", "replace")))
    dead = []
    total = 0
    for name in archive.namelist():
        if name.endswith("/"):
            continue
        total += 1
        if name.startswith("alt/"):
            target = name[len("alt/"):]
            if target not in base and target not in referred:
                dead.append(name)
        elif not name.startswith(DELIBERATE):
            dead.append(name + "  (not under alt/, not animation data)")
    print(f"{mod}: {total} entries, {len(dead)} the game will not read")
    for name in dead:
        print("  " + name)
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
