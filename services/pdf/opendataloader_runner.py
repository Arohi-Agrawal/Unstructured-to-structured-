from __future__ import annotations

import importlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.parser_base import TableData
from services.validators import Validators
from services.pdf.pdf_table_detector import PDFTableDetector


@dataclass
class PDFRunResult:
    success: bool
    mode_used: str
    parser_used: str
    raw_json: dict[str, Any] = field(default_factory=dict)
    markdown_text: str = ""
    tables: list[TableData] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    artifact_dir: str | None = None
    detected_scanned: bool = False
    ocr_words: list[dict[str, Any]] = field(default_factory=list)
    ocr_lines: list[dict[str, Any]] = field(default_factory=list)


class OpenDataLoaderRunner:
    HYBRID_ENGINE = "docling-fast"
    HYBRID_DPI = 320
    OCR_HINT = (
        "Scanned or image-only PDF detected. Install hybrid support with "
        "pip install -U \"opendataloader-pdf[hybrid]\" and run "
        "opendataloader-pdf-hybrid --port 5002 --force-ocr"
    )

    @staticmethod
    def check_dependencies() -> dict[str, Any]:
        issues: list[str] = []
        java_ok, java_message = Validators.check_java_version()
        if not java_ok:
            issues.append(java_message)

        pypdf_ok, pypdf_message = Validators.check_python_module(
            "pypdf",
            "Python package 'pypdf' is not installed. Run: pip install pypdf",
        )
        if not pypdf_ok and pypdf_message:
            issues.append(pypdf_message)

        try:
            importlib.import_module("opendataloader_pdf")
        except ImportError:
            issues.append("Python package 'opendataloader-pdf' is not installed. Run: pip install -U opendataloader-pdf")

        return {"ok": not issues, "issues": issues}

    @staticmethod
    def check_ocr_dependencies() -> dict[str, Any]:
        issues: list[str] = []
        for module_name, install_hint in (
            ("fitz", "Python package 'PyMuPDF' is not installed. Run: pip install PyMuPDF"),
            ("PIL", "Python package 'Pillow' is not installed. Run: pip install Pillow"),
            ("numpy", "Python package 'numpy' is not installed. Run: pip install numpy"),
        ):
            try:
                importlib.import_module(module_name)
            except ImportError:
                issues.append(install_hint)

        tesseract_ok = False
        try:
            import pytesseract

            tesseract_cmd = OpenDataLoaderRunner.resolve_tesseract_cmd()
            if tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            pytesseract.get_tesseract_version()
            tesseract_ok = True
        except Exception:
            tesseract_ok = False

        rapidocr_ok = False
        try:
            importlib.import_module("rapidocr_onnxruntime")
            rapidocr_ok = True
        except ImportError:
            rapidocr_ok = False

        if not tesseract_ok and not rapidocr_ok:
            issues.append(
                "No local OCR engine is available. Install Tesseract and either add it to PATH or set TESSERACT_CMD, "
                "or install rapidocr-onnxruntime."
            )

        return {"ok": not issues, "issues": issues}

    @staticmethod
    def resolve_tesseract_cmd() -> str | None:
        env_value = os.environ.get("TESSERACT_CMD")
        if env_value and Path(env_value).exists():
            return env_value

        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            str(Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"),
        ]
        for candidate in common_paths:
            if Path(candidate).exists():
                return candidate
        return None

    @classmethod
    def run_local(cls, file_path: Path) -> PDFRunResult:
        return cls._run(file_path, mode="local")

    @classmethod
    def run_hybrid(cls, file_path: Path) -> PDFRunResult:
        return cls._run(file_path, mode="hybrid")

    @classmethod
    def _run(cls, file_path: Path, mode: str) -> PDFRunResult:
        opendataloader_pdf = importlib.import_module("opendataloader_pdf")
        temp_dir = Path(tempfile.mkdtemp(prefix=f"odl_{mode}_"))

        kwargs: dict[str, Any] = {
            "input_path": [str(file_path)],
            "output_dir": str(temp_dir),
            "format": "markdown,json",
        }
        if mode == "hybrid":
            kwargs["hybrid"] = cls.HYBRID_ENGINE
            kwargs["force_ocr"] = True
            kwargs["dpi"] = cls.HYBRID_DPI

        notes = []
        issues = []
        success = True
        try:
            cls._invoke_convert_with_fallbacks(opendataloader_pdf, kwargs)
        except Exception as exc:
            retry_kwargs = dict(kwargs)
            retry_kwargs["format"] = "json"
            try:
                cls._invoke_convert_with_fallbacks(opendataloader_pdf, retry_kwargs)
                notes.append(f"Primary {mode} run failed, but retry with JSON-only output succeeded.")
            except Exception as retry_exc:
                success = False
                issues.append(f"OpenDataLoader {mode} mode failed: {exc}")
                issues.append(f"OpenDataLoader {mode} retry failed: {retry_exc}")
                if mode == "hybrid":
                    issues.append(cls.OCR_HINT)

        raw_json, markdown_text = cls._load_artifacts(temp_dir)
        tables = cls._extract_structured_tables(raw_json)
        notes.append(
            "Hybrid mode requested through opendataloader_pdf.convert(..., hybrid='docling-fast'). "
            f"Forced OCR requested with high-resolution rendering target ({cls.HYBRID_DPI} DPI). "
            "Hybrid server must allow forced OCR."
            if mode == "hybrid"
            else "Local mode requested through opendataloader_pdf.convert(...)."
        )

        return PDFRunResult(
            success=success and bool(raw_json or markdown_text),
            mode_used=mode,
            parser_used="opendataloader_pdf",
            raw_json=raw_json,
            markdown_text=markdown_text,
            tables=tables,
            notes=notes,
            issues=issues,
            artifact_dir=str(temp_dir),
            detected_scanned=cls.is_image_only_output(markdown_text),
        )

    @classmethod
    def _invoke_convert_with_fallbacks(cls, module, kwargs: dict[str, Any]) -> None:
        attempts = [dict(kwargs)]
        if "force_ocr" in kwargs:
            no_force = dict(kwargs)
            no_force.pop("force_ocr", None)
            attempts.append(no_force)
        if "dpi" in kwargs:
            no_dpi = dict(kwargs)
            no_dpi.pop("dpi", None)
            attempts.append(no_dpi)
        if "force_ocr" in kwargs and "dpi" in kwargs:
            plain = dict(kwargs)
            plain.pop("force_ocr", None)
            plain.pop("dpi", None)
            attempts.append(plain)

        last_error = None
        seen_payloads: set[tuple[tuple[str, str], ...]] = set()
        for attempt in attempts:
            signature = tuple(sorted((str(k), str(v)) for k, v in attempt.items()))
            if signature in seen_payloads:
                continue
            seen_payloads.add(signature)
            try:
                module.convert(**attempt)
                return
            except TypeError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = exc
                # For non-signature errors, still allow next fallback attempt.
                continue
        if last_error:
            raise last_error

    @staticmethod
    def is_image_only_output(markdown_text: str) -> bool:
        lines = [line.strip() for line in markdown_text.splitlines() if line.strip()]
        if not lines:
            return False
        image_lines = [line for line in lines if line.startswith("![image ")]
        return len(image_lines) == len(lines) or not any(PDFTableDetector.table_like_notes(line) for line in lines)

    @classmethod
    def _load_artifacts(cls, output_dir: Path) -> tuple[dict[str, Any], str]:
        json_payload: dict[str, Any] = {}
        markdown_text = ""

        json_candidates = list(output_dir.rglob("*.json"))
        markdown_candidates = [path for path in output_dir.rglob("*.md")]

        if json_candidates:
            best_payload: dict[str, Any] = {}
            best_score = -1
            best_name = ""
            for candidate in sorted(json_candidates):
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8", errors="ignore"))
                except json.JSONDecodeError:
                    continue
                score = cls._score_artifact_payload(payload)
                if score > best_score or (score == best_score and candidate.name < best_name):
                    best_payload = payload
                    best_score = score
                    best_name = candidate.name
            json_payload = best_payload

        if markdown_candidates:
            latest_md = sorted(markdown_candidates)[0]
            markdown_text = latest_md.read_text(encoding="utf-8", errors="ignore")

        return json_payload, markdown_text

    @classmethod
    def _score_artifact_payload(cls, payload: dict[str, Any]) -> int:
        score = 0
        score += len(cls._walk_table_nodes(payload)) * 100

        if isinstance(payload.get("kids"), list):
            score += len(payload["kids"])
        if isinstance(payload.get("children"), list):
            score += len(payload["children"])
        if isinstance(payload.get("tables"), list):
            score += len(payload["tables"]) * 50
        if isinstance(payload.get("markdown"), str):
            score += 10
        if isinstance(payload.get("content"), str):
            score += 5
        return score

    @classmethod
    def _extract_structured_tables(cls, payload: dict[str, Any]) -> list[TableData]:
        table_nodes = cls._walk_table_nodes(payload)
        tables: list[TableData] = []
        for index, node in enumerate(table_nodes, start=1):
            table = cls._node_to_table(node, index)
            if table and table.rows:
                tables.append(table)
        return tables

    @classmethod
    def _walk_table_nodes(cls, nodes: Any, seen: set[int] | None = None) -> list[dict[str, Any]]:
        if seen is None:
            seen = set()
        found: list[dict[str, Any]] = []
        if isinstance(nodes, dict):
            node_id = id(nodes)
            if node_id in seen:
                return found
            seen.add(node_id)

            node_type = str(nodes.get("type", "")).strip().lower()
            if "table" in node_type:
                found.append(nodes)

            for value in nodes.values():
                if isinstance(value, (dict, list)):
                    found.extend(cls._walk_table_nodes(value, seen))
        elif isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, (dict, list)):
                    found.extend(cls._walk_table_nodes(node, seen))
        return found

    @classmethod
    def _node_to_table(cls, node: dict[str, Any], index: int) -> TableData | None:
        row_nodes = node.get("rows", [])
        grid: dict[int, dict[int, str]] = {}
        max_col = -1
        page_numbers = [node.get("page number")] if node.get("page number") else []

        for row_node in row_nodes:
            row_number = int(row_node.get("row number", len(grid)))
            row_cells = row_node.get("cells", [])
            row_payload: dict[int, str] = {}
            for cell in row_cells:
                column_number = int(cell.get("column number", len(row_payload)))
                row_payload[column_number] = cls._flatten_cell_text(cell)
                max_col = max(max_col, column_number)
            if row_payload:
                grid[row_number] = row_payload

        if not grid:
            return None

        ordered_rows = []
        for row_number in sorted(grid):
            row = {f"column_{column + 1}": grid[row_number].get(column, "") for column in range(max_col + 1)}
            ordered_rows.append(row)

        header = ordered_rows[0] if ordered_rows else {}
        body = ordered_rows[1:] if len(ordered_rows) > 1 else []
        header_values = [value or f"column_{idx + 1}" for idx, value in enumerate(header.values())]
        normalized_rows = [
            {header_values[idx]: row.get(f"column_{idx + 1}", "") for idx in range(len(header_values))}
            for row in body
        ]
        return TableData(
            table_id=f"table_{index:03d}",
            name=f"Table {index}",
            columns=header_values,
            rows=normalized_rows,
            source="opendataloader_structured",
            page_numbers=[page for page in page_numbers if page],
            confidence=0.92,
        )

    @classmethod
    def _flatten_cell_text(cls, node: dict[str, Any]) -> str:
        texts: list[str] = []

        def walk(current: dict[str, Any]) -> None:
            content = current.get("content")
            if isinstance(content, str) and content.strip():
                texts.append(content.strip())
            for key in ("kids", "cells", "rows", "list items"):
                child = current.get(key)
                if isinstance(child, list):
                    for item in child:
                        if isinstance(item, dict):
                            walk(item)

        walk(node)
        return " ".join(texts).strip()
