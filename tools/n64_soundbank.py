"""Read a Nintendo 64 sound bank, and repackage its samples for Ship of Harkinian.

    python tools/n64_soundbank.py --rom kirby.z64 --scan
    python tools/n64_soundbank.py --rom kirby.z64 --ctl 0x2A8CB0 --tbl 0x2B1510 --dump out/

Every Nintendo 64 game that uses the stock audio library stores its sounds the same way,
in a pair of files the tools call `ctl` and `tbl`. The `ctl` is a tree of structures
describing banks, instruments and sounds; the `tbl` is one long run of sample data. The
samples are VADPCM, a four-bit ADPCM variant with a per-bank codebook of predictors.

The useful consequence for a Ship of Harkinian mod is that **Ocarina of Time stores its
samples in exactly the same codec, with the codebook and loop points carried alongside**.
So a sound can be moved from one game to the other without decoding or re-encoding
anything: copy the compressed bytes, copy the codebook, copy the loop, and write the
port's own 64-byte container around them. No encoder is needed, which is the whole
reason this is tractable.

The structures are the ones in the library's own headers:

    ALBankFile  s16 revision (0x4231, "B1"), s16 bankCount, s32 bankOffset[]
    ALBank      s16 instCount, u8 flags, u8 pad, s32 sampleRate, s32 percussion,
                s32 instOffset[]
    ALInstrument u8 volume, pan, priority, flags, tremType, tremRate, tremDepth,
                tremDelay, vibType, vibRate, vibDepth, vibDelay, s16 bendRange,
                s16 soundCount, s32 soundOffset[]
    ALSound     s32 envelope, keyMap, wavetable, u8 samplePan, sampleVolume, flags
    ALWaveTable s32 base (offset into the tbl), s32 len, u8 type, u8 flags,
                then for ADPCM: s32 loopOffset, s32 bookOffset
    ALADPCMBook s32 order, s32 npredictors, s16 book[order * npredictors * 8]
    ALADPCMloop u32 start, end, count, s16 state[16]

Offsets inside the ctl are relative to the start of the ctl itself.
"""

from __future__ import annotations

import argparse
import os
import struct

REVISION = 0x4231          # "B1", the bank file's own magic
CODEC_ADPCM = 0
CODEC_RAW16 = 1


def be(data: bytes, offset: int, fmt: str):
    return struct.unpack_from(">" + fmt, data, offset)


class Sample:
    """One sound, with everything needed to replay it, and nothing decoded."""

    def __init__(self, bank: int, instrument: int, index: int, ctl: bytes,
                 wavetable: int, sample_rate: int) -> None:
        self.bank = bank
        self.instrument = instrument
        self.index = index
        self.sample_rate = sample_rate

        base, length = be(ctl, wavetable, "ii")
        self.base = base
        self.length = length
        self.type = ctl[wavetable + 8]
        self.flags = ctl[wavetable + 9]

        self.loop_start = self.loop_end = self.loop_count = 0
        self.loop_state: bytes = b""
        self.book_order = self.book_predictors = 0
        self.book: bytes = b""

        if self.type == CODEC_ADPCM:
            loop_off, book_off = be(ctl, wavetable + 12, "ii")
            if loop_off:
                self.loop_start, self.loop_end, self.loop_count = be(ctl, loop_off, "III")
                # A looping sound carries 16 shorts of decoder state; a one-shot does not.
                if self.loop_count:
                    self.loop_state = ctl[loop_off + 16: loop_off + 16 + 32]
            if book_off:
                self.book_order, self.book_predictors = be(ctl, book_off, "ii")
                size = self.book_order * self.book_predictors * 8 * 2
                self.book = ctl[book_off + 8: book_off + 8 + size]

    @property
    def frames(self) -> int:
        """VADPCM packs sixteen samples into nine bytes."""
        return self.length // 9

    @property
    def seconds(self) -> float:
        return (self.frames * 16) / float(self.sample_rate or 22050)

    def name(self) -> str:
        return f"b{self.bank}_i{self.instrument:03d}_s{self.index}"

    def data(self, tbl: bytes) -> bytes:
        return tbl[self.base: self.base + self.length]


def read_bank_file(ctl: bytes) -> list[Sample]:
    """Walk the whole tree and return every sound it reaches."""
    revision, bank_count = be(ctl, 0, "hh")
    if revision != REVISION:
        raise ValueError(f"not a bank file: revision {revision:#06x}, wanted {REVISION:#06x}")

    out: list[Sample] = []
    for b in range(bank_count):
        bank_off = be(ctl, 4 + b * 4, "i")[0]
        if not bank_off or bank_off >= len(ctl):
            continue
        inst_count, _flags, _pad = be(ctl, bank_off, "hBB")
        sample_rate = be(ctl, bank_off + 4, "i")[0]
        for i in range(inst_count):
            inst_off = be(ctl, bank_off + 12 + i * 4, "i")[0]
            if not inst_off or inst_off >= len(ctl):
                continue
            sound_count = be(ctl, inst_off + 14, "h")[0]
            if sound_count < 0 or sound_count > 512:
                continue
            for s in range(sound_count):
                sound_off = be(ctl, inst_off + 16 + s * 4, "i")[0]
                if not sound_off or sound_off + 16 > len(ctl):
                    continue
                wavetable = be(ctl, sound_off + 8, "i")[0]
                if not wavetable or wavetable + 16 > len(ctl):
                    continue
                try:
                    out.append(Sample(b, i, s, ctl, wavetable, sample_rate))
                except (struct.error, IndexError):
                    continue
    return out


