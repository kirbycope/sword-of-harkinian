"""Move the carried pot by posing the arms, not by moving the shoulder joints.

Where the game puts a carried pot or bush, from `Player_PostLimbDrawGameplay` in
z_player_lib.c: once the right hand limb has been drawn, the held actor is placed at the
midpoint of the two hand limbs' own origins,

    heldActor->world.pos = (bodyPartsPos[PLAYER_BODYPART_R_HAND] + leftHandPos) * 0.5

with no offset in hand space. (The hand-space offset a few lines earlier, D_80126128,
belongs to the aiming state, PLAYER_STATE1_9, and does not run for a carry.) So where the
pot sits is decided by the two chains of limb matrices from the root to each hand, which
are the limb joint positions, fixed in the skeleton, and the per-frame rotations, in the
animation. The pot's own facing comes from the player's yaw, so the hands can turn
however they like.

Lowering both shoulder joints in the skeleton put the pot where it should be on Kirby,
and it crashes Ship of Harkinian's Metal backend on every scene transition, because any
SkeletonLimb served from an alt archive does, whatever bytes it holds. Animations served
the same way are fine. So the same pot position is reached the other way round: for every
frame of every carry animation, each arm is posed so its hand lands where the lowered
shoulder would have put it. Nothing of Kirby's is drawn on the arm or hand limbs, so the
arms can take any pose at all and only the pot moves.

The skeleton walk, from `SkelAnime_DrawFlexLimbLod` and `Matrix_TranslateRotateZYX`:

    M_limb = M_parent . T(jointPos) . Rz(rot.z) . Ry(rot.y) . Rx(rot.x)

with the root's translation taken from the frame. A frame is 67 signed shorts: three of
root translation, then three rotations for each of the 21 limbs in skeleton order, then
one short of eye and mouth indices. Angles are binary, 0x10000 to the full turn.

Run directly to check it against the game archive without building anything:

    python tools/carry_fk.py .toolchain/soh/app/oot.o2r adult 2200
"""
from __future__ import annotations

import math
import re
import struct
import sys
import zipfile

# The chain from the root to each hand, as skeleton limb indices. Each is a child of the
# one before it; siblings do not enter into it, because a sibling is drawn after the
# parent's matrix is popped. The shoulder, elbow and hand slots are the same on both
# skeletons: 13, 14, 15 on the left and 16, 17, 18 on the right.
CHAINS = {
    "adult": {
        "left": [("gLinkAdultRootLimb", 0), ("gLinkAdultUpperControlLimb", 9),
                 ("gLinkAdultLeftShoulderLimb", 13), ("gLinkAdultLeftArmLimb", 14),
                 ("gLinkAdultLeftHandLimb", 15)],
        "right": [("gLinkAdultRootLimb", 0), ("gLinkAdultUpperControlLimb", 9),
                  ("gLinkAdultRightShoulderLimb", 16), ("gLinkAdultRightArmLimb", 17),
                  ("gLinkAdultRightHandLimb", 18)],
    },
    "child": {
        "left": [("gLinkChildRootLimb", 0), ("gLinkChildUpperControlLimb", 9),
                 ("gLinkChildLeftshoulderLimb", 13), ("gLinkChildLeftForearmLimb", 14),
                 ("gLinkChildLeftHandLimb", 15)],
        "right": [("gLinkChildRootLimb", 0), ("gLinkChildUpperControlLimb", 9),
                  ("gLinkChildRightshoulderLimb", 16), ("gLinkChildRightForearmLimb", 17),
                  ("gLinkChildRightHandLimb", 18)],
    },
}
LEG_CHAINS = {
    "adult": {
        "right": [("gLinkAdultRootLimb", 0), ("gLinkAdultWaistLimb", 1), ("gLinkAdultLowerControlLimb", 2),
                  ("gLinkAdultRightThighLimb", 3), ("gLinkAdultRightLegLimb", 4), ("gLinkAdultRightFootLimb", 5)],
        "left": [("gLinkAdultRootLimb", 0), ("gLinkAdultWaistLimb", 1), ("gLinkAdultLowerControlLimb", 2),
                 ("gLinkAdultLeftThighLimb", 6), ("gLinkAdultLeftLegLimb", 7), ("gLinkAdultLeftFootLimb", 8)],
    },
    "child": {
        "right": [("gLinkChildRootLimb", 0), ("gLinkChildWaistLimb", 1), ("gLinkChildLowerControlLimb", 2),
                  ("gLinkChildRightThighLimb", 3), ("gLinkChildRightShinLimb", 4), ("gLinkChildRightFootLimb", 5)],
        "left": [("gLinkChildRootLimb", 0), ("gLinkChildWaistLimb", 1), ("gLinkChildLowerControlLimb", 2),
                 ("gLinkChildLeftThighLimb", 6), ("gLinkChildLeftShinLimb", 7), ("gLinkChildLeftFootLimb", 8)],
    },
}

# How much of Link's stride a shoe takes. His foot travels further than Kirby is wide;
# at 0.3 the shoes step under the ball instead of swinging out past it and up to his ear.
FOOT_REACH = 0.3

OBJECT = {"adult": "objects/object_link_boy/", "child": "objects/object_link_child/"}

# Every animation the player is in while a pot or bush is in hand. The pickup is the
# carryB group (free or not, by what else is held), a bush pulled without the bracelet is
# nocarry_free and its two tails, and carryB_wait is the upper-body loop played over
# idling and walking for as long as the thing is held.
CARRY_ANIMATIONS = (
    "gPlayerAnim_link_normal_carryB_free",
    "gPlayerAnim_link_normal_carryB",
    "gPlayerAnim_link_normal_carryB_wait",
    "gPlayerAnim_link_normal_nocarry_free",
    "gPlayerAnim_link_normal_nocarry_free_wait",
    "gPlayerAnim_link_normal_nocarry_free_end",
)

SHORTS_PER_FRAME = 67

# The two limbs that matter to the sheathed sword and shield, both children of
# UpperControl: the torso draws the body, the sheath draws whatever is on his back. Where
# one sits against the other is a question about the standing idle, not the rest pose:
# the torso idles at zero rotation but the sheath limb idles at (-90, 150, -4) degrees,
# so a placement measured in Blender's zero-rotation rest pose lands a body height away
# from where the game draws it.
BACK_LIMBS = {
    "adult": [("gLinkAdultRootLimb", 0), ("gLinkAdultUpperControlLimb", 9),
              ("gLinkAdultSwordAndSheathLimb", 19), ("gLinkTorsoLimb", 20)],
    "child": [("gLinkChildRootLimb", 0), ("gLinkChildUpperControlLimb", 9),
              ("gLinkChildSwordAndSheathLimb", 19), ("gLinkChildTorsoLimb", 20)],
}
IDLE = "gPlayerAnim_link_normal_wait_free"

TAU = 2.0 * math.pi


def angle(short: int) -> float:
    return short * TAU / 65536.0


