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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from link_skeleton import DEFORM, FAR_DL, NEAR_DL, limb_of_bone  # noqa: E402
from carry_fk import all_animations, hand_offset, shield_hang  # noqa: E402
from mitten_hands import hand_textures, mitten_vertex_lists  # noqa: E402

# How far a sword-swinging hand travels, as a fraction of Link's own shoulder-to-hand
# vector, around its mitten. One is Link's arc; Kirby's arms are stubs.
SWING_REACH = 0.2

# Limbs this mod does not touch. The sheath keeps its vanilla display list so the
# sword and scabbard the game gives Link still appear; Kirby carries none of his own.
LEAVE_ALONE = ("Sheath",)

# How a limb this character does not have is hidden.
#
# An empty list, nothing but EndDisplayList, is the obvious answer and it is what this
# shipped with for a long time. It is also what the published 3DS Link mod pointedly does
# NOT do: its smallest list is 349 bytes, for a shoulder, and rather than being empty it
# draws a single degenerate triangle. Somebody solved this before me and chose a stub.
#
# That matters because a list with no commands at all is exactly the kind of thing one
# renderer backend tolerates and another runs off the end of, and the same archives that
# play fine on the DirectX build crash the Metal one inside the skeleton walk, at the
# frame where it loads a display list by name. So the stub is the safer shape, it costs
# three vertices that cover no area and draw nothing, and it matches what is known to work.
EMPTY_DL = '<DisplayList Version="0">\n\t<EndDisplayList/>\n</DisplayList>\n'


def animation_headers(archive_path: str) -> dict[str, list[tuple[str, bytes]]]:
    """For each animation data path, the header resources rewritten to point at the copy
    this mod ships under misc/link_animetion/kirby/, keyed by the data's original path."""
    out: dict[str, list[tuple[str, bytes]]] = {}
    with zipfile.ZipFile(archive_path) as game:
        for path in game.namelist():
            if not path.startswith("objects/gameplay_keep/gPlayerAnim_"):
                continue
            raw = game.read(path)
            if struct.unpack_from("<I", raw, 64)[0] != 1:
                continue
            length = struct.unpack_from("<I", raw, 70)[0]
            segment = raw[74:74 + length].decode("utf8", "replace")
            data_path = segment.replace("__OTR__", "")
            renamed = "__OTR__misc/link_animetion/kirby/" + data_path.split("/")[-1]
            header = bytearray(raw[:70]) + struct.pack("<I", len(renamed)) + renamed.encode() + raw[74 + length:]
            out.setdefault(data_path, []).append((path, bytes(header)))
    return out


def stub_display_list(name: str, obj: str, material: str) -> tuple[str, str, str]:
    """A list that draws one triangle of zero area, which is to say nothing at all.

    Returns the display list, the name of the vertex list it needs, and that list. All
    three vertices sit on the limb's own origin, so the triangle has no area and the
    rasteriser discards it, but the list itself is a real one with real commands in it.
    """
    vtx_name = f"{name}KirbyStubVtx"
    vertices = "\n".join(
        '\t<Vtx X="0" Y="0" Z="0" S="0" T="0" R="0" G="0" B="127" A="255"/>'
        for _ in range(3))
    vtx_xml = f'<Vertex Version="0">\n{vertices}\n</Vertex>\n'
    dl_xml = (
        '<DisplayList Version="0">\n'
        f'\t<CallDisplayList Path="{obj}/{material}"/>\n'
        f'\t<LoadVertices Path="{obj}/{vtx_name}" VertexBufferIndex="0" '
        'VertexOffset="0" Count="3"/>\n'
        '\t<Triangle1 V00="0" V01="1" V02="2" Flag0="0"/>\n'
        '\t<EndDisplayList/>\n'
        '</DisplayList>\n')
    return dl_xml, vtx_name, vtx_xml

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


