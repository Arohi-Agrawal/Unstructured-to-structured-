from __future__ import annotations

import csv
from pathlib import Path

from services.parser_base import BaseParser, ParserResult, TableData, default_metadata


class TXTParser(BaseParser):
    parser_name = "txt_parser"
    implemented = True

    def __init__(self) -> None:
        self._candidate_encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]
        self._candidate_delimiters = [",", "\t", ";", "|"]

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        return detected_type in {"txt", "ep-txt", "mt940-as-txt"} or file_path.lower().endswith(".txt")

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        metadata = default_metadata(file_path.name)
        notes: list[str] = []
        issues: list[str] = []
        tables: list[TableData] = []

        text, encoding = self._read_text(file_path)
        metadata["rawLabelValues"] = {"encoding": encoding}
        metadata["reportName"] = file_path.stem

        lines = text.splitlines()
        non_empty_lines = [line.strip() for line in lines if line.strip()]
        metadata["narrativeText"] = non_empty_lines[:200]
        metadata["paragraphs"] = non_empty_lines[:200]
        metadata["summaryText"] = [f"Line count: {len(lines)}"]

        delimiter = self._detect_table_delimiter(non_empty_lines)
        if delimiter:
            notes.append(f"Detected delimited text table with delimiter: {repr(delimiter)}")
            table_rows = self._parse_delimited_rows(non_empty_lines, delimiter)
            if table_rows:
                columns = list(table_rows[0].keys())
                tables.append(
                    TableData(
                        table_id="table_001",
                        name="table_001_text_table",
                        columns=columns,
                        rows=table_rows,
                        source="txt_delimited",
                        confidence=0.85,
                    )
                )
        else:
            notes.append("No reliable tabular delimiter detected in TXT; returning metadata-only output.")

        if detected_type == "mt940-as-txt":
            notes.append("TXT content matches MT940 tags; consider uploading as .mt940 for richer parsing.")
        if detected_type == "ep-txt":
            notes.append("EP-style TXT detected and parsed as narrative/delimited text.")

        metadata["rawLabelValues"]["lineCount"] = str(len(lines))
        metadata["rawLabelValues"]["detectedDelimiter"] = delimiter or ""

        confidence = 0.85 if tables else 0.7
        return ParserResult(
            status="success",
            message="TXT parsed successfully.",
            implemented=True,
            parser_used="txt_generic",
            detected_type=detected_type,
            mode_used="text_delimited" if tables else "text_narrative",
            metadata=metadata,
            notes=notes,
            tables=tables,
            issues=issues,
            confidence=confidence,
        )

    def _read_text(self, file_path: Path) -> tuple[str, str]:
        for encoding in self._candidate_encodings:
            try:
                return file_path.read_text(encoding=encoding), encoding
            except UnicodeDecodeError:
                continue
        return file_path.read_text(encoding="utf-8", errors="replace"), "utf-8-replace"

    def _detect_table_delimiter(self, lines: list[str]) -> str | None:
        candidate_lines = lines[:60]
        best_delim = None
        best_score = 0
        for delim in self._candidate_delimiters:
            counts = [line.count(delim) for line in candidate_lines if delim in line]
            if len(counts) < 3:
                continue
            modal = max(set(counts), key=counts.count)
            score = counts.count(modal)
            if modal > 0 and score >= 3 and score > best_score:
                best_score = score
                best_delim = delim
        return best_delim

    def _parse_delimited_rows(self, lines: list[str], delimiter: str) -> list[dict[str, str]]:
        parsed_rows = [row for row in csv.reader(lines, delimiter=delimiter) if row and any(cell.strip() for cell in row)]
        if len(parsed_rows) < 2:
            return []

        header = [col.strip() if col.strip() else f"column_{i + 1}" for i, col in enumerate(parsed_rows[0])]
        rows: list[dict[str, str]] = []
        for row in parsed_rows[1:]:
            row_map = {}
            for i, column in enumerate(header):
                row_map[column] = row[i].strip() if i < len(row) else ""
            rows.append(row_map)
        return rows
