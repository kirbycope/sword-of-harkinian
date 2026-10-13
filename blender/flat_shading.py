"""Render these models the way the N64 draws them: flat, unlit texture.

Lighting a ripped N64 model with studio lamps is actively misleading. The textures are
already fully shaded, so a key and fill light darken and tint them: Link's gold cap
fringe came out a muddy olive against the vivid yellow of the reference, and Kirby's
pink read dusty. Nothing was wrong with the model, only with how it was being lit.

Swapping every material for an emission shader fed by its own texture shows the colours
the game actually puts on screen, and matches how reference renders of these rips are
presented, so the two can be compared honestly.
"""

from __future__ import annotations

import bpy


def flatten(material: bpy.types.Material) -> bool:
    """Drive a material straight from its texture, with no lighting applied."""
    if not material.use_nodes:
        material.use_nodes = True

    tree = material.node_tree
    output = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if output is None:
        output = tree.nodes.new("ShaderNodeOutputMaterial")

    principled = tree.nodes.get("Principled BSDF")
    source = None
    colour = (0.8, 0.8, 0.8, 1.0)
    if principled is not None:
        base = principled.inputs["Base Color"]
        if base.is_linked:
            source = base.links[0].from_socket
        else:
            colour = tuple(base.default_value)

    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (output.location.x - 220, output.location.y)
    emission.inputs["Strength"].default_value = 1.0
    if source is not None:
        tree.links.new(source, emission.inputs["Color"])
    else:
        emission.inputs["Color"].default_value = colour

    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return source is not None


def flatten_all(obj: bpy.types.Object) -> int:
    """Flatten every material on a mesh. Returns how many are texture driven."""
    textured = 0
    for material in obj.data.materials:
        if material is not None and flatten(material):
            textured += 1
    return textured