# Textures that should draw at their own colour rather than being multiplied by the
# lighting. Empty on purpose: the shoes were in here for a while, on the theory that a
# dark underside read as a hole, and it turned out the hole was back-face culling and
# the unlit shoe was just too bright. Everything lights the same way now.
FLAT_TEXTURES: tuple[str, ...] = ()


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

    # The colour combiner's first cycle computes (A - B) * C + D. The usual form here is
    # TEXEL0 * SHADE, which is the texture lit by the vertex normal. For a flat material
    # everything but D is zero, so the texture comes through at its own brightness.
    if texture_name in FLAT_TEXTURES:
        colour = ('A0="G_CCMUX_0" B0="G_CCMUX_0" C0="G_CCMUX_0" D0="G_CCMUX_TEXEL0"')
    else:
        colour = ('A0="G_CCMUX_TEXEL0" B0="G_CCMUX_0" C0="G_CCMUX_SHADE" D0="G_CCMUX_0"')

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
\t<SetCombineLERP {colour} Aa0="G_ACMUX_0" Ab0="G_ACMUX_0" Ac0="G_ACMUX_0" Ad0="G_ACMUX_1" A1="G_CCMUX_COMBINED" B1="G_CCMUX_0" C1="G_CCMUX_PRIMITIVE" D1="G_CCMUX_0" Aa1="G_ACMUX_0" Ab1="G_ACMUX_0" Ac1="G_ACMUX_0" Ad1="G_ACMUX_COMBINED"/>
\t<SetRenderMode Mode1="G_RM_FOG_SHADE_A" Mode2="G_RM_AA_ZB_OPA_SURF2" />
\t<ClearGeometryMode G_CULL_FRONT="1" G_CULL_BACK="1" G_TEXTURE_GEN="1" G_TEXTURE_GEN_LINEAR="1"/>
\t<SetGeometryMode G_FOG="1" G_LIGHTING="1" G_SHADE="1" G_SHADING_SMOOTH="1" G_ZBUFFER="1"/>
\t<SetPrimColor M="0" L="0" R="255" G="255" B="255" A="255"/>
\t<EndDisplayList/>
</DisplayList>
"""


SHEATH_BONES = {"adult": "SwordAndSheath", "child": "SwordAndSheath"}


def archive_vertex_names(object_dir: str) -> dict[str, str]:
    """The name the game archive gives each vertex array, keyed by its decomp name.

    The decomp names a vertex array after what it draws, `gLinkChildDekuShieldSwordAnd
    SheathNearVtx`, but Ship of Harkinian's own extraction did not carry those names
    across: in `oot.o2r` the array is `object_link_childVtx_00CAA0`, the object's name and
    the array's offset in it, and the binary display lists refer to it by a hash of that
    path. An override shipped under the decomp name is therefore never read, however
    right it is; the sheath lists were shipped that way for a day and the game drew the
    vanilla sheath throughout. The offsets are in the decomp's extraction XML, so a list
    is shipped under both names and the one the game asks for is there.
    """
    import re

    obj = os.path.basename(os.path.normpath(object_dir))
    xml = os.path.normpath(os.path.join(object_dir, *([".."] * 5), "assets", "xml", "objects",
                                        obj + ".xml"))
    if not os.path.exists(xml):
        print(f"  no extraction XML at {xml}; vertex lists ship under their decomp names only")
        return {}
    names: dict[str, str] = {}
    for m in re.finditer(r'<Array\s+Name="(\w+)"[^>]*Offset="0x([0-9A-Fa-f]+)"',
                         open(xml, encoding="utf8").read()):
        names[m.group(1)] = f"{obj}Vtx_{int(m.group(2), 16):06X}"
    return names


def sheath_vertices(object_dir: str, kirby, torso_to_bone, ratio: float,
                    game_archive: str, age: str, body_material: str = "") -> dict[str, str]:
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
    Kirby is and then put back against the ball's own back.

    Where the ball is, seen from the sheath limb, is measured in the game's standing idle
    through the same limb walk the game uses (carry_fk.idle_pose), never in Blender's rest
    pose. The rest pose has every limb at zero rotation; the sheath limb idles at
    (-90, 150, -4) degrees, so the two disagree by a quarter turn and a body height, and
    a set placed against the rest-pose ball comes out in the game riding on the hat with
    the shield facing forward. In the idle Link's own set already lies flat on his back
    with the scabbard down his spine, so it is not turned at all: only scaled, pushed
    straight back until it clears the ball's back-most point, and centred on the ball.
    """
    import glob
    import re

    from carry_fk import apply, idle_pose, invert_rigid

    # Kirby's body in the torso limb's own frame, in the game's units, which is how the
    # game draws it. His body, and only his body: the cap's tail reaches a long way past
    # the back of the ball, and a bounding box that includes it floats the set a clear
    # gap behind him.
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
    if not members:
        return {}
    sheath_m, torso_m = idle_pose(game_archive, age)
    to_sheath = invert_rigid(sheath_m)
    body = []
    for i in members:
        local = torso_to_bone @ (kirby.matrix_world @ kirby.data.vertices[i].co)
        body.append(apply(torso_m, (local.x * GAME_SCALE, local.y * GAME_SCALE,
                                    local.z * GAME_SCALE)))
    centre_x = sum(p[0] for p in body) / len(body)
    centre_y = sum(p[1] for p in body) / len(body)
    back_z = min(p[2] for p in body)                   # his back is -z in model space
    clearance = 0.08 * GAME_SCALE
    print(f"  ball in the idle: centre ({centre_x / GAME_SCALE:.2f}, {centre_y / GAME_SCALE:.2f}), "
          f"back at z {back_z / GAME_SCALE:.2f}")

    pattern = re.compile(r"VTX\(\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),"
                         r"\s*(-?0x[0-9A-Fa-f]+|-?\d+),\s*(-?0x[0-9A-Fa-f]+|-?\d+),"
                         r"\s*(0x[0-9A-Fa-f]+|\d+),\s*(0x[0-9A-Fa-f]+|\d+),"
                         r"\s*(0x[0-9A-Fa-f]+|\d+),\s*(0x[0-9A-Fa-f]+|\d+)\s*\)")

    # Some of these come in pairs. A display list that needs more than the vertex buffer
    # holds splits its vertices across `...Vtx1` and `...Vtx2`, and the two halves are one
    # object: the shield's face and its rim, say. Centring each file on its own bounding
    # box moves the halves by different amounts and pulls the object apart, so the pair is
    # measured together and moved together.
    def family(path: str) -> str:
        return re.sub(r"\d+$", "", os.path.basename(path)[:-len(".inc.c")])

    groups: dict[str, list[str]] = {}
    for path in sorted(glob.glob(os.path.join(object_dir, "*Sheath*Vtx*.inc.c"))):
        groups.setdefault(family(path), []).append(path)

    out: dict[str, str] = {}
    for name, paths in sorted(groups.items()):
        parsed = [(path, pattern.findall(open(path, encoding="utf8", errors="replace").read()))
                  for path in paths]
        parsed = [(path, rows) for path, rows in parsed if rows]
        if not parsed:
            continue
        if len(parsed) > 1:
            print(f"  {name}: {len(parsed)} lists measured together so they stay one piece")

        # Scale the set about its own middle, in the limb's frame, then see where that
        # puts it in the idle. The whole set, sword hilt included, is centred on the
        # ball's middle, and pushed back until its nearest point clears the ball's
        # back-most point by the clearance; it keeps Link's own orientation.
        everything = [(int(r[0]), int(r[1]), int(r[2])) for _path, rows in parsed for r in rows]
        lo = [min(p[i] for p in everything) for i in range(3)]
        hi = [max(p[i] for p in everything) for i in range(3)]
        middle = [(lo[i] + hi[i]) * 0.5 for i in range(3)]

        def scaled(p):
            return tuple(middle[i] + (p[i] - middle[i]) * ratio for i in range(3))

        placed = [apply(sheath_m, scaled(p)) for p in everything]
        set_x = (min(p[0] for p in placed) + max(p[0] for p in placed)) * 0.5
        set_y = (min(p[1] for p in placed) + max(p[1] for p in placed)) * 0.5
        shift = (centre_x - set_x, centre_y - set_y,
                 (back_z - clearance) - max(p[2] for p in placed))
        print(f"  {name}: moved ({shift[0] / GAME_SCALE:+.2f}, {shift[1] / GAME_SCALE:+.2f}, "
              f"{shift[2] / GAME_SCALE:+.2f}) in the idle, clear of the ball's back")

        for path, rows in parsed:
            lines = ['<Vertex Version="0">']
            for row in rows:
                world = apply(sheath_m, scaled((int(row[0]), int(row[1]), int(row[2]))))
                moved = apply(to_sheath, tuple(world[i] + shift[i] for i in range(3)))
                s_, t_ = (int(row[3], 0), int(row[4], 0))
                lines.append(
                    f'\t<Vtx X="{int(round(moved[0]))}" Y="{int(round(moved[1]))}" '
                    f'Z="{int(round(moved[2]))}" S="{s_}" T="{t_}" '
                    f'R="{int(row[5], 0)}" G="{int(row[6], 0)}" B="{int(row[7], 0)}" '
                    f'A="{int(row[8], 0)}"/>')
            lines.append("</Vertex>\n")
            out[os.path.basename(path)[:-len(".inc.c")]] = "\n".join(lines)
    archive_names = archive_vertex_names(object_dir)
    for decomp_name, xml in list(out.items()):
        archive_name = archive_names.get(decomp_name)
        if archive_name:
            out[archive_name] = xml
        else:
            print(f"  {decomp_name}: no archive name found, the game will not read it")
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
# Measured off a screenshot of him actually carrying a pot, rather than from the rest
# pose. The carry animation raises the arms well above where they hang, so the 0.65 the
# rest pose suggested left the pot a full body height above his cap. Scaling from that
# picture, his body is about 2.05 units tall and the pot sat roughly 3.6 units above
# where it should, so the drop has to be several times what the standing measurement
# implied. Negative joint values are fine here: the arms attach below the upper body
# control and nothing of Kirby's hangs off them.
# Bracketed in the game. At 0.65 the pot floated a full body height above his cap; at
# 3.65 it had come down far enough to overlap his body. The right look is resting just
# above the cap, so the answer is between them.
SHOULDER_DROP = 2200

