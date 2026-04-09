from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd

from services.parser_base import BaseParser, ParserResult, TableData, default_metadata


def _sanitize_cell(value: Any) -> Any:
    if pd.isna(value):
        return ""
    text = str(value)
    return text.strip()


class CSVParser(BaseParser):
    parser_name = "csv_parser"
    implemented = True

    def __init__(self) -> None:
        self._candidate_delimiters = [",", ";", "\t", "|"]

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        return detected_type == "csv" or file_path.lower().endswith(".csv")

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        metadata = default_metadata(file_path.name)
        notes: list[str] = []
        issues: list[str] = []

        delimiter = self._detect_delimiter(file_path)
        if delimiter:
            notes.append(f"Detected delimiter: {repr(delimiter)}")
        else:
            notes.append("Could not confidently detect delimiter; defaulted to comma.")
            delimiter = ","

        try:
            dataframe = pd.read_csv(
                file_path,
                sep=delimiter,
                dtype=str,
                keep_default_na=False,
                engine="python",
                on_bad_lines="warn",
            )
        except Exception as exc:
            return ParserResult(
                status="error",
                message="CSV parsing failed.",
                implemented=True,
                parser_used="pandas_csv",
                detected_type=detected_type,
                mode_used="tabular_failed",
                metadata=metadata,
                notes=notes,
                issues=[f"Failed to parse CSV: {exc}"],
                confidence=0.0,
            )

        columns = [str(col).strip() for col in dataframe.columns.tolist()]
        rows: list[dict[str, Any]] = []
        for _, record in dataframe.iterrows():
            row = {col: _sanitize_cell(record[col]) for col in dataframe.columns}
            rows.append(row)

        metadata["reportName"] = file_path.stem
        metadata["rawLabelValues"] = {
            "delimiter": delimiter,
            "rowCount": str(len(rows)),
            "columnCount": str(len(columns)),
        }
        metadata["headers"] = columns
        metadata["summaryText"] = [f"Rows: {len(rows)}", f"Columns: {len(columns)}"]

        table = TableData(
            table_id="table_001",
            name="table_001_data",
            columns=columns,
            rows=rows,
            source="csv_file",
            confidence=0.95 if rows else 0.6,
        )

        return ParserResult(
            status="success",
            message="CSV parsed successfully.",
            implemented=True,
            parser_used="pandas_csv",
            detected_type=detected_type,
            mode_used="tabular",
            metadata=metadata,
            notes=notes,
            tables=[table],
            issues=issues,
            confidence=0.95 if rows else 0.6,
        )

    def _detect_delimiter(self, file_path: Path) -> str | None:
        sample = file_path.read_text(encoding="utf-8", errors="ignore")[:4096]
        if not sample.strip():
            return ","
        try:
            sniffed = csv.Sniffer().sniff(sample, delimiters=self._candidate_delimiters)
            return sniffed.delimiter
        except Exception:
            counts = {d: sample.count(d) for d in self._candidate_delimiters}
            best_delimiter = max(counts, key=counts.get)
            if counts[best_delimiter] > 0:
                return best_delimiter
            return None
