"""Printable PDF generation for CAF pick sheets."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus.flowables import Flowable
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PAGE_SIZE = landscape(A4)
TABLE_FONT_SIZE = 8
TABLE_LEADING = 9.5
HEADER_BLUE = colors.HexColor("#1F4E78")
GRID_BLUE = colors.HexColor("#D9E2F3")
ROW_BLUE = colors.HexColor("#F7F9FC")
ISSUE_COLOURS = [
    colors.HexColor("#D9EAF7"),
    colors.HexColor("#DDEED8"),
    colors.HexColor("#FFF2CC"),
    colors.HexColor("#EADCF8"),
    colors.HexColor("#FCE4D6"),
    colors.HexColor("#DDEBF7"),
    colors.HexColor("#E2F0D9"),
    colors.HexColor("#F4CCCC"),
]
ISSUE_SYMBOLS = ["#", "+", "*", "X", "O", "=", "%", "@", "!", "A", "B", "C", "D", "E", "F", "G"]

MASTER_COLUMNS = [
    "Symbol",
    "Picked",
    "From Bin",
    "Part",
    "Description",
    "Issue Qty.",
    "Requisition Reference",
    "Comments",
]

COVER_COLUMNS = [
    "Symbol",
    "Picked",
    "Part",
    "Description",
    "Issue Qty.",
    "From Bin",
    "Comments",
]

DISPLAY_NAMES = {
    "Symbol": "Symbol",
    "Picked": "Picked",
    "From Bin": "Location",
    "Part": "Part",
    "Description": "Description",
    "Issue Qty.": "Qty",
    "Requisition Reference": "Issue Number",
    "Comments": "Comments",
}


def dataframe_to_pdf_bytes(
    df: pd.DataFrame,
    area_name: str,
    released_by: str = "",
    released_at: str = "",
    master_pick_page_line_limit: int = 20,
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None = None,
    current_pick_code: str = "",
) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=16 * mm,
        bottomMargin=12 * mm,
    )
    styles = _build_styles()
    story = []

    story.extend(
        _master_pick_list_story(
            df,
            area_name,
            released_by,
            released_at,
            styles,
            doc.width,
            master_pick_page_line_limit,
        )
    )

    if not df.empty:
        grouped = df.copy()
        grouped["Requisition Reference"] = grouped["Requisition Reference"].fillna("").astype(str)
        colour_map = _issue_colour_map(grouped["Requisition Reference"])
        symbol_map = _issue_symbol_map(grouped["Requisition Reference"])
        for requisition, group in grouped.groupby("Requisition Reference", sort=True, dropna=False):
            story.append(PageBreak())
            story.extend(
                _issue_cover_story(
                    group=group,
                    issue_number=requisition.strip() or "Not recorded",
                    issue_key=requisition,
                    issue_colour=colour_map.get(requisition, ISSUE_COLOURS[0]),
                    issue_symbol=symbol_map.get(requisition, "?"),
                    issue_zones=_issue_zones(requisition, requisition_zone_map, current_pick_code),
                    current_pick_code=current_pick_code,
                    area_name=area_name,
                    released_by=released_by,
                    released_at=released_at,
                    styles=styles,
                    available_width=doc.width,
                )
            )

    doc.build(story, onFirstPage=_draw_page_frame, onLaterPages=_draw_page_frame)
    return buffer.getvalue()


def _master_pick_list_story(
    df: pd.DataFrame,
    area_name: str,
    released_by: str,
    released_at: str,
    styles: dict[str, ParagraphStyle],
    available_width: float,
    master_pick_page_line_limit: int,
) -> list:
    if df.empty:
        story = _master_pick_header_story(area_name, released_by, released_at, styles)
        story.append(Paragraph("No rows to display.", styles["Meta"]))
        return story

    colour_map = _issue_colour_map(df["Requisition Reference"].fillna("").astype(str))
    symbol_map = _issue_symbol_map(df["Requisition Reference"].fillna("").astype(str))
    chunks = _chunk_dataframe(df, master_pick_page_line_limit)
    story = []

    for chunk_index, chunk in enumerate(chunks, start=1):
        if chunk_index > 1:
            story.append(PageBreak())
        page_suffix = f" - Page {chunk_index} of {len(chunks)}" if len(chunks) > 1 else ""
        requisitions = _requisition_numbers(chunk)
        story.extend(
            _master_pick_header_story(
                area_name,
                released_by,
                released_at,
                styles,
                available_width,
                requisitions,
                page_suffix,
            )
        )
        story.append(
            _build_table(
                chunk,
                MASTER_COLUMNS,
                _master_col_widths(available_width),
                styles,
                repeat_rows=True,
                issue_colour_map=colour_map,
                issue_symbol_map=symbol_map,
            )
        )
    return story


def _master_pick_header_story(
    area_name: str,
    released_by: str,
    released_at: str,
    styles: dict[str, ParagraphStyle],
    available_width: float | None = None,
    requisition_numbers: list[str] | None = None,
    page_suffix: str = "",
) -> list:
    title = Paragraph(f"Master Pick List ({area_name}){page_suffix}", styles["Title"])
    requisitions = Paragraph(_requisition_header_text(requisition_numbers), styles["RequisitionHeader"])
    if available_width:
        header = Table(
            [[title, requisitions]],
            colWidths=[available_width * 0.62, available_width * 0.38],
            hAlign="LEFT",
        )
        header.setStyle(_header_table_style())
        first_item = header
    else:
        first_item = title

    return [
        first_item,
        Paragraph(f"Area: <b>{area_name}</b>", styles["Meta"]),
        Paragraph(f"Released by: {released_by or 'Not recorded'}", styles["Meta"]),
        Paragraph(f"Released at: {released_at or 'Not recorded'}", styles["Meta"]),
        Spacer(1, 6),
    ]


def _issue_cover_story(
    group: pd.DataFrame,
    issue_number: str,
    issue_key: str,
    issue_colour,
    issue_symbol: str,
    issue_zones: set[str],
    current_pick_code: str,
    area_name: str,
    released_by: str,
    released_at: str,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> list:
    story = [
        _cover_page_top_band(
            issue_number=issue_number,
            issue_colour=issue_colour,
            issue_symbol=issue_symbol,
            issue_zones=issue_zones,
            current_pick_code=current_pick_code,
            styles=styles,
            available_width=available_width,
        ),
        Paragraph(f"Issue Number Cover Pages ({issue_number})", styles["Title"]),
        Paragraph(f"Area: <b>{area_name}</b>", styles["Meta"]),
        Paragraph(f"Issue Number: <b>{issue_number}</b>", styles["Meta"]),
        Paragraph(f"Released by: {released_by or 'Not recorded'}", styles["Meta"]),
        Paragraph(f"Released at: {released_at or 'Not recorded'}", styles["Meta"]),
        Spacer(1, 6),
    ]
    story.append(
        _build_table(
            group,
            COVER_COLUMNS,
            _single_cover_col_widths(available_width),
            styles,
            repeat_rows=True,
            issue_colour_map={issue_key: issue_colour},
            issue_symbol_map={issue_key: issue_symbol},
        )
    )
    return story


def _build_table(
    df: pd.DataFrame,
    columns: list[str],
    col_widths: list[float],
    styles: dict[str, ParagraphStyle],
    repeat_rows: bool,
    issue_colour_map: dict[str, object] | None = None,
    issue_symbol_map: dict[str, str] | None = None,
) -> Table:
    output = _with_table_columns(df, issue_symbol_map)
    available_columns = [column for column in columns if column in output.columns]
    table_data = [[Paragraph(DISPLAY_NAMES.get(column, column), styles["TableHeader"]) for column in available_columns]]

    for _, row in output.loc[:, available_columns].iterrows():
        table_data.append([_table_cell(column, row[column], styles) for column in available_columns])

    table = Table(table_data, colWidths=col_widths[: len(available_columns)], repeatRows=1 if repeat_rows else 0)
    style = _table_style(available_columns)
    symbol_col = available_columns.index("Symbol") if "Symbol" in available_columns else None
    if issue_colour_map and "Requisition Reference" in output.columns:
        for row_number, issue_number in enumerate(output["Requisition Reference"].fillna("").astype(str), start=1):
            if symbol_col is not None:
                style.add(
                    "BACKGROUND",
                    (symbol_col, row_number),
                    (symbol_col, row_number),
                    issue_colour_map.get(issue_number, ROW_BLUE),
                )
    table.setStyle(style)
    return table


def _table_cell(column: str, value: object, styles: dict[str, ParagraphStyle]):
    if column == "Picked":
        return CheckBoxFlowable()
    if column == "Comments":
        return Paragraph(" ", styles["TableCell"])
    if column == "Symbol":
        return Paragraph(_cell_value(value), styles["SymbolCell"])
    return Paragraph(_cell_value(value), styles["TableCell"])


def _with_table_columns(df: pd.DataFrame, issue_symbol_map: dict[str, str] | None) -> pd.DataFrame:
    output = df.copy()
    if issue_symbol_map and "Requisition Reference" in output.columns:
        issue_numbers = output["Requisition Reference"].fillna("").astype(str)
        output["Symbol"] = issue_numbers.map(issue_symbol_map).fillna("?")
    else:
        output["Symbol"] = ""
    output["Picked"] = ""
    output["Comments"] = ""
    return output


def _master_col_widths(available_width: float) -> list[float]:
    weights = [0.05, 0.07, 0.12, 0.14, 0.27, 0.06, 0.10, 0.19]
    return [available_width * weight for weight in weights]


def _single_cover_col_widths(available_width: float) -> list[float]:
    weights = [0.05, 0.07, 0.15, 0.31, 0.07, 0.12, 0.23]
    return [available_width * weight for weight in weights]


def _table_style(columns: list[str]) -> TableStyle:
    style = TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), HEADER_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.black),
            ("LINEBELOW", (0, 0), (-1, -1), 0.55, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 5.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ]
    )

    for col_index, column in enumerate(columns):
        if column in {"Symbol", "Picked", "Issue Qty.", "Requisition Reference"}:
            style.add("ALIGN", (col_index, 1), (col_index, -1), "CENTER")
        if column == "Comments":
            style.add("LINEBEFORE", (col_index, 0), (col_index, -1), 0.4, colors.HexColor("#9EADCC"))
    return style


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "PickTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            alignment=0,
            spaceAfter=4,
        ),
        "Meta": ParagraphStyle(
            "PickMeta",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
        ),
        "TableHeader": ParagraphStyle(
            "PickTableHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=TABLE_FONT_SIZE,
            leading=TABLE_LEADING,
            textColor=colors.white,
        ),
        "TableCell": ParagraphStyle(
            "PickTableCell",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=TABLE_FONT_SIZE,
            leading=TABLE_LEADING,
        ),
        "RequisitionHeader": ParagraphStyle(
            "PickRequisitionHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=20,
            alignment=2,
        ),
        "SymbolCell": ParagraphStyle(
            "PickSymbolCell",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=TABLE_FONT_SIZE + 1,
            leading=TABLE_LEADING,
            alignment=1,
        ),
    }


class CheckBoxFlowable(Flowable):
    def __init__(self, size: float = 8.5):
        super().__init__()
        self.width = size
        self.height = size
        self.size = size

    def draw(self) -> None:
        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(0.8)
        self.canv.rect(0, 0, self.size, self.size, stroke=1, fill=0)


class ZoneIndicatorFlowable(Flowable):
    def __init__(self, zones: set[str], current_zone: str, size: float = 26):
        super().__init__()
        self.zones = {zone.upper() for zone in zones}
        self.current_zone = current_zone.upper()
        self.size = size
        self.letters = [letter for letter in ("G", "H") if letter in self.zones]
        self.width = max(len(self.letters), 1) * size * 1.15
        self.height = size * 1.35

    def draw(self) -> None:
        self.canv.setFont("Helvetica-Bold", self.size)
        self.canv.setFillColor(colors.black)
        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(1.4)

        x = 0
        baseline = self.size * 0.25
        for letter in self.letters:
            if letter == self.current_zone:
                self.canv.circle(x + self.size * 0.35, baseline + self.size * 0.42, self.size * 0.44, stroke=1, fill=0)
            self.canv.drawString(x, baseline, letter)
            x += self.size * 1.05


def _cover_page_top_band(
    issue_number: str,
    issue_colour,
    issue_symbol: str,
    issue_zones: set[str],
    current_pick_code: str,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> Table:
    left_cell = [
        ZoneIndicatorFlowable(issue_zones, current_pick_code),
        Spacer(1, 2),
        _issue_indicator(issue_colour, issue_symbol, styles),
    ]
    right_cell = Paragraph(_requisition_header_text([issue_number]), styles["RequisitionHeader"])
    table = Table(
        [[left_cell, right_cell]],
        colWidths=[available_width * 0.55, available_width * 0.45],
        hAlign="LEFT",
    )
    table.setStyle(_header_table_style())
    return table


def _issue_indicator(issue_colour, issue_symbol: str, styles: dict[str, ParagraphStyle]) -> Table:
    table = Table(
        [["", Paragraph(f"Symbol: <b>{issue_symbol}</b>", styles["Meta"])]],
        colWidths=[18 * mm, 32 * mm],
        rowHeights=[7 * mm],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), issue_colour),
                ("BOX", (0, 0), (0, 0), 0.5, colors.HexColor("#666666")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 0),
                ("TOPPADDING", (0, 0), (0, 0), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 0),
            ]
        )
    )
    return table


def _header_table_style() -> TableStyle:
    return TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
    )


def _issue_colour_map(issue_numbers: pd.Series) -> dict[str, object]:
    unique_numbers = [str(value) for value in issue_numbers.drop_duplicates().tolist()]
    return {
        issue_number: ISSUE_COLOURS[index % len(ISSUE_COLOURS)]
        for index, issue_number in enumerate(unique_numbers)
    }


def _issue_symbol_map(issue_numbers: pd.Series) -> dict[str, str]:
    unique_numbers = [str(value) for value in issue_numbers.drop_duplicates().tolist()]
    return {
        issue_number: ISSUE_SYMBOLS[index % len(ISSUE_SYMBOLS)]
        for index, issue_number in enumerate(unique_numbers)
    }


def _requisition_numbers(df: pd.DataFrame) -> list[str]:
    if "Requisition Reference" not in df.columns:
        return []
    numbers = df["Requisition Reference"].fillna("").astype(str).str.strip()
    return [number for number in numbers.drop_duplicates().tolist() if number]


def _requisition_header_text(requisition_numbers: list[str] | None) -> str:
    numbers = requisition_numbers or []
    value = ", ".join(numbers) if numbers else "Not recorded"
    return f"REQ<br/><b>{value}</b>"


def _issue_zones(
    issue_number: str,
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None,
    current_pick_code: str,
) -> set[str]:
    if requisition_zone_map:
        zones = requisition_zone_map.get(issue_number) or requisition_zone_map.get(issue_number.strip())
        if zones:
            return {str(zone).upper() for zone in zones}
    return {current_pick_code.upper()} if current_pick_code else set()


def _chunk_dataframe(df: pd.DataFrame, line_limit: int) -> list[pd.DataFrame]:
    try:
        parsed_limit = int(line_limit)
    except (TypeError, ValueError):
        parsed_limit = 20
    parsed_limit = max(parsed_limit, 1)
    return [df.iloc[index : index + parsed_limit] for index in range(0, len(df), parsed_limit)]


def _draw_page_frame(canvas, doc) -> None:
    canvas.saveState()
    _draw_logos(canvas, doc)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#666666"))
    page_width, _ = doc.pagesize
    canvas.drawRightString(page_width - doc.rightMargin, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _draw_logos(canvas, doc) -> None:
    logo_paths = _logo_paths()
    if not logo_paths:
        return

    page_width, page_height = doc.pagesize
    x = page_width - doc.rightMargin
    y = page_height - 13 * mm
    for path in reversed(logo_paths):
        width = 34 * mm if "Kaizen" in path.name else 24 * mm
        height = 8 * mm
        x -= width
        try:
            canvas.drawImage(str(path), x, y, width=width, height=height, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
        x -= 3 * mm


def _logo_paths() -> list[Path]:
    logo_dir = Path("assets")
    if not logo_dir.exists():
        return []
    paths = []
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        paths.extend(sorted(logo_dir.glob(pattern)))
    return paths[:2]


def _cell_value(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)