def to_short(radians: float) -> int:
    value = int(round(radians * 65536.0 / TAU)) % 65536
    return value - 65536 if value >= 32768 else value


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def translate(x, y, z):
    return [[1, 0, 0, x], [0, 1, 0, y], [0, 0, 1, z], [0, 0, 0, 1]]


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]]


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]]


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]


def limb_matrix(parent, pos, rot):
    """One step of the walk: M_parent . T(pos) . Rz . Ry . Rx."""
    m = mat_mul(parent, translate(*pos))
    m = mat_mul(m, rot_z(rot[2]))
    m = mat_mul(m, rot_y(rot[1]))
    return mat_mul(m, rot_x(rot[0]))


def joint_positions(archive: zipfile.ZipFile, age: str) -> dict[int, tuple[int, int, int]]:
    out = {}
    for chain in list(CHAINS[age].values()) + list(LEG_CHAINS[age].values()):
        for name, index in chain:
            if index not in out:
                data = archive.read(OBJECT[age] + name)
                out[index] = struct.unpack_from("<3h", data, len(data) - 8)
    return out


def chain_matrix(frame: list[int], joints: dict, chain: list, rotations: dict | None = None):
    """The matrix of the last limb in `chain` for this frame, in the root's frame.

    `rotations` overrides the frame's rotations for the limbs it names, in radians.
    """
    m = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    for _name, index in chain:
        pos = frame[0:3] if index == 0 else joints[index]
        if rotations and index in rotations:
            rot = rotations[index]
        else:
            rot = tuple(angle(v) for v in frame[3 + 3 * index: 6 + 3 * index])
        m = limb_matrix(m, pos, rot)
    return m


def hand_origin(frame: list[int], joints: dict, chain: list, rotations: dict | None = None):
    """Where the last limb in `chain` has its origin for this frame, in the root's frame."""
    m = chain_matrix(frame, joints, chain, rotations)
    return (m[0][3], m[1][3], m[2][3])


def invert_rigid(m):
    """The inverse of a rotation-and-translation matrix: transposed rotation, negated move."""
    r = transpose(rotation(m))
    t = [m[i][3] for i in range(3)]
    return [[r[i][0], r[i][1], r[i][2], -sum(r[i][k] * t[k] for k in range(3))]
            for i in range(3)] + [[0, 0, 0, 1]]


def idle_pose(archive_path: str, age: str):
    """World matrices of the sheath limb and the torso limb on the first frame of the
    standing idle, in the player's model space: +y up, facing +z, his back along -z."""
    with zipfile.ZipFile(archive_path) as archive:
        joints = {}
        for name, index in BACK_LIMBS[age]:
            data = archive.read(OBJECT[age] + name)
            joints[index] = struct.unpack_from("<3h", data, len(data) - 8)
        _frames, blob = animation_blob(archive, IDLE)
        frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", archive.read(blob), 68))
    spine = chain_matrix(frame, joints, BACK_LIMBS[age][:2])
    limbs = []
    for _name, index in BACK_LIMBS[age][2:]:
        rot = tuple(angle(v) for v in frame[3 + 3 * index: 6 + 3 * index])
        limbs.append(limb_matrix(spine, joints[index], rot))
    return limbs[0], limbs[1]


def apply(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))


def rotation(m):
    return [row[:3] for row in m[:3]]


def transpose(r):
    return [[r[j][i] for j in range(3)] for i in range(3)]


def mat3_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def euler_zyx(r) -> tuple[float, float, float]:
    """Angles (x, y, z) with Rz(z) . Ry(y) . Rx(x) equal to `r`, the walk's own order."""
    sy = max(-1.0, min(1.0, -r[2][0]))
    y = math.asin(sy)
    if abs(math.cos(y)) > 1e-6:
        x = math.atan2(r[2][1], r[2][2])
        z = math.atan2(r[1][0], r[0][0])
    else:
        x = 0.0
        z = math.atan2(-r[0][1], r[1][1])
    return (x, y, z)


def midpoint(a, b):
    return tuple((a[i] + b[i]) * 0.5 for i in range(3))


def pot_position(frame, joints, chains, rotations=None):
    """Where the carried actor lands: halfway between the two hands."""
    return midpoint(hand_origin(frame, joints, chains["left"], rotations),
                    hand_origin(frame, joints, chains["right"], rotations))


def arm_angles(frame: list[int], shoulder: int, elbow: int | None = None) -> list[float]:
    """The four angles one limb's solve may change: pivot x y z and hinge z."""
    elbow = shoulder + 1 if elbow is None else elbow
    return [angle(v) for v in frame[3 + 3 * shoulder: 6 + 3 * shoulder]] + \
           [angle(frame[3 + 3 * elbow + 2])]


def write_arm(frame: list[int], shoulder: int, theta: list[float], elbow: int | None = None) -> None:
    elbow = shoulder + 1 if elbow is None else elbow
    frame[3 + 3 * shoulder: 6 + 3 * shoulder] = [to_short(t) for t in theta[0:3]]
    frame[3 + 3 * elbow + 2] = to_short(theta[3])


def solve_arm(frame: list[int], joints: dict, chain: list, target, start=None,
              iterations: int = 60, pivot: int = 2):
    """Shoulder and elbow angles that put this hand's origin at `target`.

    Four angles for three coordinates, so among the poses that reach the target the
    solve takes the one nearest `start`: the previous frame's answer, so that
    consecutive frames stay alike and the pot does not jitter, or the game's own pose
    for the first frame. Damped Gauss-Newton with a numerical Jacobian.
    """
    shoulder = chain[pivot][1]
    elbow = chain[pivot + 1][1]
    original = list(start) if start is not None else arm_angles(frame, shoulder, elbow)
    theta = list(original)
    elbow_xy = [angle(v) for v in frame[3 + 3 * elbow: 5 + 3 * elbow]]
    n = 4

    def place(t):
        return hand_origin(frame, joints, chain,
                           {shoulder: (t[0], t[1], t[2]), elbow: (elbow_xy[0], elbow_xy[1], t[3])})

    # Cost: distance to the target squared, plus `keep` times the angle change squared.
    # `keep` is in game units squared per radian squared, so its square root is how many
    # units of miss are worth one radian of change: small enough that a reachable target
    # is hit, large enough to pick the nearest pose among the many that reach it.
    keep = 30.0 ** 2

    def cost(t):
        p = place(t)
        return sum((p[i] - target[i]) ** 2 for i in range(3)) + \
            keep * sum((t[k] - original[k]) ** 2 for k in range(n))

    damping = 1.0
    current = cost(theta)
    for _ in range(iterations):
        p = place(theta)
        residual = [p[i] - target[i] for i in range(3)]
        h = 1e-5
        jac = []
        for k in range(n):
            bumped = list(theta)
            bumped[k] += h
            q = place(bumped)
            jac.append([(q[i] - p[i]) / h for i in range(3)])
        # Levenberg-Marquardt: (J^T J + keep I + damping I) d = -(J^T r + keep (theta - original)).
        gradient = [sum(jac[i][m] * residual[m] for m in range(3)) + keep * (theta[i] - original[i])
                    for i in range(n)]
        improved = False
        for _attempt in range(12):
            a = [[sum(jac[i][m] * jac[j][m] for m in range(3)) + ((keep + damping) if i == j else 0.0)
                  for j in range(n)] for i in range(n)]
            step = solve_linear(a, [-g for g in gradient])
            trial = [theta[k] + step[k] for k in range(n)]
            trial_cost = cost(trial)
            if trial_cost < current:
                theta, current = trial, trial_cost
                damping = max(damping / 3.0, 1e-6)
                improved = True
                break
            damping *= 10.0
        if not improved or max(abs(s) for s in step) < 1e-6:
            break
    p = place(theta)
    error = math.sqrt(sum((p[i] - target[i]) ** 2 for i in range(3)))
    return theta, error


