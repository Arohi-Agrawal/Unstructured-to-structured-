from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OCRFallbackResult:
    success: bool
    notes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    words: list[dict[str, Any]] = field(default_factory=list)
    lines: list[dict[str, Any]] = field(default_factory=list)
    cropped_regions: list[dict[str, Any]] = field(default_factory=list)


class PDFOCRFallback:
    RENDER_DPI = 300
    FALLBACK_DPI = 400

    @classmethod
    def run_scanned_pdf_ocr(cls, file_path: Path) -> OCRFallbackResult:
        import fitz
        import numpy as np

        result = OCRFallbackResult(success=False)
        document = fitz.open(str(file_path))

        for page_index in range(len(document)):
            page = document.load_page(page_index)
            best_words: list[dict[str, Any]] = []
            best_lines: list[dict[str, Any]] = []
            best_engine = ""
            best_crop = (0, 0, 0, 0)
            best_score = -1.0

            for dpi in (cls.RENDER_DPI, cls.FALLBACK_DPI):
                image = cls.render_page(page, fitz, dpi=dpi)
                crop_box = cls.detect_content_region(image, np)
                cropped = cls.preprocess_image(image.crop(crop_box))

                words, lines, engine = cls.run_local_ocr(cropped, page_index + 1, crop_box)
                signal = float(len(words)) + float(len(lines)) * 0.35
                if signal > best_score and words:
                    best_words = words
                    best_lines = lines
                    best_engine = engine
                    best_crop = crop_box
                    best_score = signal

            if best_words:
                result.words.extend(best_words)
                result.lines.extend(best_lines)
                result.cropped_regions.append(
                    {
                        "page": page_index + 1,
                        "cropBox": {
                            "left": int(best_crop[0]),
                            "top": int(best_crop[1]),
                            "right": int(best_crop[2]),
                            "bottom": int(best_crop[3]),
                        },
                    }
                )
                if best_engine:
                    result.notes.append(f"OCR fallback used {best_engine} on page {page_index + 1}.")

        result.success = bool(result.words)
        if result.success:
            result.notes.append("Rendered page images at high resolution and extracted OCR words with coordinates.")
        else:
            result.issues.append("OCR fallback did not return any words.")
        return result

    @classmethod
    def render_page(cls, page, fitz_module, dpi: int | None = None):
        target_dpi = dpi or cls.RENDER_DPI
        scale = target_dpi / 72.0
        pix = page.get_pixmap(matrix=fitz_module.Matrix(scale, scale), alpha=False)
        from PIL import Image

        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    @staticmethod
    def preprocess_image(image):
        from PIL import ImageEnhance, ImageFilter

        gray = image.convert("L")
        contrast = ImageEnhance.Contrast(gray).enhance(1.22)
        sharp = contrast.filter(ImageFilter.SHARPEN)
        return sharp

    @staticmethod
    def detect_content_region(image, np_module):
        gray = np_module.array(image.convert("L"))
        mask = gray < 246
        if not mask.any():
            return (0, 0, image.width, image.height)
        ys, xs = np_module.where(mask)
        top = max(0, int(ys.min()) - 30)
        left = max(0, int(xs.min()) - 30)
        right = min(image.width, int(xs.max()) + 30)
        bottom = min(image.height, int(ys.max()) + 30)
        # Avoid trimming the lower page too aggressively. Scanned statements often
        # place valid transaction and closing-balance rows in the lower bands.
        # Keep the full detected non-empty extent instead of biasing toward the top.
        return (left, top, right, bottom)

    @classmethod
    def run_local_ocr(cls, cropped_image, page_number: int, crop_box) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        best_words: list[dict[str, Any]] = []
        best_lines: list[dict[str, Any]] = []
        best_engine = ""
        best_score = -1.0

        try:
            import pytesseract
            from services.pdf.opendataloader_runner import OpenDataLoaderRunner

            tesseract_cmd = OpenDataLoaderRunner.resolve_tesseract_cmd()
            if tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            pytesseract.get_tesseract_version()
            for psm in (6, 4, 11):
                words, lines = cls._run_tesseract(cropped_image, pytesseract, page_number, crop_box, psm=psm)
                score = len(words) + len(lines) * 0.4
                if score > best_score and words:
                    best_words, best_lines, best_engine = words, lines, f"Tesseract-psm{psm}"
                    best_score = score
        except Exception:
            pass

        try:
            from rapidocr_onnxruntime import RapidOCR

            words, lines = cls._run_rapidocr(cropped_image, RapidOCR(), page_number, crop_box)
            score = len(words) + len(lines) * 0.4
            if score > best_score and words:
                best_words, best_lines, best_engine = words, lines, "RapidOCR"
                best_score = score
        except Exception:
            pass
        return best_words, best_lines, best_engine

    @staticmethod
    def _run_tesseract(cropped_image, pytesseract_module, page_number: int, crop_box, psm: int = 6):
        data = pytesseract_module.image_to_data(
            cropped_image,
            output_type=pytesseract_module.Output.DICT,
            config=f"--oem 3 --psm {psm}",
        )
        words: list[dict[str, Any]] = []
        line_map: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
        offset_x, offset_y = crop_box[0], crop_box[1]

        for index, text in enumerate(data.get("text", [])):
            value = (text or "").strip()
            if not value:
                continue
            try:
                confidence = float(data["conf"][index])
            except Exception:
                confidence = -1.0
            if confidence < 0:
                continue
            left = int(data["left"][index]) + offset_x
            top = int(data["top"][index]) + offset_y
            width = int(data["width"][index])
            height = int(data["height"][index])
            word = {
                "text": value,
                "confidence": confidence,
                "page": page_number,
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "x_center": left + width / 2,
                "y_center": top + height / 2,
                "width": width,
                "height": height,
                "line_num": int(data["line_num"][index]),
                "block_num": int(data["block_num"][index]),
            }
            words.append(word)
            key = (page_number, word["block_num"], word["line_num"])
            line_map.setdefault(key, []).append(word)

        lines = []
        for (_, _, _), line_words in line_map.items():
            ordered = sorted(line_words, key=lambda item: item["left"])
            lines.append(
                {
                    "page": page_number,
                    "text": " ".join(word["text"] for word in ordered),
                    "left": min(word["left"] for word in ordered),
                    "right": max(word["right"] for word in ordered),
                    "top": min(word["top"] for word in ordered),
                    "bottom": max(word["bottom"] for word in ordered),
                    "words": ordered,
                }
            )
        lines.sort(key=lambda item: (item["page"], item["top"], item["left"]))
        return words, lines

    @staticmethod
    def _run_rapidocr(cropped_image, engine, page_number: int, crop_box):
        import numpy as np

        result, _ = engine(np.array(cropped_image))
        words: list[dict[str, Any]] = []
        lines: list[dict[str, Any]] = []
        if not result:
            return words, lines
        offset_x, offset_y = crop_box[0], crop_box[1]
        for item in result:
            box, text, score = item
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            left = int(min(xs)) + offset_x
            right = int(max(xs)) + offset_x
            top = int(min(ys)) + offset_y
            bottom = int(max(ys)) + offset_y
            tokens = [token for token in text.strip().split() if token]
            if not tokens:
                continue
            token_width = max(1, (right - left) / len(tokens))
            token_words = []
            for token_index, token in enumerate(tokens):
                token_left = int(left + token_index * token_width)
                token_right = int(left + (token_index + 1) * token_width)
                token_words.append(
                    {
                        "text": token,
                        "confidence": float(score),
                        "page": page_number,
                        "left": token_left,
                        "top": top,
                        "right": token_right,
                        "bottom": bottom,
                        "x_center": (token_left + token_right) / 2,
                        "y_center": (top + bottom) / 2,
                        "width": token_right - token_left,
                        "height": bottom - top,
                        "line_num": len(lines) + 1,
                        "block_num": 1,
                    }
                )
            words.extend(token_words)
            lines.append(
                {
                    "page": page_number,
                    "text": text.strip(),
                    "left": left,
                    "right": right,
                    "top": top,
                    "bottom": bottom,
                    "words": token_words,
                }
            )
        lines.sort(key=lambda item: (item["page"], item["top"], item["left"]))
        return words, lines
