"""Table to string grid, shared by the PDF, XLSX and CSV renderers so a layout decision is made once.

表格 → 字符串网格。PDF、XLSX、CSV 三种格式共用这一份，版式只在这里决定一次。

"一个决策只能有一处实现"（docs/zh-CN/plan.md 旧教训第 7 条）：值格里粘不粘单位、箭头放哪、参考范围带不带 `&`，
如果 PDF 与 XLSX 各写一遍，迟早一边改了一边没改，而真值只有一份——那时真值就是错的。
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
            # 以数字开头的单位（10^9/L）直接粘上去会变成 7.7310^9/L——连人都分不清数在哪断，
            # 越过了可读性下界。真实单子在这里印空格或 ×。
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
        # 美式化验单：正常值印在 In Range 列，异常值印在 Out Of Range 列，另一列空着
        abnormal = cells.status in ("high", "low")
        if (role == "result_out") == abnormal:
            text = cells.value
            if cells.unit_at == "value" and cells.unit:
                text += (" " if cells.unit[0].isdigit() else "") + cells.unit
            return text
        return ""
    return ""


def table_grid(doc: Doc, table: Table) -> tuple[list[list[str]], list[list[str]], list[int | None]]:
    """(表头行, 数据行, 每个数据行对应的印刷行下标)。

    双栏版式（列组本身是两套"名称/结果/…"并排）把读数对半分到左右两栏；
    这时一个网格行对应两个印刷行，第三个返回值记左栏那个（右栏的行号按顺序可推）。
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
