"""Build the Ship of Harkinian mod straight from the rigged Blender model.

    blender --background --python tools/make_mod.py -- \
        --blend build/sword_kirby_rigged.blend --out build/sword_kirby.o2r

No ROM, no MIPS toolchain, no packer. A mod is a ZIP of per-resource files under
`alt/objects/<object>/`, and the geometry resources in it are XML. The format was read
off a published model mod; `docs/mod-format.md` records it.

What gets written, for each of Link's eighteen drawing limbs:

  * a vertex list, as `<Vtx>` elements in that limb's own space;
  * a display list, as `<LoadVertices>` and `<Triangles2>` elements, named after the
    vanilla list it replaces so the game picks it up;
  * a material display list per texture, carrying the tile and combiner setup;
  * the textures, as a 64-byte resource header and then 16-bit pixels.

Two numbers here were measured rather than assumed, because guessing either produces a
model that is silently wrong on screen. Blender units convert to game units by a factor
of 1000, which is Fast64's own scale times its actor scale, and the axes map straight
across with no swap or flip. `tools/calibrate_axes.py` derives both from Link's real
geometry and reports a zero extent error against it.
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import zipfile

import bpy
from mathutils import Vector

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "blender"))
from link_skeleton import DEFORM, FAR_DL, NEAR_DL, limb_of_bone  # noqa: E402

# Limbs this mod does not touch. The sheath keeps its vanilla display list so the
# sword and scabbard the game gives Link still appear; Kirby carries none of his own.
LEAVE_ALONE = ("Sheath",)

# An empty display list is legal, and it is how a limb this character does not have is
# hidden: the vanilla limb then draws nothing at all.
EMPTY_DL = '<DisplayList Version="0">\n\t<EndDisplayList/>\n</DisplayList>\n'

# The hand limb's own display list is not the only thing drawn at the hand bone, and
# this is the trap that leaves two skin-coloured lumps hanging in the air beside a short
# character's head. The player code picks a hand list by what Link is holding, from
# tables in z_player_lib.c, and an empty hand walking around uses the *fist* lists, not
# the hand ones. Replacing only gLinkChildLeftHandNearDL therefore hides nothing during
# ordinary play: gLinkChildLeftFistNearDL draws vanilla Link's fist instead, up at the
# hand bone, which on a character as short as Kirby is level with the top of his cap.
#
# So every list that draws a bare hand is emptied too. The ones that draw a hand holding
# something are deliberately left alone, because the item's own geometry lives inside
# them, and blanking those would take the sword, shield, slingshot and ocarina with it.
HIDE_HANDS = {
    "adult": (
        "gLinkAdultLeftHandClosedNearDL", "gLinkAdultLeftHandClosedFarDL",
        "gLinkAdultRightHandClosedNearDL", "gLinkAdultRightHandClosedFarDL",
        "gLinkAdultLeftHandOutNearDL", "gLinkAdultRightHandOutNearDL",
    ),
    "child": (
        "gLinkChildLeftFistNearDL", "gLinkChildLeftFistFarDL",
        "gLinkChildRightHandClosedNearDL", "gLinkChildRightHandClosedFarDL",
        "gLinkChildLeftHandUpNearDL", "gLinkChildLeftHandUpFarDL",
    ),
}

OBJECT = "objects/object_link_boy"
ALT = "alt/" + OBJECT

# Ocarina of Time has two player models and the game opens on the younger one, so a mod
# that replaces only the adult shows nothing for hours of play. Child Link is a separate
# object with the same 21 limbs in the same order, so the same geometry serves both: the
# vertices are in bone-local space, and Kirby is the same size whichever Link he stands
# in for. Only the display list names differ, and they are read from the extracted
# object rather than guessed, because one of them is "Leftshoulder" with a small s.
# Child Link is about 0.70 the size of the adult, measured from their limb data: the
# root joint is 2376 against 3377 and the head joint 996 against 1392. The geometry is
# authored against the adult rig, so it has to be scaled for the child or Kirby stands
# a third taller than the Link he is replacing.
CHILD_RATIO = 0.70
CHILD_OBJECT = "objects/object_link_child"
CHILD_ALT = "alt/" + CHILD_OBJECT
CHILD_LIMB_STEMS = [
    "gLinkChildRootLimb", "gLinkChildWaistLimb", "gLinkChildLowerControlLimb",
    "gLinkChildRightThighLimb", "gLinkChildRightShinLimb", "gLinkChildRightFootLimb",
    "gLinkChildLeftThighLimb", "gLinkChildLeftShinLimb", "gLinkChildLeftFootLimb",
    "gLinkChildUpperControlLimb", "gLinkChildHeadLimb", "gLinkChildHatLimb",
    "gLinkChildCollarLimb", "gLinkChildLeftshoulderLimb", "gLinkChildLeftForearmLimb",
    "gLinkChildLeftHandLimb", "gLinkChildRightshoulderLimb", "gLinkChildRightForearmLimb",
    "gLinkChildRightHandLimb", "gLinkChildSwordAndSheathLimb", "gLinkChildTorsoLimb",
]


def child_display_lists(object_dir: str) -> dict[str, tuple[str | None, str | None]]:
    """Read child Link's near and far display list names, limb by limb."""
    import re

    from link_skeleton import LIMBS

    pattern = re.compile(r"^\s*(\w+),\s*//\s*[01]\s*$", re.MULTILINE)
    out: dict[str, tuple[str | None, str | None]] = {}
    for (limb, _p, _d, _pos, _s, _n, _f), stem in zip(LIMBS, CHILD_LIMB_STEMS):
        path = os.path.join(object_dir, f"{stem}.inc.c")
        if not os.path.isfile(path):
            continue
        names = [n for n in pattern.findall(open(path, encoding="utf8", errors="replace").read())
                 if n != "NULL"]
        out[limb] = (names[0] if names else None, names[1] if len(names) > 1 else None)
    return out

