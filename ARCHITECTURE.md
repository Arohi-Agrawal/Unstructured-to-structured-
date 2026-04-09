# Document Processing App - Architecture & Implementation Guide

## System Overview

This is a modular Python document processing application that:
1. **Detects** file format via extension and content analysis
2. **Routes** to appropriate parser based on format
3. **Normalizes** output to standard JSON/CSV structure
4. **Serves** via FastAPI web UI with download capability

## Complete Processing Flow

### Step 1: File Upload & Validation

```python
User uploads file
    ↓
FastAPI receives multipart form-data
    ↓
Save to temporary upload directory
    ↓
Validate:
    - File exists and readable
    - Extension in supported list
    - File size < 500MB
    ↓
Pass/Fail → Proceed or Return Error
```

**Validation Rules:**
- Allowed extensions: `.csv`, `.xlsx`, `.xls`, `.docx`, `.doc`, `.pdf`, `.txt`, `.940`, `.mt940`
- Maximum file size: 500 MB
- File must have read permission

### Step 2: Format Detection

**FormatRouter.detect_format()** determines file type:

```
Extension Check:
    .csv/.txt/.xlsx/.docx/.pdf/.940/.mt940
    ↓
Special Case - .txt file?
    YES → Analyze content:
        - Contains :XX: format tags? → "EP TXT" or "MT940"
        - Structured records? → "EP TXT"
        - Regular text → "Plain Text"
    NO → Use extension directly
    ↓
Return (format_type, detected_type_description)
```

**Detection Examples:**
- `invoice.csv` → ("csv", "CSV")
- `report.xlsx` → ("xlsx", "XLSX")
- `data.940` → ("mt940", "MT940")
- `structured.txt` → ("txt", "EP TXT" or "Plain Text")
- `document.pdf` → ("pdf", "PDF")

### Step 3: Format-Specific Routing

Each format routes to its dedicated parser:

```
CSV → CSVParser.parse()
    ├─ Detect delimiter (,;|\t)
    ├─ Parse with csv.DictReader
    ├─ Convert each row to dict
    └─ Return as single table

XLSX → XLSXParser.parse()
    ├─ Load workbook with pandas
    ├─ Iterate each sheet
    ├─ Convert to dict per row
    └─ Return per-sheet tables

DOCX → DOCXParser.parse()
    ├─ Load document with python-docx
    ├─ Extract paragraphs (narrative)
    ├─ Extract tables
    ├─ Detect headings by style
    └─ Return tables + narrative content

TXT → TXTParser.parse()
    ├─ Detect variant (Plain/EP TXT/MT940)
    ├─ If EP TXT: Parse :TAG: format
    ├─ If MT940: Parse banking records
    ├─ If Plain: Split paragraphs, detect tables
    └─ Return tables + content

MT940 → MT940Parser.parse()
    ├─ Regex parse SWIFT tags
    ├─ Extract account, balances, transactions
    ├─ Structure transaction details
    └─ Return as transaction table

PDF → PDFParser.parse()
    ├─ Check Java available
    ├─ Try LOCAL mode optimization
    │   ├─ Call OpenDataLoader –mode LOCAL
    │   ├─ Extract structured tables
    │   └─ Success? Keep and return
    ├─ If LOCAL fails → Try HYBRID mode
    │   ├─ Call OpenDataLoader –mode HYBRID
    │   ├─ Combines OCR + structure
    │   ├─ Reconstructs borderless tables
    │   └─ Success? Return
    └─ Return best result or error
```

### Step 4: Output Normalization

Every parser returns a standard structure:

```python
{
    "status": "success" | "error",
    "metadata": {
        "fileName": str,
        "fileSize": int,
        # Format-specific fields
    },
    "content": {
        # Non-tabular content (paragraphs, narrative, etc.)
    },
    "tables": [
        {
            "table_id": "table_001",
            "title": str,
            "columns": [list of column names],
            "rows": [list of {column: value} dicts]
        },
        # ... more tables
    ],
    "notes": [list of processing notes],
    "issues": [list of warnings/problems],
    "confidence": 0.0-1.0,
    "parser": "csv" | "xlsx" | "docx" | "txt" | "mt940" | "pdf"
}
```

### Step 5: Output Generation

