"""Put a mitten on the game's own hand where it holds something.

Every hand list that holds an item keeps its vanilla display list, because the item's
geometry lives inside it: blanking the Kokiri sword list takes the sword with it. So a
drawn sword is Link's own fist on the hilt, flesh coloured and five fingered, at the end
of an arm Kirby does not have.

Two overrides turn that fist into a mitten without touching the list. The list names its
vertices by resource path, so a replacement vertex list under the same name reshapes the
drawing: every vertex drawn under a hand texture is projected onto a sphere around the
fist's own centre, which makes a ball with the fist's triangulation and leaves the sword,
shield or ocarina vertices exactly where they were. And the hand textures themselves are
replaced by Kirby's skin, which the port honours as a texture-pack style replacement
whatever format the list asked for. The child fist is one texture on one palette; the
adult hand is a gauntlet and a closed hand on two palettes, one of which the back sheath
shares, which is why the textures are replaced and the palettes are not.

The hand batches are found by reading the list: `gsDPLoadTextureBlock` names the texture
in force, and the `gsSPVertex` calls that follow it under a hand texture are the hand.
"""
from __future__ import annotations

import glob
import os
import re
import struct

# The textures a vanilla hand list draws its hand with, per player object. The gauntlet
# is part of the adult hand model and goes into the ball with it.
HAND_TEXTURES = {
    "child": ("gLinkChildHandTex",),
    "adult": ("gLinkAdultHandTex", "gLinkAdultClosedHandThumbTex", "gLinkAdultClosedHandSideTex",
              "gLinkAdultDefaultGauntlet1Tex", "gLinkAdultDefaultGauntlet2Tex"),
}

# Mitten radius as a multiple of the fist's own reach from its centre. One would be a
# ball the fist just fits in; Kirby's mitten is bigger than that against his sword.
HAND_BALL = 1.35

# The fist on a sword, as a fraction of the mitten's radius.
SWORD_FIST = 0.62
# The ball on a sword's hilt, as a fraction of the mitten's radius.
SWORD_HAND = 0.65

# Lists worth touching: the ones that hold something. Bare fists are blanked elsewhere.
# Lists drawn at a hand that is not the plain open hand: holding something, or the
# closed and raised variants the game swaps in while running and guarding.
HOLDING = re.compile(r"Fist|Holding|HandClosed|HandUp|HandOut")

LOAD = re.compile(r"gsDPLoadTextureBlock\(\s*(\w+)|gsDPSetTextureImage\([^,]*,[^,]*,[^,]*,\s*(\w+)")
VERTEX = re.compile(r"gsSPVertex\(&(\w+)\[(\d+)\],\s*(\d+),\s*0\)")
VTX = re.compile(r"VTX\(([^)]*)\)")


def signed_byte(value: float) -> int:
    return max(-128, min(127, int(round(value * 127.0)))) & 0xFF


def hand_ranges(object_dir: str, age: str, skip=()) -> dict[str, set[int]]:
    """Which vertices of which vertex lists are drawn under a hand texture."""
    hands = set(HAND_TEXTURES[age])
    ranges: dict[str, set[int]] = {}
    for path in sorted(glob.glob(os.path.join(object_dir, "*DL.inc.c"))):
        name = os.path.basename(path)[:-len(".inc.c")]
        if not HOLDING.search(name) or name in skip:
            continue
        current = None
        for line in open(path, encoding="utf8", errors="replace"):
            m = LOAD.search(line)
            if m:
                current = m.group(1) or m.group(2)
                continue
            m = VERTEX.search(line)
            if m and current in hands:
                start, count = int(m.group(2)), int(m.group(3))
                ranges.setdefault(m.group(1), set()).update(range(start, start + count))
    return ranges


# Lists whose item is a sword. These hang the other way round: the fist sits `hang`
# BEFORE the joint along the hand's x, so the blade runs up through the joint from a
# fist at the flank, and an upright blade needs the joint above the fist, which is where
# Link's arm reaches from a shoulder above the cap. Everything else hangs past the joint.
SWORD_LISTS = re.compile(r"KokiriSword|MasterSword|Bgs|Hammer|BrokenGiantsKnife")
# Lists whose item is a shield in the hand. These hang along a vector of their own, the
# guard's, rather than the hand's x; see carry_fk.shield_hang.
SHIELD_LISTS = re.compile(r"Shield")
# Lists that hold something, named with what they hold.
ITEM_LISTS = re.compile(r"And|Holding")


SKIN_PINK = (240, 160, 176)


def rgba16(colour) -> bytes:
    r, g, b = colour
    return struct.pack(">H", ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1) | 1)


def texture_resource(fmt: int, width: int, height: int, data: bytes) -> bytes:
    """A Texture resource as the port reads it: 64-byte header, then format, width,
    height, a zero, two unit scales and the byte count. Format 2 is RGBA16, 4 is CI8."""
    header = bytearray(64)
    header[4:8] = b"XETO"
    struct.pack_into("<I", header, 8, 1)
    struct.pack_into("<II", header, 12, 0xDEADBEEF, 0xDEADBEEF)
    return bytes(header) + struct.pack("<4I2fI", fmt, width, height, 0, 1.0, 1.0, len(data)) + data