# Measured by tools/calibrate_axes.py against Link's own vertices, zero error.
GAME_SCALE = 1000.0

# The N64 holds 32 vertices at a time, so triangles are emitted in batches that fit.
VERTEX_BUFFER = 32

# 16-bit colour. 32x32 is 2048 bytes, comfortably inside the 4KB of texture memory;
# 64x64 at this depth would be 8192 and will not load as a single tile.
TEXTURE_SIZE = 32
FORMAT_RGBA16 = 2


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def to_signed_byte(value: float) -> int:
    return clamp(int(round(value * 127.0)), -128, 127) & 0xFF


class Texture:
    """One image, resized and packed into the 16-bit format the game wants."""

    def __init__(self, image: bpy.types.Image, name: str,
                 blank: tuple[int, int, int, int] | None = None) -> None:
        self.name = name
        self.width = TEXTURE_SIZE
        self.height = TEXTURE_SIZE
        if blank is not None:
            self._blank(image, blank)
        if tuple(image.size) != (self.width, self.height):
            image.scale(self.width, self.height)
        self.pixels = list(image.pixels)

    @staticmethod
    def _blank(image: bpy.types.Image, box: tuple[int, int, int, int]) -> None:
        """Flood a region of the full-size image with the colour in its top left corner.

        The face has moved to a tile of its own, so the body texture no longer needs it,
        and keeping it there actively hurts: squeezing 128 by 128 down to 32 by 32
        averages the eyes and mouth into the pink around them, and the body triangles
        beside the face then sample that darkened pink and ring the face in shadow.
        Painting the face out first leaves the whole area the one flat colour it should
        be, and the join stops being visible.

        Blender stores pixels bottom row first, which is why the rows are flipped.
        """
        width, height = image.size
        x0, y0, x1, y1 = box
        pixels = list(image.pixels)
        corner = pixels[0:4]                       # bottom left, always background
        for row in range(max(0, height - y1), min(height, height - y0)):
            start = row * width * 4
            for col in range(max(0, x0), min(width, x1)):
                pixels[start + col * 4: start + col * 4 + 4] = corner
        image.pixels = pixels

    def pack(self) -> bytes:
        """RGBA5551, row order flipped: Blender stores bottom-up, the N64 top-down."""
        out = bytearray()
        for row in range(self.height - 1, -1, -1):
            for col in range(self.width):
                i = (row * self.width + col) * 4
                r, g, b, a = self.pixels[i:i + 4]
                # Quantise by taking the top five bits of the eight, which is what
                # every other tool in this pipeline does and what the game assumes when
                # it expands them again. Scaling by 31 instead costs a step: 240 becomes
                # 29 rather than 30, so the body came out one shade darker than the eye
                # tile and drew a visible line around the face where the two met.
                value = (
                    (clamp(int(round(r * 255.0)) >> 3, 0, 31) << 11)
                    | (clamp(int(round(g * 255.0)) >> 3, 0, 31) << 6)
                    | (clamp(int(round(b * 255.0)) >> 3, 0, 31) << 1)
                    | (1 if a > 0.5 else 0)
                )
                out += struct.pack(">H", value)
        return bytes(out)

    def resource(self) -> bytes:
        data = self.pack()
        header = bytearray(64)
        header[4:8] = b"XETO"                      # OTEX, little-endian
        struct.pack_into("<I", header, 8, 1)       # version
        struct.pack_into("<II", header, 12, 0xDEADBEEF, 0xDEADBEEF)
        body = struct.pack("<4I2fI", FORMAT_RGBA16, self.width, self.height, 0,
                           1.0, 1.0, len(data))
        return bytes(header) + body + data


def scale_vertices(xml: str, ratio: float) -> str:
    """Scale a vertex list's positions, leaving texture coordinates and colour alone.

    Only X, Y and Z are touched. S and T are texture coordinates on a fixed-point
    scale of their own, and scaling those would slide the texture across the model.
    """
    import re

    def one(match):
        return f'{match.group(1)}="{int(round(int(match.group(2)) * ratio))}"'

    return re.sub(r'\b([XYZ])="(-?\\d+)"', one, xml)