# And how far to push them out in front of him.
#
# Dropping alone is not enough. Link holds a carried thing in front of a narrow chest, so
# the same spot on a character who is a wide sphere is inside his body, which is where the
# pot ended up once the height was right. The arms have to come forward as well as down.
#
# Sign, from the same measurement as the drop: a stored (x, y, z) lands at world
# (z, -y, x) with the parent unrotated, and his face is toward negative world Y, so a
# larger stored y moves the arms toward his face.
# Zero, and it stays zero. Link carries a pot or a handful of grass *above his head*,
# not in front of his chest, so there was never anything to clear and pushing the arms
# out only moved it to his side. The one dial that matters is the drop.
SHOULDER_FORWARD = 0

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
# Doubled after looking at it in the game. The measured hang got him most of the way
# there and no further, which says the seated pose does not hold his body the same
# distance below the spine that standing does, so the measurement was a good starting
# point rather than the answer.
# Where he sits on Epona, measured against the title screen's attract loop (tools/reseat.py
# rewrites these into a packed archive in a second, so the loop is: reseat, restart, look).
# Standing and waiting, he sits on the saddle at this offset from Link's own root; the
# moving animations need much more, because Link leans forward into them and a ball cannot.
RIDE_LIFT = 1600
RIDE_FORWARD = -800             # negative is toward the horse's head
RIDE_MOVING_LIFT = 1300         # extra, on walk, run and jump
RIDE_MOVING_FORWARD = -4700     # extra, same
                          #
                          # The sign is the opposite of what the jump animation implied.
                          # Link's run-jump drives its root strongly negative in z while
                          # leaping ahead, which says negative is forward, and that is
                          # how this was built. On the horse it is the other way: raising
                          # this value walked him backwards off the rump, proved at 8.0
                          # where he ends up behind the horse entirely. The riding
                          # animations are authored in a frame of their own, so the
                          # standing convention does not carry over to them.


