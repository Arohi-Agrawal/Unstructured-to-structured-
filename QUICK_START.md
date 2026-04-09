# Quick Start: PDF to CSV/JSON
## Get Your Scanned PDF Data Now

### Step 1: Start the Backend Server
```powershell
# Open a PowerShell terminal in: d:\pdf conversion\document_app

python -m pip install -r requirements.txt

# Install hybrid support for scanned/OCR-heavy PDFs:
python -m pip install -U "opendataloader-pdf[hybrid]"

# For heavy/scanned PDFs (RECOMMENDED):
opendataloader-pdf-hybrid --port 5002 --force-ocr

# For lighter/digital PDFs (alternative):
opendataloader-pdf-hybrid --port 5002
```

### Step 2: Start the Flask App
```powershell
# Open another PowerShell terminal in: d:\pdf conversion\document_app

python app.py
```

### Step 3: Access the UI
```
Browser: http://127.0.0.1:8000
```

### Step 4: Upload Your PDF

1. Click "Choose File"
2. Select your scanned bank statement or PDF
3. Click "Upload & Process"
4. Wait 10-30 seconds

### Step 5: Download Results

✅ **JSON file** - Always available (all extracted data)
✅ **CSV file** - Now available even for scanned PDFs (best-effort)

---

## What You'll Get

### For Clean Digital PDFs (Light):
- JSON: Full metadata + perfect tables
- CSV: All transactions correctly mapped
- Confidence: 0.85+ (very reliable)

### For Scanned Bank Statements (Heavy):
- JSON: Available metadata + reconstructed tables
- CSV: Exported with [BEST-EFFORT] quality notes
- Confidence: 0.58-0.70 (usable, verify totals)
- Notes: Shows what was compromised during OCR

---

## Expected Flow (Scanned PDF)

```
Upload scanned_bank_statement.pdf
   ↓
System detects: "heavy" PDF (scanned, OCR-heavy)
   ↓
Route through DYNAMIC thresholds (relaxed validation)
   ↓
Reconstruct table from OCR (11 rows from 15 visible)
   ↓
Validation: "heavy PDF → export with [BEST-EFFORT] warnings"
   ↓
✅ Download JSON + CSV
   ├─ JSON: {"tables": [...], "notes": "[BEST-EFFORT] Visible rows partially missing...", ...}
   └─ CSV: Transaction data (best-effort reconstruction)
```

---

## Troubleshooting

### OCR Server Crashes
```
Error: "Cannot connect to opendataloader on port 5002"
```
**Solution:** Make sure server in Terminal 1 is running:
```powershell
# Terminal 1 - Check server is active
opendataloader-pdf-hybrid --port 5002 --force-ocr
# Should show: Listening on port 5002...
```

### Still Getting "PDF_TABLE_VALIDATION_FAILED"
```
Check the notes in JSON output:
```
- If it says `PDF classified as: light` → Your PDF isn't scanned enough
- If it says `PDF classified as: heavy` → Should have exported CSV
  - Try uploading again (timing-dependent OCR)
  - Check browser developer console (F12) for errors

### CSV is Empty or Wrong Data
```
Notes will show what went wrong:
- [BEST-EFFORT] Visible balance rows partially missing
  → Means some summary rows couldn't be reconstructed
- [BEST-EFFORT] Visible body rows partially missing
  → Means some transaction rows were lost in OCR

This is normal for scanned PDFs. Verify totals manually.
```

---

## Key Improvements

| Before | After |
|--------|-------|
| ❌ Scanned PDF → CSV blocked | ✅ Scanned PDF → CSV exported |
| ❌ No insight into why | ✅ [BEST-EFFORT] warnings explain quality |
| ❌ JSON + error message | ✅ JSON + CSV + quality notes |
| ❌ User manual data entry | ✅ User imports CSV (fixes ~95%) |

---

## File Output Structure

### result.json
```json
{
  "status": "success",
  "parser_used": "opendataloader_pdf",
  "detected_type": "scanned-pdf",
  "confidence": 0.62,
  "tables": [
    {
      "table_id": "table_001_statement",
      "columns": ["Value Date", "Description", "Reference", "Debit", "Credit", "Balance"],
      "rows": [...],
      "confidence": 0.62
    }
  ],
  "metadata": {
    "accountNumber": "...",
    "currency": "USD",
    ...
  },
  "notes": [
    "PDF classified as: heavy",
    "[BEST-EFFORT] Exporting tables with quality warnings because PDF is complex (scanned/OCR-heavy)",
    ...
  ],
  "issues": []
}
```

### result_table_001.csv
```
Value Date,Description,Reference,Debit,Credit,Balance
01 Jan 2024,Opening Balance,,,,1000.00
02 Jan 2024,Transfer,...,...,...,...
...
```

---

## Advanced Options

### Use DEFAULT (no OCR) mode
```powershell
opendataloader-pdf-hybrid --port 5002
# Good for: native digital PDFs
# Result: Faster, more accurate, but won't work on scanned
```

### Use DYNAMIC OpenDataLoader (Recommended)
```powershell
opendataloader-pdf-hybrid --port 5002 --force-ocr
# Good for: mixed PDFs (auto-detects and uses OCR when needed)
# Result: Slower but works on scanned + digital
```

---

## Support

If CSV still doesn't export:
1. Check `notes` in JSON for [BEST-EFFORT] status
2. Run with `--force-ocr` server option
3. Verify PDF has at least 2 visible rows of data
4. Check server logs (Terminal 1) for OCR errors

---

**Ready? Start Terminal 1 and Terminal 2 above, then go to http://127.0.0.1:8000**
