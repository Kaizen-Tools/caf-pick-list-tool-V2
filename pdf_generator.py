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
TABLE_FONT_SIZE = 8.8
TABLE_LEADING = 9.4
HEADER_ROW_HEIGHT = 24 * mm
MASTER_TABLE_HEADER_HEIGHT = 7 * mm
MASTER_TABLE_ROW_HEIGHT = 8.2 * mm
DEFAULT_TABLE_HEADER_HEIGHT = 8 * mm
DEFAULT_TABLE_ROW_HEIGHT = 9.5 * mm
HEADER_BLUE = colors.HexColor("#1F4E78")
GRID_BLUE = colors.HexColor("#D9E2F3")
ROW_BLUE = colors.HexColor("#F7F9FC")
DEFAULT_DEPOT_GREY = colors.HexColor("#F2F2F2")

MASTER_COLUMNS = [
    "Symbol",
    "Picked",
    "Missing",
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
    "Missing",
    "Part",
    "Description",
    "Issue Qty.",
    "From Bin",
    "Comments",
]

DISPLAY_NAMES = {
    "Symbol": "Depot",
    "Picked": "Picked",
    "Missing": "Missing",
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
    df = _combine_pdf_rows(df)
    depot_colour_map = _depot_colour_map([df])
    return _build_pdf_document(
        _pick_section_story(
            df,
            released_by,
            master_pick_page_line_limit,
            requisition_zone_map,
            current_pick_code,
            depot_colour_map,
        )
    )


def combined_pick_pdf_bytes(
    ground_df: pd.DataFrame,
    height_df: pd.DataFrame,
    released_by: str = "",
    released_at: str = "",
    master_pick_page_line_limit: int = 20,
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None = None,
) -> bytes:
    sections = [(_combine_pdf_rows(ground_df), "G"), (_combine_pdf_rows(height_df), "H")]
    depot_colour_map = _depot_colour_map([section_df for section_df, _ in sections])
    story = []
    for section_df, pick_code in sections:
        if section_df.empty:
            continue
        section_story = _pick_section_story(
            section_df,
            released_by,
            master_pick_page_line_limit,
            requisition_zone_map,
            pick_code,
            depot_colour_map,
        )
        if story and section_story:
            story.append(PageBreak())
        story.extend(section_story)

    if not story:
        story = _pick_section_story(
            pd.DataFrame(),
            released_by,
            master_pick_page_line_limit,
            requisition_zone_map,
            "G",
            depot_colour_map,
        )

    return _build_pdf_document(story)


def _build_pdf_document(story: list) -> bytes:

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=12 * mm,
        bottomMargin=10 * mm,
    )
    doc.build(story, onFirstPage=_draw_page_frame, onLaterPages=_draw_page_frame)
    return buffer.getvalue()


def _pick_section_story(
    df: pd.DataFrame,
    released_by: str,
    master_pick_page_line_limit: int,
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None,
    current_pick_code: str,
    depot_colour_map: dict[str, colors.Color],
) -> list:
    styles = _build_styles()
    available_width = PAGE_SIZE[0] - (20 * mm)
    story = []

    story.extend(
        _master_pick_list_story(
            df,
            released_by,
            styles,
            available_width,
            master_pick_page_line_limit,
            requisition_zone_map,
            current_pick_code,
            depot_colour_map,
        )
    )

    if not df.empty:
        grouped = df.copy()
        grouped["Requisition Reference"] = grouped["Requisition Reference"].fillna("").astype(str).str.strip()
        for requisition, group in grouped.groupby("Requisition Reference", sort=True, dropna=False):
            story.append(PageBreak())
            story.extend(
                _issue_cover_story(
                    group=group,
                    issue_number=requisition.strip() or "Not recorded",
                    issue_zones=_issue_zones(requisition, requisition_zone_map, current_pick_code),
                    current_pick_code=current_pick_code,
                    released_by=released_by,
                    styles=styles,
                    available_width=available_width,
                    depot_colour_map=depot_colour_map,
                )
            )

    return story