def solve_linear(a, b):
    """Gaussian elimination, four by four."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [m[r][k] - f * m[col][k] for k in range(n + 1)]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def animation_blob(archive: zipfile.ZipFile, header_name: str):
    """Frame count and the data blob's archive path, read from the header resource."""
    raw = archive.read("objects/gameplay_keep/" + header_name)
    if struct.unpack_from("<I", raw, 64)[0] != 1:
        raise ValueError(f"{header_name} is not a player animation")
    frames = struct.unpack_from("<h", raw, 68)[0]
    length = struct.unpack_from("<I", raw, 70)[0]
    segment = raw[74:74 + length].decode("utf8", "replace").replace("__OTR__", "")
    found = [n for n in archive.namelist() if n.endswith(segment)]
    if not found:
        raise KeyError(segment)
    return frames, found[0]


def lowered(joints: dict, drop: int, forward: int) -> dict:
    """The joint table with both shoulders moved, which is the limb edit being replaced."""
    out = dict(joints)
    for shoulder in (13, 16):
        x, y, z = joints[shoulder]
        out[shoulder] = (x - drop, y + forward, z)
    return out


def wrap(radians: float) -> float:
    """Into (-pi, pi], so a warm start cannot wind an elbow through whole turns."""
    return (radians + math.pi) % TAU - math.pi


def solve_frame(frame, joints, targets_joints, chains, previous):
    """Pose both arms for one frame. Returns the angles per side and the pot's miss."""
    # Targets come from the frame as the game authored it, before either arm is touched.
    wanted = pot_position(frame, targets_joints, chains)
    targets = {side: hand_origin(frame, targets_joints, chains[side]) for side in chains}
    result = {}
    for side in ("left", "right"):
        chain = chains[side]
        theta, _error = solve_arm(frame, joints, chain, targets[side],
                                  start=previous.get(side) if previous else None)
        theta = [wrap(t) for t in theta]
        result[side] = theta
        write_arm(frame, chain[2][1], theta)
    got = pot_position(frame, joints, chains)
    miss = math.sqrt(sum((got[i] - wanted[i]) ** 2 for i in range(3)))
    return result, miss


def carry_animations(archive_path: str, age: str, drop: int, forward: int = 0,
                     names=CARRY_ANIMATIONS, log=print) -> dict[str, bytes]:
    """Rewritten data blobs, keyed by archive path, with the pot moved down by `drop`.

    The target for each frame is where the pot would be with both shoulder joints lowered
    by `drop` (the limb edit that cannot ship), computed through the same walk the game
    does.
    """
    out = {}
    with zipfile.ZipFile(archive_path) as archive:
        chains = CHAINS[age]
        joints = joint_positions(archive, age)
        wanted = lowered(joints, drop, forward)

        for header in names:
            frames, blob_path = animation_blob(archive, header)
            data = bytearray(archive.read(blob_path))
            count = struct.unpack_from("<I", data, 64)[0]
            if count != frames * SHORTS_PER_FRAME:
                log(f"  note: {header} is {count} shorts, not {frames} x {SHORTS_PER_FRAME}; left alone")
                continue
            worst, previous = 0.0, None
            for f in range(frames):
                at = 68 + f * SHORTS_PER_FRAME * 2
                frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", data, at))
                previous, miss = solve_frame(frame, joints, wanted, chains, previous)
                worst = max(worst, miss)
                struct.pack_into(f"<{SHORTS_PER_FRAME}h", data, at, *frame)
            out[blob_path] = bytes(data)
            log(f"  {header}: {frames} frames, pot within {worst / 1000.0:.3f} of the target at worst")
    return out


