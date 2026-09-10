"""Package entry that re-exports the matching comparison, role policy and adjudication API."""

from __future__ import annotations

from app.nav.match.adjudicate import (Remedy, SEPARATOR_FIELDS, adjudicate,
                                      adjudicate_variant, consultation_order,
                                      remedy, remedy_code, remedy_detail)
from app.nav.match.compare import (COMPARABLE_FIELDS, FieldComparison, Verdict,
                                   compare_all, compare_designation,
                                   compare_field)
from app.nav.match.roles import (CONTRADICTING, ROLE_TABLE, FieldRole, Role,
                                 eliminates, fields_with_role, role_of, why)

__all__ = [
    "adjudicate",
    "adjudicate_variant",
    "Remedy",
    "remedy",
    "remedy_code",
    "remedy_detail",
    "SEPARATOR_FIELDS",
    "consultation_order",
    "compare_all",
    "compare_field",
    "compare_designation",
    "FieldComparison",
    "Verdict",
    "COMPARABLE_FIELDS",
    "ROLE_TABLE",
    "Role",
    "FieldRole",
    "role_of",
    "eliminates",
    "fields_with_role",
    "why",
    "CONTRADICTING",
]