def _master_pick_list_story(
    df: pd.DataFrame,
    released_by: str,
    styles: dict[str, ParagraphStyle],
    available_width: float,
    master_pick_page_line_limit: int,
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None,
    current_pick_code: str,
    depot_colour_map: dict[str, colors.Color],
) -> list:
    requisitions = _requisition_numbers(df)
    issue_zones = _document_zones(requisitions, requisition_zone_map, current_pick_code)

    if df.empty:
        story = [
            _document_header(
                document_type="MASTER PICK LIST",
                released_by=released_by,
                split_text="0 OF 0",
                requisition_numbers=requisitions,
                depot_names=[],
                issue_zones=issue_zones,
                current_pick_code=current_pick_code,
                include_depot=False,
                styles=styles,
                available_width=available_width,
            )
        ]
        story.append(Paragraph("No rows to display.", styles["Meta"]))
        return story

    chunks = _chunk_dataframe(df, master_pick_page_line_limit)
    story = []
    special_summary = _special_location_summary(df, styles)

    for chunk_index, chunk in enumerate(chunks, start=1):
        if chunk_index > 1:
            story.append(PageBreak())
        story.append(
            _document_header(
                document_type="MASTER PICK LIST",
                released_by=released_by,
                split_text=f"{chunk_index} OF {len(chunks)}",
                requisition_numbers=_requisition_numbers(chunk),
                depot_names=[],
                issue_zones=issue_zones,
                current_pick_code=current_pick_code,
                include_depot=False,
                styles=styles,
                available_width=available_width,
            )
        )
        if chunk_index == 1 and special_summary:
            story.append(special_summary)
        story.append(Spacer(1, 6))
        story.append(
            _build_table(
                chunk,
                MASTER_COLUMNS,
                _master_col_widths(available_width),
                styles,
                repeat_rows=True,
                table_kind="master",
                depot_colour_map=depot_colour_map,
            )
        )
    return story


def _issue_cover_story(
    group: pd.DataFrame,
    issue_number: str,
    issue_zones: set[str],
    current_pick_code: str,
    released_by: str,
    styles: dict[str, ParagraphStyle],
    available_width: float,
    depot_colour_map: dict[str, colors.Color],
) -> list:
    story = [
        _document_header(
            document_type="GOODS OUT PAGE",
            released_by=released_by,
            split_text="N/A",
            requisition_numbers=[issue_number],
            depot_names=_depot_names(group),
            issue_zones=issue_zones,
            current_pick_code=current_pick_code,
            include_depot=True,
            styles=styles,
            available_width=available_width,
        ),
        Spacer(1, 6),
    ]
    story.append(
        _build_table(
            group,
            COVER_COLUMNS,
            _single_cover_col_widths(available_width),
            styles,
            repeat_rows=True,
            table_kind="cover",
            depot_colour_map=depot_colour_map,
        )
    )
    return story


def _build_table(
    df: pd.DataFrame,
    columns: list[str],
    col_widths: list[float],
    styles: dict[str, ParagraphStyle],
    repeat_rows: bool,
    table_kind: str = "default",
    depot_colour_map: dict[str, colors.Color] | None = None,
) -> Table:
    output = _with_table_columns(df)
    available_columns = [column for column in columns if column in output.columns]
    table_data = [[Paragraph(DISPLAY_NAMES.get(column, column), styles["TableHeader"]) for column in available_columns]]

    for _, row in output.loc[:, available_columns].iterrows():
        table_data.append([_table_cell(column, row[column], styles) for column in available_columns])

    row_heights = _table_row_heights(table_kind, len(table_data) - 1)
    table = Table(
        table_data,
        colWidths=col_widths[: len(available_columns)],
        rowHeights=row_heights,
        repeatRows=1 if repeat_rows else 0,
    )
    depot_row_colours = _depot_row_colours(output, depot_colour_map)
    style = _table_style(available_columns, compact=table_kind == "master", depot_row_colours=depot_row_colours)
    table.setStyle(style)
    return table


def _table_cell(column: str, value: object, styles: dict[str, ParagraphStyle]):
    if column in {"Picked", "Missing"}:
        return CheckBoxFlowable()
    if column == "Comments":
        return Paragraph(" ", styles["TableCell"])
    if column == "Symbol":
        return Paragraph(_cell_value(value), styles["DepotMarkerCell"])
    return Paragraph(_cell_value(value), styles["TableCell"])


