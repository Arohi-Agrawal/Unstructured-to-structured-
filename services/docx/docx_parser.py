from __future__ import annotations

from pathlib import Path

from docx import Document

from services.parser_base import BaseParser, ParserResult, TableData, default_metadata


class DOCXParser(BaseParser):
    parser_name = "docx_parser"
    implemented = True

    def __init__(self) -> None:
        pass

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        lower = file_path.lower()
        return detected_type == "docx" or lower.endswith(".docx") or lower.endswith(".doc")

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        metadata = default_metadata(file_path.name)
        notes: list[str] = []
        issues: list[str] = []
        tables: list[TableData] = []

        try:
            document = Document(str(file_path))
        except Exception as exc:
            return ParserResult(
                status="error",
                message="DOCX parsing failed.",
                implemented=True,
                parser_used="python_docx",
                detected_type=detected_type,
                mode_used="document_failed",
                metadata=metadata,
                notes=notes,
                issues=[f"Failed to parse DOCX: {exc}"],
                confidence=0.0,
            )

        headings: list[str] = []
        paragraphs: list[str] = []
        narrative: list[str] = []
        for paragraph in document.paragraphs:
            text = (paragraph.text or "").strip()
            if not text:
                continue
            style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
            if "heading" in style_name or "title" in style_name:
                headings.append(text)
            else:
                paragraphs.append(text)
                narrative.append(text)

        for index, table in enumerate(document.tables, start=1):
            matrix = []
            for row in table.rows:
                matrix.append([(cell.text or "").strip() for cell in row.cells])
            if not matrix:
                continue

            header = matrix[0]
            if not any(header):
                header = [f"column_{i + 1}" for i in range(max(len(r) for r in matrix))]

            rows = []
            for row in matrix[1:]:
                row_map = {}
                for col_index, col_name in enumerate(header):
                    key = col_name if col_name else f"column_{col_index + 1}"
                    row_map[key] = row[col_index] if col_index < len(row) else ""
                rows.append(row_map)

            tables.append(
                TableData(
                    table_id=f"table_{index:03d}",
                    name=f"table_{index:03d}_docx",
                    columns=[col if col else f"column_{idx + 1}" for idx, col in enumerate(header)],
                    rows=rows,
                    source="docx_table",
                    confidence=0.9 if rows else 0.65,
                )
            )

        core_title = document.core_properties.title if document.core_properties else None
        metadata["title"] = core_title or (headings[0] if headings else file_path.stem)
        metadata["reportName"] = metadata["title"]
        metadata["headings"] = headings
        metadata["paragraphs"] = paragraphs
        metadata["narrativeText"] = narrative
        metadata["summaryText"] = [f"Document tables: {len(tables)}", f"Paragraphs: {len(paragraphs)}"]
        metadata["rawLabelValues"] = {
            "tableCount": str(len(tables)),
            "headingCount": str(len(headings)),
            "paragraphCount": str(len(paragraphs)),
        }

        return ParserResult(
            status="success",
            message="DOCX parsed successfully.",
            implemented=True,
            parser_used="python_docx",
            detected_type=detected_type,
            mode_used="document",
            metadata=metadata,
            notes=notes,
            tables=tables,
            issues=issues,
            confidence=0.9 if tables or narrative else 0.7,
        )
