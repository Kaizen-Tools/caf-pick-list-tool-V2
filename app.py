from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from pdf_generator import combined_pick_pdf_bytes
from picksheet_generator import (
    REQUIRED_COLUMNS,
    build_pick_packs,
    consolidate_issue_exports,
)


APP_TITLE = "CAF Pick List Consolidation Tool"


st.set_page_config(page_title=APP_TITLE, layout="wide")


def check_password() -> bool:
    """Optional Streamlit Cloud password gate using st.secrets['app_password']."""

    expected_password = get_configured_password()
    if not expected_password:
        return True

    if st.session_state.get("authenticated"):
        return True

    st.title(APP_TITLE)
    st.caption("Enter the client access password to continue.")
    password = st.text_input("Password", type="password")
    if st.button("Unlock", type="primary"):
        if password == expected_password:
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Incorrect password.")
    return False


def get_configured_password() -> str | None:
    """Return the configured password, or None when secrets are not configured."""

    try:
        return st.secrets.get("app_password")
    except (FileNotFoundError, StreamlitSecretNotFoundError):
        return None


def main() -> None:
    if not check_password():
        return

    initialise_session_state()

    st.title(APP_TITLE)
    st.write(
        "Upload one GMAO STS Issue Excel export at a time, confirm its Requisition Number and Depot Name, "
        "then consolidate all confirmed files into ground-level and height/FLT pick sheets."
    )

    with st.expander("Prototype rules", expanded=False):
        st.markdown(
            """
- Uploaded files are STS Issue exports from GMAO.
- The export does not contain the Requisition Number or Depot Name, so the app prompts for both per file.
- Required columns are: `Line`, `Part`, `Description`, `Issue Qty.`, `UOM`, `From Bin`.
- Locations are parsed as `Aisle-Bay-Vertical-Lateral`, for example `07-09-B-04`.
- Aisles `01` to `10` are always ground-level, even when the location does not follow the full four-variable format.
- Locations from `65-01-X-XX` to `65-06-X-XX` are treated as ground-level picks.
- For aisles above `10`, vertical `A` is ground-level and `B+` is height/FLT.
            """
        )

    st.subheader("Trial Settings")
    settings_col1, settings_col2 = st.columns(2)
    master_pick_page_line_limit = settings_col1.number_input(
        "Max pick list lines per split",
        min_value=1,
        max_value=100,
        value=15,
        step=1,
        help="Maximum lines shown on each master pick list page.",
    )
    separate_issue_line_threshold = settings_col2.number_input(
        "Max issue lines to generate separate pack",
        min_value=1,
        max_value=500,
        value=30,
        step=1,
        help="Issues above this line count generate their own separate picking pack.",
    )

    st.subheader("1. Upload STS Issue Export")
    uploaded_file = st.file_uploader(
        "Upload one STS Issue Excel export",
        type=["xlsx", "xls"],
        accept_multiple_files=False,
        key=f"issue_upload_{st.session_state['upload_nonce']}",
    )

    if uploaded_file is not None:
        duplicate_filename = is_duplicate_filename(uploaded_file.name)
        if duplicate_filename:
            st.error(
                f"`{uploaded_file.name}` has already been confirmed. "
                "The same original file name cannot be uploaded twice."
            )
        elif not st.session_state.get("pending_requisition_prompt"):
            st.session_state["pending_requisition_prompt"] = {
                "filename": uploaded_file.name,
                "bytes": uploaded_file.getvalue(),
            }
            st.rerun()
    else:
        st.info("Upload one STS Issue export, then enter and confirm its Requisition Number and Depot Name.")

    if st.session_state.get("pending_requisition_prompt"):
        requisition_number_dialog()

    st.subheader("2. Confirmed Files")
    staged_files = st.session_state["staged_issue_files"]
    if staged_files:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Source File": item["filename"],
                        "Requisition Number": item["requisition_reference"],
                        "Depot Name": item.get("depot_name", ""),
                    }
                    for item in staged_files
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        if st.button("Clear confirmed files"):
            st.session_state["staged_issue_files"] = []
            reset_generated_outputs()
            st.rerun()
    else:
        st.caption("No files confirmed yet. Repeat the upload and confirmation step for each STS Issue export.")
        reset_generated_outputs()
        return

    process = False
    if staged_files:
        process = st.button("Consolidate pick sheets", type="primary", use_container_width=True)
        if process:
            st.session_state["pending_consolidation_confirmation"] = True
            st.rerun()

    if st.session_state.get("pending_consolidation_confirmation"):
        confirm_consolidation_dialog()

    process = st.session_state.pop("run_consolidation", False)
    if not process and "pick_packs" not in st.session_state:
        return

    if process:
        file_records = staged_records_to_file_records()

        try:
            consolidated, validations = consolidate_issue_exports(file_records)
        except ValueError as exc:
            st.error(str(exc))
            return

        invalid = [validation for validation in validations if not validation.is_valid]
        if invalid:
            st.error("One or more uploaded files are missing required columns.")
            for validation in invalid:
                st.write(
                    f"**{validation.filename}** missing: "
                    f"{', '.join(validation.missing_columns)}"
                )
            st.stop()

        pick_packs = build_pick_packs(consolidated, separate_issue_line_threshold)
        st.session_state["consolidated"] = consolidated
        st.session_state["pick_packs"] = pick_packs
        st.session_state["validations"] = [asdict(validation) for validation in validations]
        st.session_state["pack_settings"] = {
            "separate_issue_line_threshold": separate_issue_line_threshold,
            "master_pick_page_line_limit": master_pick_page_line_limit,
        }

    consolidated = st.session_state["consolidated"]
    pick_packs = st.session_state["pick_packs"]
    validations = st.session_state["validations"]
    if not pick_packs:
        st.warning("No valid issue lines were available to generate picking packs.")
        return

    st.success("Pick sheets generated.")
    release_metadata = st.session_state.get("release_metadata", {})

    st.subheader("3. Processing Summary")
    total_rows = len(consolidated)
    ground_rows = sum(len(pack["pick_sheets"]["ground"]) for pack in pick_packs)
    height_rows = sum(len(pack["pick_sheets"]["height"]) for pack in pick_packs)
    exception_rows = sum(len(pack["pick_sheets"]["exceptions"]) for pack in pick_packs)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Issue lines", total_rows)
    col2.metric("Picking packs", len(pick_packs))
    col3.metric("Ground-level", ground_rows)
    col4.metric("Height/FLT", height_rows)

    if exception_rows:
        st.warning(
            f"{exception_rows} row(s) could not be placed into a pick sheet and need review. "
            "These rows are not included in the PDF outputs."
        )

    if pick_packs:
        st.dataframe(
            pack_summary_rows(pick_packs),
            use_container_width=True,
            hide_index=True,
        )

    settings_used = st.session_state.get("pack_settings", {})
    active_master_pick_page_line_limit = settings_used.get(
        "master_pick_page_line_limit",
        master_pick_page_line_limit,
    )
    st.caption(
        "Trial settings used: "
        f"separate issue threshold = {settings_used.get('separate_issue_line_threshold', separate_issue_line_threshold)} lines; "
        f"master pick page limit = {active_master_pick_page_line_limit} lines."
    )
    if settings_used and (
        settings_used.get("separate_issue_line_threshold") != separate_issue_line_threshold
        or settings_used.get("master_pick_page_line_limit") != master_pick_page_line_limit
    ):
        st.info("Trial settings have changed. Select **Consolidate pick sheets** again to regenerate using the new values.")

    with st.expander("File validation details", expanded=False):
        st.dataframe(pd.DataFrame(validations), use_container_width=True)
        st.write("Required columns:", ", ".join(REQUIRED_COLUMNS))

    st.subheader("4. Downloads")
    for pack in pick_packs:
        with st.expander(pack["name"], expanded=len(pick_packs) == 1):
            st.caption(pack["reason"])
            zone_map = build_requisition_zone_map(pack["pick_sheets"])
            combined_pdf = combined_pick_pdf_bytes(
                pack["pick_sheets"]["ground"],
                pack["pick_sheets"]["height"],
                released_by=release_metadata.get("released_by", ""),
                released_at=release_metadata.get("released_at", ""),
                master_pick_page_line_limit=active_master_pick_page_line_limit,
                requisition_zone_map=zone_map,
            )

            st.download_button(
                "Download print pack PDF (Ground + Height)",
                data=combined_pdf,
                file_name=f"caf_{pack['slug']}_print_pack.pdf",
                mime="application/pdf",
                use_container_width=True,
            )


