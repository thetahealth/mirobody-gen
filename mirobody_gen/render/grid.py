"""Table to string grid, shared by the PDF, XLSX and CSV renderers so a layout decision is made once.

"One decision, one implementation" (docs/zh-CN/plan.md, lesson 7): whether a unit is glued to its value,
where the flag arrow goes, whether a reference range carries an `&` -- if PDF and XLSX each wrote this
out separately, one would eventually be updated and the other not, and ground truth is single-sourced, so
that drift would make it wrong.
"""

from __future__ import annotations

from ..document import Cells, Doc, Table


def cell_text(cells: Cells, role: str, paren: tuple[str, str] = ("(", ")")) -> str:
    if cells.raw is not None:
        return cells.raw.get(role, "")
    if role == "name":
        return cells.name
    if role == "abbr":
        return cells.abbr
    if role == "result":
        text = cells.value
        if cells.unit_at == "value" and cells.unit:
            # A unit starting with a digit (10^9/L) glued straight on reads as 7.7310^9/L -- even a
            # human can't tell where the number ends, past the readability floor. Real slips print a
            # space or a multiplication sign here.
            sep = "\n" if cells.unit_newline else (" " if cells.unit[0].isdigit() else "")
            text += sep + cells.unit
        if cells.flag:
            if cells.flag_at == "glued":
                text += cells.flag
            elif cells.flag_at == "spaced":
                text += " " + cells.flag
            elif cells.flag_at == "paren":
                text += " " + paren[0] + cells.flag + paren[1]
        return text
    if role == "flag":
        return cells.flag if cells.flag_at == "column" else ""
    if role == "unit":
        return cells.unit if cells.unit_at == "column" else ""
    if role == "reference":
        text = cells.range
        if cells.unit and cells.unit_at == "reference":
            text = f"{text}{chr(10) if cells.unit_newline else ' '}{cells.unit}".strip()
        elif cells.unit and cells.unit_at == "reference_amp":
            text = f"{text}&{cells.unit}" if text else cells.unit
        return text
    if role == "previous":
        return cells.previous
    if role == "category":
        return cells.category
    if role in ("result_in", "result_out"):
        # US-style lab slip: normal values print in the In Range column, abnormal ones in Out Of Range,
        # the other column left blank
        abnormal = cells.status in ("high", "low")
        if (role == "result_out") == abnormal:
            text = cells.value
            if cells.unit_at == "value" and cells.unit:
                text += (" " if cells.unit[0].isdigit() else "") + cells.unit
            return text
        return ""
    return ""


def table_grid(doc: Doc, table: Table) -> tuple[list[list[str]], list[list[str]], list[int | None]]:
    """(header rows, data rows, each data row's printed-row index).

    A two-up layout (a column group that is two "name/result/..." sets side by side) splits readings into
    left and right halves; a grid row then corresponds to two printed rows, and the third return value
    records the left one (the right one's index follows in order).
    """
    f = doc.family
    paren = ("（", "）") if f.paren_style == "fullwidth" else ("(", ")")
    columns = table.columns
    two_up = columns.count("name") > 1
    half = len(columns) // 2 if two_up else len(columns)
    body: list[list[str]] = []
    owners: list[int | None] = []

    def one(cells: Cells, roles: list[str], seq: int) -> list[str]:
        out = []
        for role in roles:
            if role == "seq":
                out.append(str(seq) if cells.printed is not None else (cells.raw or {}).get("seq", ""))
            elif role == "lab":
                out.append(f.lab_code if cells.printed is not None else (cells.raw or {}).get("lab", ""))
            else:
                out.append(cell_text(cells, role, paren))
        return out

    if not two_up:
        seq = 0
        for cells in table.rows:
            if cells.printed is not None:
                seq += 1
            body.append(one(cells, columns, seq))
            owners.append(cells.printed)
        return table.headers, body, owners

    left_roles, right_roles = columns[:half], columns[half:]
    lead = [c for c in table.rows if c.printed is None]
    rows = [c for c in table.rows if c.printed is not None]
    mid = (len(rows) + 1) // 2
    for cells in lead:
        body.append(one(cells, left_roles, 0) + [""] * len(right_roles))
        owners.append(None)
    for i in range(mid):
        left = one(rows[i], left_roles, i + 1)
        right = one(rows[mid + i], right_roles, mid + i + 1) if mid + i < len(rows) else [""] * len(right_roles)
        body.append(left + right)
        owners.append(rows[i].printed)
    return table.headers, body, owners
