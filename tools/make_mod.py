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
from link_skeleton import DEFORM, FAST64_BONE, FAR_DL, NEAR_DL  # noqa: E402

OBJECT = "objects/object_link_boy"
ALT = "alt/" + OBJECT

# Ocarina of Time has two player models and the game opens on the younger one, so a mod
# that replaces only the adult shows nothing for hours of play. Child Link is a separate
# object with the same 21 limbs in the same order, so the same geometry serves both: the
# vertices are in bone-local space, and Kirby is the same size whichever Link he stands
# in for. Only the display list names differ, and they are read from the extracted
# object rather than guessed, because one of them is "Leftshoulder" with a small s.
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

    def __init__(self, image: bpy.types.Image, name: str) -> None:
        self.name = name
        self.width = TEXTURE_SIZE
        self.height = TEXTURE_SIZE
        if tuple(image.size) != (self.width, self.height):
            image.scale(self.width, self.height)
        self.pixels = list(image.pixels)

    def pack(self) -> bytes:
        """RGBA5551, row order flipped: Blender stores bottom-up, the N64 top-down."""
        out = bytearray()
        for row in range(self.height - 1, -1, -1):
            for col in range(self.width):
                i = (row * self.width + col) * 4
                r, g, b, a = self.pixels[i:i + 4]
                value = (
                    (clamp(int(r * 31), 0, 31) << 11)
                    | (clamp(int(g * 31), 0, 31) << 6)
                    | (clamp(int(b * 31), 0, 31) << 1)
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


def material_xml(texture_name: str, obj: str, width: int, height: int) -> str:
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

    return f"""<DisplayList Version="0">
\t<PipeSync/>
\t<SetTextureLUT Mode="G_TT_NONE"/>
\t<Texture S="65535" T="65535" Level="0" Tile="0" On="1"/>
\t<SetTextureImage Path="{obj}/{texture_name}" Format="G_IM_FMT_RGBA" Size="G_IM_SIZ_16b_LOAD_BLOCK" Width="1"/>
\t<SetTile Format="G_IM_FMT_RGBA" Size="G_IM_SIZ_16b_LOAD_BLOCK" Line="0" TMem="0" Tile="7" Palette="0" Cms0="G_TX_WRAP" Cms1="G_TX_NOMIRROR" Cmt0="G_TX_WRAP" Cmt1="G_TX_NOMIRROR" MaskS="0" ShiftS="0" MaskT="0" ShiftT="0"/>
\t<LoadSync/>
\t<LoadBlock Tile="7" Uls="0" Ult="0" Lrs="{texels - 1}" Dxt="{dxt}" />
\t<PipeSync/>
\t<SetTile Format="G_IM_FMT_RGBA" Size="G_IM_SIZ_16b" Line="{row_words}" TMem="0" Tile="0" Palette="0" Cms0="G_TX_WRAP" Cms1="G_TX_NOMIRROR" Cmt0="G_TX_WRAP" Cmt1="G_TX_NOMIRROR" MaskS="{mask_s}" ShiftS="0" MaskT="{mask_t}" ShiftT="0"/>
\t<SetTileSize T="0" Uls="0" Ult="0" Lrs="{(width - 1) * 4}" Lrt="{(height - 1) * 4}"/>
\t<SetCombineLERP A0="G_CCMUX_TEXEL0" B0="G_CCMUX_0" C0="G_CCMUX_SHADE" D0="G_CCMUX_0" Aa0="G_ACMUX_0" Ab0="G_ACMUX_0" Ac0="G_ACMUX_0" Ad0="G_ACMUX_1" A1="G_CCMUX_COMBINED" B1="G_CCMUX_0" C1="G_CCMUX_PRIMITIVE" D1="G_CCMUX_0" Aa1="G_ACMUX_0" Ab1="G_ACMUX_0" Ac1="G_ACMUX_0" Ad1="G_ACMUX_COMBINED"/>
\t<SetRenderMode Mode1="G_RM_FOG_SHADE_A" Mode2="G_RM_AA_ZB_OPA_SURF2" />
\t<ClearGeometryMode G_CULL_FRONT="1" G_TEXTURE_GEN="1" G_TEXTURE_GEN_LINEAR="1"/>
\t<SetGeometryMode G_CULL_BACK="1" G_FOG="1" G_LIGHTING="1" G_SHADE="1" G_SHADING_SMOOTH="1" G_ZBUFFER="1"/>
\t<SetPrimColor M="0" L="0" R="255" G="255" B="255" A="255"/>
\t<EndDisplayList/>
</DisplayList>
"""


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


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--out", required=True)
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

    # One texture per material that has one; the rest fall back to a flat colour image.
    textures: dict[int, Texture] = {}
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
        if image is None:
            continue
        stem = os.path.splitext(os.path.basename(image.name))[0]
        safe = "".join(c if c.isalnum() else "_" for c in stem)[:40]
        texture_name = f"kirby_{safe}"
        if texture_name not in {t.name for t in textures.values()}:
            textures[index] = Texture(image, texture_name)
        material_names[index] = f"mat_kirby_{index}_{safe}"
        texture_sizes[index] = (TEXTURE_SIZE, TEXTURE_SIZE)

    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        print("FAIL: the mesh has no UV map, so there is nothing to texture with")
        sys.exit(1)
    mesh.calc_loop_triangles()

    group_of = {g.index: g.name for g in kirby.vertex_groups}
    limb_of_vertex: dict[int, str] = {}
    bone_name_to_limb = {FAST64_BONE[n]: n for n in DEFORM}
    for v in mesh.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                limb = bone_name_to_limb.get(group_of.get(g.group, ""))
                if limb:
                    limb_of_vertex[v.index] = limb
                    break

    builders: dict[str, LimbBuilder] = {}
    for limb in DEFORM:
        bone = armature.data.bones[FAST64_BONE[limb]]
        builders[limb] = LimbBuilder(limb, armature.matrix_world @ bone.matrix_local,
                                     texture_sizes)

    world = kirby.matrix_world
    skipped = 0
    for tri in mesh.loop_triangles:
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
    child_files: dict[str, str | bytes] = {}
    child_lists = child_display_lists(os.path.abspath(args.child_assets))
    print(f"child display lists found for {len(child_lists)} limb(s)")
    total_tris = 0
    print("\nper limb:")
    for limb in DEFORM:
        dl_xml, vertex_files = builders[limb].build(material_names)
        count = sum(len(v) for v in builders[limb].by_material.values())
        total_tris += count
        near, far = NEAR_DL[limb], FAR_DL[limb]
        files[near] = dl_xml
        if far:
            files[far] = dl_xml
        files.update(vertex_files)

        child_near, child_far = child_lists.get(limb, (None, None))
        child_xml = dl_xml.replace(OBJECT + "/", CHILD_OBJECT + "/")
        for name in (child_near, child_far):
            if name:
                child_files[name] = child_xml
        for name, payload in vertex_files.items():
            child_files[name] = payload.replace(OBJECT + "/", CHILD_OBJECT + "/")

        print(f"  {limb:<15} {count:>4} tris -> {near}"
              + (f" / {child_near}" if child_near else "  (no child list)"))

    for index, texture in textures.items():
        files[texture.name] = texture.resource()
        child_files[texture.name] = texture.resource()
    for index, name in material_names.items():
        texture_name = (textures[index].name if index in textures
                        else next(iter(textures.values())).name)
        files[name] = material_xml(texture_name, OBJECT, TEXTURE_SIZE, TEXTURE_SIZE)
        child_files[name] = material_xml(texture_name, CHILD_OBJECT, TEXTURE_SIZE, TEXTURE_SIZE)

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            data = payload.encode("utf8") if isinstance(payload, str) else payload
            archive.writestr(f"{ALT}/{name}", data)
        for name, payload in sorted(child_files.items()):
            data = payload.encode("utf8") if isinstance(payload, str) else payload
            archive.writestr(f"{CHILD_ALT}/{name}", data)

    size = os.path.getsize(out)
    print(f"\n{total_tris} triangles placed, {skipped} with no limb were dropped")
    print(f"wrote {len(files)} adult and {len(child_files)} child resources "
          f"to {out} ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
