# Quick Start Guide

## 5-Minute Setup

### Step 1: Install Python Dependencies

```bash
cd document_app
pip install -r requirements.txt
```

Expected output:
```
Successfully installed fastapi-0.104.1 uvicorn-0.24.0 python-multipart-0.0.6 pandas-2.1.1 ...
```

### Step 2: Start the Application

```bash
python app.py
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Application startup complete
```

### Step 3: Open in Browser

Navigate to: **http://localhost:8000**

You should see the upload interface with a drag-and-drop zone.

## Usage Examples

### Example 1: Processing a CSV File

1. Go to http://localhost:8000
2. Drag `sales_data.csv` onto the upload box
3. Wait for processing (usually < 1 second)
4. View the results:
   - Detected Type: **CSV**
   - Parser Used: **csv**
   - Extracted 1 table with X rows
5. Download:
   - `result.json` - Metadata and table definition
   - `data_table_001.csv` - The extracted data

### Example 2: Processing an Excel File with Multiple Sheets

1. Upload `quarterly_report.xlsx` (3 sheets)
2. Results show:
   - Detected Type: **XLSX**
   - Parser Used: **xlsx**
   - 3 tables extracted (one per sheet)
3. Download outputs:
   - `result.json` - Complete metadata
   - `data_table_001.csv` - Sheet 1 data
   - `data_table_002.csv` - Sheet 2 data
   - `data_table_003.csv` - Sheet 3 data

### Example 3: Processing a Word Document

1. Upload `report.docx`
2. Results show:
   - Detected Type: **DOCX**
   - Parser Used: **docx**
   - Content includes paragraphs and 2 tables
3. JSON output contains:
   - Document metadata (author, title)
   - Extracted paragraphs (narrative content)
   - Embedded tables as structured data

### Example 4: Processing a PDF (if Java installed)

1. Install Java: `java -version` should work
2. Upload `scan.pdf` or `digital.pdf`
3. Results show:
   - Parsing Mode: **LOCAL** or **HYBRID**
   - Isscanned: **true/false**
   - Extracted tables

## Folder Structure After Processing

```
document_app/
├── uploads/                    # Temporary (cleaned after processing)
│   └── abc12345_document.xlsx  # Temporary copy
├── outputs/                    # Permanent results
│   ├── abc12345/               # Session folder
│   │   ├── result.json         # Complete metadata
│   │   ├── data_table_001.csv  # First table
│   │   └── data_table_002.csv  # Second table (if multiple)
│   └── def67890/               # Another session
│       ├── result.json
│       └── data_table_001.csv
└── app.py
```

## Common Commands

### Run with custom port
```bash
uvicorn app:app --port 8001
```

### Run in production mode
```bash
uvicorn app:app --host 0.0.0.0 --port 80 --workers 4
```

### Run with live reload (development)
```bash
uvicorn app:app --reload
```

### View Python version
```bash
python --version
```

### Check installed packages
```bash
pip list
```

### Upgrade packages
```bash
pip install --upgrade -r requirements.txt
```

## Supported File Formats Reference

| Format | Extension | Parser | Use Case |
|--------|-----------|--------|----------|
| CSV | `.csv` | csv | Tabular data, exported from databases/Excel |
| Excel | `.xlsx`, `.xls` | xlsx | Business reports, multiple sheets |
| Word | `.docx` | docx | Documents with tables and text |
| PDF | `.pdf` | pdf | Digital or scanned documents |
| MT940 | `.940`, `.mt940` | mt940 | SWIFT banking transactions |
| Text | `.txt` | txt | Structured or plain text data |

## Output File Reference

### result.json Structure

```json
{
  "fileName": "input_file.xlsx",
  "fileType": "xlsx",
  "detectedType": "XLSX",
  "parserUsed": "xlsx",
  "processedAt": "2024-01-15T14:30:00",
  "metadata": { /* format-specific metadata */ },
  "content": { /* non-tabular content if any */ },
  "tables": [ /* array of table objects */ ],
  "notes": [ /* processing notes */ ],
  "issues": [ /* any warnings */ ],
  "confidence": 0.95,
  "exports": {
    "jsonFile": "path/to/result.json",
    "csvFiles": ["path/to/data_table_001.csv", ...]
  }
}
```

### CSV Files

Each table becomes a separate CSV file:
- Headers on first row
- Data rows with matching columns
- UTF-8 encoding
- Blank cells for missing values

## Troubleshooting

### Port 8000 already in use

**Error**: `Address already in use: ('0.0.0.0', 8000)`

**Solution**: Use different port
```bash
uvicorn app:app --port 8001
```

### Module not found errors

**Error**: `ModuleNotFoundError: No module named 'fastapi'`

**Solution**: Install requirements
```bash
pip install -r requirements.txt
```

### File upload fails

**Error**: `File is not readable`

**Causes**:
- File permission issue
- File path contains special characters
- File is locked by another process

**Solution**: Close any open file handlers, check permissions

### PDF processing not available

**Error**: `Java not found - PDF parsing requires Java for OpenDataLoader`

**Solution**: Install Java
```bash
# Windows
# Download from java.com or install via chocolatey
choco install jre8

# Ubuntu/Debian
sudo apt-get install default-jre

# Check installation
java -version
```

### Large file hangs

**Behavior**: Upload seems stuck

**Causes**:
- Processing large file (5+ GB)
- Insufficient disk space
- Memory issues

**Solution**:
- Increase timeout in uvicorn
- Use smaller files
- Check available disk space: `df -h` (Linux) or `dir C:` (Windows)

## Next Steps

1. **Read** [README.md](README.md) for complete documentation
2. **Review** [ARCHITECTURE.md](ARCHITECTURE.md) for technical details
3. **Extend** by adding custom parsers for new formats
4. **Deploy** using the production configuration
5. **Monitor** output quality with sample files

## API Endpoints (Direct Access)

If you want to integrate with other systems:

### Process File (POST)
```bash
curl -X POST http://localhost:8000/api/process \
  -F "file=@document.xlsx"
```

### Download Result (GET)
```bash
# Download JSON
curl http://localhost:8000/api/download/abc12345/json > result.json

# Download CSV (table 1)
curl http://localhost:8000/api/download/abc12345/csv_1 > data.csv
```

## Performance Expectations

- **CSV (1MB)**: 0.3 seconds
- **XLSX (5MB, 3 sheets)**: 1.5 seconds
- **DOCX (2MB)**: 1.2 seconds
- **TXT (1MB)**: 0.2 seconds
- **MT940 (500KB)**: 0.5 seconds
- **PDF (10 pages, digital)**: 3 seconds
- **PDF (20 pages, scanned)**: 12 seconds

---

**Need help?** Check the [README.md](README.md) or review [ARCHITECTURE.md](ARCHITECTURE.md)