# Kirby's face does not name a texture. It samples segment 8, which Player_Draw rebinds
# every frame to whichever of the eight eye textures matches what Link is doing, so the
# game blinks him, darts his eyes while he idles and winces when he is hit. The address
# is written as a path with a leading angle bracket, which is how a published mod does
# it. The tile is the game's own 64 by 32, which at 16-bit colour is exactly the 4096
# bytes of texture memory.
FACE_MATERIAL = "kirby_face"
FACE_SEGMENT = ">0x08000000"
FACE_TILE = (64, 32)


def material_xml(texture_name: str, obj: str, width: int, height: int,
                 path: str | None = None, clamp: bool = False) -> str:
    """The tile and combiner setup for one texture.

    The numbers follow from the texture size and were checked against a published mod:
    a load block covers width*height texels less one, its step is 2048 divided by the
    bytes per row in 64-bit words, the render tile's line is the row in those words, the
    masks are the log2 of each side, and the tile size is each side less one in quarter
    texels.
    """
    texels = width * height
    row_words = max(1, (width * 2) // 8)
    dxt = 2048 // row_words
    mask_s = int(math.log2(width))
    mask_t = int(math.log2(height))
    # A face is clamped, not wrapped: the tile holds the whole of it, and wrapping would
    # fold a cheek round onto the far side of his head at the seam.
    wrap = "G_TX_CLAMP" if clamp else "G_TX_WRAP"

    return f"""<DisplayList Version="0">
\t<PipeSync/>
\t<SetTextureLUT Mode="G_TT_NONE"/>
\t<Texture S="65535" T="65535" Level="0" Tile="0" On="1"/>
\t<SetTextureImage Path="{path or f'{obj}/{texture_name}'}" Format="G_IM_FMT_RGBA" Size="G_IM_SIZ_16b_LOAD_BLOCK" Width="1"/>
\t<SetTile Format="G_IM_FMT_RGBA" Size="G_IM_SIZ_16b_LOAD_BLOCK" Line="0" TMem="0" Tile="7" Palette="0" Cms0="G_TX_WRAP" Cms1="G_TX_NOMIRROR" Cmt0="G_TX_WRAP" Cmt1="G_TX_NOMIRROR" MaskS="0" ShiftS="0" MaskT="0" ShiftT="0"/>
\t<LoadSync/>
\t<LoadBlock Tile="7" Uls="0" Ult="0" Lrs="{texels - 1}" Dxt="{dxt}" />
\t<PipeSync/>
\t<SetTile Format="G_IM_FMT_RGBA" Size="G_IM_SIZ_16b" Line="{row_words}" TMem="0" Tile="0" Palette="0" Cms0="{wrap}" Cms1="G_TX_NOMIRROR" Cmt0="{wrap}" Cmt1="G_TX_NOMIRROR" MaskS="{mask_s}" ShiftS="0" MaskT="{mask_t}" ShiftT="0"/>
\t<SetTileSize T="0" Uls="0" Ult="0" Lrs="{(width - 1) * 4}" Lrt="{(height - 1) * 4}"/>
\t<SetCombineLERP A0="G_CCMUX_TEXEL0" B0="G_CCMUX_0" C0="G_CCMUX_SHADE" D0="G_CCMUX_0" Aa0="G_ACMUX_0" Ab0="G_ACMUX_0" Ac0="G_ACMUX_0" Ad0="G_ACMUX_1" A1="G_CCMUX_COMBINED" B1="G_CCMUX_0" C1="G_CCMUX_PRIMITIVE" D1="G_CCMUX_0" Aa1="G_ACMUX_0" Ab1="G_ACMUX_0" Ac1="G_ACMUX_0" Ad1="G_ACMUX_COMBINED"/>
\t<SetRenderMode Mode1="G_RM_FOG_SHADE_A" Mode2="G_RM_AA_ZB_OPA_SURF2" />
\t<ClearGeometryMode G_CULL_FRONT="1" G_TEXTURE_GEN="1" G_TEXTURE_GEN_LINEAR="1"/>
\t<SetGeometryMode G_CULL_BACK="1" G_FOG="1" G_LIGHTING="1" G_SHADE="1" G_SHADING_SMOOTH="1" G_ZBUFFER="1"/>
\t<SetPrimColor M="0" L="0" R="255" G="255" B="255" A="255"/>
\t<EndDisplayList/>
</DisplayList>
"""


SHEATH_BONES = {"adult": "SwordAndSheath", "child": "SwordAndSheath"}


def sheath_vertices(object_dir: str, armature, kirby, limb_bone: str,
                    ratio: float, body_material: str = "") -> dict[str, str]:
    """Move the game's own sheathed sword and shield onto Kirby's back.

    The sheath limb keeps its vanilla display lists, so whatever the player has equipped
    still draws: the Kokiri sword, the Deku shield, the Hylian shield, the Master sword.
    What does not work is where they end up. That geometry is authored around Link's own
    shoulder blade, and the bone it hangs from stays where Link's is however short the
    character replacing him is, so on a ball two thirds of Link's height the sword and
    shield hang in the air above and behind him. It shows most in the opening ride,
    which is where this was spotted.

    A display list names its vertices by resource path, so shipping a moved copy of the
    vertex list under the same name moves the drawing without touching the list itself,
    and every variant can be handled the same way. Each is scaled by how much shorter
    Kirby is and then put back against the ball's own back, measured from his geometry
    in the sheath bone's frame rather than from a guessed offset.
    """
    import glob
    import re

    bone = armature.data.bones.get(limb_bone)
    if bone is None:
        return {}
    to_bone = (armature.matrix_world @ bone.matrix_local).inverted()

    # Kirby's body in the sheath bone's own frame, in the game's units.
    # His body, and only his body. Measuring his whole bounding box instead puts the
    # sheath behind the cap's tail, which reaches a long way past the back of the ball,
    # and it then floats a clear gap behind him.
    wanted = {i for i, m in enumerate(kirby.data.materials)
              if m is not None and m.name == body_material}
    members = {v for poly in kirby.data.polygons if poly.material_index in wanted
               for v in poly.vertices}
    if not members:
        filler = kirby.vertex_groups.get("role_filler")
        skip = set()
        if filler is not None:
            skip = {v.index for v in kirby.data.vertices
                    if any(g.group == filler.index and g.weight > 0.0 for g in v.groups)}
        members = {v.index for v in kirby.data.vertices if v.index not in skip}
    points = [(to_bone @ (kirby.matrix_world @ kirby.data.vertices[i].co)) * GAME_SCALE
              for i in members]
    if not points:
        return {}
    centre = Vector((sum(p.x for p in points) / len(points),
                     sum(p.y for p in points) / len(points),
                     sum(p.z for p in points) / len(points)))

    # Which way is his back, in this bone's frame. World +Y is behind him, measured in
    # rig_to_link from the direction the cap's tail hangs.
    back = (to_bone.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
    depth = max((p - centre).dot(back) for p in points)
    target = centre + back * depth

    pattern = re.compile(r"VTX\(\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),"
                         r"\s*(-?0x[0-9A-Fa-f]+|-?\d+),\s*(-?0x[0-9A-Fa-f]+|-?\d+),"
                         r"\s*(0x[0-9A-Fa-f]+|\d+),\s*(0x[0-9A-Fa-f]+|\d+),"
                         r"\s*(0x[0-9A-Fa-f]+|\d+),\s*(0x[0-9A-Fa-f]+|\d+)\s*\)")

    out: dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(object_dir, "*Sheath*Vtx*.inc.c"))):
        rows = pattern.findall(open(path, encoding="utf8", errors="replace").read())
        if not rows:
            continue
        positions = [Vector((int(r[0]), int(r[1]), int(r[2]))) for r in rows]
        lo = Vector((min(p.x for p in positions), min(p.y for p in positions),
                     min(p.z for p in positions)))
        hi = Vector((max(p.x for p in positions), max(p.y for p in positions),
                     max(p.z for p in positions)))
        middle = (lo + hi) * 0.5

        lines = ['<Vertex Version="0">']
        for position, row in zip(positions, rows):
            moved = (position - middle) * ratio + target
            s_, t_ = (int(row[3], 0), int(row[4], 0))
            lines.append(
                f'\t<Vtx X="{int(round(moved.x))}" Y="{int(round(moved.y))}" '
                f'Z="{int(round(moved.z))}" S="{s_}" T="{t_}" '
                f'R="{int(row[5], 0)}" G="{int(row[6], 0)}" B="{int(row[7], 0)}" '
                f'A="{int(row[8], 0)}"/>')
        lines.append("</Vertex>\n")
        out[os.path.basename(path)[:-len(".inc.c")]] = "\n".join(lines)
    return out