The app creates two types of output files:

#### A. JSON Output (result.json)

Contains full document semantic:

```json
{
  "fileName": "invoice.xlsx",
  "fileType": "xlsx",
  "detectedType": "XLSX",
  "parserUsed": "xlsx",
  "processedAt": "2024-01-15T14:30:00.123456",
  "metadata": {
    "sheetCount": 3,
    "sheetNames": ["Invoices", "Items", "Summary"],
    "author": "Finance Dept"
  },
  "content": {
    "notes": ["Document contains fiscal 2023 data"],
    "headers": ["invoice information"],
    "footers": []
  },
  "tables": [
    {
      "table_id": "table_001",
      "title": "Invoices",
      "columns": ["InvoiceID", "Date", "Amount"],
      "rows": [
        {"InvoiceID": "INV001", "Date": "2024-01-01", "Amount": "1000"},
        {"InvoiceID": "INV002", "Date": "2024-01-02", "Amount": "2000"}
      ]
    }
  ],
  "notes": [
    "Extracted 3 sheets",
    "Found 2 transactions"
  ],
  "issues": [],
  "confidence": 0.98,
  "exports": {
    "jsonFile": "d:\\...\\result.json",
    "csvFiles": [
      "d:\\...\\data_table_001.csv",
      "d:\\...\\data_table_002.csv"
    ]
  }
}
```

#### B. CSV Output Files (data_table_001.csv, etc.)

One CSV per table with proper headers:

```
InvoiceID,Date,Amount
INV001,2024-01-01,1000
INV002,2024-01-02,2000
```

**CSV Generation Rules:**
1. Extract all unique column names from rows
2. Sort columns alphabetically for consistency
3. Write header row
4. Write data rows (empty cells as '')
5. One file per distinct table (not 'table' per sheet mix)

### Step 6: API Response

Web UI receives processing result:

```json
{
  "status": "success",
  "sessionId": "abc12345",
  "fileName": "invoice.xlsx",
  "fileType": "xlsx",
  "detectedType": "XLSX",
  "parserUsed": "xlsx",
  "metadata": { ... },
  "tableCount": 3,
  "tables": [
    {
      "id": "table_001",
      "title": "Invoices",
      "rowCount": 100,
      "columnCount": 5
    }
  ],
  "confidence": 0.98,
  "csvCount": 3,
  "jsonUrl": "/api/download/abc12345/json",
  "csvUrls": [
    "/api/download/abc12345/csv_1",
    "/api/download/abc12345/csv_2",
    "/api/download/abc12345/csv_3"
  ]
}
```

## Parser Details

### CSV Parser
- **Input**: Delimited file (,;|\t detected)
- **Processing**: Single pass with csv.DictReader
- **Output**: One table with detected columns
- **Edge cases**: Handles quoted fields, multi-line cells

### XLSX Parser
- **Input**: Excel workbook
- **Processing**: pandas.read_excel per sheet
- **Output**: One table per sheet
- **Edge cases**: Handles merged cells, formulas become values

### DOCX Parser
- **Input**: Office Open XML document
- **Processing**: 
  - Extract paragraphs with text analysis
  - Detect headings via style.name
  - Parse all embedded tables
- **Output**: Tables + narrative content
- **Edge cases**: Complex styles may not fully transfer

### TXT Parser
- **Input**: Plain text file
- **Processing**: Content analysis → variant detection
- **Output**: Varies by variant
  - Plain text: Paragraphs + detected tables
  - EP TXT: Structured fields as table row
  - MT940: Transaction records
- **Edge cases**: Encoding issues handled with 'replace' error handler

### MT940 Parser
- **Input**: SWIFT MT940 banking format
- **Processing**: Regex tag extraction and field mapping
- **Output**: Transaction table with balances
- **Fields extracted**:
  - :20: Transaction Reference
  - :25: Account ID
  - :28: Statement Number
  - :60: Opening Balance
  - :61: Transaction Detail
  - :86: Description
  - :62: Closing Balance
- **Edge cases**: Optional fields handled gracefully

### PDF Parser (OpenDataLoader)
- **Mode Selection**:
  - LOCAL (default): Optimized for structured digital PDFs
  - HYBRID (fallback): For scanned/complex PDFs
  - FULL (not used): Comprehensive but slower

