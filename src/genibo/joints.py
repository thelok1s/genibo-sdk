"""Joint naming and calibration: maps friendly joint names to servo IDs and raw values.

The legacy ``CGenibo_Bt`` had no built-in Genibo calibration: every application
called ``set_param(axis, id, dir, limitUp, limitDn, center, mechMove, degree)``
itself, and an axis without parameters silently encoded every angle as raw 0.
This module makes the calibration explicit and refuses angle commands for joints
that have none.

Nothing in the repository documents the real Genibo servo IDs or calibration.
The built-in profile below is assembled from two legacy sources and is marked
``verified=False`` everywhere:

* joint names / IDs: the motor ID constants of ``Ojw.CGenibo`` (TCP class);
* 1024 counts per 333.3 degrees, centre 512: the comment in ``CalcEvd2Angle``
  ("1024:333.3 = pulse:angle").

Confirm both on hardware (small moves, one joint at a time) before trusting it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from genibo.errors import JointError
from genibo.protocol.constants import CGENIBO_MOTOR_IDS
from genibo.protocol.convert import (
    ServoCalibration,
    angle_to_raw,
    clip_raw_legacy,
    raw_limits,
    raw_to_angle,
)

#: Raw position range accepted by the encoder before the legacy bit-14 masking applies.
RAW_POSITION_MAX = 0x3FFF


@dataclass(frozen=True)
class Joint:
    name: str
    servo_id: int
    calibration: ServoCalibration | None = None
    #: Hard raw bounds checked before anything is sent (independent of angle limits).
    raw_min: int = 0
    raw_max: int = 1023
    #: True only after the mapping and calibration were confirmed on a physical robot.
    verified: bool = False
    note: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.servo_id <= 0xFD:
            raise JointError(f"joint {self.name!r}: servo id {self.servo_id} outside 0..253")
        if not 0 <= self.raw_min <= self.raw_max <= RAW_POSITION_MAX:
            raise JointError(f"joint {self.name!r}: invalid raw bounds {self.raw_min}..{self.raw_max}")

    def require_calibration(self) -> ServoCalibration:
        if self.calibration is None:
            raise JointError(
                f"joint {self.name!r} has no calibration; use raw values or add [joints.{self.name}] "
                "center/mech_move/degree to genibo.toml"
            )
        return self.calibration

    def angle_limits(self) -> tuple[float | None, float | None]:
        cal = self.calibration
        if cal is None:
            return None, None
        lo = cal.limit_dn if cal.limit_dn != 0 else None
        hi = cal.limit_up if cal.limit_up != 0 else None
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        return lo, hi

    def to_raw(self, angle: float, *, clip: bool = False, speed_mode: bool = False) -> int:
        """Angle (degrees) -> raw servo value, exactly as the legacy ``set_cmd_angle``.

        Out-of-limit targets raise :class:`JointError` unless ``clip=True``, which
        reproduces the legacy silent clipping.
        """
        cal = self.require_calibration()
        raw = angle_to_raw(cal, angle, speed_mode=speed_mode)
        if speed_mode:
            return raw
        lo_a, hi_a = self.angle_limits()
        within = (lo_a is None or angle >= lo_a) and (hi_a is None or angle <= hi_a)
        if not within and not clip:
            raise JointError(
                f"joint {self.name!r}: {angle} deg is outside the limits "
                f"{lo_a if lo_a is not None else '-inf'}..{hi_a if hi_a is not None else '+inf'} deg"
            )
        # Always apply the legacy limit step: it is the identity for in-limit
        # targets, and it is exactly what ``set_cmd_angle`` did when clip=True.
        raw = clip_raw_legacy(cal, raw)
        self.check_raw(raw, source=f"{angle} deg")
        return raw

    def check_raw(self, raw: int, *, source: str = "") -> int:
        if not self.raw_min <= raw <= self.raw_max:
            what = f" ({source})" if source else ""
            raise JointError(
                f"joint {self.name!r}: raw position {raw}{what} outside the allowed range "
                f"{self.raw_min}..{self.raw_max}"
            )
        return raw

    def to_angle(self, raw: int, *, speed_mode: bool = False) -> float:
        return raw_to_angle(self.require_calibration(), raw, speed_mode=speed_mode)

    def raw_limits(self) -> tuple[int, int]:
        cal = self.require_calibration()
        lo, hi = raw_limits(cal)
        return max(lo, self.raw_min), min(hi, self.raw_max)


@dataclass
class JointMap(Mapping[str, Joint]):
    """Ordered name -> :class:`Joint` table (one per project / robot)."""

    joints: dict[str, Joint] = field(default_factory=dict)
    profile: str = "custom"

    def __getitem__(self, key: str) -> Joint:
        try:
            return self.joints[key]
        except KeyError:
            known = ", ".join(self.joints) or "(none)"
            raise JointError(f"unknown joint {key!r}; known joints: {known}") from None

    def __iter__(self) -> Iterator[str]:
        return iter(self.joints)

    def __len__(self) -> int:
        return len(self.joints)

    def by_servo_id(self, servo_id: int) -> Joint | None:
        for j in self.joints.values():
            if j.servo_id == servo_id:
                return j
        return None

    def resolve(self, key: str | int) -> Joint:
        """Look up by name, or by servo ID (ints) for joints not in the table."""
        if isinstance(key, int):
            j = self.by_servo_id(key)
            return j if j is not None else Joint(f"servo{key}", key)
        return self[key]

    @property
    def all_verified(self) -> bool:
        return all(j.verified for j in self.joints.values())

    def with_overrides(self, overrides: Mapping[str, Mapping[str, Any]]) -> JointMap:
        """Return a copy with per-joint settings from ``genibo.toml`` ``[joints.<name>]`` tables."""
        out = dict(self.joints)
        for name, cfg in overrides.items():
            base = out.get(name)
            out[name] = joint_from_config(name, cfg, base)
        return JointMap(out, profile=self.profile + "+overrides" if overrides else self.profile)

    def to_config(self) -> dict[str, dict[str, Any]]:
        return {name: joint_to_config(j) for name, j in self.joints.items()}


_CAL_KEYS = ("center", "mech_move", "degree", "direction", "limit_up", "limit_dn")


def joint_from_config(name: str, cfg: Mapping[str, Any], base: Joint | None = None) -> Joint:
    unknown = set(cfg) - {"servo_id", "raw_min", "raw_max", "verified", "note", *_CAL_KEYS}
    if unknown:
        raise JointError(f"[joints.{name}] has unknown keys: {', '.join(sorted(unknown))}")
    cal = base.calibration if base else None
    cal_cfg = {k: cfg[k] for k in _CAL_KEYS if k in cfg}
    if cal_cfg:
        if cal is not None:
            cal = replace(cal, **cal_cfg)
        else:
            missing = {"center", "mech_move", "degree"} - set(cal_cfg)
            if missing:
                raise JointError(
                    f"[joints.{name}] needs {', '.join(sorted(missing))} to define a calibration"
                )
            cal = ServoCalibration(**cal_cfg)
    servo_id = cfg.get("servo_id", base.servo_id if base else None)
    if servo_id is None:
        raise JointError(f"[joints.{name}] needs servo_id")
    return Joint(
        name=name,
        servo_id=int(servo_id),
        calibration=cal,
        raw_min=int(cfg.get("raw_min", base.raw_min if base else 0)),
        raw_max=int(cfg.get("raw_max", base.raw_max if base else 1023)),
        verified=bool(cfg.get("verified", False)),  # overriding never inherits "verified"
        note=str(cfg.get("note", base.note if base else "")),
    )


def joint_to_config(j: Joint) -> dict[str, Any]:
    d: dict[str, Any] = {
        "servo_id": j.servo_id,
        "raw_min": j.raw_min,
        "raw_max": j.raw_max,
        "verified": j.verified,
    }
    if j.calibration is not None:
        c = j.calibration
        d.update(
            center=c.center,
            mech_move=c.mech_move,
            degree=c.degree,
            direction=c.direction,
            limit_up=c.limit_up,
            limit_dn=c.limit_dn,
        )
    if j.note:
        d["note"] = j.note
    return d


#: Parameters from the legacy comment "1024:333.3 = pulse:angle" with centre 512.
LEGACY_COMMENT_CALIBRATION = ServoCalibration(center=512, mech_move=1024.0, degree=333.3)


def legacy_cgenibo_profile() -> JointMap:
    """Joint names/IDs from ``Ojw.CGenibo`` with the legacy-comment calibration. UNVERIFIED."""
    note = "ID from OpenJigWare CGenibo constants; calibration from a code comment; not hardware-verified"
    return JointMap(
        {
            name: Joint(name, sid, LEGACY_COMMENT_CALIBRATION, note=note)
            for name, sid in CGENIBO_MOTOR_IDS.items()
        },
        profile="legacy-cgenibo (unverified)",
    )


def raw_only_profile() -> JointMap:
    """Same names and IDs, but no calibration: only raw values can be commanded."""
    note = "ID from OpenJigWare CGenibo constants; no calibration"
    return JointMap(
        {name: Joint(name, sid, None, note=note) for name, sid in CGENIBO_MOTOR_IDS.items()},
        profile="legacy-cgenibo-raw (unverified)",
    )


PROFILES = {
    "legacy-cgenibo": legacy_cgenibo_profile,
    "legacy-cgenibo-raw": raw_only_profile,
    "empty": lambda: JointMap({}, profile="empty"),
}


def load_profile(name: str) -> JointMap:
    try:
        return PROFILES[name]()
    except KeyError:
        raise JointError(f"unknown joint profile {name!r}; available: {', '.join(PROFILES)}") from None