# How far to drop both shoulders, in the game's own units, which are a thousandth of a
# Blender unit. Lowering the shoulders lowers everything hanging off them, and the only
# thing hanging off them that anyone sees is what Link is holding.
#
# Nothing of Kirby's is weighted to the arm chain any more: his mittens ride his body and
# the bare-hand display lists are empty. So the arm bones exist purely as an attachment
# point for carried things, and moving them costs nothing visually. Measured on the child
# skeleton, the hand bone sits at z 2.00 against a ball whose top is 2.19, so a pot was
# being held above his head; 650 brings it to about his middle.
#
# This is pose independent, which the alternative is not. The hand's own joint offset
# runs along the forearm, so changing it moves the hand in whatever direction the arm
# happens to be pointing, which is a different direction in every animation.
SHOULDER_DROP = 650

SHOULDER_LIMBS = {
    "adult": ("gLinkAdultLeftShoulderLimb", "gLinkAdultRightShoulderLimb"),
    "child": ("gLinkChildLeftshoulderLimb", "gLinkChildRightshoulderLimb"),
}


def lowered_shoulders(archive: str, age: str, drop: int) -> dict[str, bytes]:
    """Copies of the shoulder limbs with their joints moved down.

    A limb resource is the game's own 64-byte header, the limb type, its two display list
    paths as length-prefixed strings, and then eight bytes: the joint position as three
    signed shorts, then the child and sibling limb indices. Only the joint moves here.

    The stored axes are not world axes. Measured against the imported skeleton, a stored
    (x, y, z) lands at world (z, -y, x) when the parent is unrotated, so the first short
    is the one that means up.
    """
    import struct
    import zipfile

    out: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as handle:
        inside = {n.split("/")[-1]: n for n in handle.namelist()}
        for name in SHOULDER_LIMBS[age]:
            if name not in inside:
                print(f"  note: {name} is not in the game archive, leaving the arm alone")
                continue
            data = bytearray(handle.read(inside[name]))
            x, y, z = struct.unpack_from("<3h", data, len(data) - 8)
            struct.pack_into("<3h", data, len(data) - 8, x - drop, y, z)
            print(f"  {name}: joint x {x} -> {x - drop}, "
                  f"dropping that arm {drop / GAME_SCALE:.2f} units")
            out[name] = bytes(data)
    return out