class LimbBuilder:
    """Collects one limb's triangles and turns them into game resources."""

    def __init__(self, limb: str, bone_matrix, texture_sizes: dict[int, tuple[int, int]]) -> None:
        self.limb = limb
        self.to_bone = bone_matrix.inverted()
        self.texture_sizes = texture_sizes
        self.by_material: dict[int, list[tuple]] = {}
        # Game units added along the limb's own x after the bone transform. The hand
        # limbs hang their mitten this far out along the arm; see carry_fk.hand_offset.
        self.shift_x = 0.0

    def add(self, material_index: int, corners: list[tuple]) -> None:
        self.by_material.setdefault(material_index, []).append(tuple(corners))

    def _vertex(self, position: Vector, uv, normal: Vector, material_index: int) -> tuple:
        local = self.to_bone @ position
        width, height = self.texture_sizes.get(material_index, (TEXTURE_SIZE, TEXTURE_SIZE))
        s = clamp(int(round(uv[0] * width * 32.0)), -32768, 32767)
        t = clamp(int(round((1.0 - uv[1]) * height * 32.0)), -32768, 32767)
        return (
            clamp(int(round(local.x * GAME_SCALE + self.shift_x)), -32768, 32767),
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
    parser.add_argument("--no-face", action="store_true",
                        help="leave the face on the body texture, for bisecting a crash")
    parser.add_argument("--hide-hands", action="store_true",
                        help="draw nothing for the closed-hand lists the game swaps in while "
                             "running and guarding; by default they draw Link's fist in "
                             "Kirby's skin, like every hand that holds something")
    parser.add_argument("--no-sheath", action="store_true",
                        help="leave the sheathed sword and shield where the game put "
                             "them, for bisecting a crash")
    parser.add_argument("--fist-hands", action="store_true",
                        help="keep Link's own fist on held items instead of a fist-sized "
                             "pink ball")
    parser.add_argument("--stub-empty", action="store_true",
                        help="draw a degenerate triangle for limbs with no geometry "
                             "instead of shipping an empty display list")
    parser.add_argument("--no-ride", action="store_true",
                        help="leave the riding animations alone, for bisecting a crash")
    parser.add_argument("--no-shoulders", action="store_true",
                        help="leave the carry animations alone, so a carried pot sits "
                             "where the game puts it")
    parser.add_argument("--no-mittens", action="store_true",
                        help="keep Link's own fist on the sword and the other held items")
    parser.add_argument("--no-swing", action="store_true",
                        help="leave the sword animations alone, so the swing is at "
                             "Link's reach")
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
        if material is not None and material.name == FACE_MATERIAL and not args.no_face:
            # Bisection: fall through and treat it as an ordinary textured material.
            pass
        if material is not None and material.name == FACE_MATERIAL and not args.no_face:
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
    # Any real material will do for a triangle that covers no pixels; take the body's.
    stub_material = next((n for i, n in sorted(material_names.items())
                          if n != "mat_kirby_face"), None)

    # The mittens hang out along the arm, where the animations aim them; see carry_fk.
    hand_shift = hand_offset(os.path.abspath(args.game_archive), args.age)
    for limb in ("LeftHand", "RightHand"):
        if limb in builders:
            builders[limb].shift_x = -hand_shift      # before the joint, like every hand list
    print(f"mittens hung {hand_shift / GAME_SCALE:.2f} before the joint along the hand's x")

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
            # Nothing to draw here. Either shape hides the vanilla limb; see EMPTY_DL
            # for why the stub is the safer one.
            if args.stub_empty and stub_material:
                dl_xml, vtx_name, vtx_xml = stub_display_list(limb, obj_path, stub_material)
                vertex_files = {vtx_name: vtx_xml}
            else:
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
    sheath = {} if args.no_sheath else sheath_vertices(
        object_dir, kirby, builders["Torso"].to_bone, ratio,
        os.path.abspath(args.game_archive), args.age, body_material=face_source)
    files.update(sheath)
    print(f"{len(sheath)} sheath vertex list(s) scaled to {ratio:.2f} and moved onto his "
          f"back, so the game's own sword and shield ride where they should")

    # A mitten in place of Link's fist on every hand list that holds something: the
    # hand's vertices balled, and the hand textures replaced by Kirby's skin.
    hand_verts = [v for v, owner in limb_of_vertex.items() if owner == "LeftHand"]
    mitten_radius = max((builders["LeftHand"].to_bone @ (world @ mesh.vertices[v].co)).length
                        for v in hand_verts) * GAME_SCALE if hand_verts else 450.0
    hidden = HIDE_HANDS[args.age] if args.hide_hands else ()
    if not args.no_mittens:
        print("\nmittens on the hands that hold things:")
        print(f"  mitten radius {mitten_radius / GAME_SCALE:.2f}, from the rig")
        mittens = mitten_vertex_lists(object_dir, args.age, hidden,
                                      radius=mitten_radius, hang=hand_shift,
                                      ball=not args.fist_hands,
                                      shield_dir=shield_hang(os.path.abspath(args.game_archive), args.age))
        # Under the archive's own names as well, or the game never reads them; see
        # archive_vertex_names. Every hand list then hangs its ball the way the empty
        # hand does, so no fist is ever drawn at a joint that sits a hang short.
        archive_names = archive_vertex_names(object_dir)
        for decomp_name, xml in list(mittens.items()):
            if decomp_name in archive_names:
                mittens[archive_names[decomp_name]] = xml
            else:
                print(f"  {decomp_name}: no archive name found, the game will not read it")
        files.update(mittens)
        # The hand textures are palette-indexed, so the skin goes in as a palette fill
        # and the hand-only palettes turn pink; see mitten_hands.hand_textures.
        files.update(hand_textures(object_dir, args.age, os.path.abspath(args.game_archive)))

    # Animations. Kept apart from `files` because these names are already full paths from the root of
    # the archive. Everything in `files` is a bare resource name that gets the player
    # object's folder prefixed to it, which would bury an animation somewhere the game
    # never looks for one.
    rooted: dict[str, bytes] = {}

    # Every animation gets the arms posed: the stubby reach around the mittens, the
    # carry set holding the pot at his middle, the ride set shifted onto the saddle.
    # See carry_fk.py for why this is done in animation data and never in a limb.
    print("\nposing the arms in every animation:")
    rooted.update(all_animations(
        os.path.abspath(args.game_archive), args.age,
        drop=SHOULDER_DROP, forward=SHOULDER_FORWARD, reach=SWING_REACH, fist=mitten_radius,
        ride_lift=RIDE_LIFT, ride_forward=RIDE_FORWARD,
        ride_moving_lift=RIDE_MOVING_LIFT, ride_moving_forward=RIDE_MOVING_FORWARD,
        carry=not args.no_shoulders, swing=not args.no_swing,
        ride=(args.age == "adult" and not args.no_ride),
        cache_dir=os.path.abspath("build")))

    # The game swaps a hand limb's list for a closed or raised variant as he runs,
    # climbs and, through the pause screen's model, whenever a sword is equipped. Those
    # lists must not draw the open mitten: with the sword out, the holding list already
    # draws a fist on the grip, and an open mitten on top of it is a pink disc the size
    # of the hand over the hilt. So each variant draws nothing at all, the stub, and
    # the running hand briefly shows no mitten rather than a wrong one.
    for name in hidden:
        if args.stub_empty and stub_material:
            dl_xml, vtx_name, vtx_xml = stub_display_list(name, obj_path, stub_material)
            files[name] = dl_xml
            files[vtx_name] = vtx_xml
        else:
            files[name] = EMPTY_DL
    print(f"{chr(10)}{len(hidden)} closed-hand list(s) draw nothing, so no mitten sits over a held grip")

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
        # An animation's data is never overridden in place. Ship of Harkinian 9.3.0 draws
        # a player whose animation data comes from an alt archive wrong for a frame or two
        # at every animation change, whatever the bytes (verified with blobs identical to
        # the game's own). Data under a name of its own, with only the small header
        # overridden to point at it, draws cleanly. See carry_fk.py.
        headers = animation_headers(os.path.abspath(args.game_archive))
        for name, payload in sorted(rooted.items()):
            if not name.startswith("misc/link_animetion/"):
                archive.writestr(f"alt/{name}", payload)
                continue
            base = name.split("/")[-1]
            archive.writestr(f"misc/link_animetion/kirby/{base}", payload)
            for header_path, header in headers.get(name, ()):
                archive.writestr(f"alt/{header_path}", header)

    size = os.path.getsize(out)
    print(f"\n{total_tris} triangles placed, {skipped} with no limb were dropped")
    print(f"wrote {len(files) + len(rooted)} {args.age} resources to {out} "
          f"({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
