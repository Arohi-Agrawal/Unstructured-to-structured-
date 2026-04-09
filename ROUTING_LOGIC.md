# Routing Logic - Complete Reference

## Visual Flow Chart

```
┌─────────────────────────────────────────────────────────────────┐
│                    File Upload Received                         │
│                                                                 │
│     POST /api/process with multipart/form-data file            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
            ┌──────────────────────────────────┐
            │ Save Temporary Upload            │
            │ uploads/{session_id}_{filename}  │
            └──────────────┬───────────────────┘
                           │
                           ▼
            ┌──────────────────────────────────┐
            │ Validate File                    │
            │ - Exists?                        │
            │ - Readable?                      │
            │ - Size < 500MB?                  │
            │ - Extension allowed?             │
            └──────────────┬───────────────────┘
                           │
                    No ────┴──── Yes
                    │             │
                    ▼             ▼
            ┌──────────────┐  ┌─────────────────────┐
            │ Return Error │  │ Detect Format       │
            │ to User      │  │ FormatRouter.       │
            └──────────────┘  │ detect_format()     │
                              └──────────┬──────────┘
                                         │
                ┌────────────────────────┼────────────────────────┐
                │                        │                        │
                ▼                        ▼                        ▼
        ┌─────────────────┐    ┌───────────────────┐   ┌───────────────┐
        │ Extensionl.csv  │    │ Extension=.txt?   │   │ Other formats │
        │                 │    │                   │   │ (easy route)  │
        │ → CSV Parser    │    │ YES: Content      │   │ → Direct to   │
        │                 │    │ analysis          │   │   parser      │
        │ Delimiter       │    │                   │   └───────────────┘
        │ detection       │    │ Check for:        │
        └─────────────────┘    │ - :20:,:25: tags? │
                               │   → MT940/EP TXT  │
                               │ - Multiple lines  │
                               │   with colons?    │
                               │   → EP TXT        │
                               │ - Regular text?   │
                               │   → Plain Text    │
                               │                   │
                               └─────────┬─────────┘
                                         │
         ┌───────────────┬───────────────┼────────────┬──────────────┐
         │               │               │            │              │
         ▼               ▼               ▼            ▼              ▼
    ┌────────┐  ┌─────────────┐  ┌──────────┐  ┌─────────┐  ┌─────────────┐
    │ CSV    │  │ XLSX        │  │ DOCX     │  │ MT940   │  │ PDF         │
    │Parser  │  │ Parser      │  │ Parser   │  │ Parser  │  │ Parser      │
    └────┬───┘  └──────┬──────┘  └────┬─────┘  └────┬────┘  └─────┬───────┘
         │             │              │             │              │
         │             ▼              │             │              ▼
         │     ┌──────────────────┐   │             │     ┌──────────────────┐
         │     │ For each sheet:  │   │             │     │ Check Java       │
         │     │ - Read with      │   │             │     │ available?       │
         │     │   pandas         │   │             │     └────┬──────────────┘
         │     │ - Convert to     │   │             │          │
         │     │   list of dicts  │   │             │    Yes───┴───No
         │     │ - Add to tables  │   │             │     │       │
         │     └──────────────────┘   │             │     │       ▼
         │                            │             │     │   ┌─────────────┐
         │                            ▼             │     │   │ Java not    │
         │                    ┌──────────────────┐  │     │   │ found       │
         │                    │ Extract:         │  │     │   │             │
         │                    │ - Paragraphs     │  │     │   │ Return      │
         │                    │   (narrative)    │  │     │   │ warning,    │
         │                    │ - Tables         │  │     │   │ no PDF      │
         │                    │ - Headings       │  │     │   │ parsing     │
         │                    │ - Properties     │  │     │   └─────────────┘
         │                    │   (author, etc)  │  │     │
         │                    └──────────────────┘  │     │
         │                                          │     │
         │                            ▼             │     ▼
         │                    ┌──────────────────┐  │   ┌──────────────────┐
         │                    │ Regex parse      │  │   │ Try LOCAL Mode   │
         │                    │ :TAG: format     │  │   │ OpenDataLoader   │
         │                    │ Extract:         │  │   │ –mode LOCAL      │
         │                    │ - Account ID     │  │   │                  │
         │                    │ - Transactions   │  │   │ Check result:    │
         │                    │ - Balances       │  │   │ - Has tables?    │
         │                    └──────────────────┘  │   │ - Good qual?     │
         │                                          │   └────┬─────────────┘
         ▼                                          ▼        │
    ┌──────────────────────────────────────────────────┐  Yes─┴─No
    │                                                  │    │   │
    │ All Parsers Return Standard Structure:          │    │   ▼
    │                                                  │    │  ┌──────────────────┐
    │ {                                                │    │  │ Try HYBRID Mode  │
    │   "status": "success|error",                    │    │  │ OpenDataLoader   │
    │   "metadata": {...},                            │    │  │ –mode HYBRID     │
    │   "content": {...},                             │    │  │                  │
    │   "tables": [{...}, ...],                        │    │  │ (with OCR)       │
    │   "notes": [...],                               │    │  │                  │
    │   "issues": [...],                              │    │  │ Check result:    │
    │   "confidence": 0.0-1.0,                        │    │  │ - Text found?    │
    │   "parser": "csv|xlsx|docx|txt|mt940|pdf"       │    │  │ - Tables found?  │
    │ }                                                │    │  └────┬────────────┘
    │                                                  │    │       │
    └──────────────────┬───────────────────────────────┘    │   Success─┘
                       │                                    │
                       ▼                                    ▼
         ┌──────────────────────────────────┐   ┌──────────────────────────┐
         │ Normalize Output Structure       │   │ Format as PDF Result     │
         │                                  │   │ - Mode: LOCAL/HYBRID     │
         │ - Extract tables & rows          │   │ - Is scanned: true/false │
         │ - Build metadata JSON            │   │ - Confidence: 0.85-0.95  │
         │ - Organize content               │   └──────────────┬───────────┘
         │ - Calculate confidence           │                  │
         └──────────────┬───────────────────┘                  │
                        │◄─────────────────────────────────────┘
                        │
                        ▼
         ┌──────────────────────────────────┐
         │ Generate Output Files            │
         │                                  │
         │ (a) Write JSON                   │
         │     {session_id}/result.json     │
         │     - Full metadata              │
         │     - All tables                 │
         │     - All content                │
         │                                  │
         │ (b) Write CSVs                   │
         │     For each table:              │
         │     {session_id}/                │
         │     data_table_001.csv           │
         │     data_table_002.csv           │
         │     etc.                         │
         │                                  │
         └──────────────┬───────────────────┘
                        │
                        ▼
         ┌──────────────────────────────────┐
         │ Build API Response               │
         │                                  │
         │ {                                │
         │   status: "success",             │
         │   sessionId: "abc12345",         │
         │   fileName: "...",               │
         │   fileType: "xlsx",              │
         │   detectedType: "XLSX",          │
         │   parserUsed: "xlsx",            │
         │   tableCount: 3,                 │
         │   tables: [...],                 │
         │   confidence: 0.98,              │
         │   csvCount: 3,                   │
         │   jsonUrl: "/api/download/...",  │
         │   csvUrls: ["...", "...", "..."]│
         │ }                                │
         │                                  │
         └──────────────┬───────────────────┘
                        │
                        ▼
         ┌──────────────────────────────────┐
         │ Return to Web UI                 │
         │                                  │
         │ - Display file info              │
         │ - Show table summary             │
         │ - Show metadata preview          │
         │ - Provide download links         │
         │ - Show any issues/notes          │
         │                                  │
         └──────────────────────────────────┘
```