# Where Kirby sits on the horse, in the game's own units.
#
# A ball rigged to a humanoid cannot sit well. Every spine bone on Link is above his
# hips, while a ball's centre is at its own middle, so Kirby's body hangs 1.79 below
# whichever bone drives it on the adult skeleton. Standing you never see it, because his
# feet are pinned to the ground and the ball lands where it should. Seated, the saddle
# holds the spine where Link's chest would be and Kirby drops below and behind it, while
# the sword and shield stay with their own bone and end up in front of him.
#
# Raising the ball would fix the saddle and ruin standing, which is the rest of the game.
# The riding animations are the way out: each frame of a player animation begins with the
# root's translation, so shifting that moves the whole character, and only while that
# animation is playing. Nothing else in the game is touched.
#
# Axes, measured rather than assumed. The jump animation's root runs z -22 to -447 while
# rising y 2680 to 2992, so y is up and negative z is forward.
RIDE_LIFT = 1790          # up, to undo the hang measured on the adult rig
RIDE_FORWARD = 400        # onto the saddle; subtracted from z, because -z is forward

# Only the adult rides, so this goes in the adult archive alone. The animation data is
# shared between the two ages, so there is nothing age-specific to decide.
RIDE_PREFIX = "gPlayerAnim_link_uma"


def riding_animations(archive: str, lift: int, forward: int) -> dict[str, bytes]:
    """Copies of the riding animations with the character moved up and forwards.

    A player animation is two resources. The header carries the frame count and the name
    of the data blob; the blob is one flat run of signed shorts, 67 per frame, being the
    root's translation as three, then three rotations for each of the 21 limbs, then one
    short packing the eye and mouth flipbook indices. Only the first three of each frame
    are touched, so every pose is exactly as the game authored it and the whole rider
    simply sits somewhere else.

    The nineteen riding animations were checked to own their data blobs outright: no
    other animation in the game shares one, so nothing else can move.
    """
    import struct
    import zipfile

    out: dict[str, bytes] = {}
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        for header in [n for n in names if RIDE_PREFIX in n and "/link_animetion/" not in n]:
            raw = handle.read(header)
            if struct.unpack_from("<I", raw, 64)[0] != 1:
                continue                      # not a player animation
            frames = struct.unpack_from("<h", raw, 68)[0]
            length = struct.unpack_from("<I", raw, 70)[0]
            segment = raw[74:74 + length].decode("utf8", "replace").replace("__OTR__", "")

            found = [n for n in names if n.endswith(segment)]
            if not found:
                continue
            data = bytearray(handle.read(found[0]))
            count = struct.unpack_from("<I", data, 64)[0]
            if count != frames * 67:
                print(f"  note: {segment.split('/')[-1]} is {count} shorts, not "
                      f"{frames} x 67; left alone")
                continue
            for frame in range(frames):
                at = 68 + frame * 67 * 2
                x, y, z = struct.unpack_from("<3h", data, at)
                struct.pack_into("<3h", data, at,
                                 x, clamp(y + lift, -32768, 32767),
                                 clamp(z - forward, -32768, 32767))
            out[segment] = bytes(data)
    return out


