from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from services.parser_base import BaseParser, ParserResult, TableData, default_metadata


def _clean_cell(value: Any) -> Any:
    if pd.isna(value):
        return ""
    return str(value).strip()


class XLSXParser(BaseParser):
    parser_name = "excel_parser"
    implemented = True

    def __init__(self) -> None:
        self._default_engine = "openpyxl"

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        lower = file_path.lower()
        return detected_type in {"xlsx", "xls"} or lower.endswith(".xlsx") or lower.endswith(".xls")

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        metadata = default_metadata(file_path.name)
        notes: list[str] = []
        issues: list[str] = []

        engine = self._default_engine
        if file_path.suffix.lower() == ".xls":
            engine = None
            notes.append("Using pandas default engine for .xls input (requires xlrd).")

        try:
            workbook = pd.read_excel(
                file_path,
                sheet_name=None,
                dtype=str,
                keep_default_na=False,
                engine=engine,
            )
        except Exception as exc:
            return ParserResult(
                status="error",
                message="Excel parsing failed.",
                implemented=True,
                parser_used="pandas_excel",
                detected_type=detected_type,
                mode_used="workbook_failed",
                metadata=metadata,
                notes=notes,
                issues=[f"Failed to parse workbook: {exc}"],
                confidence=0.0,
            )

        tables: list[TableData] = []
        sheet_stats: dict[str, dict[str, int]] = {}
        for index, (sheet_name, frame) in enumerate(workbook.items(), start=1):
            normalized = frame.fillna("").astype(str)
            columns = [str(col).strip() for col in normalized.columns.tolist()]
            rows: list[dict[str, Any]] = []
            for _, record in normalized.iterrows():
                row = {col: _clean_cell(record[col]) for col in normalized.columns}
                rows.append(row)

            table_name = f"table_{index:03d}_{sheet_name.strip().lower().replace(' ', '_') or f'sheet_{index}'}"
            tables.append(
                TableData(
                    table_id=f"table_{index:03d}",
                    name=table_name,
                    columns=columns,
                    rows=rows,
                    source="excel_sheet",
                    confidence=0.94 if rows else 0.6,
                )
            )
            sheet_stats[sheet_name] = {"rowCount": len(rows), "columnCount": len(columns)}

        metadata["reportName"] = file_path.stem
        metadata["rawLabelValues"] = {"workbook": file_path.name}
        metadata["summaryText"] = [f"Sheets parsed: {len(workbook)}"]
        metadata["headings"] = list(workbook.keys())
        metadata["headers"] = list(workbook.keys())
        metadata["paragraphs"] = [f"{name}: {stats['rowCount']} rows, {stats['columnCount']} cols" for name, stats in sheet_stats.items()]
        metadata["rawLabelValues"]["sheets"] = sheet_stats

        confidence = 0.94 if tables else 0.0
        return ParserResult(
            status="success",
            message="Excel workbook parsed successfully.",
            implemented=True,
            parser_used="pandas_excel",
            detected_type=detected_type,
            mode_used="workbook",
            metadata=metadata,
            notes=notes,
            tables=tables,
            issues=issues,
            confidence=confidence,
        )
