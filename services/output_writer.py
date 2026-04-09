from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from services.format_router import RouteResult
from services.parser_base import ParserResult, TableData, default_metadata
from services.validators import Validators


class OutputWriter:
    @staticmethod
    def persist_result(output_dir: Path, upload_path: Path, parse_result: ParserResult, route: RouteResult) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_files = OutputWriter._write_tables(output_dir, parse_result.tables)
        json_file = OutputWriter._write_json_export(output_dir, upload_path.name, route.format_type, parse_result, csv_files)

        payload = {
            "fileName": upload_path.name,
            "fileType": route.format_type,
            "detectedType": parse_result.detected_type or route.detected_type,
            "parserImplemented": parse_result.implemented,
            "parserUsed": parse_result.parser_used,
            "modeUsed": parse_result.mode_used,
            "metadata": parse_result.metadata or default_metadata(upload_path.name),
            "notes": parse_result.notes,
            "tables": [table.to_dict() for table in parse_result.tables],
            "issues": parse_result.issues,
            "confidence": parse_result.confidence,
            "validation_warnings": (parse_result.metadata or {}).get("validationWarnings", []),
            "exports": {
                "jsonFile": json_file,
                "csvFiles": csv_files,
            },
        }

        with (output_dir / "result.json").open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)

        payload["jobId"] = output_dir.name
        return payload

    @staticmethod
    def _write_tables(output_dir: Path, tables: list[TableData]) -> list[str]:
        csv_files: list[str] = []
        for index, table in enumerate(tables, start=1):
            if not table.rows:
                continue
            columns, normalized_rows = OutputWriter._normalize_table_for_csv(table, index)
            if not columns or not normalized_rows:
                continue
            table.columns = columns
            table.rows = normalized_rows
            safe_name = OutputWriter._slugify(table.name or f"table_{index:03d}") or f"table_{index:03d}"
            file_name = f"{safe_name}.csv"
            table.file_name = file_name
            with (output_dir / file_name).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(normalized_rows)
            csv_files.append(file_name)
        return csv_files

    @staticmethod
    def _normalize_table_for_csv(table: TableData, index: int) -> tuple[list[str], list[dict]]:
        declared = [str(col).strip() for col in (table.columns or []) if str(col).strip()]
        extra_keys: list[str] = []

        # Preserve declared schema order first, then add observed keys deterministically.
        for row in table.rows:
            if not isinstance(row, dict):
                continue
            for key in row.keys():
                key_str = str(key).strip()
                if not key_str:
                    continue
                if key_str not in declared and key_str not in extra_keys:
                    extra_keys.append(key_str)

        columns = declared + extra_keys
        if not columns:
            columns = [f"column_{index:03d}"]

        normalized_rows: list[dict] = []
        for row in table.rows:
            if isinstance(row, dict):
                normalized = {column: row.get(column, "") for column in columns}
            else:
                normalized = {column: "" for column in columns}
                normalized[columns[0]] = str(row)
            normalized_rows.append(normalized)

        return columns, normalized_rows

    @staticmethod
    def _write_json_export(
        output_dir: Path,
        file_name: str,
        file_type: str,
        parse_result: ParserResult,
        csv_files: list[str],
    ) -> str:
        export_name = "non_tabular_metadata.json"
        payload = {
            "fileName": file_name,
            "fileType": file_type,
            "detectedType": parse_result.detected_type,
            "parserUsed": parse_result.parser_used,
            "modeUsed": parse_result.mode_used,
            "metadata": parse_result.metadata or default_metadata(file_name),
            "notes": parse_result.notes,
            "issues": parse_result.issues,
            "confidence": parse_result.confidence,
            "validation_warnings": (parse_result.metadata or {}).get("validationWarnings", []),
            "exports": {
                "jsonFile": export_name,
                "csvFiles": csv_files,
            },
        }
        with (output_dir / export_name).open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        return export_name

    @staticmethod
    def _slugify(value: str) -> str:
        lowered = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower())
        lowered = lowered.strip("_")
        return Validators.sanitize_filename(lowered)
