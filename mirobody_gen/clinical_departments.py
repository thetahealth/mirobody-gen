"""Clinical profile: departments (`build --clinical`).

A default build prints a department drawn from the template vocabulary on lab slips, checkup books
and handwritten clinic booklets, so clinician review saw a kidney patient's clinic slip filed under
the checkup centre and checkup books filed under endocrinology. In clinical mode a document prints
the visit's explicit department (`Encounter.department`), the checkup centre for a visit at a
checkup centre, and otherwise no department. Nothing is inferred from the institution, the person
or the condition. The random draw is still made, so the document stream advances as by default.
"""

from __future__ import annotations

from .model import Encounter

#: The checkup centre as the template vocabulary (`templates.json` departments) spells it.
CHECKUP_CENTRE = {"zh": "体检中心", "en": "Health Check-up Centre"}


def printed(encounter: Encounter, lang: str) -> str:
    """The department a document of this visit prints; "" for none."""
    if encounter.department and encounter.department.strip():
        return encounter.department
    return CHECKUP_CENTRE.get(lang, "") if encounter.exam_location == "checkup-center" else ""
