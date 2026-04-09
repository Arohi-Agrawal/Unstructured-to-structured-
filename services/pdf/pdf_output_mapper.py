from __future__ import annotations

from services.parser_base import ParserResult, TableData, default_metadata
from services.pdf.opendataloader_runner import PDFRunResult
from services.pdf.pdf_table_detector import PDFTableDetector


class PDFOutputMapper:
    @staticmethod
    def build_dependency_failure(file_name: str, issues: list[str], detected_type: str) -> ParserResult:
        return ParserResult(
            status="error",
            message="PDF parser dependencies are missing.",
            implemented=True,
            parser_used="opendataloader_pdf",
            detected_type=detected_type,
            mode_used="dependency_check_failed",
            metadata=default_metadata(file_name),
            notes=[],
            tables=[],
            issues=issues,
            confidence=0.0,
        )

    @staticmethod
    def build_result(
        file_name: str,
        run_result: PDFRunResult,
        metadata: dict,
        tables: list[TableData],
        notes: list[str],
        issues: list[str],
        detected_type: str,
        mode_used: str,
    ) -> ParserResult:
        table_confidence = max([table.confidence for table in tables], default=0.0)
        metadata_sparse = PDFTableDetector.metadata_is_sparse(metadata)
        table_dominant = PDFTableDetector.is_table_dominant(run_result)
        confidence = 0.35
        if mode_used == "local":
            confidence += 0.15
        elif mode_used == "hybrid":
            confidence += 0.2
        elif mode_used == "ocr_layout_fallback":
            confidence += 0.12
        confidence += min(table_confidence, 0.35)
        if not metadata_sparse:
            confidence += 0.1
        if issues:
            confidence -= min(0.2, len(issues) * 0.05)
        if mode_used == "ocr_layout_fallback" and table_confidence < 0.7:
            confidence -= 0.08
        if any("Retried OCR table reconstruction" in note for note in notes):
            confidence -= 0.12
        if any("visible body rows were missing" in issue.lower() for issue in issues):
            confidence -= 0.15
        if "PDF_VISIBLE_ROWS_MISSING" in issues and not tables:
            confidence = min(confidence, 0.3)
        if "PDF_BALANCE_ROWS_MISSING" in issues and not tables:
            confidence = min(confidence, 0.3)
        if "PDF_ROW_START_DETECTION_FAILED" in issues and not tables:
            confidence = min(confidence, 0.28)
        if any("rightmost numeric placement was unstable" in issue.lower() for issue in issues):
            confidence -= 0.12
        if any("date tokens remained split" in issue.lower() for issue in issues):
            confidence -= 0.12
        if "PDF_TABLE_VALIDATION_FAILED" in issues and not tables:
            confidence = min(confidence, 0.2)
        if not tables:
            confidence = min(confidence, 0.45)
        if metadata_sparse and not tables:
            confidence = min(confidence, 0.3)
        if isinstance(metadata.get("accountNumber"), str) and PDFTableDetector.looks_like_generic_placeholder(metadata["accountNumber"]):
            confidence = min(confidence, 0.25)
        if any(PDFTableDetector.looks_like_body_row(footer) for footer in metadata.get("footers", [])):
            confidence = min(confidence, 0.25)
        if any(len(set(table.columns)) != len(table.columns) for table in tables):
            confidence = min(confidence, 0.25)
        if any(table.confidence < 0.75 for table in tables) and mode_used == "ocr_layout_fallback":
            confidence = min(confidence, 0.65)
        if metadata_sparse and mode_used == "ocr_layout_fallback" and not tables:
            confidence = min(confidence, 0.35)
        if metadata.get("title") and PDFTableDetector.looks_like_branding_text(str(metadata["title"])):
            confidence = min(confidence, 0.25)
        if metadata.get("reportName") and PDFTableDetector.looks_like_branding_text(str(metadata["reportName"])):
            confidence = min(confidence, 0.25)
        if metadata.get("institutionName") and PDFTableDetector.looks_like_branding_text(str(metadata["institutionName"])):
            confidence = min(confidence, 0.25)
        if not any(metadata.get(field) for field in ("accountNumber", "statementDate", "periodStart", "periodEnd", "customerName", "customerId")):
            confidence = min(confidence, 0.3)
        if metadata.get("customerName") and metadata.get("customerId") and str(metadata["customerName"]).strip() == str(metadata["customerId"]).strip():
            confidence = min(confidence, 0.25)
        if metadata.get("institutionName") and PDFTableDetector.looks_like_body_row(str(metadata["institutionName"])):
            confidence = min(confidence, 0.25)
        page_info = metadata.get("pageInfo", [])
        if len(page_info) != len({str(item).strip().lower() for item in page_info}):
            confidence = min(confidence, 0.25)
        raw_label_values = metadata.get("rawLabelValues", {})
        if isinstance(raw_label_values, dict):
            noisy_pairs = [value for value in raw_label_values.values() if isinstance(value, str) and PDFTableDetector.looks_like_body_row(value)]
            if noisy_pairs:
                confidence = min(confidence, 0.25)
        if metadata_sparse and not tables:
            confidence = min(confidence, 0.25)
        if table_dominant and tables and "PDF_VISIBLE_ROWS_MISSING" not in issues and "PDF_BALANCE_ROWS_MISSING" not in issues:
            confidence = max(confidence, 0.65)
        confidence = max(0.0, min(1.0, confidence))

        blocking_validation_issue = (not tables) and any(
            code in issues
            for code in ("PDF_TABLE_VALIDATION_FAILED", "PDF_VISIBLE_ROWS_MISSING", "PDF_BALANCE_ROWS_MISSING", "PDF_ROW_START_DETECTION_FAILED")
        )
        status = "success" if run_result.success and tables and not blocking_validation_issue else "warning"
        if "PDF_TABLE_VALIDATION_FAILED" in issues:
            status = "warning"
        message = "PDF processed successfully." if tables and not blocking_validation_issue else "PDF processed with warnings."

        return ParserResult(
            status=status,
            message=message,
            implemented=True,
            parser_used=run_result.parser_used,
            detected_type=detected_type,
            mode_used=mode_used,
            metadata=metadata,
            notes=list(dict.fromkeys(notes)),
            tables=tables,
            issues=list(dict.fromkeys(issues)),
            confidence=round(confidence, 2),
        )