def hand_textures(object_dir: str, age: str, game_archive: str, log=print) -> dict[str, bytes]:
    """Replacement hand textures and palettes, keyed by archive name.

    Link's hand textures are CI8: each texel is an index into a palette the display list
    loads beside it. A replacement in RGBA16, which is what the body textures are, is
    read as indices anyway, and the hand comes out striped in whatever the palette holds
    at those numbers; that is where the green stripes on the fist came from. So each
    hand texture is shipped as a CI8 fill of one index, and each palette only hands use
    is shipped all pink, so any index is pink. A palette shared with something else (the
    adult gauntlet's, which the back sheath uses too) is left alone and the fill picks
    its nearest pink.
    """
    import glob
    import re
    import zipfile

    obj = os.path.basename(os.path.normpath(object_dir))
    xml_path = os.path.normpath(os.path.join(object_dir, *([".."] * 5), "assets", "xml",
                                             "objects", obj + ".xml"))
    xml = open(xml_path, encoding="utf8").read() if os.path.exists(xml_path) else ""
    declared = {m.group(1): (m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5), 16))
                for m in re.finditer(r'<Texture\s+Name="(\w+)"\s+Format="(\w+)"\s+Width="(\d+)"'
                                     r'\s+Height="(\d+)"\s+Offset="0x([0-9A-Fa-f]+)"', xml)}
    tlut_of: dict[str, str] = {}
    users: dict[str, set[str]] = {}
    for path in glob.glob(os.path.join(object_dir, "*DL.inc.c")):
        current = None
        for line in open(path, encoding="utf8", errors="replace"):
            m = re.search(r"gsDPLoadTextureBlock\((\w+),\s*G_IM_FMT_CI", line)
            if m:
                current = m.group(1)
                continue
            m = re.search(r"gsDPLoadTLUT_pal256\((\w+)\)", line)
            if m and current:
                tlut_of[current] = m.group(1)
                users.setdefault(m.group(1), set()).add(os.path.basename(path))
                current = None
    archive = zipfile.ZipFile(game_archive)
    names = set(archive.namelist())

    def archive_name(decomp_name: str, kind: str) -> str | None:
        if f"objects/{obj}/{decomp_name}" in names:
            return decomp_name
        if decomp_name in declared:
            candidate = f"{obj}{kind}_{declared[decomp_name][3]:06X}"
            if f"objects/{obj}/{candidate}" in names:
                return candidate
        return None

    out: dict[str, bytes] = {}
    for tex in HAND_TEXTURES[age]:
        tlut = tlut_of.get(tex)
        if tex not in declared or tlut is None:
            log(f"  {tex}: not a palette texture in the extraction, left alone")
            continue
        _fmt, width, height, _offset = declared[tex]
        tlut_name = archive_name(tlut, "TLUT")
        if tlut_name is None:
            log(f"  {tex}: palette {tlut} not found in the archive, left alone")
            continue
        hand_only = all(HOLDING.search(u) or "Hand" in u for u in users.get(tlut, ()))
        if hand_only:
            if tlut_name not in out:
                # The archive's own size, not the extraction's: the port pads a
                # palette out to what pal256 loads, and a shorter one leaves stale
                # entries behind it.
                head = archive.read(f"objects/{obj}/{tlut_name}")
                _f, pw, ph, size = struct.unpack_from("<4I", head, 64)
                out[tlut_name] = texture_resource(2, pw, ph, rgba16(SKIN_PINK) * (size // 2))
                log(f"  {tlut}: hand-only palette, {size // 2} entries all skin pink")
            index = 0
        else:
            data = archive.read(f"objects/{obj}/{tlut_name}")
            size = struct.unpack_from("<I", data, 76)[0]
            entries = struct.unpack_from(f">{size // 2}H", data, 80)
            colours = [(((v >> 11) & 31) * 8, ((v >> 6) & 31) * 8, ((v >> 1) & 31) * 8) for v in entries]
            index = min(range(len(colours)),
                        key=lambda i: sum((a - b) ** 2 for a, b in zip(colours[i], SKIN_PINK)))
            log(f"  {tlut}: shared palette, nearest pink is entry {index} {colours[index]}")
        out[tex] = texture_resource(4, width, height, bytes([index]) * (width * height))
        log(f"  {tex}: {width}x{height} CI8 filled with entry {index}")
    return out


def mitten_vertex_lists(object_dir: str, age: str, skip=(), radius: float | None = None,
                        hang: float = 0.0, log=print, ball: bool = True,
                        shield_dir=None) -> dict[str, str]:
    """Replacement vertex lists, keyed by their vanilla name, with the hand made a ball.

    With `radius` given, the ball is that size, in game units, and sits on the hand
    limb's own origin, which is where the character's mitten is drawn when the hand is
    empty; so the hand looks the same whether it holds something or not. Without it,
    the ball is around the fist's own centre at HAND_BALL times the fist's reach.

    With `ball` off the hand keeps Link's own fist and only moves with the item by the
    hang. A ball the mitten's size on a held shield pokes out through its face, and a
    small fist on the hilt is what has been looked at and approved.
    """
    out: dict[str, str] = {}
    for vtx_name, hand in sorted(hand_ranges(object_dir, age, skip).items()):
        path = os.path.join(object_dir, vtx_name + ".inc.c")
        if not os.path.exists(path):
            log(f"  note: {vtx_name}.inc.c is missing, that hand keeps its fingers")
            continue
        rows = []
        for m in VTX.finditer(open(path, encoding="utf8", errors="replace").read()):
            fields = [int(f.strip(), 0) for f in m.group(1).split(",")]
            if len(fields) == 9:
                rows.append(fields)
        points = {i: rows[i][:3] for i in hand if i < len(rows)}
        if not points:
            continue
        n = len(points)
        # With `hang`, the whole list, ball and item alike, moves out along the limb's x
        # by that much, so the hilt sits in the mitten the limb draws when it is empty,
        # which is hung the same distance (see carry_fk.hand_offset).
        is_sword = bool(SWORD_LISTS.search(vtx_name))
        # Every hand is a ball. An empty one, closed or raised, is the mitten at the
        # mitten's size; a hand with something in it is a ball the size of Link's fist
        # centred where his fist was, on the hilt or behind the shield, so he keeps his
        # arms while holding equipment and nothing pokes through the shield. With `ball`
        # off a held item keeps Link's own fist.
        is_item = bool(ITEM_LISTS.search(vtx_name))
        this_ball = ball or not is_item
        # Every list hangs the same way, fist BEFORE the joint along the hand's x, as
        # the sword lists always did. The game plays one animation for the guard whether
        # the sword is out or sheathed (link_normal_defense_wait), drawing the sword
        # list in one case and the closed fist in the other, so the two cannot hang in
        # opposite directions; and the empty hand's mitten hangs this way too.
        this_hang = -hang
        # A shield list hangs along the guard's vector; everything else along x.
        shift = [this_hang, 0.0, 0.0]
        if shield_dir is not None and SHIELD_LISTS.search(vtx_name):
            shift = [hang * shield_dir[k] for k in range(3)]
        fist = [sum(p[k] for p in points.values()) / n for k in range(3)]
        # A sword's ball sits where Link's fist sat, on the grip between pommel and
        # guard, moved with the item by the hang, and at the fist's own reach rather
        # than the mitten's: at the mitten's size, or centred on the joint instead of
        # the grip, it hangs off the hilt as a lump and reads as a disc in the game.
        if radius is not None and is_item:
            # On the hilt the ball is SWORD_HAND of the mitten's size: the fist's own
            # size floats off the body, the mitten's swallows the hilt. Behind a shield
            # it stays the fist's size.
            centre = [fist[k] + shift[k] for k in range(3)]
            this_radius = SWORD_HAND * radius if is_sword else None
        else:
            centre = fist if radius is None else [this_hang, 0.0, 0.0]
            this_radius = radius

        def offset(p):
            # Directions come from the fist's own centre, whatever the ball is placed
            # at, or every vertex lands on the one hemisphere facing the old fist.
            return [p[k] - fist[k] for k in range(3)]

        def length(v):
            return (v[0] ** 2 + v[1] ** 2 + v[2] ** 2) ** 0.5

        if radius is not None and is_item and this_radius is None:
            this_radius = HAND_BALL * max(length(offset(p)) for p in points.values())
        ball_radius = this_radius if this_radius is not None else HAND_BALL * max(length(offset(p)) for p in points.values())
        lines = ['<Vertex Version="0">']
        for i, row in enumerate(rows):
            x, y, z, s, t, r, g, b, a = row
            if radius is not None and (i not in points or not this_ball):
                # The item keeps its own orientation, blade along the hand's x. Only the
                # hang is applied, and a sword's goes the other way; see SWORD_LISTS.
                x, y, z = (int(round(x + shift[0])), int(round(y + shift[1])), int(round(z + shift[2])))
            if i in points and this_ball:
                d = offset(row[:3])
                if length(d) < 1e-6:
                    d = [0.0, 1.0, 0.0]
                unit = [v / length(d) for v in d]
                x, y, z = (int(round(centre[k] + unit[k] * ball_radius)) for k in range(3))
                r, g, b = (signed_byte(unit[k]) for k in range(3))
            lines.append(f'\t<Vtx X="{x}" Y="{y}" Z="{z}" S="{s}" T="{t}" '
                         f'R="{r}" G="{g}" B="{b}" A="{a}"/>')
        lines.append("</Vertex>\n")
        out[vtx_name] = "\n".join(lines)
        what = f"balled, radius {ball_radius / 1000.0:.2f}" if this_ball else "kept as Link's fist"
        where = (f"hung {this_hang / 1000.0:+.2f}" if shift[1] == 0.0 and shift[2] == 0.0
                 else "hung along the guard's vector " + str([round(v / 1000.0, 2) for v in shift]))
        log(f"  {vtx_name}: {n} of {len(rows)} hand vertices {what}, {where}")
    return out