- **Auto-Fallback Logic**:
  ```
  Try LOCAL:
      If successful and has tables: USE LOCAL
      If has few/no tables: Try HYBRID
      If borderless/grid issues: Try HYBRID
  
  Try HYBRID:
      If successful: USE HYBRID
      If OCR found text: USE HYBRID result
  
  Both fail: Return error + best attempt
  ```

- **Implementation Notes**:
  - Requires Java runtime (JRE 8+)
  - Requires OpenDataLoader JAR file
  - Set `OPENDATALOADER_JAR` environment variable
  - Handles scanned PDFs with OCR reconstruction
  - Reconstructs tables from layout

## Data Model

### Standardized Table Object

```python
{
    "table_id": "table_001",           # Unique identifier
    "title": "Transactions",            # Display name
    "columns": [                        # Header row
        "Date",
        "Reference",
        "Amount"
    ],
    "rows": [                          # Data rows
        {
            "Date": "2024-01-01",
            "Reference": "TXN001",
            "Amount": "1000.00"
        }
    ]
}
```

### Metadata Convention

Every document includes:
- `fileName`: Original filename
- `fileSize`: Bytes
- `fileType`: Extension-based type
- `detectedType`: Classification
- `parserUsed`: Module name
- Format-specific fields

## Error Handling Strategy

### Validation Errors
- **Trigger**: Before parsing (format/size validation)
- **Response**: 400 Bad Request with message
- **Example**: "File extension .xyz not supported"

### Parsing Errors
- **Trigger**: During parser execution
- **Response**: 
  - Return "error" status
  - Include error message in "issues"
  - Return empty tables[] array
  - Confidence set to 0.0
- **Example**: Corrupted Excel file → return empty tables

### Partial Success
- **Trigger**: Some content extracted but issues found
- **Response**:
  - Status remains "success"
  - Include relevant issues list
  - Confidence = 0.7-0.9 (reduced)
  - Return what could be extracted
- **Example**: "Could not parse sheet 2 due to encryption"

## Performance Characteristics

| Format | Typical Time | Factors |
|--------|----------|---------|
| CSV | < 0.5s | File size, delimiter detection |
| XLSX | 1-3s | Sheet count, row count |
| DOCX | 1-2s | Table complexity, content volume |
| TXT | < 1s | File size, variant detection |
| MT940 | < 1s | Record count, tag variety |
| PDF-LOCAL | 2-5s | Page count, table density |
| PDF-HYBRID | 5-20s | OCR processing, image resolution |

## Deployment Notes

### Single File Application
- All routes in one `app.py`
- No external dependencies beyond requirements.txt
- Stateless processing (results in outputs/ dir)
- Easy horizontal scaling (any instance can handle any request)

### Session Management
- Session ID = 8-char hex
- Output per session in `outputs/{session_id}/`
- Results available for download immediately
- Clean up old sessions manually (files persist)

### Resource Usage
- **Memory**: ~100MB base + 2-10x file size for processing
- **Disk**: Output size ≈ input size + overhead
- **CPU**: Single-core adequate for most files

## Extension Points

### Adding New Parser

1. Create `services/{format}/{format}_parser.py`
2. Implement class with `parse(file_path: str)` method
3. Return standard output structure
4. Add to `parse_by_format()` routing in app.py
5. Update `FormatRouter.SUPPORTED_FORMATS`

### Modifying Output Structure

- Edit `OutputWriter.create_output_structure()` for new fields
- Update JSON schema in result outputs
- Regenerate docs if API changes

### Custom Validation Rules

- Edit `Validators` class for new checks
- Modify `MAX_FILE_SIZE_MB` constant
- Add new MIME type mappings in FormatRouter

## Testing Recommendations

Create test files:
```
test_files/
  ├── sample.csv
  ├── sample.xlsx
  ├── sample.docx
  ├── sample.pdf (digital)
  ├── sample_scanned.pdf
  ├── sample.mt940
  ├── sample.txt
  └── sample_ep.txt
```

Test scenarios:
1. Normal upload and download
2. Large files (near 500MB limit)
3. Corrupted files
4. Unusual character encodings
5. Multiple tables/sheets
6. Empty files
7. Concurrent uploads
