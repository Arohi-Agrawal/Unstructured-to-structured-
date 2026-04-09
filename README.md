# Document Processing App

Standalone Python-only document processor with a FastAPI + Jinja UI, a modular parser architecture, and a real PDF pipeline built around `opendataloader-pdf`.

Current implementation scope:

- Fully implemented: `pdf`, `scanned pdf`
- Architecture-ready placeholders only: `csv`, `xlsx`, `docx`, `mt940 / 940`, `txt`, `ep txt`

Core routing flow:

`input file -> detect format -> route to parser -> process -> normalize result -> write JSON/CSV outputs -> show result in UI`

## Architecture goals

- Python only
- No JavaScript parsing libraries
- Modular parser contract for future formats
- Generic, rule-based, layout-aware PDF extraction
- No bank-specific, merchant-specific, report-specific, or sample-file-specific rules
- Real OpenDataLoader integration for PDF
- OCR/layout fallback for scanned PDFs
- One common internal result model across all formats

## Current features

- Single-file upload UI
- Generic format detection and parser routing
- Clean placeholder handling for not-yet-implemented formats
- PDF local mode, hybrid mode, and OCR/layout fallback
- JSON output for metadata, notes, issues, headers, footers, and narrative content
- One CSV per true table
- Download endpoints for JSON and per-table CSV files

## Supported routes

- `GET /`
- `POST /upload`
- `GET /result/{job_id}`
- `GET /download/json/{job_id}`
- `GET /download/csv/{job_id}/{table_name}`

## Project structure

```text
document_app/
  app.py
  requirements.txt
  README.md
  templates/
    index.html
    result.html
  static/
    style.css
  uploads/
  outputs/
  services/
    __init__.py
    parser_base.py
    format_router.py
    output_writer.py
    validators.py
    pdf/
      __init__.py
      pdf_parser.py
      opendataloader_runner.py
      pdf_mode_router.py
      pdf_metadata_extractor.py
      pdf_table_detector.py
      pdf_table_reconstructor.py
      pdf_ocr_fallback.py
      pdf_output_mapper.py
    csv/
      __init__.py
      csv_parser.py
    xlsx/
      __init__.py
      xlsx_parser.py
    docx/
      __init__.py
      docx_parser.py
    mt940/
      __init__.py
      mt940_parser.py
    txt/
      __init__.py
      txt_parser.py
```

## Requirements

- Python 3.10+
- Java 11+
- `pip install -r requirements.txt`

PDF-related dependencies:

- `opendataloader-pdf`
- `PyMuPDF`
- `Pillow`
- `numpy`
- `pytesseract` or `rapidocr-onnxruntime` for OCR fallback

Recommended hybrid setup:

```bash
pip install -U "opendataloader-pdf[hybrid]"
opendataloader-pdf-hybrid --port 5002 --force-ocr
```

## Setup

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run instructions

### Start the app

```bash
uvicorn app:app --reload
```

Open:

`http://127.0.0.1:8000`

### Optional: start the OpenDataLoader hybrid backend

For scanned PDFs and OCR-heavy pages, run this in a separate terminal:

```bash
opendataloader-pdf-hybrid --port 5002 --force-ocr
```

## How PDF routing works

For PDF files only, the parser does the following:

1. Save the uploaded file under `uploads/<job_id>/`
2. Run OpenDataLoader local mode first
3. Evaluate extraction quality
4. If local output is weak, image-only, metadata-sparse, or table-poor, run hybrid mode
5. If hybrid output is still insufficient, render page images and run OCR/layout reconstruction
6. Build final outputs:
   - `result.json` with the common internal model
   - `non_tabular_metadata.json` for metadata/non-tabular content
   - one CSV per true table

The PDF fallback is generic. It uses:

- high-resolution page rendering
- content-region cropping
- OCR words and line boxes with coordinates
- row clustering by y-position
- left-to-right token sorting
- generic header signal detection
- generic column boundary inference
- continuation-line merging
- confidence scoring and issue reporting

## Common internal result model

`result.json` uses this normalized shape:

```json
{
  "fileName": "statement.pdf",
  "fileType": "pdf",
  "detectedType": "scanned-pdf",
  "parserImplemented": true,
  "parserUsed": "opendataloader_pdf",
  "modeUsed": "ocr_layout_fallback",
  "metadata": {},
  "notes": [],
  "tables": [
    {
      "table_id": "table_001",
      "name": "table_001",
      "columns": [],
      "rows": []
    }
  ],
  "issues": [],
  "confidence": 0.0,
  "exports": {
    "jsonFile": "non_tabular_metadata.json",
    "csvFiles": []
  }
}
```

## Generic JSON and CSV generation

### JSON

The JSON export stores non-tabular content such as:

- metadata
- notes
- issues
- confidence
- parser and mode information
- export references

Metadata fields are generic and include:

- `title`
- `reportName`
- `institutionName`
- `accountNumber`
- `currency`
- `statementDate`
- `periodStart`
- `periodEnd`
- `customerName`
- `address`
- `pageInfo`
- `headers`
- `footers`
- `headings`
- `paragraphs`
- `narrativeText`
- `disclaimerText`
- `summaryText`
- `rawLabelValues`

### CSV

- One CSV is written per true table
- Logical tables stay separate
- Narrative/header/footer text is excluded from rows
- Blank cells are preserved as empty strings
- If a title cannot be inferred, stable table names such as `table_001` are used

## Non-PDF parsers

The following parsers are present only as clean placeholders:

- CSV
- XLSX
- DOCX
- MT940 / 940
- TXT
- EP TXT

They return a normalized `"not implemented yet"` result without pretending to parse content.

## How to add future parsers

1. Create a parser class under `services/<format>/`
2. Inherit from `BaseParser` in `services/parser_base.py`
3. Implement:
   - `can_handle(self, file_path: str, detected_type: str) -> bool`
   - `parse(self, file_path: Path, detected_type: str) -> ParserResult`
4. Return normalized metadata, notes, tables, issues, and confidence
5. Register the parser in `FormatRouter.EXTENSION_MAP`
6. Reuse `OutputWriter.persist_result()` so JSON and CSV output stays consistent

Example:

```python
from pathlib import Path

from services.parser_base import BaseParser, ParserResult


class MyFormatParser(BaseParser):
    parser_name = "my_format_parser"
    implemented = True

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        return file_path.endswith(".myext")

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        return ParserResult(
            status="success",
            message="Parsed successfully.",
            implemented=True,
            parser_used=self.parser_name,
            detected_type=detected_type,
            mode_used="direct",
            metadata={},
            notes=[],
            tables=[],
            issues=[],
            confidence=0.9,
        )
```

## Notes

- The PDF path uses OpenDataLoader first, then escalates only when needed.
- Non-PDF parsers remain untouched placeholders until you implement them.
- `uploads/` and `outputs/` are filesystem-based so the app stays simple to deploy.
