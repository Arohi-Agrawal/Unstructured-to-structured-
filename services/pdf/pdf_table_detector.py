from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import median


@dataclass
class TableSignal:
    header_index: int | None
    score: float
    reasons: list[str]


class PDFTableDetector:
    HEADER_LABEL_LIBRARY = [
        ("value date", ("value date", "value", "date")),
        ("description", ("description", "details", "narrative")),
        ("reference", ("reference", "ref", "document", "number", "id")),
        ("post date", ("post date", "posting date", "post", "date")),
        ("debit", ("debit", "withdrawal", "paid out", "dr")),
        ("credit", ("credit", "deposit", "paid in", "cr")),
        ("balance", ("balance", "closing balance", "running balance")),
        ("amount", ("amount", "value", "amt")),
        ("currency", ("currency", "ccy")),
        ("type", ("type",)),
        ("count", ("count", "qty", "quantity")),
        ("total", ("total", "net total")),
    ]
    GENERIC_HEADER_TERMS = {
        "date",
        "description",
        "reference",
        "ref",
        "amount",
        "debit",
        "credit",
        "balance",
        "value",
        "post",
        "type",
        "count",
        "total",
        "net",
        "currency",
        "details",
        "transaction",
    }
    METADATA_LABELS = {
        "from date",
        "to date",
        "account",
        "customer",
        "portfolio",
        "page",
        "report",
        "statement",
        "printed",
        "address",
    }
    FOOTER_TERMS = {"page", "printed", "generated", "run date", "created"}
    LABEL_HINTS = {
        "accountnumber": ("account", "account no", "account number", "bank account"),
        "currency": ("currency", "ccy"),
        "customername": ("customer name", "client name", "account name", "entity name"),
        "customerid": ("customer id", "client id", "customer no", "customer number", "id"),
        "portfolioid": ("portfolio", "portfolio no", "portfolio number"),
        "statementdate": ("statement date", "date"),
        "periodstart": ("from date", "start date", "period start"),
        "periodend": ("to date", "end date", "period end"),
        "address": ("address",),
        "institutionname": ("institution", "bank", "branch", "entity"),
        "merchantcode": ("merchant code", "merchantcode", "merchant_code", "merchant no", "merchant number"),
        "trn": ("trn", "tax registration number"),
        "reporttakenby": ("report taken by", "prepared by", "taken by"),
        "merchantname": ("merchant name", "merchantname"),
    }

    @classmethod
    def detect_header_row(cls, row_bands: list[list[dict]]) -> TableSignal:
        best_index = None
        best_score = 0.0
        reasons: list[str] = []

        for index, band in enumerate(row_bands[:30]):
            row_text = " ".join(fragment["text"] for fragment in band)
            normalized = cls.normalize_text(row_text)
            if any(label in normalized for label in cls.METADATA_LABELS):
                continue

            score = 0.0
            fragment_count = len(band)
            if fragment_count >= 3:
                score += 0.25
            if cls._header_term_count(normalized) >= 2:
                score += 0.35
            if cls._mostly_textual(band):
                score += 0.2
            if cls._next_rows_align(row_bands, index):
                score += 0.2

            if score > best_score:
                best_index = index
                best_score = score
                reasons = [f"candidate row {index} scored {score:.2f}"]

        return TableSignal(best_index, best_score, reasons)

    @classmethod
    def row_looks_tabular(cls, row: list[dict]) -> bool:
        if len(row) >= 3:
            return True
        numeric_count = sum(bool(re.search(r"\d", fragment["text"])) for fragment in row)
        return numeric_count >= 2

    @classmethod
    def table_like_notes(cls, text: str) -> bool:
        normalized = cls.normalize_text(text)
        return cls._header_term_count(normalized) >= 2 or bool(re.search(r"\d.*\d.*\d", normalized))

    @classmethod
    def validate_reconstructed_table(cls, columns: list[str], rows: list[dict[str, str]]) -> float:
        if not columns or len(columns) < 3 or len(rows) < 2:
            return 0.0

        populated_cells = sum(1 for row in rows for value in row.values() if str(value).strip())
        total_cells = len(rows) * len(columns)
        fill_ratio = populated_cells / total_cells if total_cells else 0.0

        numeric_columns = 0
        for column in columns:
            values = [str(row.get(column, "")).strip() for row in rows if str(row.get(column, "")).strip()]
            if values and sum(bool(re.search(r"-?\d", value.replace(",", ""))) for value in values) / len(values) >= 0.6:
                numeric_columns += 1

        score = min(1.0, 0.4 + fill_ratio * 0.3 + min(numeric_columns, 3) * 0.1)
        return round(score, 2)

    @staticmethod
    def normalize_text(value: str) -> str:
        normalized = value.lower().strip()
        normalized = re.sub(r"[^a-z0-9:/.\- ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    @classmethod
    def cluster_words_into_rows(cls, ocr_words: list[dict]) -> list[list[dict]]:
        ordered = sorted(ocr_words, key=lambda word: (word["page"], word["y_center"], word["left"]))
        if not ordered:
            return []
        heights = [word["height"] for word in ordered if word.get("height")]
        tolerance = max(7, int(median(heights) * 0.8)) if heights else 10
        rows: list[list[dict]] = []
        current: list[dict] = []
        current_y = None
        current_page = None
        current_top = None
        current_bottom = None
        for word in ordered:
            if not current:
                current = [word]
                current_y = word["y_center"]
                current_page = word["page"]
                current_top = word["top"]
                current_bottom = word["bottom"]
                continue
            vertical_overlap = 0.0
            if current_top is not None and current_bottom is not None:
                overlap_top = max(current_top, word["top"])
                overlap_bottom = min(current_bottom, word["bottom"])
                vertical_overlap = max(0.0, overlap_bottom - overlap_top)
            same_row = (
                word["page"] == current_page
                and abs(word["y_center"] - current_y) <= tolerance
            ) or (
                word["page"] == current_page and vertical_overlap >= max(2.0, min(word.get("height", 0), (current_bottom - current_top) if current_bottom and current_top else 0) * 0.25)
            )
            if not same_row:
                rows.append(sorted(current, key=lambda item: item["left"]))
                current = [word]
                current_y = word["y_center"]
                current_page = word["page"]
                current_top = word["top"]
                current_bottom = word["bottom"]
            else:
                current.append(word)
                current_y = (current_y + word["y_center"]) / 2
                current_top = min(current_top, word["top"]) if current_top is not None else word["top"]
                current_bottom = max(current_bottom, word["bottom"]) if current_bottom is not None else word["bottom"]
        if current:
            rows.append(sorted(current, key=lambda item: item["left"]))
        return rows

    @classmethod
    def infer_column_boundaries(cls, header_band: list[dict]) -> list[dict]:
        sorted_band = sorted(header_band, key=lambda item: item["left"])
        boundaries: list[dict] = []
        for index, fragment in enumerate(sorted_band):
            next_left = sorted_band[index + 1]["left"] if index + 1 < len(sorted_band) else fragment["right"] + max(fragment["width"], 60)
            label = cls.canonicalize_header_label(fragment["text"]) or re.sub(r"\s+", " ", fragment["text"]).strip() or f"column_{index + 1}"
            boundaries.append(
                {
                    "label": label,
                    "left": fragment["left"] - 6,
                    "right": (fragment["right"] + next_left) / 2,
                }
            )
        return boundaries

    @classmethod
    def metadata_is_sparse(cls, metadata: dict) -> bool:
        important_fields = (
            "reportName",
            "institutionName",
            "accountNumber",
            "currency",
            "statementDate",
            "periodStart",
            "periodEnd",
            "customerName",
            "customerId",
            "address",
        )
        filled = sum(1 for key in important_fields if isinstance(metadata.get(key), str) and metadata.get(key, "").strip())
        return filled < 3

    @classmethod
    def is_table_dominant(cls, run_result) -> bool:
        tables = getattr(run_result, "tables", []) or []
        ocr_words = getattr(run_result, "ocr_words", []) or []
        ocr_lines = getattr(run_result, "ocr_lines", []) or []

        max_columns = max((len(getattr(table, "columns", []) or []) for table in tables), default=0)
        max_rows = max((len(getattr(table, "rows", []) or []) for table in tables), default=0)
        total_cells = sum(
            max(1, len(getattr(table, "columns", []) or [])) * max(1, len(getattr(table, "rows", []) or []))
            for table in tables
        )
        markdown_text = getattr(run_result, "markdown_text", "") or ""
        if not isinstance(markdown_text, str):
            markdown_text = ""
        narrative_lines = [
            line.strip()
            for line in markdown_text.splitlines()
            if line.strip() and "|" not in line and not line.startswith("![image ")
        ]

        if max_columns >= 4 and max_rows >= 3:
            return True
        if total_cells >= 12 and len(tables) >= 1:
            return True
        if total_cells >= 8 and len(narrative_lines) <= 2:
            return True

        if ocr_words and ocr_lines:
            row_bands = cls.cluster_words_into_rows(ocr_words)
            header_signal = cls.detect_header_row(row_bands) if row_bands else TableSignal(None, 0.0, [])
            repeated_aligned_rows = 0
            if header_signal.header_index is not None:
                follower_rows = row_bands[header_signal.header_index + 1 : header_signal.header_index + 7]
                repeated_aligned_rows = sum(1 for row in follower_rows if cls.row_looks_tabular(row))
            non_empty_lines = [line for line in ocr_lines if str(line.get("text", "")).strip()]
            body_like_lines = [line for line in non_empty_lines if cls.looks_like_body_row(str(line.get("text", "")))]
            body_ratio = (len(body_like_lines) / len(non_empty_lines)) if non_empty_lines else 0.0
            if header_signal.score >= 0.6 and repeated_aligned_rows >= 2 and body_ratio >= 0.5 and len(non_empty_lines) >= 6:
                return True

        return False

    @classmethod
    def structured_tables_need_ocr(cls, run_result) -> bool:
        tables = getattr(run_result, "tables", []) or []
        structured_tables = [
            table
            for table in tables
            if str(getattr(table, "source", "") or "").startswith("opendataloader_structured")
        ]
        if not structured_tables:
            return False

        total_rows = sum(len(getattr(table, "rows", []) or []) for table in structured_tables)
        if len(structured_tables) >= 2 and total_rows >= 3:
            return False

        suspicious_tables = 0
        for table in structured_tables:
            columns = [str(column).strip() for column in (getattr(table, "columns", []) or []) if str(column).strip()]
            rows = [row for row in (getattr(table, "rows", []) or []) if isinstance(row, dict)]
            merged_headers = sum(1 for column in columns if len(column.split()) >= 4)
            if len(structured_tables) == 1 and len(rows) <= 2 and len(columns) >= 6 and merged_headers >= 1:
                suspicious_tables += 1
                continue
            if len(structured_tables) == 1 and len(rows) == 1 and len(columns) >= 8 and merged_headers >= 1:
                suspicious_tables += 1

        return suspicious_tables >= 1

    @classmethod
    def merge_header_fragments(cls, header_band: list[dict]) -> list[dict]:
        if not header_band:
            return []

        ordered = sorted(header_band, key=lambda item: item["left"])
        merged: list[dict] = []
        current = dict(ordered[0])

        for fragment in ordered[1:]:
            gap = fragment["left"] - current["right"]
            combined_text = f"{current['text']} {fragment['text']}".strip()
            if cls._should_merge_header_tokens(current, fragment, gap, combined_text):
                current["text"] = combined_text
                current["right"] = max(current["right"], fragment["right"])
                current["bottom"] = max(current["bottom"], fragment["bottom"])
                current["width"] = current["right"] - current["left"]
                current["height"] = max(current.get("height", 0), fragment.get("height", 0))
                current["x_center"] = current["left"] + current["width"] / 2
                current["y_center"] = (current["top"] + current["bottom"]) / 2
            else:
                merged.extend(cls._split_header_fragment(current))
                current = dict(fragment)

        merged.extend(cls._split_header_fragment(current))
        return merged

    @classmethod
    def canonicalize_header_label(cls, value: str) -> str | None:
        normalized = cls.normalize_text(value)
        for canonical, synonyms in cls.HEADER_LABEL_LIBRARY:
            if any(synonym in normalized for synonym in synonyms):
                return canonical.title() if canonical not in {"debit", "credit", "balance", "amount", "currency", "type", "count", "total"} else canonical.title()
        return None

    @classmethod
    def split_line_regions(cls, ocr_lines: list[dict], ocr_words: list[dict]) -> dict[str, list[dict]]:
        if not ocr_lines:
            return {"branding": [], "header": [], "table": [], "footer": []}

        sorted_lines = sorted(ocr_lines, key=lambda line: (line["page"], line["top"], line["left"]))
        row_bands = cls.cluster_words_into_rows(ocr_words) if ocr_words else []
        header_signal = cls.detect_header_row(row_bands) if row_bands else TableSignal(None, 0.0, [])
        page_top = min(line["top"] for line in sorted_lines)
        page_bottom = max(line["bottom"] for line in sorted_lines)
        page_height = max(1, page_bottom - page_top)
        footer_threshold = page_bottom - min(max(70, int(page_height * 0.08)), 140)
        branding_threshold = page_top + min(max(90, int(page_height * 0.14)), 180)

        table_top = None
        if header_signal.header_index is not None and header_signal.header_index < len(row_bands):
            header_band = row_bands[header_signal.header_index]
            table_top = min(word["top"] for word in header_band) - 12
        table_dominant_page = cls._looks_table_dominant_lines(sorted_lines)

        footer: list[dict] = []
        branding: list[dict] = []
        header: list[dict] = []
        table: list[dict] = []
        for line in sorted_lines:
            normalized = cls.normalize_text(line["text"])
            line_is_body = cls.looks_like_body_row(line["text"])
            if (
                (line["top"] >= footer_threshold or any(term in normalized for term in cls.FOOTER_TERMS))
                and not line_is_body
            ):
                if table_top is None or line["top"] > table_top:
                    footer.append(line)
                    continue
            if line_is_body:
                table.append(line)
            elif table_top is not None and line["top"] >= table_top:
                table.append(line)
            elif table_dominant_page and not cls._has_strong_label_value_evidence(line.get("text", "")):
                table.append(line)
            elif line["top"] <= branding_threshold:
                if cls._looks_like_branding_line(line, page_top):
                    branding.append(line)
                else:
                    header.append(line)
            else:
                header.append(line)

        if table_top is None:
            provisional_header = [line for line in sorted_lines if line["top"] < footer_threshold and not cls.looks_like_body_row(line["text"])]
            branding = [line for line in provisional_header if cls._looks_like_branding_line(line, page_top)]
            header = [line for line in provisional_header if line not in branding]
            footer = [line for line in sorted_lines if line["top"] >= footer_threshold and not cls.looks_like_body_row(line["text"])]
            table = [line for line in sorted_lines if line not in header and line not in branding and line not in footer]

        footer = [line for line in footer if not cls.looks_like_body_row(line["text"])]
        header = [line for line in header if not cls.looks_like_body_row(line["text"]) or cls._line_density(line) < 0.03]
        branding = [line for line in branding if not cls.looks_like_body_row(line["text"])]

        if table_dominant_page:
            strong_header = [line for line in header if cls._has_strong_label_value_evidence(line.get("text", ""))]
            strong_branding = [line for line in branding if cls._has_strong_label_value_evidence(line.get("text", ""))]
            table.extend([line for line in header if line not in strong_header])
            table.extend([line for line in branding if line not in strong_branding])
            header = strong_header
            branding = strong_branding
            strict_footer: list[dict] = []
            for line in footer:
                normalized = cls.normalize_text(str(line.get("text", "")))
                if re.search(r"\bpage\s*[:\-]?\s*\d+\b", normalized):
                    strict_footer.append(line)
                    continue
                if any(term in normalized for term in ("printed", "generated", "run date", "created")) and not cls.looks_like_body_row(str(line.get("text", ""))):
                    strict_footer.append(line)
            table.extend([line for line in footer if line not in strict_footer])
            footer = strict_footer

        return {"branding": branding, "header": header, "table": table, "footer": footer}

    @classmethod
    def _looks_table_dominant_lines(cls, lines: list[dict]) -> bool:
        if not lines:
            return False
        non_empty = [line for line in lines if str(line.get("text", "")).strip()]
        if len(non_empty) < 6:
            return False
        body_like = [line for line in non_empty if cls.looks_like_body_row(str(line.get("text", "")))]
        body_ratio = len(body_like) / len(non_empty)
        return body_ratio >= 0.62

    @classmethod
    def _has_strong_label_value_evidence(cls, text: str) -> bool:
        normalized = cls.normalize_text(text)
        if not normalized:
            return False
        if ":" in text:
            label, value = text.split(":", 1)
            label_key = cls.canonical_label_key(label)
            value_clean = value.strip()
            if label_key and value_clean and not cls.looks_like_body_row(value_clean):
                return True
        if re.match(r"^(from date|to date|statement date|account(?: number| no)?|customer(?: id| name)?|currency|address)\b", normalized):
            return True
        return False

    @classmethod
    def is_balance_summary_line(cls, text: str) -> bool:
        normalized = cls.normalize_text(text)
        return "balance" in normalized and any(term in normalized for term in ("opening", "closing", "period", "start", "end", "brought", "carried"))

    @classmethod
    def line_looks_footer(cls, text: str) -> bool:
        normalized = cls.normalize_text(text)
        return any(normalized.startswith(term) or f" {term} " in normalized for term in cls.FOOTER_TERMS)

    @classmethod
    def looks_like_numeric(cls, value: str) -> bool:
        compact = value.replace(",", "").replace(" ", "")
        return bool(re.fullmatch(r"[-+]?\(?\d+(?:\.\d+)?\)?", compact))

    @classmethod
    def _header_term_count(cls, normalized_row_text: str) -> int:
        return sum(term in normalized_row_text for term in cls.GENERIC_HEADER_TERMS)

    @classmethod
    def looks_like_body_row(cls, text: str) -> bool:
        normalized = cls.normalize_text(text)
        if cls.is_balance_summary_line(text):
            return True
        numeric_tokens = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", normalized)
        date_like = bool(
            re.search(r"\b\d{1,2}\s+[a-z]{3,9}\s+\d{2,4}\b", normalized)
            or re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", normalized)
        )
        ref_like = bool(re.search(r"\b[a-z]*\d[a-z0-9/\-]{3,}\b", normalized))
        numeric_heavy = (
            (len(numeric_tokens) >= 2 and any(sep in text for sep in (",", ".")))
            or (len(numeric_tokens) == 1 and any(sep in text for sep in (",", ".")) and len(normalized.split()) <= 2)
        )
        return (date_like and len(numeric_tokens) >= 1) or (ref_like and len(numeric_tokens) >= 2) or numeric_heavy

    @classmethod
    def canonical_label_key(cls, label: str) -> str | None:
        normalized = cls.normalize_text(label)
        compact = normalized.replace(" ", "")
        for key, synonyms in cls.LABEL_HINTS.items():
            for synonym in synonyms:
                synonym_normalized = cls.normalize_text(synonym)
                if synonym_normalized in normalized or synonym_normalized.replace(" ", "") in compact:
                    return key
        return None

    @classmethod
    def looks_like_generic_placeholder(cls, value: str) -> bool:
        normalized = cls.normalize_text(value)
        return normalized in {"statement", "report", "page", "customer", "account", "details"}

    @classmethod
    def looks_like_branding_text(cls, value: str) -> bool:
        normalized = cls.normalize_text(value)
        token_count = len(normalized.split())
        if not normalized or token_count > 3:
            return False
        if any(label in normalized for label in cls.METADATA_LABELS):
            return False
        return bool(re.fullmatch(r"[a-z/\- ]+\d{0,4}", normalized))

    @staticmethod
    def _line_density(line: dict) -> float:
        text = line.get("text", "").strip()
        width = max(1, line.get("right", 0) - line.get("left", 0))
        return len(text) / width

    @classmethod
    def _looks_like_branding_line(cls, line: dict, page_top: int | float) -> bool:
        text = line.get("text", "").strip()
        if not text:
            return False
        near_top = line.get("top", 0) <= page_top + 90
        return near_top and cls.looks_like_branding_text(text)

    @staticmethod
    def _mostly_textual(band: list[dict]) -> bool:
        text_count = sum(bool(re.search(r"[A-Za-z]", fragment["text"])) for fragment in band)
        return text_count >= max(2, len(band) // 2)

    @classmethod
    def _next_rows_align(cls, row_bands: list[list[dict]], header_index: int) -> bool:
        follower_rows = row_bands[header_index + 1 : header_index + 4]
        if len(follower_rows) < 2:
            return False
        return sum(cls.row_looks_tabular(row) for row in follower_rows) >= 2

    @classmethod
    def _should_merge_header_tokens(cls, left: dict, right: dict, gap: float, combined_text: str) -> bool:
        if gap > max(20, min(left.get("width", 0), right.get("width", 0)) * 0.8):
            return False
        left_score = cls._header_term_count(cls.normalize_text(left["text"]))
        right_score = cls._header_term_count(cls.normalize_text(right["text"]))
        combined_score = cls._header_term_count(cls.normalize_text(combined_text))
        if combined_score > max(left_score, right_score):
            return True
        return cls.canonicalize_header_label(combined_text) is not None and cls.canonicalize_header_label(left["text"]) is None

    @classmethod
    def _split_header_fragment(cls, fragment: dict) -> list[dict]:
        text = re.sub(r"\s+", " ", fragment["text"]).strip()
        normalized = cls.normalize_text(text)
        split_points: list[tuple[str, int]] = []
        for canonical, synonyms in cls.HEADER_LABEL_LIBRARY:
            for synonym in synonyms:
                compact_synonym = synonym.replace(" ", "")
                compact_text = normalized.replace(" ", "")
                index = compact_text.find(compact_synonym)
                if index > 0:
                    split_points.append((canonical, index))
        if not split_points:
            fragment["text"] = cls.canonicalize_header_label(text) or text
            return [fragment]

        compact = normalized.replace(" ", "")
        ordered_points = sorted({index for _, index in split_points if 0 < index < len(compact)})
        if not ordered_points:
            fragment["text"] = cls.canonicalize_header_label(text) or text
            return [fragment]

        tokens = re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)?", text)
        if len(tokens) < 2:
            fragment["text"] = cls.canonicalize_header_label(text) or text
            return [fragment]

        pieces: list[dict] = []
        token_width = max(1.0, fragment["width"] / len(tokens))
        for idx, token in enumerate(tokens):
            left = fragment["left"] + idx * token_width
            right = fragment["left"] + (idx + 1) * token_width
            pieces.append(
                {
                    **fragment,
                    "text": cls.canonicalize_header_label(token) or token,
                    "left": left,
                    "right": right,
                    "width": right - left,
                    "x_center": (left + right) / 2,
                }
            )
        return pieces