## Format-Specific Routing Detail

### CSV Format Route

```
CSV File Upload
    │
    ▼
FormatRouter.detect_format()
    → Extension: .csv
    → Return: ('csv', 'CSV')
    │
    ▼
parse_by_format('file.csv', 'csv', 'CSV')
    → parsers['csv'] = CSVParser.parse
    │
    ▼
CSVParser.parse()
    │
    ├─ Read file content
    ├─ Detect delimiter:
    │   ├─ Count ',' → high? → Use ','
    │   ├─ Count ';' → high? → Use ';'
    │   ├─ Count '\t' → high? → Use '\t'
    │   └─ Default: ','
    │
    ├─ Parse with csv.DictReader
    │   └─ First row = headers
    │   └─ Remaining rows = data
    │
    ├─ Create table object:
    │   {
    │     "table_id": "table_001",
    │     "title": "CSV Data",
    │     "columns": [...],
    │     "rows": [...]
    │   }
    │
    └─ Return standard result
```

### XLSX Format Route

```
XLSX File Upload
    │
    ▼
FormatRouter.detect_format()
    → Extension: .xlsx or .xls
    → Return: ('xlsx', 'XLSX')
    │
    ▼
parse_by_format('file.xlsx', 'xlsx', 'XLSX')
    → parsers['xlsx'] = XLSXParser.parse
    │
    ▼
XLSXParser.parse()
    │
    ├─ Load workbook: pd.ExcelFile()
    ├─ Get sheet names
    │
    ├─ For each sheet:
    │   ├─ Read: pd.read_excel(sheet_name=sheet)
    │   ├─ Convert to list of dicts
    │   ├─ Create table:
    │   │   {
    │   │     "table_id": "table_001",
    │   │     "title": {sheet_name},
    │   │     "columns": [...],
    │   │     "rows": [...]
    │   │   }
    │   └─ Add to tables array
    │
    └─ Return standard result
```

