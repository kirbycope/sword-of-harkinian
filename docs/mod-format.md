# What a Ship of Harkinian model mod actually contains

Written after taking apart a published one, because guessing at this cost real time.

The reference is "Playas 3DS Link", a Link model replacement from GameBanana, mod 475743.
Its own readme says the whole install is "drop the OTR into the Mods folder", with no ROM
involved, which is the point: **a model mod does not need a rebuilt ROM.**

## The archive

Mods ship as `.otr` or `.o2r`. They are different containers:

| Extension | Container |
| --- | --- |
| `.otr` | an MPQ archive, magic `MPQ\x1a` |
| `.o2r` | an ordinary ZIP |

`.o2r` is the newer one and is far easier to write, being a plain ZIP.

Paths inside sit under `alt/`, which is Ship of Harkinian's alternate assets tree, then
mirror the game's own resource names:

```
alt/objects/object_link_child/gLinkChildLeftFistNearDL
alt/objects/object_link_child/gLinkChildLeftFistNearVtx
alt/objects/object_link_child/mat_gLinkChildLeftFistNearDL_f3d_yl_hands_layerOpaque
alt/objects/object_link_child/gLinkChildEyesClosedfTex
```

## The resources

Two formats, and only one of them is binary.

**Display lists, materials and vertices are XML.** This is the part worth knowing, and it
makes the whole job tractable. A display list is a list of F3D commands:

```xml
<DisplayList Version="0">
    <CallDisplayList Path="objects/object_link_child/mat_gLinkChildLeftFistNearDL_f3d_yl_hands_layerOpaque"/>
    <LoadVertices Path="objects/object_link_child/gLinkChildLeftFistNearVtx" VertexBufferIndex="0" VertexOffset="0" Count="32"/>
    <Triangles2 V00="0" V01="1" V02="2" Flag0="0" V10="0" V11="3" V12="1" Flag1="0"/>
</DisplayList>
```

A vertex list is just as plain, with position, texture coordinates and colour as integers:

```xml
<Vertex Version="0">
    <Vtx X="115" Y="333" Z="-129" S="598" T="813" R="48" G="30" B="142" A="255"/>
</Vertex>
```

A material is a display list too, carrying the texture and combiner setup: `SetTextureImage`,
`SetTile`, `LoadBlock`, `SetCombineLERP` and so on.

**Textures are binary**: a 64-byte header whose bytes 4 to 8 read `XETO`, which is the
four character code `OTEX` stored little-endian, followed by the image in an N64 texture
format.

## What this means for this project

The model does not have to go through a ROM at all. Writing the mod means emitting, per
limb: a vertex list, a display list, a material, and the textures, then zipping them under
`alt/objects/object_link_boy/`.

## The wrong turn, recorded so it is not repeated

Retro is named as the packer in Ship of Harkinian's readme, so it was the first thing
tried. Its Ship of Harkinian tools only cover music sequences and fonts, and its Custom
option packs a directory of files exactly as they already are. From that it looked as
though nothing could convert a model, and the conclusion drawn was that the game had to
extract the model from a rebuilt ROM.

That conclusion was wrong, and the evidence against it was one download away. Retro packs
files as they are because **that is all a mod needs**: the files are hand-authorable XML.
The lesson is to open a working example of the thing before deciding it is impossible.

The ROM route did get built and does work as far as compiling the model through Fast64's
exporter, so it was not wasted as a check on the model. It is simply the long way round.
