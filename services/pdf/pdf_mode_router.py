from __future__ import annotations

from dataclasses import dataclass

from services.pdf.opendataloader_runner import PDFRunResult
from services.pdf.pdf_table_detector import PDFTableDetector


@dataclass
class QualityDecision:
    score: float
    reasons: list[str]
    insufficient: bool


class PDFModeRouter:
    @staticmethod
    def evaluate(run_result: PDFRunResult, metadata: dict | None = None) -> QualityDecision:
        reasons: list[str] = []
        score = 0.0
        table_dominant = PDFTableDetector.is_table_dominant(run_result)

        if run_result.success:
            score += 0.2
        else:
            reasons.append("conversion did not complete cleanly")

        if run_result.tables:
            score += 0.4
        else:
            reasons.append("no structured tables returned")

        if run_result.markdown_text.strip():
            score += 0.15
            if PDFTableDetector.table_like_notes(run_result.markdown_text):
                reasons.append("table-like content remains in non-structured output")
        else:
            reasons.append("no usable text layer extracted")

        if run_result.detected_scanned:
            reasons.append("image-only or scanned layout suspected")

        if metadata and not PDFTableDetector.metadata_is_sparse(metadata):
            score += 0.15
        elif not table_dominant:
            reasons.append("metadata remains mostly empty")

        if run_result.ocr_lines:
            score += 0.1

        structured_tables_need_ocr = PDFTableDetector.structured_tables_need_ocr(run_result)
        if structured_tables_need_ocr:
            reasons.append("structured tables appear underpopulated and need OCR recovery")

        insufficient_threshold = 0.55 if table_dominant else 0.65

        insufficient = score < insufficient_threshold
        if not run_result.success or not run_result.tables:
            insufficient = True
        elif not run_result.markdown_text.strip() and not table_dominant:
            insufficient = True
        elif structured_tables_need_ocr:
            insufficient = True
        return QualityDecision(round(score, 2), reasons, insufficient)

    @staticmethod
    def should_try_hybrid(local_quality: QualityDecision) -> bool:
        return local_quality.insufficient

    @staticmethod
    def should_try_ocr_fallback(active_run: PDFRunResult, quality: QualityDecision) -> bool:
        return quality.insufficient or active_run.detected_scanned or not active_run.tables
