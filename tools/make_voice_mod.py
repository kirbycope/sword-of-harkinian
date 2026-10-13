"""Give Link Kirby's voice, as a separate Ship of Harkinian mod.

    python tools/make_voice_mod.py --rom "Kirby 64.z64" --out dist/kirby_voice.o2r
    python tools/make_voice_mod.py --rom "Kirby 64.z64" --pick 12,37,54 --out ...

Audio is a different kind of mod from the model and wants its own archive.

**It does not live under `alt/`.** That prefix is gated behind the game's Alternate
Assets toggle and the audio path never checks it, so a voice sample goes in at the bare
path the game knows it by, `audio/samples/<name>_META`. The names are plain English, like
`Child Link - Attack 1`, and the full list is in the game's own `oot.o2r`.

**The samples go in as ordinary WAV, not as the game's compressed format.** Ship of
Harkinian's sample reader takes an XML descriptor with `CustomFormat="wav"` and a `Path`
to a second file inside the same archive, and decodes it at load. That matters for two
reasons: nothing has to be encoded to VADPCM, which would need a codebook designer, and
the player works the pitch out from the file's own sample rate, `tuning = rate * channels
/ 32000`. Every Link voice sample is 20000 Hz, and Kirby's are 32000 or 44100, so without
that the voice would play half an octave low. Letting the port read the rate off the WAV
is the whole reason this is a small job rather than a large one.

The official documentation says sound effect replacement is not supported. That is true
of the custom *sequence* system and false of the archive, which several published voice
mods rely on.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import wave
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import n64_soundbank as N  # noqa: E402

# Where each game keeps its sounds. The Kirby 64 pairs match the decompilation's own
# segment names exactly, which is what first confirmed the reader was right; the Smash 64
# ones were found by scoring candidate offsets on whether the frame headers decode.
BANKS = {
    "kirby64": [(0x2A8CB0, 0x2B1510), (0x3E1400, 0x3E6BC0)],
    "smash64": [(0xB4E5C0, 0xB54CE0), (0xC6B650, 0xC7B1F0)],
}

# Smash 64's sound effect bank, which is the one with the character voices in it.
SFX_BANK = ("smash64", 1)

RESOLVED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kirby_voices.json")

AGES = ("Adult Link", "Child Link")


def resolve_from_decomp(decomp: str) -> dict[str, int]:
    """Turn every named Kirby voice into the sample index it plays.

    Three hops, all of them in the decompilation rather than guessed: the enum gives a
    name an id, fgm.ucd[id] is that voice's script and names an articulation, and the
    articulation's `trigger` opcode carries the sample index. See kirby_voice_map.py.
    """
    import json
    import re

    header = os.path.join(decomp, "src", "gm", "gmsound.h")
    text = open(header, encoding="utf8", errors="replace").read()
    block = text[text.index("typedef enum gmFGMVoiceID"):text.index("} gmFGMVoiceID;")]
    names, value = {}, 0
    for line in block.splitlines():
        m = re.match(r"\s*(n[A-Za-z0-9_]+)\s*(?:=\s*(0x[0-9A-Fa-f]+|\d+))?\s*,", line)
        if not m:
            continue
        if m.group(2) is not None:
            value = int(m.group(2), 0)
        names[value] = m.group(1)
        value += 1

    folder = os.path.join(os.path.dirname(decomp), "build", "fgm")
    ucd = json.load(open(os.path.join(folder, "fgm.ucd.json")))["entries"]
    tbl = json.load(open(os.path.join(folder, "fgm.tbl.json")))["entries"]

    out: dict[str, int] = {}
    for vid, name in names.items():
        if vid >= len(ucd):
            continue
        for op in ucd[vid]["program"]:
            if op[0] != "set_articulation" or op[1] >= len(tbl):
                continue
            for inner in tbl[op[1]]["program"]:
                if inner[0] == "trigger":
                    out.setdefault(name, inner[1])
                    break
            if name in out:
                break
    return out


def load_samples(rom: bytes, ctl: int, tbl: int):
    """Every distinct sound in one bank, deduplicated by where its data lives."""
    samples = N.read_bank_file(rom[ctl:tbl])
    seen, out = set(), []
    for s in samples:
        key = (s.base, s.length)
        if key in seen or s.type != N.CODEC_ADPCM or not s.book or s.length < 128:
            continue
        seen.add(key)
        out.append(s)
    return out


def voice_like(pcm: list[int], rate: int) -> float:
    """How much a clip sounds like a voice rather than a bang or a rustle.

    A voice is periodic, so it correlates strongly with itself one pitch period later.
    An impact or a rustle does not. This is only a first guess at which of several
    hundred nameless clips to try; it is no substitute for listening to them.
    """
    if len(pcm) < rate // 20:
        return 0.0
    import statistics

    window = pcm[len(pcm) // 4: len(pcm) // 4 + min(len(pcm) // 2, rate // 10)]
    if len(window) < 200:
        return 0.0
    mean = statistics.fmean(window)
    centred = [v - mean for v in window]
    energy = sum(v * v for v in centred) or 1.0
    best = 0.0
    # Pitch periods from roughly 1100 Hz down to 90 Hz.
    for lag in range(rate // 1100, min(rate // 90, len(centred) - 1)):
        total = sum(centred[i] * centred[i + lag] for i in range(0, len(centred) - lag, 3))
        best = max(best, total * 3 / energy)
    return best


def write_wav_bytes(pcm: list[int], rate: int) -> bytes:
    import io
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))
    return buffer.getvalue()


def descriptor(wav_path: str, size: int) -> str:
    """The XML the port reads instead of a compressed sample.

    The root element's name is never checked, only its attributes, so it is named for
    what it is. Codec S16 is plain 16-bit PCM, which is what a WAV holds.
    """
    return (
        '<AudioSample Version="0" '
        f'CustomFormat="wav" Codec="S16" Medium="Cart" bit26="0" '
        f'Size="{size}" Path="{wav_path}"/>\n'
    )


def link_sample_names(archive: str) -> set[str]:
    """What the game actually calls its voice samples, read from its own archive.

    Read rather than assumed, because the two ages do not carry the same set: only the
    adult has a climbing grunt, for one, and shipping a resource the game has no name for
    would be dead weight at best.
    """
    import zipfile

    with zipfile.ZipFile(archive) as handle:
        return {n.split("/")[-1][: -len("_META")]
                for n in handle.namelist()
                if "audio/samples/" in n and n.endswith("_META")}


def main() -> None:
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True, help="the Super Smash Bros. ROM")
    parser.add_argument("--game-archive",
                        default=".toolchain/soh/app/oot.o2r",
                        help="the port's own archive, read for its sample names")
    parser.add_argument("--resolve", metavar="DECOMP",
                        help="rebuild kirby_voices.json from a decompilation checkout")
    parser.add_argument("--out")
    args = parser.parse_args()

    if args.resolve:
        table = resolve_from_decomp(os.path.abspath(args.resolve))
        with open(RESOLVED, "w", encoding="utf8") as handle:
            json.dump(table, handle, indent=1, sort_keys=True)
        print(f"resolved {len(table)} named voice(s) to sample indices -> {RESOLVED}")
        if not args.out:
            return

    if not args.out:
        parser.error("--out is required unless you are only resolving")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from kirby_voice_map import VOICE_MAP

    with open(RESOLVED, encoding="utf8") as handle:
        resolved = json.load(handle)

    rom = open(args.rom, "rb").read()
    game, bank = SFX_BANK
    ctl, tbl = BANKS[game][bank]
    samples = N.read_bank_file(rom[ctl:tbl])
    data = rom[tbl:]
    rate = samples[0].sample_rate
    print(f"{os.path.basename(args.rom)}: {len(samples)} sounds at {rate} Hz")

    have = link_sample_names(args.game_archive)
    print(f"the game knows {len(have)} voice sample name(s)")

    decoded: dict[int, bytes] = {}
    files: dict[str, bytes] = {}
    used, missing, unknown = 0, [], []

    for slot, voice in sorted(VOICE_MAP.items()):
        index = resolved.get(voice)
        if index is None:
            unknown.append(voice)
            continue
        if index >= len(samples):
            continue
        if index not in decoded:
            s = samples[index]
            pcm = N.decode_vadpcm(s.data(data), s.book, s.book_order,
                                  s.book_predictors, s.loop_state)
            if not pcm:
                continue
            decoded[index] = write_wav_bytes(pcm, rate)
            files[f"custom/kirby/{voice}.wav"] = decoded[index]
        wav_path = f"custom/kirby/{voice}.wav"
        placed = False
        for age in AGES:
            name = f"{age} - {slot}"
            if name not in have:
                continue
            files[f"audio/samples/{name}_META"] = descriptor(
                wav_path, len(decoded[index]) - 44).encode("utf8")
            placed = True
        if placed:
            used += 1
        else:
            missing.append(slot)

    if unknown:
        print(f"  {len(unknown)} mapped voice(s) not in the resolved table: "
              f"{', '.join(sorted(set(unknown))[:4])}")
    if missing:
        print(f"  {len(missing)} slot(s) the game has no sample for: "
              f"{', '.join(missing[:4])}")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(files.items()):
            archive.writestr(name, payload)
    clips = len([n for n in files if n.startswith("custom/")])
    slots = len([n for n in files if n.startswith("audio/")])
    print(f"\nwrote {clips} Kirby clip(s) covering {slots} of Link's voice samples "
          f"to {out} ({os.path.getsize(out) / 1024:.0f} KB)")
    print("Drop it in the mods folder beside the model. It is a separate archive because "
          "audio does not sit under alt/ and does not follow the mods toggle.")



if __name__ == "__main__":
    main()