def _with_table_columns(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()
    if "Depot Name" in output.columns:
        output["Symbol"] = output["Depot Name"].fillna("").astype(str).map(_depot_marker)
    else:
        output["Symbol"] = ""
    output["Picked"] = ""
    output["Missing"] = ""
    output["Comments"] = ""
    return output


def _table_row_heights(table_kind: str, row_count: int) -> list[float] | None:
    if table_kind == "master":
        return [MASTER_TABLE_HEADER_HEIGHT] + [MASTER_TABLE_ROW_HEIGHT] * row_count
    if table_kind == "cover":
        return [DEFAULT_TABLE_HEADER_HEIGHT] + [DEFAULT_TABLE_ROW_HEIGHT] * row_count
    return None


def _master_col_widths(available_width: float) -> list[float]:
    weights = [0.055, 0.06, 0.06, 0.12, 0.13, 0.245, 0.055, 0.085, 0.19]
    return [available_width * weight for weight in weights]


def _single_cover_col_widths(available_width: float) -> list[float]:
    weights = [0.06, 0.065, 0.065, 0.14, 0.29, 0.065, 0.11, 0.205]
    return [available_width * weight for weight in weights]


def _table_style(
    columns: list[str],
    compact: bool = False,
    depot_row_colours: list[colors.Color] | None = None,
) -> TableStyle:
    vertical_padding = 1.4 if compact else 2.4
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
            ("TOPPADDING", (0, 0), (-1, -1), vertical_padding),
            ("BOTTOMPADDING", (0, 0), (-1, -1), vertical_padding),
        ]
    )

    for row_index, depot_colour in enumerate(depot_row_colours or [], start=1):
        style.add("BACKGROUND", (0, row_index), (-1, row_index), depot_colour)

    for col_index, column in enumerate(columns):
        if column in {"Symbol", "Picked", "Missing", "Issue Qty.", "Requisition Reference"}:
            style.add("ALIGN", (col_index, 1), (col_index, -1), "CENTER")
        if column == "Symbol":
            style.add("VALIGN", (col_index, 1), (col_index, -1), "MIDDLE")
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
        "MetaBold": ParagraphStyle(
            "PickMetaBold",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
        ),
        "HeaderInfo": ParagraphStyle(
            "PickHeaderInfo",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=12,
            leading=14,
            alignment=1,
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
        "DepotMarkerCell": ParagraphStyle(
            "PickDepotMarkerCell",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=12,
            alignment=1,
        ),
        "RequisitionHeader": ParagraphStyle(
            "PickRequisitionHeader",
            parent=base["BodyText"],
            fontName="Helvetica-Bold",
            fontSize=17,
            leading=20,
            alignment=2,
        ),
    }


class CheckBoxFlowable(Flowable):
    def __init__(self, size: float = 10.5):
        super().__init__()
        self.width = size
        self.height = size
        self.size = size

    def draw(self) -> None:
        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(0.8)
        self.canv.rect(0, 0, self.size, self.size, stroke=1, fill=0)


class ZoneIndicatorFlowable(Flowable):
    def __init__(self, zones: set[str], current_zone: str, size: float = 22):
        super().__init__()
        self.zones = {zone.upper() for zone in zones}
        self.current_zone = current_zone.upper()
        self.size = size
        self.letters = [letter for letter in ("G", "H") if letter in self.zones]
        self.segment_width = size * 1.38
        self.width = max(len(self.letters), 1) * self.segment_width
        self.height = size * 1.45

    def draw(self) -> None:
        self.canv.setFont("Helvetica-Bold", self.size)
        self.canv.setFillColor(colors.black)
        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(1.4)

        baseline = self.size * 0.22
        center_y = baseline + self.size * 0.38
        for index, letter in enumerate(self.letters):
            center_x = (index * self.segment_width) + (self.segment_width / 2)
            if letter == self.current_zone:
                self.canv.ellipse(
                    center_x - self.size * 0.64,
                    center_y - self.size * 0.55,
                    center_x + self.size * 0.64,
                    center_y + self.size * 0.58,
                    stroke=1,
                    fill=0,
                )
            self.canv.drawCentredString(center_x, baseline, letter)