def _self_check(archive_path: str, age: str, drop: int) -> None:
    """Print what the joint edit does and what the solve achieves, frame by frame."""
    with zipfile.ZipFile(archive_path) as archive:
        chains = CHAINS[age]
        joints = joint_positions(archive, age)
        wanted = lowered(joints, drop, 0)
        for header in CARRY_ANIMATIONS:
            frames, blob_path = animation_blob(archive, header)
            data = archive.read(blob_path)
            print(f"== {header} ({frames} frames)")
            previous, worst, jump = None, 0.0, 0.0
            for f in range(frames):
                at = 68 + f * SHORTS_PER_FRAME * 2
                frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", data, at))
                before = pot_position(frame, joints, chains)
                target = pot_position(frame, wanted, chains)
                solved, miss = solve_frame(frame, joints, wanted, chains, previous)
                if previous is not None:
                    for side in solved:
                        jump = max(jump, max(abs(math.degrees(a - b))
                                             for a, b in zip(solved[side], previous[side])))
                previous = solved
                worst = max(worst, miss)
                moved = tuple(round((target[i] - before[i]) / 1000.0, 2) for i in range(3))
                if f in (0, frames // 2, frames - 1):
                    l = [round(math.degrees(t)) for t in solved["left"]]
                    r = [round(math.degrees(t)) for t in solved["right"]]
                    print(f"  frame {f:2d}: pot {tuple(round(v / 1000.0, 2) for v in before)}"
                          f" -> joint edit moves it {moved}; left {l} right {r}"
                          f" lands {miss / 1000.0:.3f} off")
            print(f"  worst miss {worst / 1000.0:.3f}, largest angle step between frames {jump:.0f} deg")


# Where Kirby's mittens sit, in the torso limb's space, which is the upper control limb's
# space since the torso's own joint is at its origin. Measured off each rigged model;
# +z is the left one, the side the left shoulder's joint is on.
MITTENS = {
    "adult": {"left": (-2075, 557, 1314), "right": (-2075, 557, -1314)},
    "child": {"left": (-1295, 331, 936), "right": (-1295, 331, -932)},
}

# How far down the shoulder-to-mitten line the hand is anchored, as a fraction of the
# arm's full length. One is a straight arm with no room to swing.
ANCHOR_FRACTION = 0.75

# Reach for the sword set alone. A slash at Link's full extension sends the mitten
# clear of the body at the ordinary reach, and with no arm geometry that reads as a
# detached hand; the blade still sweeps by its direction, so the hand itself can stay
# against the flank.
SWORD_REACH = 0.12

# Every animation the player is in with the sword drawn: the fighter stance set, the
# Z-targeting stance set and the transitions into and out of them.
SWING_ANIMATION = re.compile(r"link_fighter_|link_anchor_|normal2fighter|fighter2free|fighter2long")

# Everything the player's animation table plays only with a sword drawn: names in
# columns 1 to 3 of D_80853914 in z_player.c (the one-handed, sword-and-shield and
# two-handed model types) and never in the hands-free columns. The names alone do not
# say it: the drawn-sword idle is link_normal_wait, the same stem as the hands-free
# link_normal_wait_free.
SWORD_DRAWN = frozenset((
    "gPlayerAnim_link_anchor_side_walkL", "gPlayerAnim_link_anchor_side_walkR", "gPlayerAnim_link_anchor_waitL",
    "gPlayerAnim_link_anchor_waitR", "gPlayerAnim_link_demo_doorA_link", "gPlayerAnim_link_demo_doorB_link",
    "gPlayerAnim_link_fighter_damage_run", "gPlayerAnim_link_fighter_damage_run_long", "gPlayerAnim_link_fighter_heavy_run_long",
    "gPlayerAnim_link_fighter_landing_roll_long", "gPlayerAnim_link_fighter_normal2fighter", "gPlayerAnim_link_fighter_run",
    "gPlayerAnim_link_fighter_run_long", "gPlayerAnim_link_fighter_side_walkL_long", "gPlayerAnim_link_fighter_side_walkR_long",
    "gPlayerAnim_link_fighter_side_walk_long", "gPlayerAnim_link_fighter_waitL_long", "gPlayerAnim_link_fighter_waitR_long",
    "gPlayerAnim_link_fighter_wait_long", "gPlayerAnim_link_fighter_walk_endL_long", "gPlayerAnim_link_fighter_walk_endR_long",
    "gPlayerAnim_link_fighter_walk_long", "gPlayerAnim_link_normal_45_turn", "gPlayerAnim_link_normal_carryB",
    "gPlayerAnim_link_normal_check", "gPlayerAnim_link_normal_check_end", "gPlayerAnim_link_normal_check_wait",
    "gPlayerAnim_link_normal_defense", "gPlayerAnim_link_normal_defense_end", "gPlayerAnim_link_normal_defense_wait",
    "gPlayerAnim_link_normal_down_slope_slip_end", "gPlayerAnim_link_normal_down_slope_slip_end_long", "gPlayerAnim_link_normal_fall_up",
    "gPlayerAnim_link_normal_heavy_run", "gPlayerAnim_link_normal_hip_down", "gPlayerAnim_link_normal_hip_down_long",
    "gPlayerAnim_link_normal_jump_climb_hold", "gPlayerAnim_link_normal_jump_climb_up", "gPlayerAnim_link_normal_jump_climb_wait",
    "gPlayerAnim_link_normal_landing", "gPlayerAnim_link_normal_landing_roll", "gPlayerAnim_link_normal_pull_end",
    "gPlayerAnim_link_normal_pull_start", "gPlayerAnim_link_normal_pulling", "gPlayerAnim_link_normal_put",
    "gPlayerAnim_link_normal_run", "gPlayerAnim_link_normal_short_landing", "gPlayerAnim_link_normal_side_walk",
    "gPlayerAnim_link_normal_throw", "gPlayerAnim_link_normal_up_slope_slip_end", "gPlayerAnim_link_normal_up_slope_slip_end_long",
    "gPlayerAnim_link_normal_wait", "gPlayerAnim_link_normal_wait2waitR", "gPlayerAnim_link_normal_waitL2wait",
    "gPlayerAnim_link_normal_waitR2wait", "gPlayerAnim_link_normal_walk", "gPlayerAnim_link_normal_walk_endL",
    "gPlayerAnim_link_normal_walk_endR", "gPlayerAnim_lkt_nwait",
))

# Played both ways by the table (doors, landings, the pot pickup). These keep the
# ordinary hand route: a bare mitten hung the sword way floats above his head, which
# is worse than a sword briefly held the ordinary way.
SWORD_OR_FREE = frozenset((
    "gPlayerAnim_clink_demo_doorA_link", "gPlayerAnim_clink_demo_doorB_link", "gPlayerAnim_link_demo_doorA_link_free",
    "gPlayerAnim_link_demo_doorB_link_free", "gPlayerAnim_link_fighter_wait2waitR_long", "gPlayerAnim_link_fighter_waitL2wait_long",
    "gPlayerAnim_link_fighter_waitR2wait_long", "gPlayerAnim_link_normal_45_turn_free", "gPlayerAnim_link_normal_back_walk",
    "gPlayerAnim_link_normal_carryB_free", "gPlayerAnim_link_normal_check_end_free", "gPlayerAnim_link_normal_check_free",
    "gPlayerAnim_link_normal_check_wait_free", "gPlayerAnim_link_normal_damage_run_free", "gPlayerAnim_link_normal_defense_end_free",
    "gPlayerAnim_link_normal_defense_free", "gPlayerAnim_link_normal_defense_wait_free", "gPlayerAnim_link_normal_fall_up_free",
    "gPlayerAnim_link_normal_heavy_run_free", "gPlayerAnim_link_normal_jump_climb_hold_free", "gPlayerAnim_link_normal_jump_climb_up_free",
    "gPlayerAnim_link_normal_jump_climb_wait_free", "gPlayerAnim_link_normal_landing_free", "gPlayerAnim_link_normal_normal2fighter_free",
    "gPlayerAnim_link_normal_pull_end_free", "gPlayerAnim_link_normal_pull_start_free", "gPlayerAnim_link_normal_pulling_free",
    "gPlayerAnim_link_normal_put_free", "gPlayerAnim_link_normal_short_landing_free", "gPlayerAnim_link_normal_throw_free",
    "gPlayerAnim_sude_nwait",
))

def is_sword_animation(header: str) -> bool:
    if header in SWORD_OR_FREE:
        return False
    return header in SWORD_DRAWN or bool(SWING_ANIMATION.search(header))


def swing_target(original: list[int], joints: dict, chain: list, mitten_local, reach: float):
    """Where this hand should be for a frame of a sword animation.

    Link's shoulder sits above Kirby's cap and his arm is shorter than the drop to the
    mitten, so the hand cannot get there. The anchor is on the line from the shoulder to
    the mitten, as far down it as the arm comfortably reaches, which puts the hilt at
    his upper flank with room left for the arc; the hand then moves around that anchor
    by `reach` of Link's own shoulder-to-hand vector.
    """
    mitten = apply(chain_matrix(original, joints, chain[:2]), mitten_local)
    s = hand_origin(original, joints, chain[:3])
    h = hand_origin(original, joints, chain)
    full = sum(math.sqrt(sum(v * v for v in joints[chain[k][1]])) for k in (3, 4))
    toward = [mitten[i] - s[i] for i in range(3)]
    span = math.sqrt(sum(v * v for v in toward)) or 1.0
    anchor = tuple(s[i] + toward[i] / span * min(span, ANCHOR_FRACTION * full) for i in range(3))
    return tuple(anchor[i] + reach * (h[i] - s[i]) for i in range(3))


def unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def aim_hand(frame: list[int], joints: dict, chain: list, target, blade=None, along=None) -> None:
    """Turn the hand limb so its own x axis points from its joint at `target`.

    A hand limb's x axis continues the forearm when its rotation is zero, which is the
    direction the mitten is hung along (hand_offset), so aiming x at the target puts the
    mitten on the line to it. With `blade`, a world direction, the hand's z axis is set
    as close to it as the aim allows; a held item is turned so its blade lies along z,
    so this is what keeps a swing pointing where the animation meant.
    """
    origin = hand_origin(frame, joints, chain)
    # With `along`, the hand's x is set to that world direction outright, wherever the
    # joint landed: what hangs on it then points the way the animation meant.
    x = unit(list(along)) if along is not None else unit([target[i] - origin[i] for i in range(3)])
    if blade is not None:
        d = sum(blade[i] * x[i] for i in range(3))
        z = [blade[i] - d * x[i] for i in range(3)]
        if math.sqrt(sum(v * v for v in z)) < 1e-3:
            blade = None
        else:
            z = unit(z)
    if blade is None:
        ref = [0.0, 1.0, 0.0] if abs(x[1]) < 0.9 else [0.0, 0.0, 1.0]
        z = unit(cross(x, ref))
    y = cross(z, x)
    r_world = [[x[i], y[i], z[i]] for i in range(3)]
    r_forearm = rotation(chain_matrix(frame, joints, chain[:4]))
    r_hand = mat3_mul(transpose(r_forearm), r_world)
    hand = chain[4][1]
    frame[3 + 3 * hand: 6 + 3 * hand] = [to_short(a) for a in euler_zyx(r_hand)]


def hand_offset(archive_path: str, age: str) -> float:
    """How far along its own x axis the mitten hangs from the hand joint, in game units.

    Link's shoulder sits above Kirby's cap and his arm is shorter than the drop to the
    flank, so the joint cannot reach the mitten's place; the mitten is hung this far
    along the arm instead, measured as the shortfall in the standing pose.
    """
    with zipfile.ZipFile(archive_path) as archive:
        chains = CHAINS[age]
        joints = joint_positions(archive, age)
        _frames, blob_path = animation_blob(archive, "gPlayerAnim_link_normal_wait_free")
        frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", archive.read(blob_path), 68))
        chain = chains["left"]
        mitten = apply(chain_matrix(frame, joints, chain[:2]), MITTENS[age]["left"])
        s = hand_origin(frame, joints, chain[:3])
        full = sum(math.sqrt(sum(v * v for v in joints[chain[k][1]])) for k in (3, 4))
        span = math.sqrt(sum((mitten[i] - s[i]) ** 2 for i in range(3)))
        return max(0.0, span - ANCHOR_FRACTION * full)


def keep_hand(frame: list[int], original: list[int], joints: dict, chain: list) -> list[float]:
    """Give the hand Link's own world orientation for this frame, whatever the arm above
    it now does, and return that orientation's x axis in the root's frame.

    The hand's orientation is what the game draws a held thing by: the raised shield,
    an ocarina, a bottle, a slingshot. Aiming the hand at the mitten target turned the
    shield over. So the hand is aimed only when it holds the sword, whose blade
    direction is chosen on purpose; otherwise it keeps the way Link's animation had it,
    and the mitten, hung along the hand's x, is landed by sending the joint short of
    the target along that same x.
    """
    r_world = rotation(chain_matrix(original, joints, chain))
    r_forearm = rotation(chain_matrix(frame, joints, chain[:4]))
    r_hand = mat3_mul(transpose(r_forearm), r_world)
    hand = chain[4][1]
    frame[3 + 3 * hand: 6 + 3 * hand] = [to_short(a) for a in euler_zyx(r_hand)]
    return [r_world[i][0] for i in range(3)]


# The shield rides the right hand. While it is up (the defense animations) and while the
# sword is drawn (it is held at the side), that hand keeps Link's own orientation so the
# shield faces the way the animation meant; see keep_hand.
SHIELD_HAND = "right"
SHIELD_UP = re.compile(r"defense")
GUARD = "gPlayerAnim_link_normal_defense_wait_free"


def shield_hang(archive_path: str, age: str) -> list[float]:
    """Which way, in the hand's own frame, the shield list hangs from the joint.

    A hand that keeps Link's orientation cannot also hang its load along its x: in the
    guard that x points up and away, and a shield hung there sits over the head. So the
    shield list hangs along the direction that, in Link's guard pose, runs from the
    joint toward the flank, which comes out close to minus x. The guard is then exactly
    reachable, and the drawn-sword stance, where the hand turns, is short by a third of
    a unit at worst instead of a body height.
    """
    with zipfile.ZipFile(archive_path) as archive:
        joints = joint_positions(archive, age)
        chain = CHAINS[age][SHIELD_HAND]
        _frames, blob_path = animation_blob(archive, GUARD)
        frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", archive.read(blob_path), 68))
    mitten = apply(chain_matrix(frame, joints, chain[:2]), MITTENS[age][SHIELD_HAND])
    s = hand_origin(frame, joints, chain[:3])
    h = hand_origin(frame, joints, chain)
    target = [mitten[i] + SWORD_REACH * (h[i] - s[i]) for i in range(3)]
    toward = unit([target[i] - s[i] for i in range(3)])
    r = rotation(chain_matrix(frame, joints, chain))
    return [sum(r[k][i] * toward[k] for k in range(3)) for i in range(3)]


def shield_joint(original: list[int], joints: dict, chain: list, target, offset: float,
                 hang: list[float]):
    """Where the shield hand's joint goes so the list hung along `hang` lands on target."""
    r = rotation(chain_matrix(original, joints, chain))
    world = [sum(r[i][k] * hang[k] for k in range(3)) for i in range(3)]
    return tuple(target[i] - offset * world[i] for i in range(3))


def stub_frame(frame: list[int], joints: dict, chains: dict, mittens: dict, reach: float,
               offset: float, previous, shield: bool = False, fist: float = 0.0,
               shield_dir=None):
    """Pose both arms for one frame of an ordinary animation, hands empty.

    The mitten's target is its place on the body plus `reach` of Link's arm vector. The
    joint is sent `offset` short of that along the line from the shoulder, which the arm
    can always reach, and the hand is then aimed at the target, so the mitten hung
    `offset` along the hand lands on it. With `shield`, the shield hand keeps Link's
    orientation instead and its mitten lands wherever that hand's x points from the
    joint; the shield is big enough to make that the lesser wrong.
    """
    original = list(frame)
    result = {}
    for side in ("left", "right"):
        chain = chains[side]
        mitten = apply(chain_matrix(original, joints, chain[:2]), mittens[side])
        s = hand_origin(original, joints, chain[:3])
        h = hand_origin(original, joints, chain)
        travel = [reach * (h[i] - s[i]) for i in range(3)]
        # Link's hand goes a long way in a raise or a wave; on a body a third his size
        # that puts the mitten over the head. No further than its own radius.
        span = math.sqrt(sum(v * v for v in travel))
        if fist > 0.0 and span > 0.5 * fist:
            travel = [v * 0.5 * fist / span for v in travel]
        target = [mitten[i] + travel[i] for i in range(3)]
        # Every list hangs its load a hang BEFORE the joint along the hand's x (see
        # mitten_hands), so the joint goes a hang up from the spot and the hand's x
        # points up: the same placement the sword hand gets at rest, which Link's arm
        # reaches from a shoulder above the cap.
        up = unit(list(REST_BLADE))
        if shield and side == SHIELD_HAND and shield_dir is not None:
            joint_target = shield_joint(original, joints, chain, target, offset, shield_dir)
        else:
            joint_target = tuple(target[i] + offset * up[i] for i in range(3))
        theta, _error = solve_arm(frame, joints, chain, joint_target,
                                  start=previous.get(side) if previous else None)
        theta = [wrap(t) for t in theta]
        write_arm(frame, chain[2][1], theta)
        result[side] = theta
        if shield and side == SHIELD_HAND:
            keep_hand(frame, original, joints, chain)
        else:
            aim_hand(frame, joints, chain, target, along=up)
    return result


# Sword animations where he is standing, walking or running rather than striking.
REST_SWORD = re.compile(r"wait|walk|run|brake|landing|normal2fighter|fighter2free|fighter2long|anchor2fighter")
ATTACK_SWORD = re.compile(r"kiru|jump|rolling|power|pierce|rebound|hit|defense")
ATTACK_EASE_FRAMES = 4

# The sword lists hang the fist BEFORE the joint along the hand's x, so the blade runs
# up through the joint from a fist that sits beside the body like the other mitten. At
# rest the blade points up (REST_BLADE, body frame: y up, z forward), which puts the
# joint a hang above the fist, at the brim, where Link's arm reaches. In an attack the
# hang eases onto Link's own blade direction, so the sweep is his. SWORD_OUT keeps the
# fist outside the ball, in fist radii.
SWORD_OUT = 1.0
REST_BLADE = (0.0, 1.0, 0.15)
SWORD_RAISE = 0.0
SWORD_DROP = 0.0
SWORD_FORWARD = 0.0


def swing_frame(frame: list[int], joints: dict, chains: dict, mittens: dict,
                reach: float, offset: float, previous, fist: float = 640.0,
                rest: bool = True, frame_index: int = 0, shield_dir=None):
    """Pose both arms for one frame of a sword animation; see SWORD_OUT above."""
    original = list(frame)
    result = {}
    for side in ("left", "right"):
        chain = chains[side]
        upper = chain_matrix(original, joints, chain[:2])
        holds = side == "left"
        w = 0.0 if rest else min(1.0, frame_index / float(ATTACK_EASE_FRAMES))
        push = SWORD_OUT * fist if holds else 0.0
        mitten = apply(upper, mittens[side])
        out = unit([upper[i][2] * (1.0 if mittens[side][2] > 0 else -1.0) for i in range(3)])
        s = hand_origin(original, joints, chain[:3])
        h = hand_origin(original, joints, chain)
        target = [mitten[i] + push * out[i] + reach * (h[i] - s[i]) for i in range(3)]
        toward = unit([target[i] - s[i] for i in range(3)])
        if holds:
            r_old = rotation(chain_matrix(original, joints, chain))
            link_blade = [r_old[i][0] for i in range(3)]
            blade = unit([(1.0 - w) * REST_BLADE[i] + w * link_blade[i] for i in range(3)])
            # Fist before the joint: the joint sits a hang along the blade from the fist.
            joint_target = tuple(target[i] + offset * blade[i] for i in range(3))
            along = blade
        else:
            # The off hand holds the shield at his side: Link's own orientation for the
            # hand (keep_hand), the joint placed for the shield list's own hang.
            if shield_dir is not None:
                joint_target = shield_joint(original, joints, chain, target, offset, shield_dir)
                along = None
            else:
                up = unit(list(REST_BLADE))
                joint_target = tuple(target[i] + offset * up[i] for i in range(3))
                along = up
        theta, _error = solve_arm(frame, joints, chain, joint_target,
                                  start=previous.get(side) if previous else None)
        theta = [wrap(t) for t in theta]
        write_arm(frame, chain[2][1], theta)
        result[side] = theta
        if holds or along is not None:
            aim_hand(frame, joints, chain, target, along=along)
        else:
            keep_hand(frame, original, joints, chain)
    return result


SHEATH = 19


def rest_sheath(archive: zipfile.ZipFile) -> list[int]:
    """The sheath limb's rotation in the standing idle, the three shorts as stored."""
    _frames, blob_path = animation_blob(archive, IDLE)
    frame = struct.unpack_from(f"<{SHORTS_PER_FRAME}h", archive.read(blob_path), 68)
    return list(frame[3 + 3 * SHEATH:6 + 3 * SHEATH])


def rest_feet(archive: zipfile.ZipFile, age: str, joints: dict) -> dict:
    """Where each foot joint sits in the standing idle, in the root's frame."""
    _frames, blob_path = animation_blob(archive, "gPlayerAnim_link_normal_wait_free")
    frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", archive.read(blob_path), 68))
    return {side: hand_origin(frame, joints, chain) for side, chain in LEG_CHAINS[age].items()}


def leg_frame(frame: list[int], joints: dict, legs: dict, rest: dict, reach: float, previous):
    """Pose both legs for one frame: each foot joint goes to its idle place plus `reach`
    of where Link's foot went, thigh and shin solved, and the foot keeps Link's world
    rotation so the shoe pitches through the step as his boot does."""
    original = list(frame)
    result = {}
    for side, chain in legs.items():
        foot = hand_origin(original, joints, chain)
        target = tuple(rest[side][i] + reach * (foot[i] - rest[side][i]) for i in range(3))
        theta, _error = solve_arm(frame, joints, chain, target,
                                  start=previous.get(side) if previous else None, pivot=3)
        theta = [wrap(t) for t in theta]
        write_arm(frame, chain[3][1], theta, elbow=chain[4][1])
        result[side] = theta
        r_old = rotation(chain_matrix(original, joints, chain))
        r_shin = rotation(chain_matrix(frame, joints, chain[:5]))
        r_foot = mat3_mul(transpose(r_shin), r_old)
        index = chain[5][1]
        frame[3 + 3 * index: 6 + 3 * index] = [to_short(a) for a in euler_zyx(r_foot)]
    return result


# About the radius of a pot, in the player's model units, so the mittens meet its
# sides rather than pass through its middle.
POT_RADIUS = 700.0


def carry_frame(frame: list[int], joints: dict, wanted: dict, chains: dict,
                offset: float, previous):
    """Pose both arms for one frame of a carry animation.

    The pot sits at the midpoint of the two hand joints, so the joints are spread
    symmetrically about where the lowered shoulders would have put that midpoint, each
    `offset` plus a pot radius away along the line between Link's own hands, and both
    hands aim at the pot. The hung mittens then land on the pot's sides, and the
    midpoint, and so the pot, stays where it was approved. Link's arms may not reach
    that far apart, in which case the mittens sit a little inside the pot instead.
    """
    original = list(frame)
    middle = pot_position(original, wanted, chains)
    h_left = hand_origin(original, joints, chains["left"])
    h_right = hand_origin(original, joints, chains["right"])
    spread = unit([h_left[i] - h_right[i] for i in range(3)])
    # Scale the spread down until both joints are within reach, so the shortfall is
    # shared and the midpoint, which is the pot, does not wander.
    full = {side: sum(math.sqrt(sum(v * v for v in joints[chains[side][k][1]])) for k in (3, 4))
            for side in chains}
    span = offset + POT_RADIUS
    for scale in [1.0 - 0.05 * n for n in range(19)]:
        ok = True
        for side, sign in (("left", 1.0), ("right", -1.0)):
            shoulder = hand_origin(original, joints, chains[side][:3])
            jt = [middle[i] + sign * scale * span * spread[i] for i in range(3)]
            if math.sqrt(sum((jt[i] - shoulder[i]) ** 2 for i in range(3))) > 0.95 * full[side]:
                ok = False
        if ok:
            span *= scale
            break
    result = {}
    for side, sign in (("left", 1.0), ("right", -1.0)):
        chain = chains[side]
        joint_target = tuple(middle[i] + sign * span * spread[i] for i in range(3))
        start = previous.get(side) if previous else None
        theta, error = solve_arm(frame, joints, chain, joint_target, start=start)
        if error > 150.0:
            # Out of reach after all; take the lowered hand's own place instead, which
            # is the placement that was approved, so the pot stays put.
            theta, _error = solve_arm(frame, joints, chain,
                                      hand_origin(original, wanted, chain), start=start)
        theta = [wrap(t) for t in theta]
        write_arm(frame, chain[2][1], theta)
        result[side] = theta
        # The mitten hangs a hang BEFORE the joint along the hand's x, so the hand's x
        # points away from the pot and the mitten lands on the pot's side.
        j = hand_origin(frame, joints, chain)
        aim_hand(frame, joints, chain, middle, along=unit([j[i] - middle[i] for i in range(3)]))
    return result


def swing_animations(archive_path: str, age: str, reach: float, log=print) -> dict[str, bytes]:
    """Rewritten data blobs for every sword animation, keyed by archive path."""
    out = {}
    with zipfile.ZipFile(archive_path) as archive:
        chains = CHAINS[age]
        joints = joint_positions(archive, age)
        mittens = MITTENS[age]
        headers = sorted(n.split("/")[-1] for n in archive.namelist()
                         if n.startswith("objects/gameplay_keep/gPlayerAnim_") and SWING_ANIMATION.search(n))
        total = 0
        for header in headers:
            frames, blob_path = animation_blob(archive, header)
            data = bytearray(archive.read(blob_path))
            if struct.unpack_from("<I", data, 64)[0] != frames * SHORTS_PER_FRAME:
                log(f"  note: {header} has an unexpected frame size; left alone")
                continue
            previous = None
            for f in range(frames):
                at = 68 + f * SHORTS_PER_FRAME * 2
                frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", data, at))
                previous = swing_frame(frame, joints, chains, mittens, reach, previous)
                struct.pack_into(f"<{SHORTS_PER_FRAME}h", data, at, *frame)
            out[blob_path] = bytes(data)
            total += frames
        log(f"  {len(out)} sword animations, {total} frames, hands at {reach:.2f} of Link's reach")
    return out


def _swing_check(archive_path: str, age: str, reach: float, names=()) -> None:
    """Position miss and hand-rotation error after the swing solve, per animation."""
    with zipfile.ZipFile(archive_path) as archive:
        chains = CHAINS[age]
        joints = joint_positions(archive, age)
        mittens = MITTENS[age]
        headers = list(names) or sorted(
            n.split("/")[-1] for n in archive.namelist()
            if n.startswith("objects/gameplay_keep/gPlayerAnim_") and SWING_ANIMATION.search(n))
        for header in headers:
            frames, blob_path = animation_blob(archive, header)
            data = archive.read(blob_path)
            worst_pos, worst_rot, previous = 0.0, 0.0, None
            for f in range(frames):
                at = 68 + f * SHORTS_PER_FRAME * 2
                frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", data, at))
                original = list(frame)
                previous = swing_frame(frame, joints, chains, mittens, reach, previous)
                for side in ("left", "right"):
                    chain = chains[side]
                    target = swing_target(original, joints, chain, mittens[side], reach)
                    got = hand_origin(frame, joints, chain)
                    worst_pos = max(worst_pos, math.sqrt(sum((got[i] - target[i]) ** 2 for i in range(3))))
                    r_old = rotation(chain_matrix(original, joints, chain))
                    r_new = rotation(chain_matrix(frame, joints, chain))
                    worst_rot = max(worst_rot, max(abs(r_old[i][j] - r_new[i][j])
                                                   for i in range(3) for j in range(3)))
            print(f"  {header:52s} {frames:3d} frames  hand miss {worst_pos / 1000.0:.3f}"
                  f"  rotation error {worst_rot:.4f}")


# The riding animations, which also get moved onto the saddle. Only the adult rides.
RIDE_PREFIX = "gPlayerAnim_link_uma"

# The pause screen's Equipment page plays no animation: Player_DrawPause copies one of
# these fixed joint tables into the skeleton. Each is an Array resource of 24 Vec3s,
# root translation first, then the 21 limb rotations, two spare; every element is
# written as two u32 (type 4, count 3) and three s16, 14 bytes, from offset 72.
PAUSE_TABLES = {
    "adult": ("gLinkPauseAdultJointTable", "gLinkPauseAdultShieldJointTable",
              "gLinkPauseAdultBgsJointTable"),
    "child": ("gLinkPauseChildJointTable", "gLinkPauseChildDekuShieldJointTable"),
}
PAUSE_ENTRY = 14
PAUSE_FIRST = 80

# Bump when the solve changes, so a cached result from older code is not reused.
CODE_VERSION = 31


def all_animations(archive_path: str, age: str, drop: int, forward: int, reach: float,
                   ride_lift: int, ride_forward: int, carry: bool = True, swing: bool = True,
                   ride: bool = True, cache_dir: str | None = None, log=print,
                   fist: float = 640.0) -> dict[str, bytes]:
    """Every player animation, rewritten so the arms are Kirby's and not Link's.

    The carry set holds the pot at his middle (`carry_animations`); the riding set is
    shifted onto the saddle; everything else, riding included, gets the stubby reach
    around the mittens that the sword set gets (`swing_frame`). Returned keyed by
    archive path. About seven minutes per age in pure Python, so the result is cached
    on disk against every number that went into it.
    """
    import hashlib
    import json
    import os
    import pickle

    key = json.dumps({"age": age, "drop": drop, "forward": forward, "reach": reach, "fist": fist,
                      "foot_reach": FOOT_REACH, "sword_out": SWORD_OUT, "sword_raise": SWORD_RAISE, "sword_drop": SWORD_DROP, "sword_forward": SWORD_FORWARD,
                      "anchor": ANCHOR_FRACTION, "ride_lift": ride_lift,
                      "ride_forward": ride_forward, "carry": carry, "swing": swing,
                      "ride": ride, "mittens": MITTENS[age], "code": CODE_VERSION,
                      "archive": os.path.getsize(archive_path)}, sort_keys=True)
    cache = None
    if cache_dir:
        cache = os.path.join(cache_dir, f"anim_cache_{age}_{hashlib.sha1(key.encode()).hexdigest()[:10]}.pkl")
        if os.path.exists(cache):
            with open(cache, "rb") as handle:
                out = pickle.load(handle)
            log(f"  {len(out)} animations from the cache at {os.path.basename(cache)}")
            return out

    out: dict[str, bytes] = {}
    offset = hand_offset(archive_path, age)
    with zipfile.ZipFile(archive_path) as archive:
        chains = CHAINS[age]
        joints = joint_positions(archive, age)
        wanted = lowered(joints, drop, forward)
        mittens = MITTENS[age]
        headers = sorted(n.split("/")[-1] for n in archive.namelist()
                         if n.startswith("objects/gameplay_keep/gPlayerAnim_"))
        legs = LEG_CHAINS[age]
        feet = rest_feet(archive, age, joints)
        sheath_rest = rest_sheath(archive)
        shield_dir = shield_hang(archive_path, age)
        counts = {"carry": 0, "ride": 0, "swing": 0}
        log(f"  mittens hang {offset / 1000.0:.2f} along the hand from the joint")
        # Blender block-buffers stdout when it is redirected, so a plain print says
        # nothing for ten minutes; flush so a watcher can see it moving.
        import time
        started = time.time()
        for header in headers:
            try:
                frames, blob_path = animation_blob(archive, header)
            except (ValueError, KeyError):
                continue
            data = bytearray(archive.read(blob_path))
            if struct.unpack_from("<I", data, 64)[0] != frames * SHORTS_PER_FRAME:
                continue
            is_ride = ride and header.startswith(RIDE_PREFIX)
            is_carry = carry and header in CARRY_ANIMATIONS
            if not (is_ride or is_carry or swing):
                continue
            previous = None
            previous_legs = None
            for f in range(frames):
                at = 68 + f * SHORTS_PER_FRAME * 2
                frame = list(struct.unpack_from(f"<{SHORTS_PER_FRAME}h", data, at))
                # The sheath limb idles at one rotation and swings through 284 of the
                # animations; Link's set sits close enough to the joint for that to pass
                # as a shrug of the scabbard, but Kirby's sits a ball away and swings
                # out beside him. The torso limb never rotates (a degree, at most), so
                # holding the sheath at the idle's rotation pins the set to the ball.
                frame[3 + 3 * SHEATH:6 + 3 * SHEATH] = sheath_rest
                if not header.startswith(RIDE_PREFIX) and FOOT_REACH < 1.0:
                    previous_legs = leg_frame(frame, joints, legs, feet, FOOT_REACH, previous_legs)
                if is_ride:
                    frame[1] = max(-32768, min(32767, frame[1] + ride_lift))
                    frame[2] = max(-32768, min(32767, frame[2] - ride_forward))
                if is_carry:
                    previous = carry_frame(frame, joints, wanted, chains, offset, previous)
                elif swing and is_sword_animation(header):
                    previous = swing_frame(frame, joints, chains, mittens, SWORD_REACH, offset, previous,
                                           fist=fist, rest=not ATTACK_SWORD.search(header),
                                           frame_index=f, shield_dir=shield_dir)
                elif swing:
                    previous = stub_frame(frame, joints, chains, mittens, reach, offset, previous,
                                          shield=bool(SHIELD_UP.search(header)), fist=fist,
                                          shield_dir=shield_dir)
                struct.pack_into(f"<{SHORTS_PER_FRAME}h", data, at, *frame)
            out[blob_path] = bytes(data)
            counts["carry" if is_carry else "ride" if is_ride else "swing"] += 1
            if len(out) % 50 == 0:
                print(f"  ... {len(out)} of {len(headers)} animations, {time.time() - started:.0f} s",
                      flush=True)
        log(f"  {len(out)} animations posed: {counts['carry']} carrying, {counts['ride']} riding, "
            f"{counts['swing']} with the stubby reach")
        # The pause pose, drawn with the sword out and the shield in hand, gets the same
        # treatment as a frame of the drawn-sword rest, or the hung mittens and the moved
        # sheath sit a hang away from where they belong on that one page.
        for name in PAUSE_TABLES[age]:
            path = "objects/gameplay_keep/" + name
            try:
                data = bytearray(archive.read(path))
            except KeyError:
                continue
            frame = [0] * SHORTS_PER_FRAME
            for e in range(22):
                frame[3 * e:3 * e + 3] = struct.unpack_from("<3h", data, PAUSE_FIRST + PAUSE_ENTRY * e)
            frame[3 + 3 * SHEATH:6 + 3 * SHEATH] = sheath_rest
            if FOOT_REACH < 1.0:
                leg_frame(frame, joints, legs, feet, FOOT_REACH, None)
            if swing:
                swing_frame(frame, joints, chains, mittens, SWORD_REACH, offset, None,
                            fist=fist, rest=True, frame_index=0, shield_dir=shield_dir)
            for e in range(22):
                struct.pack_into("<3h", data, PAUSE_FIRST + PAUSE_ENTRY * e, *frame[3 * e:3 * e + 3])
            out[path] = bytes(data)
        log(f"  {len(PAUSE_TABLES[age])} pause poses posed the same way")
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, "wb") as handle:
            pickle.dump(out, handle)
    return out


if __name__ == "__main__":
    if len(sys.argv) > 4 and sys.argv[4] == "swing":
        _swing_check(sys.argv[1], sys.argv[2], float(sys.argv[5]), sys.argv[6:])
    else:
        _self_check(sys.argv[1], sys.argv[2], int(sys.argv[3]))