def scan(rom: bytes, limit: int = 40) -> list[int]:
    """Every offset in the ROM that looks like the start of a bank file.

    The magic is only two bytes, so it turns up by chance. A real header is followed by a
    sane bank count and by offsets that point forwards and stay inside the file, which
    throws almost all of the noise away.
    """
    found = []
    magic = struct.pack(">h", REVISION)
    start = 0
    while len(found) < limit:
        at = rom.find(magic, start)
        if at < 0:
            break
        start = at + 2
        try:
            count = be(rom, at + 2, "h")[0]
            if not 1 <= count <= 64:
                continue
            offsets = [be(rom, at + 4 + n * 4, "i")[0] for n in range(count)]
            if any(o <= 0 or o > 0x400000 for o in offsets):
                continue
            if offsets != sorted(offsets):
                continue
            found.append(at)
        except struct.error:
            continue
    return found


# --------------------------------------------------------------- VADPCM decoding
#
# Only needed to listen to what was extracted. Moving a sample into the game does not
# decode anything.

def decode_vadpcm(data: bytes, book: bytes, order: int, npredictors: int,
                  state: bytes = b"") -> list[int]:
    coef = [[[0] * 8 for _ in range(order)] for _ in range(npredictors)]
    k = 0
    for p in range(npredictors):
        for o in range(order):
            for j in range(8):
                coef[p][o][j] = struct.unpack_from(">h", book, k * 2)[0]
                k += 1

    history = [0] * order
    if len(state) >= 32:
        tail = [struct.unpack_from(">h", state, i * 2)[0] for i in range(16)]
        history = tail[-order:]

    out: list[int] = []
    for f in range(len(data) // 9):
        frame = data[f * 9: f * 9 + 9]
        header = frame[0]
        shift = header >> 4
        predictor = header & 0xF
        if predictor >= npredictors:
            predictor = 0
        nibbles = []
        for byte in frame[1:]:
            nibbles.append(byte >> 4)
            nibbles.append(byte & 0xF)
        for half in range(2):
            block = []
            for n in nibbles[half * 8: half * 8 + 8]:
                value = n - 16 if n >= 8 else n
                block.append(value << shift)
            for i, residual in enumerate(block):
                # The prediction is the frame's coefficients against the running history,
                # plus the samples already decoded earlier in this half frame.
                total = residual << 11
                for o in range(order):
                    total += coef[predictor][o][i] * history[order - 1 - o]
                for j in range(i):
                    total += coef[predictor][0][i - 1 - j] * block[j]
                sample = max(-32768, min(32767, total >> 11))
                out.append(sample)
                block[i] = sample
            history = out[-order:] if len(out) >= order else history
    return out


def write_wav(path: str, samples: list[int], rate: int) -> None:
    import wave
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate or 22050)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))


# --------------------------------------------------------- Ship of Harkinian container

def soh_sample(sample: Sample, tbl: bytes) -> bytes:
    """Wrap a sample's own bytes in the port's container, codec and codebook intact."""
    data = sample.data(tbl)
    header = bytearray(64)
    header[4:8] = b"PMSO"                      # OSMP, little-endian
    struct.pack_into("<I", header, 8, 2)       # version 2
    struct.pack_into("<II", header, 12, 0xDEADBEEF, 0xDEADBEEF)

    body = bytes([CODEC_ADPCM, 0, 0, 0]) + struct.pack("<I", len(data)) + data
    states = 1 if sample.loop_state else 0
    loop = struct.pack("<4I", sample.loop_start, sample.loop_end,
                       sample.loop_count, states)
    if states:
        loop += sample.loop_state[:32].ljust(32, b"\0")[:16 * 2]
    book_count = sample.book_order * sample.book_predictors * 8
    book = struct.pack("<3I", sample.book_order, sample.book_predictors, book_count)
    book += sample.book[:book_count * 2].ljust(book_count * 2, b"\0")
    return bytes(header) + body + loop + book


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", required=True)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--ctl", help="offset of the bank file, decimal or 0x hex")
    parser.add_argument("--tbl", help="offset of the sample data")
    parser.add_argument("--dump", help="write every sample here as a wav")
    parser.add_argument("--max-seconds", type=float, default=4.0,
                        help="skip anything longer, which is music rather than a voice")
    args = parser.parse_args()

    rom = open(args.rom, "rb").read()
    print(f"{os.path.basename(args.rom)}: {len(rom) / 1048576:.0f} MB")

    if args.scan:
        hits = scan(rom)
        print(f"\n{len(hits)} candidate bank file(s):")
        for at in hits:
            count = be(rom, at + 2, "h")[0]
            try:
                samples = read_bank_file(rom[at:at + 0x200000])
            except ValueError:
                continue
            print(f"  {at:#09x}  {count:>2} bank(s), {len(samples):>4} sound(s)"
                  + (f", rate {samples[0].sample_rate}" if samples else ""))
        return

    ctl_at = int(args.ctl, 0)
    tbl_at = int(args.tbl, 0)
    ctl = rom[ctl_at:tbl_at]
    tbl = rom[tbl_at:]
    samples = read_bank_file(ctl)
    print(f"\n{len(samples)} sound(s) in the bank at {ctl_at:#x}")

    if args.dump:
        os.makedirs(args.dump, exist_ok=True)
        written = 0
        for sample in samples:
            if sample.type != CODEC_ADPCM or not sample.book or sample.length <= 0:
                continue
            if sample.seconds > args.max_seconds:
                continue
            pcm = decode_vadpcm(sample.data(tbl), sample.book, sample.book_order,
                                sample.book_predictors, sample.loop_state)
            if not pcm:
                continue
            write_wav(os.path.join(args.dump, f"{sample.name()}_{sample.seconds:.2f}s.wav"),
                      pcm, sample.sample_rate)
            written += 1
        print(f"wrote {written} wav(s) to {args.dump}")


if __name__ == "__main__":
    main()
