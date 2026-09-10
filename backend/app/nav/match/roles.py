"""Role table defining how each car field can eliminate or separate folder match candidates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.nav.match.compare import Verdict


class Role(str, Enum):
    """Enumeration of field roles: gate, corroborator, separator and evidence only."""
    GATE = "gate"
    CORROBORATOR = "corroborator"
    SEPARATOR = "separator"
    EVIDENCE_ONLY = "evidence_only"


@dataclass(frozen=True)
class FieldRole:
    """Table row pairing a field name with its matching role and the reason for that role."""
    field: str
    role: Role
    why: str


CONTRADICTING = (Verdict.DISAGREE, Verdict.PREFIX_ONLY)


def eliminates(role: Role, verdict: Verdict) -> bool:
    """Returns whether a verdict eliminates a candidate under a role; gates also reject one-sided silence."""
    if role is Role.GATE:
        return verdict in CONTRADICTING or verdict is Verdict.ONE_SILENT
    if role is Role.CORROBORATOR:
        return verdict in CONTRADICTING
    return False


_SETTLED: tuple[FieldRole, ...] = (
    FieldRole("designation", Role.GATE,
              "Brand+model+trim as one ordered name. PREFIX_ONLY is a "
              "contradiction here: 'COROLLA' is a prefix of 'COROLLA CROSS', "
              "and accepting that pointed 1,829 Corolla Cross cars at a "
              "Corolla spec sheet."),

    FieldRole("model_year", Role.CORROBORATOR,
              "A 2022 MG ZS matched a 2026 folder in production. When both "
              "sides state a year and the years differ, it is a different "
              "car. CORROBORATOR not GATE because 13 of the 130 real "
              "folders state no year at all, and silence must not reject them."),

    FieldRole("brand", Role.CORROBORATOR,
              "Contained inside `designation` already; kept separately so a "
              "brand clash is reported as its own sentence to the reviewer."),

    FieldRole("exterior", Role.SEPARATOR,
              "Colour distinguishes VARIANTS of one model, not models. It "
              "must never eliminate: the feed omits colour on some rows, and "
              "'MG ZS' with no colour is still an MG ZS. Among survivors it "
              "is the primary discriminator — the 5 MG ZS colour folders are "
              "separated by exactly this."),

    FieldRole("interior", Role.SEPARATOR,
              "As exterior. Used second, when exterior alone leaves a tie."),

    FieldRole("vin_tag", Role.EVIDENCE_ONLY,
              "THE VIN IN A FOLDER NAME IS A REFERENCE TAG, NOT A KEY. One "
              "folder holds one spec-and-colour combination shared by every "
              "physical unit of that spec; whichever VIN was to hand got "
              "written on it. 129 unique tags across 130 folders confirms "
              "this. So a VIN must never eliminate a candidate — a mismatch "
              "means nothing at all."),
)


_OPEN: tuple[FieldRole, ...] = (
    FieldRole("trim", Role.CORROBORATOR,
              "'MG-ZS-STANDARD' matched the 'MG-ZS-COMFORT' folder in production. "
              "Different trim means different equipment and a different spec sheet. "
              "Largely belt-and-braces since trim is already inside `designation`, "
              "but kept so the reviewer is told 'trim: STANDARD vs COMFORT' rather "
              "than the vaguer 'designation differs'."),

    FieldRole("fuel", Role.CORROBORATOR,
              "A PHEV and an HEV are different cars with different sheets. NOT a "
              "GATE: the export writes fuel three ways ('EV' / 'Electric' / "
              "'Plug_in_hybrid_Electric'), so until lexicon rows cover every "
              "spelling, a stricter role would reject correct folders over wording."),

    FieldRole("engine_l", Role.CORROBORATOR,
              "A Coaster 4.0 matched a 4.2 folder in production. Compared as "
              "numbers, so 2.4 and 2.40 agree. Must NOT be a GATE: an electric car "
              "states no displacement at all, and GATE would reject every EV whose "
              "folder is silent."),

    FieldRole("transmission", Role.CORROBORATOR,
              "Not a tie-breaker — a real discriminator. The library holds "
              "'TOYOTA-HILUX-GLX-PV-2.7-MT-26' as its own folder, so a manual and "
              "an automatic of the same trim and engine are separate cars with "
              "separate sheets. As SEPARATOR they would both survive and be told "
              "apart only if their colours happened to differ."),
)


ROLE_TABLE: tuple[FieldRole, ...] = _SETTLED + _OPEN

_BY_FIELD = {r.field: r for r in ROLE_TABLE}


def role_of(field: str) -> Role:
    """Returns the table role for a field, defaulting to EVIDENCE_ONLY for fields not listed."""
    entry = _BY_FIELD.get(field)
    return entry.role if entry else Role.EVIDENCE_ONLY


def why(field: str) -> str:
    """Returns the stated reason for a field's role, or 'not part of identity' for unlisted fields."""
    entry = _BY_FIELD.get(field)
    return entry.why if entry else "not part of identity"


def fields_with_role(role: Role) -> tuple[str, ...]:
    """Returns the names of all table fields assigned the given role, in table order."""
    return tuple(r.field for r in ROLE_TABLE if r.role is role)