def initialise_session_state() -> None:
    st.session_state.setdefault("staged_issue_files", [])
    st.session_state.setdefault("upload_nonce", 0)


def reset_generated_outputs() -> None:
    st.session_state.pop("pick_packs", None)
    st.session_state.pop("pick_sheets", None)
    st.session_state.pop("consolidated", None)
    st.session_state.pop("validations", None)
    st.session_state.pop("pack_settings", None)


def pack_summary_rows(pick_packs: list[dict]) -> pd.DataFrame:
    rows = []
    for pack in pick_packs:
        pick_sheets = pack["pick_sheets"]
        issue_references = pack.get("issue_references", [])
        rows.append(
            {
                "Pack": pack["name"],
                "Type": pack["pack_type"],
                "Reason": pack["reason"],
                "Issue Numbers": ", ".join(issue_references) if issue_references else "Not recorded",
                "Lines": pack["line_count"],
                "Ground-level": len(pick_sheets["ground"]),
                "Height/FLT": len(pick_sheets["height"]),
                "Exceptions": len(pick_sheets["exceptions"]),
            }
        )
    return pd.DataFrame(rows)


def build_requisition_zone_map(pick_sheets: dict[str, pd.DataFrame]) -> dict[str, set[str]]:
    zone_map: dict[str, set[str]] = {}
    for sheet_name, zone_code in (("ground", "G"), ("height", "H")):
        df = pick_sheets.get(sheet_name, pd.DataFrame())
        if df.empty or "Requisition Reference" not in df.columns:
            continue
        requisitions = df["Requisition Reference"].fillna("").astype(str).str.strip()
        for requisition in requisitions.drop_duplicates():
            if requisition:
                zone_map.setdefault(requisition, set()).add(zone_code)
    return zone_map