### DOCX Format Route

```
DOCX File Upload
    │
    ▼
FormatRouter.detect_format()
    → Extension: .docx or .doc
    → Return: ('docx', 'DOCX')
    │
    ▼
parse_by_format('file.docx', 'docx', 'DOCX')
    → parsers['docx'] = DOCXParser.parse
    │
    ▼
DOCXParser.parse()
    │
    ├─ Load: Document('file.docx')
    │
    ├─ Extract paragraphs:
    │   └─ Iterate doc.paragraphs
    │   └─ Collect text (narrative content)
    │
    ├─ Detect headings:
    │   └─ Check style.name
    │   └─ If contains 'Heading' → add to headings
    │
    ├─ Extract tables:
    │   └─ Iterate doc.tables
    │   └─ First row = headers (optional)
    │   └─ Remaining rows = data
    │   └─ Create table object per doc table
    │
    ├─ Extract metadata:
    │   ├─ core_properties.author
    │   ├─ core_properties.title
    │   └─ core_properties.subject
    │
    └─ Return standard result
```

### TXT Format Route

```
TXT File Upload
    │
    ▼
FormatRouter.detect_format()
    → Extension: .txt
    → Analyze content for variant
    │
    ├─ Contains ":20:", ":25:" tags?
    │   └─ YES → Return ('txt', 'MT940')
    │
    ├─ Contains ":tag:" patterns?
    │   └─ YES → Return ('txt', 'EP TXT')
    │
    └─ NO → Return ('txt', 'Plain Text')
    │
    ▼
parse_by_format('file.txt', 'txt', detected_type)
    → parsers['txt'] = TXTParser.parse
    │
    ▼
TXTParser.parse(file_path, detected_type)
    │
    ├─ If detected_type == 'EP TXT':
    │   └─ _parse_ep_txt()
    │       ├─ Regex: r':(\d+):(.*?)'
    │       ├─ Extract {Field_XX: value} pairs
    │       ├─ Create single row table
    │       └─ Return with extracted fields
    │
    ├─ If detected_type == 'MT940':
    │   └─ _parse_mt940_txt()
    │       ├─ Regex MT940 tags
    │       ├─ Extract transactions
    │       ├─ Build transaction table
    │       └─ Return with transactions
    │
    └─ If detected_type == 'Plain Text':
        └─ _parse_plain_text()
            ├─ Split into paragraphs
            ├─ Detect pipe|delimited tables
            ├─ Create table if found
            └─ Return paragraphs + tables
```

### MT940 Format Route

```
MT940 File Upload
    │
    ▼
FormatRouter.detect_format()
    → Extension: .940 or .mt940
    → Return: ('mt940', 'MT940')
    │
    ▼
parse_by_format('file.940', 'mt940', 'MT940')
    → parsers['mt940'] = MT940Parser.parse
    │
    ▼
MT940Parser.parse()
    │
    ├─ Regex parse content
    │   └─ Pattern: r':(\d+):(.*?)(?=:[0-9]:|$)'
    │   └─ Extract (tag, value) pairs
    │
    ├─ Build metadata:
    │   ├─ :20: → transaction_reference
    │   ├─ :25: → account_id
    │   ├─ :28: → statement_number
    │   ├─ :60: → opening_balance
    │   └─ :62: → closing_balance
    │
    ├─ Extract transactions:
    │   └─ For :61: tags
    │   └─ _parse_transaction_line()
    │   └─ Extract: date, amount, type
    │
    ├─ Create transaction table:
    │   {
    │     "table_id": "table_001",
    │     "title": "Transactions",
    │     "columns": [...],
    │     "rows": [...]
    │   }
    │
    └─ Return standard result
```