class LimbBuilder:
    """Collects one limb's triangles and turns them into game resources."""

    def __init__(self, limb: str, bone_matrix, texture_sizes: dict[int, tuple[int, int]]) -> None:
        self.limb = limb
        self.to_bone = bone_matrix.inverted()
        self.texture_sizes = texture_sizes
        self.by_material: dict[int, list[tuple]] = {}

    def add(self, material_index: int, corners: list[tuple]) -> None:
        self.by_material.setdefault(material_index, []).append(tuple(corners))

    def _vertex(self, position: Vector, uv, normal: Vector, material_index: int) -> tuple:
        local = self.to_bone @ position
        width, height = self.texture_sizes.get(material_index, (TEXTURE_SIZE, TEXTURE_SIZE))
        s = clamp(int(round(uv[0] * width * 32.0)), -32768, 32767)
        t = clamp(int(round((1.0 - uv[1]) * height * 32.0)), -32768, 32767)
        return (
            clamp(int(round(local.x * GAME_SCALE)), -32768, 32767),
            clamp(int(round(local.y * GAME_SCALE)), -32768, 32767),
            clamp(int(round(local.z * GAME_SCALE)), -32768, 32767),
            s, t,
            to_signed_byte(normal.x), to_signed_byte(normal.y), to_signed_byte(normal.z),
            255,
        )

    def build(self, material_names: dict[int, str]) -> tuple[str, dict[str, str]]:
        """Return the display list XML and the vertex list resources it refers to."""
        body: list[str] = ['<DisplayList Version="0">']
        vertex_files: dict[str, str] = {}
        batch_number = 0

        for material_index, faces in sorted(self.by_material.items()):
            name = material_names.get(material_index)
            if name is None:
                continue
            body.append(f'\t<CallDisplayList Path="{OBJECT}/{name}"/>')

            pending: list[tuple] = []        # unique vertices in this batch
            index_of: dict[tuple, int] = {}
            triangles: list[tuple[int, int, int]] = []

            def flush() -> None:
                nonlocal pending, index_of, triangles, batch_number
                if not triangles:
                    return
                vtx_name = f"{self.limb}KirbyVtx{batch_number}"
                rows = "\n".join(
                    f'\t<Vtx X="{v[0]}" Y="{v[1]}" Z="{v[2]}" S="{v[3]}" T="{v[4]}" '
                    f'R="{v[5]}" G="{v[6]}" B="{v[7]}" A="{v[8]}"/>'
                    for v in pending
                )
                vertex_files[vtx_name] = f'<Vertex Version="0">\n{rows}\n</Vertex>\n'
                body.append(
                    f'\t<LoadVertices Path="{OBJECT}/{vtx_name}" '
                    f'VertexBufferIndex="0" VertexOffset="0" Count="{len(pending)}"/>'
                )
                for i in range(0, len(triangles), 2):
                    a = triangles[i]
                    if i + 1 < len(triangles):
                        b = triangles[i + 1]
                        body.append(
                            f'\t<Triangles2 V00="{a[0]}" V01="{a[1]}" V02="{a[2]}" Flag0="0" '
                            f'V10="{b[0]}" V11="{b[1]}" V12="{b[2]}" Flag1="0"/>'
                        )
                    else:
                        body.append(
                            f'\t<Triangle1 V00="{a[0]}" V01="{a[1]}" V02="{a[2]}" Flag0="0"/>'
                        )
                batch_number += 1
                pending, index_of, triangles = [], {}, []

            for corners in faces:
                built = [self._vertex(p, uv, n, material_index) for p, uv, n in corners]
                fresh = [v for v in built if v not in index_of]
                if len(pending) + len(set(fresh)) > VERTEX_BUFFER:
                    flush()
                indices = []
                for v in built:
                    if v not in index_of:
                        index_of[v] = len(pending)
                        pending.append(v)
                    indices.append(index_of[v])
                triangles.append(tuple(indices))
            flush()

        body.append("\t<EndDisplayList/>")
        body.append("</DisplayList>")
        return "\n".join(body) + "\n", vertex_files


