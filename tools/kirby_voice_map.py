"""Which Kirby sound plays for which of Link's, by name on both sides.

Nothing here is guesswork, which is the point. Both games name their sounds, so the
pairing can be made on meaning rather than on listening to several hundred nameless
clips and hoping.

**Ocarina of Time's side** comes from the port's own archive, where voice samples are
stored under plain English names like `Child Link - Attack 1`.

**Smash 64's side** comes from its decompilation, which resolves in three steps:

    gmFGMVoiceID              a named enum, e.g. nSYAudioVoiceKirbyDamage
      -> fgm.ucd[id]          the voice script for that name
      -> set_articulation N   which picks an entry in fgm.tbl
      -> fgm.tbl[N]           whose `trigger` opcode carries the sample index

That last number indexes the sound effect bank directly: its triggers run 0 to 321 and
the bank holds exactly 322 sounds. Kirby's voice clips turn out to be a contiguous run,
116 to 137, which is what you would expect of one character's set and is a good sign the
chain is being read correctly.

`tools/make_voice_mod.py --resolve` rebuilds `kirby_voices.json` from a checkout of the
decompilation. The resolved table is kept in the repository so an ordinary build needs
only the ROM.
"""

# Link's sample name, without its age prefix, against the Smash 64 voice that replaces it.
#
# The pairings follow what each sound is for. Link swings his sword far more than he does
# anything else, fifteen call sites against the next most at nine, so his four attack
# grunts get Kirby's three smash grunts and the voice he uses when he has copied Link,
# which is the most on the nose this could possibly be.
VOICE_MAP = {
    "Attack 1":              "nSYAudioVoiceKirbySmash1",
    "Attack 2":              "nSYAudioVoiceKirbySmash2",
    "Attack 3":              "nSYAudioVoiceKirbySmash3",
    "Attack 4":              "nSYAudioVoiceKirbyCopyLinkSpecialN",
    "Strong Attack 1":       "nSYAudioVoiceKirbySpecialHi",
    "Strong Attack 2":       "nSYAudioVoiceKirbyCopyCaptainSpecialNPunch",

    "Hurt 1":                "nSYAudioVoiceKirbyDamage",
    "Hurt 2":                "nSYAudioVoiceKirbyOttotto",
    "Hurt 3":                "nSYAudioVoiceKirbyDamage",
    "Hurt 4":                "nSYAudioVoiceKirbyDead",
    "Hurt 5":                "nSYAudioVoiceKirbyUnkGrunt1",
    "Knocked Back":          "nSYAudioVoiceKirbyDeadUp",
    "Painful Landing":       "nSYAudioVoiceKirbyDead",

    "Falling 1":             "nSYAudioVoiceKirbyDeadUp",
    "Falling 2":             "nSYAudioVoiceKirbyFuraFura",
    "Dying Gasp":            "nSYAudioVoiceKirbyDead",

    "Hup 1":                 "nSYAudioVoiceKirbySmash1",
    "Hup 2":                 "nSYAudioVoiceKirbySmash2",
    "Grunt":                 "nSYAudioVoiceKirbyUnkGrunt1",
    "Climb Edge":            "nSYAudioVoiceKirbyHeavyGet",
    "Awakening Grunt":       "nSYAudioVoiceKirbyFuraSleep",

    "Gasp 1":                "nSYAudioVoiceKirbyUnkGrunt1",
    "Gasp 2":                "nSYAudioVoiceKirbyUnkGrunt2",
    "Gasp 3":                "nSYAudioVoiceKirbyOttotto",
    "Dramatic Gasp":         "nSYAudioVoiceKirbySpecialHi",
    "Dangling Gasp 1":       "nSYAudioVoiceKirbyOttotto",
    "Dangling Gasp 2":       "nSYAudioVoiceKirbyUnkGrunt2",

    "Exhausted Panting":     "nSYAudioVoiceKirbyFuraFura",
    "Wheeze":                "nSYAudioVoiceKirbyFuraFura",
    "Sigh 1":                "nSYAudioVoiceKirbyFuraSleep",
    "Sigh 2":                "nSYAudioVoiceKirbyFuraSleep",
    "Yawn Starts":           "nSYAudioVoiceKirbyFuraSleep",
    "Yawn and Stretch":      "nSYAudioVoiceKirbyAppeal",
    "Stretch Refreshingly":  "nSYAudioVoiceKirbyAppeal",
    "Refreshed":             "nSYAudioVoiceKirbyAppeal",
    "Moans From Heat":       "nSYAudioVoiceKirbyFuraFura",
    "Shivers":               "nSYAudioVoiceKirbyFuraFura",
    "Wiping Off Sweat":      "nSYAudioVoiceKirbyHeavyGet",
    "Sneeze":                "nSYAudioVoiceKirbyUnkGrunt2",
    "Cast Attack Spell":     "nSYAudioVoiceKirbySpecialHi",
    "Cast Attacking Magic":  "nSYAudioVoiceKirbySpecialHi",
}