### PDF Format Route

```
PDF File Upload
    │
    ▼
FormatRouter.detect_format()
    → Extension: .pdf
    → Return: ('pdf', 'PDF')
    │
    ▼
parse_by_format('file.pdf', 'pdf', 'PDF')
    → parsers['pdf'] = PDFParser.parse
    │
    ▼
PDFParser.parse()
    │
    ├─ Check Java available?
    │   └─ subprocess.run(['java', '-version'])
    │
    ├─ NO → Return warning,
    │   └─ "Java not found"
    │   └─ No PDF parsing capability
    │
    └─ YES → Continue
        │
        ▼
        Try LOCAL Mode
        │
        ├─ Call _try_local_mode(file_path)
        ├─ Command: java -jar odl.jar --mode LOCAL ...
        │
        ├─ Success? Has tables?
        │   └─ YES → Return LOCAL result
        │       └─ confidence: 0.95
        │
        └─ NO or weak → Try HYBRID
            │
            ▼
            Try HYBRID Mode
            │
            ├─ Call _try_hybrid_mode(file_path)
            ├─ Command: java -jar odl.jar --mode HYBRID ...
            │
            ├─ Success? Found content?
            │   └─ YES → Return HYBRID result
            │       ├─ scanned_detected: true
            │       └─ confidence: 0.85
            │
            └─ NO → Return error
                └─ Both modes failed
```

## Error Handling Routes

```
Process Error?
    │
    ├─ Validation Error
    │   ├─ Invalid extension
    │   ├─ File too large
    │   ├─ File not readable
    │   └─ Action: Return 400 Bad Request
    │
    ├─ Parser Error
    │   ├─ Corrupted file
    │   ├─ Wrong format
    │   ├─ Missing dependency
    │   └─ Action: Return error status,
    │       │       empty tables,
    │       │       confidence = 0.0
    │
    ├─ Partial Success
    │   ├─ Some sheets unreadable
    │   ├─ Some tables malformed
    │   ├─ Some content skipped
    │   └─ Action: Return success status,
    │       │       partial data,
    │       │       reduced confidence,
    │       │       issues list
    │
    └─ System Error
        ├─ Disk full
        ├─ Out of memory
        ├─ Permission denied
        └─ Action: Return 500 Server Error
```

## Output Generation Routes

```
Parse Result Received
    │
    ▼
OutputWriter.create_output_structure()
    ├─ Build metadata
    ├─ Include tables
    ├─ Include content
    ├─ Calculate confidence
    └─ Create base JSON
    │
    ▼
Write JSON
    │
    ├─ Create outputs/{session_id}/
    ├─ Write result.json
    │   ├─ Complete metadata
    │   ├─ All tables
    │   ├─ All content
    │   ├─ Processing notes
    │   └─ File paths
    │
    └─ Available for download
        │
        ▼
Write CSVs (one per table)
    │
    ├─ For each table in tables[]:
    │   ├─ Extract rows
    │   ├─ Get unique columns
    │   ├─ Write CSV:
    │   │   ├─ Headers
    │   │   ├─ Data rows
    │   │   └─ UTF-8 encoding
    │   │
    │   └─ File: data_table_NNN.csv
    │
    └─ All available for download
```

## Summary: Format-to-Parser Mapping

| File Extension | Detected Type | Parser Module | Route |
|---|---|---|---|
| .csv | CSV | csv_parser.py | `CSVParser.parse()` |
| .xlsx/.xls | XLSX | xlsx_parser.py | `XLSXParser.parse()` |
| .docx/.doc | DOCX | docx_parser.py | `DOCXParser.parse()` |
| .pdf | PDF | pdf_parser.py | `PDFParser.parse()` |
| .940/.mt940 | MT940 | mt940_parser.py | `MT940Parser.parse()` |
| .txt + ":XX:" | EP TXT | txt_parser.py | `TXTParser._parse_ep_txt()` |
| .txt + MT940 | MT940 (TXT) | txt_parser.py | `TXTParser._parse_mt940_txt()` |
| .txt + plain | Plain Text | txt_parser.py | `TXTParser._parse_plain_text()` |

---

This routing logic ensures each file type follows the optimal processing path with appropriate format detection, parsing, output generation, and error handling.
