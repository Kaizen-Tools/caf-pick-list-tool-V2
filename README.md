# CAF Pick List Consolidation Tool

Prototype Streamlit app for consolidating GMAO STS Issue Excel exports into two route-sorted pick sheets:

- Ground-level pick sheet
- Height/FLT pick sheet
- Separate picking packs for issues above the trial line-count threshold

The app prompts for one Requisition Number per uploaded STS Issue export because that reference is not included in the exported Excel file.

## Business Logic

Uploaded files are STS Issue exports from GMAO. The export does not include the Issue or Requisition reference directly, so the app prompts the user to confirm those references per uploaded file.

Required input columns:

- `Line`
- `Part`
- `Description`
- `Issue Qty.`
- `From Bin`

Location format:

`Aisle-Bay-Vertical-Lateral`, for example `07-09-B-04`.

Classification:

- Aisles `01` to `10`: ground-level regardless of vertical letter
- Aisles `01` to `10`: ground-level even when the location only contains aisle and bay, for example `10-04`
- Aisles above `10` with vertical `A`: ground-level
- Aisles above `10` with vertical `B` or later: height/FLT
- Missing or unparseable locations outside aisles `01` to `10`: held for review and excluded from the PDF pick sheets

Route sort:

1. Aisle ascending
2. Bay ascending
3. Vertical level ascending
4. Lateral position ascending
5. Part number
6. Line number

Trial pack rules:

- The app shows a configurable separate issue pack threshold. The default is `30` lines.
- Issues with line counts above the threshold are generated as their own separate picking pack.
- Issues at or below the threshold are consolidated into the normal consolidated picking pack.
- The app also shows a configurable master pick page line limit. The default is `15` lines per master pick page.

PDF format:

- PDFs are landscape A4.
- Every master pick list and issue cover page includes a left-hand symbol column.
- The symbol column mirrors the issue colour grouping so colour-blind users can match rows and cover pages.
- PDF rows with the same Requisition Number and SKU code are combined, with quantities summed.
- Every PDF table includes picked and missing confirmation boxes.
- Every master pick list and issue cover page includes a right-hand comments column.
- Table text has been enlarged for readability.
- Master pick lists include a summary master page followed by split pages.

## Run Locally

```powershell
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud

Deploy the repository to Streamlit Cloud with `app.py` as the entry point.

Optional password protection can be enabled by adding this secret in Streamlit Cloud:

```toml
app_password = "replace-with-client-password"
```

If `app_password` is not configured, the app runs without a password gate.

## Prototype Notes

Workflow:

1. Upload one STS Issue Excel export.
2. Enter the Requisition Number in the modal prompt.
3. Confirm the Requisition Number.
4. Repeat until all required Issue exports have been added.
5. Select **Consolidate pick sheets**.
6. Enter the name of the person releasing the pick list and confirm release.
7. Review the generated picking packs.
8. Download the ground-level and height/FLT PDF outputs for each pack.

Duplicate original filenames cannot be staged twice.

PDF outputs are split by pick type and by pack. Each PDF starts with route-ordered master pick list pages, then includes one issue-number cover page per unique number. Rows on the master list are colour-coded and symbol-coded by issue number, and the matching cover page uses the same colour and symbol indicators for visual management. Each row includes a picked confirmation box and a comments area for the material operator.

This is an MVP for client demonstration. The most important client validation points are:

- Whether the Issue/Requisition reference prompt matches the real GMAO workflow
- Whether route sorting should account for left-hand/right-hand aisle direction beyond numeric aisle/bay ordering
- Whether lateral positions should be picked in ascending order for all aisles or mirrored for one side of the warehouse
- Whether Stores Inventory data should be added later to override or validate `From Bin`