@st.dialog("Enter Requisition Details")
def requisition_number_dialog() -> None:
    pending = st.session_state["pending_requisition_prompt"]
    st.write("Enter the Requisition Number and Depot Name for this STS Issue export before it can be added.")
    st.write(f"**File:** {pending['filename']}")
    requisition_reference = st.text_input(
        "Requisition Number",
        placeholder="e.g. 502801",
        help="This number will appear on the PDF page for the picked SKUs.",
    )
    depot_name = st.text_input(
        "Depot Name",
        placeholder="e.g. Manchester",
        help="This depot name will appear in uppercase on every PDF page for this requisition.",
    )

    col1, col2 = st.columns(2)
    if col1.button("Cancel", use_container_width=True):
        st.session_state.pop("pending_requisition_prompt", None)
        st.rerun()
    if col2.button("Confirm Requisition Details", type="primary", use_container_width=True):
        if not requisition_reference.strip():
            st.error("Enter the Requisition Number shown in GMAO before confirming.")
            return
        if not depot_name.strip():
            st.error("Enter the Depot Name before confirming.")
            return
        if is_duplicate_filename(pending["filename"]):
            st.error("This original file name has already been confirmed and cannot be added twice.")
            return
        st.session_state["staged_issue_files"].append(
            {
                "filename": pending["filename"],
                "bytes": pending["bytes"],
                "requisition_reference": requisition_reference.strip(),
                "depot_name": depot_name.strip(),
            }
        )
        st.session_state.pop("pending_requisition_prompt", None)
        st.session_state["upload_nonce"] += 1
        reset_generated_outputs()
        st.rerun()


@st.dialog("Confirm Consolidation")
def confirm_consolidation_dialog() -> None:
    count = len(st.session_state["staged_issue_files"])
    st.write(f"You have confirmed **{count}** STS Issue export(s).")
    released_by = st.text_input(
        "Released by",
        placeholder="Enter your name",
        help="This name will appear on every PDF page for accountability.",
    )
    st.write("Do you want to release and consolidate these files into the ground-level and height/FLT pick sheets now?")

    col1, col2 = st.columns(2)
    if col1.button("No, continue uploading", use_container_width=True):
        st.session_state["pending_consolidation_confirmation"] = False
        st.rerun()
    if col2.button("Yes, release pick lists", type="primary", use_container_width=True):
        if not released_by.strip():
            st.error("Enter the name of the person releasing the pick list before continuing.")
            return
        st.session_state["pending_consolidation_confirmation"] = False
        st.session_state["release_metadata"] = {
            "released_by": released_by.strip(),
            "released_at": datetime.now(ZoneInfo("Europe/London")).strftime("%Y-%m-%d %H:%M"),
        }
        st.session_state["run_consolidation"] = True
        st.rerun()


def staged_records_to_file_records() -> list[dict]:
    records = []
    for item in st.session_state["staged_issue_files"]:
        file_obj = BytesIO(item["bytes"])
        file_obj.name = item["filename"]
        records.append(
            {
                "file": file_obj,
                "issue_reference": "",
                "requisition_reference": item["requisition_reference"],
                "depot_name": item.get("depot_name", ""),
            }
        )
    return records


def is_duplicate_filename(filename: str) -> bool:
    return any(item["filename"] == filename for item in st.session_state["staged_issue_files"])


if __name__ == "__main__":
    main()