class RequisitionListFlowable(Flowable):
    def __init__(self, requisition_numbers: list[str] | None, width: float = 100, height: float = HEADER_ROW_HEIGHT - 8):
        super().__init__()
        self.requisition_numbers = [str(number).upper() for number in (requisition_numbers or []) if str(number).strip()]
        if not self.requisition_numbers:
            self.requisition_numbers = ["NOT RECORDED"]
        self.width = width
        self.height = height

    def wrap(self, availWidth: float, availHeight: float) -> tuple[float, float]:
        self.width = availWidth
        return self.width, self.height

    def draw(self) -> None:
        label_size = 8.5
        number_size = 10
        line_height = 9.4
        top = self.height - label_size

        self.canv.setFillColor(colors.black)
        self.canv.setFont("Helvetica", label_size)
        self.canv.drawCentredString(self.width / 2, top, "REQ NO.")

        usable_height = max(top - 3, line_height)
        rows_per_column = max(1, int(usable_height // line_height))
        column_count = max(1, (len(self.requisition_numbers) + rows_per_column - 1) // rows_per_column)
        column_width = self.width / column_count

        if column_count > 3:
            number_size = 8.5
            line_height = 8.2
            rows_per_column = max(1, int(usable_height // line_height))
            column_count = max(1, (len(self.requisition_numbers) + rows_per_column - 1) // rows_per_column)
            column_width = self.width / column_count

        self.canv.setFont("Helvetica-Bold", number_size)
        start_y = top - 10
        for index, number in enumerate(self.requisition_numbers):
            column_index = index // rows_per_column
            row_index = index % rows_per_column
            x = (column_index * column_width) + (column_width / 2)
            y = start_y - (row_index * line_height)
            self.canv.drawCentredString(x, y, number)


def _document_header(
    document_type: str,
    released_by: str,
    split_text: str,
    requisition_numbers: list[str],
    depot_names: list[str],
    issue_zones: set[str],
    current_pick_code: str,
    include_depot: bool,
    styles: dict[str, ParagraphStyle],
    available_width: float,
) -> Table:
    cells = [
        ZoneIndicatorFlowable(issue_zones, current_pick_code),
        Paragraph(f"<b>{str(document_type).upper()}</b>", styles["HeaderInfo"]),
        Paragraph(_header_info_text("Released By", released_by or "Not recorded"), styles["HeaderInfo"]),
        Paragraph(_header_info_text("Split No.", split_text), styles["HeaderInfo"]),
        RequisitionListFlowable(requisition_numbers),
    ]
    if include_depot:
        cells.insert(3, Paragraph(_header_info_text("Depot", _depot_header_text(depot_names)), styles["HeaderInfo"]))
        weights = [0.08, 0.18, 0.17, 0.17, 0.12, 0.28]
    else:
        weights = [0.08, 0.24, 0.20, 0.14, 0.34]

    table = Table(
        [cells],
        colWidths=[available_width * weight for weight in weights],
        rowHeights=[HEADER_ROW_HEIGHT],
        hAlign="LEFT",
    )
    table.setStyle(_document_header_style())
    return table


def _document_header_style() -> TableStyle:
    return TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]
    )


def _requisition_numbers(df: pd.DataFrame) -> list[str]:
    if "Requisition Reference" not in df.columns:
        return []
    numbers = df["Requisition Reference"].fillna("").astype(str).str.strip()
    return [number for number in numbers.drop_duplicates().tolist() if number]


def _depot_names(df: pd.DataFrame) -> list[str]:
    if "Depot Name" not in df.columns:
        return []
    names = df["Depot Name"].fillna("").astype(str).str.strip()
    return [name for name in names.drop_duplicates().tolist() if name]


def _depot_header_text(depot_names: list[str] | None) -> str:
    names = depot_names or []
    return ", ".join(names) if names else "Not recorded"


def _depot_marker(value: object) -> str:
    depot_name = _normalise_depot_name(value)
    compact_name = "".join(char for char in depot_name if char.isalnum())
    return compact_name[:3].upper()


def _depot_colour_map(dataframes: list[pd.DataFrame]) -> dict[str, colors.Color]:
    depot_names = []
    for df in dataframes:
        if df.empty or "Depot Name" not in df.columns:
            continue
        for value in df["Depot Name"]:
            depot_name = _normalise_depot_name(value)
            if depot_name and depot_name not in depot_names:
                depot_names.append(depot_name)

    if not depot_names:
        return {}
    if len(depot_names) == 1:
        return {depot_names[0]: DEFAULT_DEPOT_GREY}

    lightest = 0.94
    darkest = 0.78
    step = (lightest - darkest) / (len(depot_names) - 1)
    return {
        depot_name: colors.Color(lightest - (index * step), lightest - (index * step), lightest - (index * step))
        for index, depot_name in enumerate(depot_names)
    }


def _depot_row_colours(
    df: pd.DataFrame,
    depot_colour_map: dict[str, colors.Color] | None,
) -> list[colors.Color]:
    if df.empty or "Depot Name" not in df.columns:
        return []

    colours = []
    for value in df["Depot Name"]:
        depot_name = _normalise_depot_name(value)
        colours.append((depot_colour_map or {}).get(depot_name, DEFAULT_DEPOT_GREY))
    return colours


def _normalise_depot_name(value: object) -> str:
    if pd.isna(value):
        return ""
    return " ".join(str(value).strip().split())


def _header_info_text(label: str, value: str) -> str:
    return f"{label.upper()}<br/><b>{str(value).upper()}</b>"


def _issue_zones(
    issue_number: str,
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None,
    current_pick_code: str,
) -> set[str]:
    if requisition_zone_map:
        zones = requisition_zone_map.get(issue_number) or requisition_zone_map.get(issue_number.strip())
        if zones:
            zone_set = {str(zone).upper() for zone in zones}
            if current_pick_code:
                zone_set.add(current_pick_code.upper())
            return zone_set
    return {current_pick_code.upper()} if current_pick_code else set()


def _document_zones(
    requisition_numbers: list[str],
    requisition_zone_map: dict[str, set[str] | list[str] | tuple[str, ...]] | None,
    current_pick_code: str,
) -> set[str]:
    zones = set()
    if requisition_zone_map:
        for requisition in requisition_numbers:
            mapped = requisition_zone_map.get(requisition) or requisition_zone_map.get(requisition.strip())
            if mapped:
                zones.update(str(zone).upper() for zone in mapped)
    if current_pick_code:
        zones.add(current_pick_code.upper())
    return zones


def _special_location_summary(df: pd.DataFrame, styles: dict[str, ParagraphStyle]) -> Paragraph | None:
    special_locations = _special_location_values(df)
    if not special_locations:
        return None
    return Paragraph(
        f"SPECIAL/NON-NUMERIC LOCATIONS: <b>{'; '.join(special_locations)}</b>",
        styles["MetaBold"],
    )


def _special_location_values(df: pd.DataFrame) -> list[str]:
    if df.empty or "From Bin" not in df.columns:
        return []

    values = []
    for value in df["From Bin"].fillna("").astype(str):
        for candidate in value.split(","):
            text = candidate.strip()
            if text and any(char.isalpha() for char in text) and not any(char.isdigit() for char in text):
                if text not in values:
                    values.append(text)
    return values


def _combine_pdf_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not {"Requisition Reference", "Part", "Issue Qty."}.issubset(df.columns):
        return df.copy()

    working = df.copy()
    working["_Original Order"] = range(len(working))
    working["_Req Key"] = working["Requisition Reference"].fillna("").astype(str).str.strip()
    working["_Part Key"] = working["Part"].fillna("").astype(str).str.strip()

    rows = []
    for _, group in working.groupby(["_Req Key", "_Part Key"], sort=False, dropna=False):
        first = group.iloc[0].copy()
        first["Issue Qty."] = _combined_quantity(group["Issue Qty."])
        for column in group.columns:
            if column in {"Issue Qty.", "_Req Key", "_Part Key"}:
                continue
            if column == "_Original Order":
                first[column] = group[column].min()
            elif column in {"From Bin", "Source File", "Line"}:
                first[column] = _join_unique_values(group[column])
            else:
                first[column] = _first_non_empty(group[column])
        rows.append(first)

    combined = pd.DataFrame(rows).sort_values("_Original Order", kind="stable")
    return combined.drop(columns=["_Original Order", "_Req Key", "_Part Key"], errors="ignore").reset_index(drop=True)


def _combined_quantity(values: pd.Series) -> object:
    numeric_values = pd.to_numeric(values, errors="coerce")
    if numeric_values.notna().any():
        total = numeric_values.fillna(0).sum()
        return int(total) if float(total).is_integer() else total
    return _join_unique_values(values)


def _first_non_empty(values: pd.Series) -> object:
    for value in values:
        if pd.notna(value) and str(value).strip():
            return value
    return ""


def _join_unique_values(values: pd.Series) -> str:
    unique_values = []
    for value in values:
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text and text not in unique_values:
            unique_values.append(text)
    return ", ".join(unique_values)


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