def bone_name_for(armature, limb: str) -> str:
    """The bone this armature calls a given limb; the two ages name them differently."""
    for bone in armature.data.bones:
        if limb_of_bone(bone.name) == limb:
            return bone.name
    return ""


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--age", choices=["adult", "child"], default="adult",
                        help="which player object this build replaces")
    parser.add_argument("--game-archive", default=".toolchain/soh/app/oot.o2r",
                        help="the port's own archive, read for the limbs being adjusted")
    parser.add_argument("--eyes", default="build/eyes",
                        help="where tools/face_textures.py --out put the eye resources")
    parser.add_argument("--child-assets",
                        default="decomp/extracted/ntsc-1.2/assets/objects/object_link_child")
    args = parser.parse_args(argv)

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    armature = next(a for a in armatures if not a.name.endswith("_lod"))
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and o.parent is armature]
    kirby = max(meshes, key=lambda o: len(o.data.vertices))
    mesh = kirby.data
    print(f"model {kirby.name!r}: {len(mesh.vertices)} verts, {len(mesh.materials)} materials")

    # Textures are keyed by their own name, not by material index, because several
    # materials share one image. Keying by index recorded only the first of them and
    # left the rest to fall back on an arbitrary texture, which is how the red of the
    # feet ended up painted across Link's cap.
    # Set by rig_to_link.split_face: which material the face triangles were taken from,
    # and the box in that material's image they used to occupy.
    face_source = mesh.get("face_source_material") or ""
    face_box = tuple(mesh.get("face_box_px") or ()) or None
    if face_source:
        print(f"face came off {face_source!r}; painting it out of that texture at {face_box}")

    textures: dict[str, Texture] = {}
    texture_of_material: dict[int, str] = {}
    material_names: dict[int, str] = {}
    texture_sizes: dict[int, tuple[int, int]] = {}
    for index, material in enumerate(mesh.materials):
        image = None
        if material is not None and material.use_nodes:
            for node in material.node_tree.nodes:
                candidate = getattr(node, "image", None)
                if candidate is not None:
                    image = candidate
                    break
        if material is not None and material.name == FACE_MATERIAL:
            # No texture resource of its own: the pixels come from whichever eye texture
            # the game has bound to segment 8 this frame.
            material_names[index] = "mat_kirby_face"
            texture_sizes[index] = FACE_TILE
            continue
        if image is None:
            continue
        stem = os.path.splitext(os.path.basename(image.name))[0]
        safe = "".join(c if c.isalnum() else "_" for c in stem)[:40]
        texture_name = f"kirby_{safe}"
        if texture_name not in textures:
            textures[texture_name] = Texture(image, texture_name,
                                             blank=face_box if material.name == face_source
                                             else None)
        texture_of_material[index] = texture_name
        material_names[index] = f"mat_kirby_{index}_{safe}"
        texture_sizes[index] = (TEXTURE_SIZE, TEXTURE_SIZE)
    print(f"{len(textures)} distinct texture(s) across "
          f"{len(material_names)} textured material(s)")

    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        print("FAIL: the mesh has no UV map, so there is nothing to texture with")
        sys.exit(1)
    mesh.calc_loop_triangles()

    filler_group = kirby.vertex_groups.get("role_filler")
    filler_vertices: set[int] = set()
    if filler_group is not None:
        filler_vertices = {
            v.index for v in mesh.vertices
            if any(g.group == filler_group.index and g.weight > 0.0 for g in v.groups)
        }
    print(f"{len(filler_vertices)} filler vertices left out of the mod")

    # Bone names differ between the adult and child skeletons, so read them off the
    # armature in hand rather than assuming the adult's.
    bone_name_to_limb = {}
    for bone in armature.data.bones:
        limb = limb_of_bone(bone.name)
        if limb in DEFORM:
            bone_name_to_limb[bone.name] = limb
    print(f"recognised {len(bone_name_to_limb)} drawing limb(s) on {armature.name!r}")

    group_of = {g.index: g.name for g in kirby.vertex_groups}
    limb_of_vertex: dict[int, str] = {}
    for v in mesh.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                limb = bone_name_to_limb.get(group_of.get(g.group, ""))
                if limb:
                    limb_of_vertex[v.index] = limb
                    break

    limb_to_bone = {limb: name for name, limb in bone_name_to_limb.items()}
    builders: dict[str, LimbBuilder] = {}
    for limb in DEFORM:
        bone = armature.data.bones[limb_to_bone[limb]]
        builders[limb] = LimbBuilder(limb, armature.matrix_world @ bone.matrix_local,
                                     texture_sizes)

    world = kirby.matrix_world
    skipped = 0
    for tri in mesh.loop_triangles:
        if any(v in filler_vertices for v in tri.vertices):
            skipped += 1
            continue
        owners = [limb_of_vertex.get(v) for v in tri.vertices]
        present = [o for o in owners if o]
        if not present:
            skipped += 1
            continue
        # A triangle straddling two limbs goes to whichever owns most of its corners.
        # Dropping them instead leaves holes along every seam, which cost 38 triangles
        # out of 706 on the first run.
        limb = max(set(present), key=present.count)
        corners = []
        for loop_index, vertex_index in zip(tri.loops, tri.vertices):
            position = world @ mesh.vertices[vertex_index].co
            uv = uv_layer.data[loop_index].uv
            normal = (world.to_3x3() @ mesh.vertices[vertex_index].normal).normalized()
            corners.append((position, uv, normal))
        builders[limb].add(tri.material_index, corners)

    files: dict[str, str | bytes] = {}
    if args.age == "child":
        obj_path, alt_path = CHILD_OBJECT, CHILD_ALT
        child_lists = child_display_lists(os.path.abspath(args.child_assets))
        near_of = {limb: names[0] for limb, names in child_lists.items()}
        far_of = {limb: names[1] for limb, names in child_lists.items()}
        print(f"child build: display lists found for {len(child_lists)} limb(s)")
    else:
        obj_path, alt_path = OBJECT, ALT
        near_of, far_of = dict(NEAR_DL), dict(FAR_DL)
        print("adult build")
    total_tris = 0
    print("\nper limb:")
    for limb in DEFORM:
        if limb in LEAVE_ALONE:
            print(f"  {limb:<15}    - left vanilla, so the game's own sword still draws")
            continue
        dl_xml, vertex_files = builders[limb].build(material_names)
        count = sum(len(v) for v in builders[limb].by_material.values())
        total_tris += count
        if count == 0:
            # Nothing to draw here, so draw nothing. An empty list still hides
            # the vanilla limb, which is what Kirby wants for shins and shoulders.
            dl_xml = EMPTY_DL
        near, far = near_of.get(limb), far_of.get(limb)
        dl_xml = dl_xml.replace(OBJECT + "/", obj_path + "/")
        if near:
            files[near] = dl_xml
        if far:
            files[far] = dl_xml
        for name, payload in vertex_files.items():
            files[name] = payload.replace(OBJECT + "/", obj_path + "/")
        print(f"  {limb:<15} {count:>4} tris -> {near}")

    # The sheathed sword and shield, moved onto his back.
    object_dir = os.path.abspath(args.child_assets) if args.age == "child" else \
        os.path.join(os.path.dirname(os.path.abspath(args.child_assets)), "object_link_boy")
    link_z = [(armature.matrix_world @ b.head_local).z for b in armature.data.bones]
    kirby_z = [(kirby.matrix_world @ v.co).z for v in mesh.vertices]
    ratio = (max(kirby_z) - min(kirby_z)) / max(max(link_z) - min(link_z), 1e-6)
    sheath = sheath_vertices(object_dir, armature, kirby,
                             bone_name_for(armature, "Sheath"), ratio,
                             body_material=face_source)
    files.update(sheath)
    print(f"{len(sheath)} sheath vertex list(s) scaled to {ratio:.2f} and moved onto his "
          f"back, so the game's own sword and shield ride where they should")

    # Lower both shoulders so a carried pot or bush is held at Kirby's middle rather
    # than over his head.
    if SHOULDER_DROP:
        print("\nlowering the arms:")
        files.update(lowered_shoulders(os.path.abspath(args.game_archive), args.age,
                                       SHOULDER_DROP))

    # Seat him properly on the horse. Only the adult rides.
    #
    # Kept apart from `files` because these names are already full paths from the root of
    # the archive. Everything in `files` is a bare resource name that gets the player
    # object's folder prefixed to it, which would bury an animation somewhere the game
    # never looks for one.
    rooted: dict[str, bytes] = {}
    if args.age == "adult" and (RIDE_LIFT or RIDE_FORWARD):
        rides = riding_animations(os.path.abspath(args.game_archive),
                                  RIDE_LIFT, RIDE_FORWARD)
        rooted.update(rides)
        print(f"\n{len(rides)} riding animation(s) shifted up {RIDE_LIFT / GAME_SCALE:.2f} "
              f"and forward {RIDE_FORWARD / GAME_SCALE:.2f}, so he sits on the saddle")

    hidden = HIDE_HANDS[args.age]
    for name in hidden:
        files[name] = EMPTY_DL
    print(f"\nemptied {len(hidden)} bare-hand list(s), so no vanilla hand draws at the "
          f"hand bones; the lists that hold an item keep their vanilla geometry")

    for texture in textures.values():
        files[texture.name] = texture.resource()
    for index, name in material_names.items():
        if name == "mat_kirby_face":
            files[name] = material_xml("", obj_path, *FACE_TILE,
                                       path=FACE_SEGMENT, clamp=True)
        else:
            files[name] = material_xml(texture_of_material[index], obj_path,
                                       TEXTURE_SIZE, TEXTURE_SIZE)

    if any(n == "mat_kirby_face" for n in material_names.values()):
        # Built by tools/face_textures.py and read back here, rather than built inline,
        # because this script runs inside Blender and Blender ships no imaging library.
        folder = os.path.join(os.path.abspath(args.eyes), args.age)
        if not os.path.isdir(folder):
            print(f"FAIL: no eye textures at {folder}.")
            print("Run: python tools/face_textures.py --out build/eyes")
            sys.exit(1)
        eyes = sorted(os.listdir(folder))
        for name in eyes:
            with open(os.path.join(folder, name), "rb") as handle:
                files[name] = handle.read()
        print(f"{len(eyes)} eye texture(s) packed, so the game's own blink, glance and "
              f"wince swap Kirby's whole face")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            data = payload.encode("utf8") if isinstance(payload, str) else payload
            archive.writestr(f"{alt_path}/{name}", data)
        for name, payload in sorted(rooted.items()):
            archive.writestr(f"alt/{name}", payload)

    size = os.path.getsize(out)
    print(f"\n{total_tris} triangles placed, {skipped} with no limb were dropped")
    print(f"wrote {len(files) + len(rooted)} {args.age} resources to {out} "
          f"({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
