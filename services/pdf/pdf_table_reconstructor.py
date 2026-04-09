from __future__ import annotations

import re

from services.parser_base import TableData
from services.pdf.opendataloader_runner import PDFRunResult
from services.pdf.pdf_table_detector import PDFTableDetector


class PDFTableReconstructor:
    MONTH_NORMALIZATION = {
        "JAN": "JAN",
        "FEB": "FEB",
        "PEB": "FEB",
        "MAR": "MAR",
        "APR": "APR",
        "MAY": "MAY",
        "JUN": "JUN",
        "JUL": "JUL",
        "AUG": "AUG",
        "SEP": "SEP",
        "OCT": "OCT",
        "NOV": "NOV",
        "DEC": "DEC",
    }

    LEDGER_COLUMNS = ["Value Date", "Description", "Reference", "Post Date", "Debit", "Credit", "Balance"]
    SUMMARY_CARD_LABELS = (
        "Sub-Total",
        "Master DCC",
        "Visa DCC",
        "Master",
        "Visa",
        "Benefit",
        "Maestro",
        "Others",
        "Total",
    )

    @classmethod
    def looks_like_multi_section_merchant_advice_run(cls, run: PDFRunResult) -> bool:
        blob_parts = []
        if getattr(run, "markdown_text", None):
            blob_parts.append(run.markdown_text or "")
        if getattr(run, "ocr_lines", None):
            blob_parts.append("\n".join(str(line.get("text", "")) for line in (run.ocr_lines or [])))
        raw_json = getattr(run, "raw_json", None) or {}
        if isinstance(raw_json, dict):
            blob_parts.append(str(raw_json))
        return cls._looks_like_multi_section_merchant_advice_text("\n".join(blob_parts))

    @classmethod
    def reconstruct_tables(cls, run_result: PDFRunResult) -> tuple[list[TableData], list[str], list[str]]:
        notes: list[str] = []
        issues: list[str] = []

        structured = cls._normalize_structured_tables(run_result.tables or [])

        if run_result.ocr_lines:
            line_tables = cls._reconstruct_from_ocr_lines(run_result.ocr_lines, run_result.ocr_words)
            if line_tables:
                notes.append("Recovered validated table(s) from OCR line reconstruction.")
                return line_tables, issues, notes

        if cls.looks_like_multi_section_merchant_advice_run(run_result):
            multi_tables, multi_notes = cls._reconstruct_multi_block_tables_from_run(run_result)
            if multi_tables:
                notes.extend(multi_notes)
                notes.append("Recovered multi-block merchant advice tables from OCR/text lines.")
                return multi_tables, issues, notes

        markdown_tables = cls._extract_markdown_tables(run_result.markdown_text or "")
        if markdown_tables:
            notes.append("Recovered table(s) from markdown output.")
            return markdown_tables, issues, notes

        if structured:
            notes.append("Used OpenDataLoader structured tables as fallback.")
            return structured, issues, notes

        issues.append("No structured tables found; OCR/layout reconstruction did not produce a confident table.")
        return [], issues, notes

    @classmethod
    def reconstruct_tables_force_ocr(cls, run_result: PDFRunResult) -> list[TableData]:
        if cls.looks_like_multi_section_merchant_advice_run(run_result):
            multi_tables, _ = cls._reconstruct_multi_block_tables_from_run(run_result)
            if multi_tables:
                return multi_tables

        if run_result.ocr_lines:
            return cls._reconstruct_from_ocr_lines(run_result.ocr_lines, run_result.ocr_words)

        return []

    @classmethod
    def merge_with_detected_tables(cls, detected: list[TableData], reconstructed: list[TableData]) -> list[TableData]:
        if not detected:
            return reconstructed
        if not reconstructed:
            return detected

        merged: list[TableData] = []
        seen: set[tuple[str, int, int]] = set()

        for table in detected + reconstructed:
            signature = (
                "|".join(table.columns or []),
                len(table.rows or []),
                len(table.columns or []),
            )
            if signature in seen:
                continue
            seen.add(signature)
            merged.append(table)

        def sort_key(table: TableData):
            name = (table.name or "").lower()
            if "transaction" in name:
                return (0, name)
            if "batch" in name:
                return (1, name)
            if "merchant" in name and "summary" in name:
                return (2, name)
            return (3, name)

        return sorted(merged, key=sort_key)

    @classmethod
    def filter_valid_tables(
        cls,
        tables: list[TableData],
        ocr_lines: list[dict] | None = None,
        ocr_words: list[dict] | None = None,
    ) -> tuple[list[TableData], list[str], list[str], list[str]]:
        valid_tables: list[TableData] = []
        issues: list[str] = []
        notes: list[str] = []
        validation_warnings: list[str] = []

        for table in tables:
            table.rows = cls._consolidate_logical_rows(
                cls._normalize_rows(table.rows or []),
                table.columns or [],
            )

            ok, warnings, confidence = cls._validate_block_aware_table(table)
            table.confidence = max(float(getattr(table, "confidence", 0.0) or 0.0), confidence)

            if ok or len(table.rows or []) >= 2:
                if not ok:
                    warnings.append(
                        "Accepted partially reconstructed table because it contains at least two rows."
                    )
                valid_tables.append(table)
                validation_warnings.extend(warnings)
            else:
                issues.append(f"Rejected table block '{table.name}' because structural validation did not pass.")

        if tables and not valid_tables:
            issues.append("PDF_TABLE_VALIDATION_FAILED")
            notes.append("CSV export was blocked because no reconstructed table passed structural validation.")

        return (
            valid_tables,
            list(dict.fromkeys(issues)),
            list(dict.fromkeys(notes)),
            list(dict.fromkeys(validation_warnings)),
        )

    @classmethod
    def _normalize_structured_tables(cls, tables: list[TableData]) -> list[TableData]:
        normalized: list[TableData] = []

        for index, table in enumerate(tables, start=1):
            columns = [str(col).strip() for col in (table.columns or []) if str(col).strip()]
            rows = cls._normalize_rows(table.rows or [])

            if not columns and rows:
                observed = []
                seen = set()
                for row in rows:
                    for key in row.keys():
                        k = str(key).strip()
                        if k and k not in seen:
                            seen.add(k)
                            observed.append(k)
                columns = observed

            if not columns or not rows:
                continue

            normalized.append(
                TableData(
                    table_id=getattr(table, "table_id", f"table_{index:03d}"),
                    name=getattr(table, "name", f"table_{index:03d}"),
                    columns=columns,
                    rows=rows,
                    source=str(getattr(table, "source", "") or "opendataloader_structured"),
                    confidence=max(0.7, float(getattr(table, "confidence", 0.0) or 0.0)),
                )
            )

        return normalized

    @classmethod
    def _looks_like_multi_section_merchant_advice_text(cls, text_blob: str) -> bool:
        if not text_blob:
            return False
        text_blob = text_blob.lower()
        anchors = (
            "merchant advice",
            "transaction details",
            "tax invoice",
            "postingdate",
            "txn.date",
            "batch summary",
            "merchant summary",
            "card type",
            "txn.amount",
            "net amount",
            "vat amount",
            "cback amount",
        )
        hits = sum(1 for token in anchors if token in text_blob)
        return hits >= 4

    @classmethod
    def _reconstruct_multi_block_tables_from_run(cls, run_result: PDFRunResult) -> tuple[list[TableData], list[str]]:
        text_lines: list[str] = []

        if run_result.ocr_lines:
            text_lines.extend(
                re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
                for line in run_result.ocr_lines
                if str(line.get("text", "")).strip()
            )

        if run_result.markdown_text:
            text_lines.extend(
                re.sub(r"\s+", " ", line).strip()
                for line in run_result.markdown_text.splitlines()
                if re.sub(r"\s+", " ", line).strip()
            )

        text_lines = list(dict.fromkeys([line for line in text_lines if line]))

        notes: list[str] = []
        tables: list[TableData] = []

        tx_rows = cls._parse_merchant_transaction_rows(text_lines)
        if tx_rows:
            tables.append(
                TableData(
                    table_id="table_001",
                    name="table_001_transactions",
                    columns=[
                        "PostingDate",
                        "Txn.Date",
                        "Terminal",
                        "Batch",
                        "Seq#",
                        "Card No.",
                        "Type",
                        "Txn.Amount",
                        "Com.Amount",
                        "Vat Amount",
                        "Net Amount",
                        "Cback Amount",
                    ],
                    rows=tx_rows,
                    source="ocr_merchant_transactions",
                    confidence=0.84,
                )
            )
            notes.append("Transaction block reconstructed from merchant-advice text/OCR content.")

        batch_rows = cls._parse_merchant_summary_rows(text_lines, summary_name="batch")
        if batch_rows:
            tables.append(
                TableData(
                    table_id="table_002",
                    name="table_002_batch_summary",
                    columns=["Card Type", "Count", "Txn.Amount", "Com.Amount", "Net Amount", "Cback Amount", "Vat Amount"],
                    rows=batch_rows,
                    source="ocr_merchant_batch_summary",
                    confidence=0.74,
                )
            )
            notes.append("Batch summary block reconstructed as a separate table.")

        merchant_rows = cls._parse_merchant_summary_rows(text_lines, summary_name="merchant")
        if merchant_rows:
            tables.append(
                TableData(
                    table_id="table_003",
                    name="table_003_merchant_summary",
                    columns=["Card Type", "Count", "Txn.Amount", "Com.Amount", "Net Amount", "Cback Amount", "Vat Amount"],
                    rows=merchant_rows,
                    source="ocr_merchant_summary",
                    confidence=0.72,
                )
            )
            notes.append("Merchant summary block reconstructed as a separate table.")

        return tables, notes

    @classmethod
    def _parse_merchant_transaction_rows(cls, lines: list[str]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []

        for raw in lines:
            text = re.sub(r"\s+", " ", raw).strip().lstrip("-").strip()
            row = cls._parse_transaction_line(text)
            if row:
                rows.append(row)

        return cls._dedupe_rows(rows, key_fields=("PostingDate", "Txn.Date", "Terminal", "Batch", "Seq#"))

    @classmethod
    def _parse_merchant_summary_rows(cls, lines: list[str], summary_name: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen = set()
        source_lines = cls._slice_summary_section_lines(lines, summary_name)
        if summary_name == "batch":
            allowed_cards = {"total", "sub-total", "subtotal", "master", "visa"}
        else:
            allowed_cards = {
                "total",
                "sub-total",
                "subtotal",
                "master",
                "master dcc",
                "visa",
                "visa dcc",
                "benefit",
                "maestro",
                "others",
            }

        for raw in source_lines:
            text = re.sub(r"\s+", " ", raw).strip()
            segments = cls._split_summary_line_segments(text) or [text]
            for segment in segments:
                parsed = cls._parse_summary_segment(segment)
                if not parsed:
                    parsed = cls._parse_summary_line(segment)
                if not parsed:
                    continue

                card = cls._normalize_card_type_label(parsed.get("Card Type", ""))
                if card not in allowed_cards:
                    continue
                parsed["Card Type"] = cls._title_case_card_label(card)
                sig = tuple(parsed.get(k, "") for k in ("Card Type", "Count", "Txn.Amount", "Com.Amount", "Net Amount", "Cback Amount", "Vat Amount"))
                if sig not in seen:
                    seen.add(sig)
                    rows.append(parsed)

        return rows

    @classmethod
    def _slice_summary_section_lines(cls, lines: list[str], summary_name: str) -> list[str]:
        if not lines:
            return []
        normalized_lines = [PDFTableDetector.normalize_text(line) for line in lines]

        batch_start = None
        merchant_start = None
        for idx, normalized in enumerate(normalized_lines):
            if batch_start is None and "batch summary" in normalized:
                batch_start = idx
            if merchant_start is None and ("merchant summary" in normalized or "visa dcc" in normalized or "master dcc" in normalized):
                merchant_start = idx

        if summary_name == "batch":
            if batch_start is None:
                return lines
            end = merchant_start if merchant_start is not None and merchant_start > batch_start else len(lines)
            return lines[batch_start:end]

        if merchant_start is not None:
            return lines[merchant_start:]
        if batch_start is not None:
            return lines[batch_start:]
        return lines

    @classmethod
    def _split_summary_line_segments(cls, text: str) -> list[str]:
        if not text:
            return []
        label_pattern = r"(?i)\b(sub-?total|master dcc|visa dcc|master|visa|benefit|maestro|others|total)\b"
        matches = list(re.finditer(label_pattern, text))
        if not matches:
            return []
        segments: list[str] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            segment = re.sub(r"\s+", " ", text[start:end]).strip(" -|")
            if segment:
                segments.append(segment)
        return segments

    @classmethod
    def _parse_summary_segment(cls, text: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) < 2:
            return None

        label_match = re.match(r"(?i)^(sub-?total|master dcc|visa dcc|master|visa|benefit|maestro|others|total)\b", normalized)
        if not label_match:
            return None

        label = cls._title_case_card_label(cls._normalize_card_type_label(label_match.group(1)))
        remainder = normalized[label_match.end():].strip()
        numeric_tokens = [cls._normalize_numeric_token(tok, "Amount") for tok in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", remainder)]
        if not numeric_tokens:
            return None

        count = ""
        amount_tokens = numeric_tokens
        first = numeric_tokens[0]
        if re.fullmatch(r"[-+]?\d+", first):
            count = first
            amount_tokens = numeric_tokens[1:]

        row = {
            "Card Type": label,
            "Count": count,
            "Txn.Amount": amount_tokens[0] if len(amount_tokens) >= 1 else "",
            "Com.Amount": amount_tokens[1] if len(amount_tokens) >= 2 else "",
            "Net Amount": amount_tokens[2] if len(amount_tokens) >= 3 else "",
            "Cback Amount": amount_tokens[3] if len(amount_tokens) >= 4 else "",
            "Vat Amount": amount_tokens[4] if len(amount_tokens) >= 5 else "",
        }
        if not any(row.get(col, "").strip() for col in ("Count", "Txn.Amount", "Com.Amount", "Net Amount", "Cback Amount", "Vat Amount")):
            return None
        return row

    @staticmethod
    def _normalize_card_type_label(label: str) -> str:
        normalized = re.sub(r"\s+", " ", str(label)).strip().lower()
        normalized = normalized.replace("subtotal", "sub-total")
        return normalized

    @staticmethod
    def _title_case_card_label(label: str) -> str:
        if label in {"visa dcc", "master dcc"}:
            return label.title()
        if label == "sub-total":
            return "Sub-Total"
        return label.title()

    @staticmethod
    def _dedupe_rows(rows: list[dict[str, str]], key_fields: tuple[str, ...]) -> list[dict[str, str]]:
        deduped = []
        seen = set()
        for row in rows:
            sig = tuple(row.get(field, "") for field in key_fields)
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(row)
        return deduped

    @classmethod
    def _extract_markdown_tables(cls, markdown_text: str) -> list[TableData]:
        lines = [line.rstrip() for line in markdown_text.splitlines()]
        blocks: list[list[str]] = []
        current: list[str] = []

        for line in lines:
            if "|" in line:
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)

        tables: list[TableData] = []
        for index, block in enumerate(blocks, start=1):
            rows = []
            for line in block:
                if set(line.replace("|", "").strip()) <= {"-", ":"}:
                    continue
                rows.append([cell.strip() for cell in line.strip("|").split("|")])

            if len(rows) < 2:
                continue

            headers = [header or f"column_{idx + 1}" for idx, header in enumerate(rows[0])]
            body = [
                {headers[idx]: row[idx] if idx < len(row) else "" for idx in range(len(headers))}
                for row in rows[1:]
            ]
            tables.append(
                TableData(
                    table_id=f"table_{index:03d}",
                    name=f"table_{index:03d}",
                    columns=headers,
                    rows=body,
                    source="markdown_reconstruction",
                    confidence=0.62,
                )
            )
        return tables

    @classmethod
    def _reconstruct_from_ocr_lines(cls, ocr_lines: list[dict], ocr_words: list[dict] | None = None) -> list[TableData]:
        if not ocr_lines:
            return []

        ordered = sorted(
            ocr_lines,
            key=lambda line: (line.get("page", 1), line.get("top", 0), line.get("left", 0)),
        )

        text_blob = "\n".join(re.sub(r"\s+", " ", str(line.get("text", ""))).strip() for line in ordered if str(line.get("text", "")).strip())
        if cls._looks_like_multi_section_merchant_advice_text(text_blob):
            dummy_run = type("Run", (), {"ocr_lines": ordered, "markdown_text": text_blob})()
            multi_tables, _ = cls._reconstruct_multi_block_tables_from_run(dummy_run)
            if multi_tables:
                return multi_tables

        # Geometry-first path for ledger-like statements.
        ledger_signal = bool(
            re.search(r"\bvalue\s*date\b", text_blob, flags=re.IGNORECASE)
            and re.search(r"\bdebit\b", text_blob, flags=re.IGNORECASE)
            and re.search(r"\bcredit\b", text_blob, flags=re.IGNORECASE)
            and re.search(r"\bbalance\b", text_blob, flags=re.IGNORECASE)
        )
        if ledger_signal:
            generic_layout_table = cls._reconstruct_generic_layout_table(ocr_lines, ocr_words or [])
            if generic_layout_table and len(generic_layout_table.rows or []) >= 2:
                repaired = cls._repair_bni_rows(cls._normalize_rows(generic_layout_table.rows or []))
                if repaired:
                    generic_layout_table.rows = repaired
                    generic_layout_table.source = "ocr_layout_geometry_first"
                    generic_layout_table.confidence = max(generic_layout_table.confidence, 0.86)
                    return [generic_layout_table]

        header_index = cls._find_statement_header_line(ordered)
        if header_index is None:
            header_index = cls._find_first_ledger_anchor_line(ordered)
            if header_index is None:
                return []

        header_line = ordered[header_index] if 0 <= header_index < len(ordered) else None
        rows = cls._rebuild_bni_rows(ordered[header_index:], ocr_words or [], header_line)
        rows = cls._consolidate_logical_rows(cls._normalize_rows(rows), cls.LEDGER_COLUMNS)
        rows = cls._repair_bni_rows(rows)

        if not rows:
            return []

        return [
            TableData(
                table_id="table_001",
                name="table_001_transactions",
                columns=cls.LEDGER_COLUMNS,
                rows=rows,
                source="ocr_line_reconstruction_second_pass",
                confidence=0.86,
            )
        ]

    @classmethod
    def _repair_bni_rows(cls, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        if not rows:
            return rows

        repaired: list[dict[str, str]] = []
        idx = 0
        while idx < len(rows):
            row = dict(rows[idx])
            desc = str(row.get("Description", "")).strip().lower()
            balance = str(row.get("Balance", "")).strip()
            has_amount = bool(str(row.get("Debit", "")).strip() or str(row.get("Credit", "")).strip())
            has_post = bool(str(row.get("Post Date", "")).strip())
            has_ref = bool(str(row.get("Reference", "")).strip())

            # Minor cleanup only: drop obviously empty fragments.
            if not any(str(row.get(k, "")).strip() for k in ("Description", "Reference", "Debit", "Credit", "Balance", "Post Date", "Value Date")):
                idx += 1
                continue

            # Minimal merge for split transfer reference.
            if "transfer" in desc and idx + 1 < len(rows):
                nxt = rows[idx + 1]
                nxt_ref = str(nxt.get("Reference", "")).strip()
                nxt_desc = str(nxt.get("Description", "")).strip().lower()
                if (not has_ref) and (nxt_ref.startswith("FT") or "ft" in nxt_desc):
                    row["Reference"] = nxt_ref or row.get("Reference", "")
                    idx += 1

            repaired.append(row)
            idx += 1

        return repaired

    @classmethod
    def _reconstruct_generic_layout_table(cls, ocr_lines: list[dict], ocr_words: list[dict]) -> TableData | None:
        if not ocr_words:
            return None

        row_bands = PDFTableDetector.cluster_words_into_rows(ocr_words)
        if not row_bands:
            return None

        logical_rows: list[list[dict]] = []
        current_row: list[dict] | None = None

        for band in row_bands:
            band_sorted = sorted(band, key=lambda item: item.get("left", 0))
            band_text = " ".join(str(word.get("text", "")).strip() for word in band_sorted if str(word.get("text", "")).strip())
            normalized = PDFTableDetector.normalize_text(band_text)
            if not band_text:
                continue
            if PDFTableDetector.line_looks_footer(band_text) or cls._looks_like_table_header_line(band_text) or cls._looks_like_metadata_or_header_line(band_text):
                continue

            has_anchor_date = bool(
                re.search(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", band_text, flags=re.IGNORECASE)
                or re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", band_text)
            )

            if has_anchor_date:
                if current_row:
                    logical_rows.append(current_row)
                current_row = list(band_sorted)
                continue

            if current_row and not normalized.startswith(("page", "customer", "currency", "statement")):
                current_row.extend(band_sorted)

        if current_row:
            logical_rows.append(current_row)

        if len(logical_rows) < 2:
            return None

        all_words = [word for row in logical_rows for word in row]
        centers = cls._compute_column_centers(all_words)
        if len(centers) != len(cls.LEDGER_COLUMNS):
            return None

        parsed_rows: list[dict[str, str]] = []
        for row_words in logical_rows:
            parsed = cls._parse_row_with_column_centers(row_words, centers)
            if parsed:
                parsed_rows.append(parsed)

        parsed_rows = cls._consolidate_logical_rows(cls._normalize_rows(parsed_rows), cls.LEDGER_COLUMNS)
        if len(parsed_rows) < 2:
            return None

        return TableData(
            table_id="table_001",
            name="table_001_transactions",
            columns=list(cls.LEDGER_COLUMNS),
            rows=parsed_rows,
            source="ocr_layout_generic",
            confidence=0.83,
        )

    @classmethod
    def _compute_column_centers(cls, words: list[dict]) -> list[float]:
        x_values = sorted(
            float(word.get("x_center", 0.0))
            for word in words
            if isinstance(word.get("x_center"), (int, float))
        )
        if len(x_values) < 7:
            return []

        min_x = min(x_values)
        max_x = max(x_values)
        if max_x <= min_x:
            return []

        initial = [min_x + (max_x - min_x) * ratio for ratio in (0.05, 0.20, 0.38, 0.54, 0.70, 0.83, 0.94)]
        centers = list(initial)
        for _ in range(14):
            buckets = [[] for _ in centers]
            for x in x_values:
                idx = min(range(len(centers)), key=lambda i: abs(x - centers[i]))
                buckets[idx].append(x)
            updated = []
            for idx, bucket in enumerate(buckets):
                if bucket:
                    updated.append(sum(bucket) / len(bucket))
                else:
                    updated.append(centers[idx])
            centers = updated

        return sorted(centers)

    @classmethod
    def _parse_row_with_column_centers(cls, row_words: list[dict], centers: list[float]) -> dict[str, str] | None:
        buckets: list[list[str]] = [[] for _ in centers]
        for word in sorted(row_words, key=lambda item: item.get("left", 0)):
            token = str(word.get("text", "")).strip()
            if not token:
                continue
            x = float(word.get("x_center", 0.0))
            idx = min(range(len(centers)), key=lambda i: abs(x - centers[i]))
            buckets[idx].append(token)

        values = [" ".join(bucket).strip() for bucket in buckets]
        row_text = " ".join(values).strip()
        if not row_text:
            return None

        date_tokens = re.findall(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", row_text, flags=re.IGNORECASE)
        value_date = cls._normalize_date_token(date_tokens[0]) if date_tokens else values[0]
        post_date = cls._normalize_date_token(date_tokens[1]) if len(date_tokens) > 1 else values[3]

        description = values[1]
        reference = values[2]
        if not reference:
            ref_match = re.search(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", description)
            if ref_match:
                reference = ref_match.group(0)
                description = (description[: ref_match.start()] + " " + description[ref_match.end():]).strip()

        if not reference:
            ref_match_all = re.search(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", row_text)
            if ref_match_all:
                reference = ref_match_all.group(0)

        debit = cls._pick_numeric_from_text(values[4], "Amount")
        credit = cls._pick_numeric_from_text(values[5], "Amount")
        balance = cls._pick_numeric_from_text(values[6], "Balance")

        if not balance:
            right_side = " ".join(values[4:7])
            stitched = cls._stitch_split_decimal_tokens(
                re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", right_side)
            )
            if stitched:
                balance = cls._normalize_numeric_token(stitched[-1], "Balance")
                if len(stitched) >= 2 and not debit and not credit:
                    amt = cls._normalize_numeric_token(stitched[-2], "Amount")
                    if amt.startswith("-") or any(term in description.lower() for term in ("purchase", "transfer")):
                        debit = amt if amt.startswith("-") else f"-{amt}"
                    else:
                        credit = amt

        has_signal = bool(value_date or description or reference or debit or credit or balance)
        if not has_signal:
            return None

        return {
            "Value Date": value_date or "",
            "Description": description or "",
            "Reference": reference or "",
            "Post Date": post_date or "",
            "Debit": debit or "",
            "Credit": credit or "",
            "Balance": balance or "",
        }

    @classmethod
    def _pick_numeric_from_text(cls, text: str, column_name: str) -> str:
        tokens = cls._stitch_split_decimal_tokens(
            re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text or "")
        )
        if not tokens:
            return ""
        return cls._normalize_numeric_token(tokens[-1], column_name)

    @classmethod
    def _find_first_ledger_anchor_line(cls, ordered_lines: list[dict]) -> int | None:
        for idx, line in enumerate(ordered_lines):
            text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            normalized = PDFTableDetector.normalize_text(text)
            if PDFTableDetector.is_balance_summary_line(text):
                return idx
            if re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", text, flags=re.IGNORECASE):
                return idx
            if "securities purchase" in normalized or "transfer" in normalized:
                return idx
        return None

    @classmethod
    def _rebuild_bni_rows(
        cls,
        lines: list[dict],
        ocr_words: list[dict] | None = None,
        header_line: dict | None = None,
    ) -> list[dict[str, str]]:
        ordered_lines = sorted(
            [
                line
                for line in (lines or [])
                if re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            ],
            key=lambda line: (line.get("page", 1), line.get("top", 0), line.get("left", 0)),
        )
        if not ordered_lines:
            return []

        column_bands = cls._derive_ledger_column_bands(header_line, ordered_lines)
        row_blocks: list[list[dict]] = []
        current_block: list[dict] = []

        for line in ordered_lines:
            text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            if not text:
                continue
            if PDFTableDetector.line_looks_footer(text) or cls._looks_like_table_header_line(text) or cls._looks_like_metadata_or_header_line(text):
                continue
            if PDFTableDetector.is_balance_summary_line(text):
                if current_block:
                    row_blocks.append(current_block)
                    current_block = []
                balance_row = cls._parse_balance_line(text)
                if balance_row:
                    row_blocks.append([{"text": text, "row": balance_row, "is_balance": True}])
                continue

            if cls._line_has_strong_row_anchor(line, column_bands):
                if current_block:
                    row_blocks.append(current_block)
                current_block = [line]
                continue

            if current_block and cls._is_continuation_line(line, current_block[-1], column_bands):
                current_block.append(line)
            elif current_block:
                row_blocks.append(current_block)
                current_block = [line]
            else:
                current_block = [line]

        if current_block:
            row_blocks.append(current_block)

        rows: list[dict[str, str]] = []
        for block in row_blocks:
            if not block:
                continue
            if isinstance(block[0], dict) and block[0].get("is_balance"):
                rows.append(dict(block[0].get("row", {})))
                continue

            block_text = " ".join(re.sub(r"\s+", " ", str(item.get("text", ""))).strip() for item in block).strip()
            if not block_text:
                continue
            parsed = cls._parse_bni_window(block_text, block, column_bands)
            if not parsed:
                parsed = cls._parse_statement_line_loose(block_text, cls.LEDGER_COLUMNS, block, column_bands)
            if parsed:
                rows.append(parsed)
        return rows

    @classmethod
    def _parse_bni_window(
        cls,
        text: str,
        row_lines: list[dict] | None = None,
        column_bands: dict[str, tuple[float, float]] | None = None,
    ) -> dict[str, str] | None:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None

        if PDFTableDetector.is_balance_summary_line(text):
            return cls._parse_balance_line(text)

        # Geometry-aware parse when row lines and bands are available.
        if row_lines and column_bands:
            geo_parsed = cls._parse_statement_line_loose(text, cls.LEDGER_COLUMNS, row_lines, column_bands)
            if geo_parsed:
                return geo_parsed

        # Fallback text parse.
        date_matches = list(re.finditer(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", text, flags=re.IGNORECASE))
        if len(date_matches) < 2:
            return cls._parse_bni_single_date_window(text)

        value_date = cls._normalize_date_token(date_matches[0].group(0))
        post_date = cls._normalize_date_token(date_matches[1].group(0))

        start_desc = date_matches[0].end()
        end_desc = date_matches[1].start()
        description_ref_chunk = text[start_desc:end_desc].strip()
        tail = text[date_matches[1].end():].strip()

        tail_refs = re.findall(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", tail)
        tail_for_numbers = tail
        for ref_token in tail_refs:
            tail_for_numbers = tail_for_numbers.replace(ref_token, " ")
        tail_for_numbers = re.sub(r"\s+", " ", tail_for_numbers).strip()

        numeric_tokens = cls._stitch_split_decimal_tokens(
            re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", tail_for_numbers)
        )
        if len(numeric_tokens) < 2:
            return None

        # Last token is balance, previous token is amount.
        balance = cls._normalize_numeric_token(numeric_tokens[-1], "Balance")
        amount = cls._normalize_numeric_token(numeric_tokens[-2], "Amount")

        debit = ""
        credit = ""
        if amount.startswith("-"):
            debit = amount
        else:
            # For statements like BNI, OCR may strip sign; treat purchase-like descriptions as debit
            if "purchase" in description_ref_chunk.lower() or "transfer" in description_ref_chunk.lower():
                debit = amount if amount.startswith("-") else f"-{amount}"
            else:
                credit = amount

        # Reference often buried in description chunk or tail
        ref_match = re.search(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", description_ref_chunk)
        reference = ref_match.group(0) if ref_match else (tail_refs[0] if tail_refs else "")
        description = description_ref_chunk.replace(reference, "").strip() if reference else description_ref_chunk

        # Pull extra text before amount block into description
        tail_prefix = re.sub(r"[-+]?\d[\d,]*(?:\.\d+)?", " ", tail_for_numbers)
        tail_prefix = re.sub(r"\s+", " ", tail_prefix).strip()
        if tail_prefix and not reference:
            ref_match_tail = re.search(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", tail_prefix)
            if ref_match_tail:
                reference = ref_match_tail.group(0)
                tail_prefix = tail_prefix.replace(reference, "").strip()
        if tail_prefix:
            description = f"{description} {tail_prefix}".strip()

        if not description and not reference and not balance:
            return None

        return {
            "Value Date": value_date,
            "Description": description,
            "Reference": reference,
            "Post Date": post_date,
            "Debit": debit,
            "Credit": credit,
            "Balance": balance,
        }

    @classmethod
    def _parse_bni_single_date_window(cls, text: str) -> dict[str, str] | None:
        text = re.sub(r"\s+", " ", text).strip()
        date_match = re.search(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", text, flags=re.IGNORECASE)
        if not date_match:
            return None

        value_date = cls._normalize_date_token(date_match.group(0))
        working = f"{text[:date_match.start()]} {text[date_match.end():]}".strip()

        references = re.findall(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", working)
        reference = references[0] if references else ""
        if reference:
            working = working.replace(reference, " ")
            working = re.sub(r"\s+", " ", working).strip()
        else:
            embedded_refs = re.findall(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", working)
            if embedded_refs:
                reference = embedded_refs[0]
                working = working.replace(reference, " ")
                working = re.sub(r"\s+", " ", working).strip()

        numeric_tokens = cls._stitch_split_decimal_tokens(
            re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", working)
        )
        if len(numeric_tokens) < 2:
            return None

        balance = cls._normalize_numeric_token(numeric_tokens[-1], "Balance")
        amount = cls._normalize_numeric_token(numeric_tokens[-2], "Amount")
        description = re.sub(r"[-+]?\d[\d,]*(?:\.\d+)?", " ", working)
        description = re.sub(r"\s+", " ", description).strip()

        debit = ""
        credit = ""
        if amount.startswith("-") or any(term in description.lower() for term in ("purchase", "transfer")):
            debit = amount if amount.startswith("-") else f"-{amount}"
        else:
            credit = amount

        return {
            "Value Date": value_date,
            "Description": description,
            "Reference": reference,
            "Post Date": value_date,
            "Debit": debit,
            "Credit": credit,
            "Balance": balance,
        }

    @classmethod
    def _parse_balance_line(cls, text: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        numbers = cls._stitch_split_decimal_tokens(
            re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", normalized)
        )
        if not numbers:
            return None
        label = cls._normalize_summary_label(normalized)
        preferred = next((n for n in numbers if "," in n or "." in n), numbers[-1])
        return {
            "Value Date": "",
            "Description": label,
            "Reference": "",
            "Post Date": "",
            "Debit": "",
            "Credit": "",
            "Balance": cls._normalize_numeric_token(preferred, "Balance"),
        }

    @classmethod
    def _find_statement_header_line(cls, ordered_lines: list[dict], min_score: int = 4) -> int | None:
        best_idx = None
        best_score = 0

        for idx, line in enumerate(ordered_lines[:35]):
            text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            normalized = PDFTableDetector.normalize_text(text)
            score = sum(
                1 for token in ("value", "date", "description", "reference", "post", "debit", "credit", "balance")
                if token in normalized
            )
            if score > best_score:
                best_idx = idx
                best_score = score

        return best_idx if best_score >= min_score else None

    @classmethod
    def _looks_like_table_header_line(cls, text: str) -> bool:
        normalized = PDFTableDetector.normalize_text(text)
        hits = sum(
            1 for token in ("value", "date", "description", "reference", "post", "debit", "credit", "balance")
            if token in normalized
        )
        return hits >= 4

    @classmethod
    def _parse_statement_line_loose(
        cls,
        text: str,
        columns: list[str],
        row_lines: list[dict] | None = None,
        column_bands: dict[str, tuple[float, float]] | None = None,
    ) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return None

        if PDFTableDetector.is_balance_summary_line(normalized):
            return cls._parse_balance_line(normalized)

        date_matches = list(
            re.finditer(r"\b\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", normalized, flags=re.IGNORECASE)
        )
        if not date_matches:
            return None

        row = {column: "" for column in columns}

        # Geometry-first token assignment if row lines and bands are available.
        if row_lines and column_bands:
            token_items = cls._estimate_tokens_with_x(row_lines)
            assigned = cls._assign_tokens_to_ledger_columns(token_items, column_bands)
            for col in columns:
                row[col] = assigned.get(col, "")
            row["Value Date"] = cls._normalize_date_token(row.get("Value Date", "")) if row.get("Value Date") else ""
            row["Post Date"] = cls._normalize_date_token(row.get("Post Date", "")) if row.get("Post Date") else ""

            # Normalize numeric fields conservatively.
            for col_name in ("Debit", "Credit", "Balance"):
                val = str(row.get(col_name, "")).strip()
                if val:
                    norm_val = cls._normalize_numeric_token(val, col_name if col_name != "Debit" and col_name != "Credit" else "Amount")
                    if norm_val in {"-0", "-0.0", "-0.00", "-0.000"}:
                        norm_val = "0.000"
                    row[col_name] = norm_val

            if row.get("Description") or row.get("Reference") or row.get("Balance"):
                return row

        working = normalized
        for match in reversed(date_matches[:2]):
            start, end = match.span()
            working = f"{working[:start]} {working[end:]}".strip()

        numeric_tokens = cls._stitch_split_decimal_tokens(
            re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", working)
        )
        if numeric_tokens:
            row["Balance"] = cls._normalize_numeric_token(numeric_tokens[-1], "Balance")
            leading = numeric_tokens[:-1]
            if len(leading) >= 2:
                amount = cls._normalize_numeric_token(leading[-2], "Amount")
                if "purchase" in working.lower() or "transfer" in working.lower():
                    row["Debit"] = amount if amount.startswith("-") else f"-{amount}"
                else:
                    row["Credit"] = amount
            elif len(leading) == 1:
                value = cls._normalize_numeric_token(leading[0], "Amount")
                if "purchase" in working.lower() or "transfer" in working.lower():
                    row["Debit"] = value if value.startswith("-") else f"-{value}"
                else:
                    row["Credit"] = value

        text_without_numeric = re.sub(r"[-+]?\d[\d,]*(?:\.\d+)?", " ", working)
        text_without_numeric = re.sub(r"\s+", " ", text_without_numeric).strip()

        ref_match = re.search(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", text_without_numeric)
        if ref_match:
            row["Reference"] = ref_match.group(0)
            row["Description"] = (
                text_without_numeric[: ref_match.start()] + " " + text_without_numeric[ref_match.end():]
            ).strip()
        else:
            row["Description"] = text_without_numeric

        if not row.get("Description") and not row.get("Reference") and not row.get("Balance"):
            return None

        return row

    @classmethod
    def _derive_ledger_column_bands(
        cls,
        header_line: dict | None,
        lines: list[dict],
    ) -> dict[str, tuple[float, float]]:
        seed = header_line if header_line and str(header_line.get("text", "")).strip() else (lines[0] if lines else None)
        left = float((seed or {}).get("left", 0.0) or 0.0)
        width = float((seed or {}).get("width", 1200.0) or 1200.0)
        if width <= 0:
            width = 1200.0
        anchor_lines = []
        for line in lines or []:
            text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            if re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", text, flags=re.IGNORECASE):
                anchor_lines.append(line)
        if anchor_lines:
            min_left = min(float(line.get("left", left) or left) for line in anchor_lines)
            max_right = max(
                float(line.get("left", left) or left) + float(line.get("width", 0.0) or 0.0)
                for line in anchor_lines
            )
            if max_right > min_left:
                left = min_left
                width = max(width, max_right - min_left)
        # Generic ledger proportions: Date | Desc | Ref | Post | Debit | Credit | Balance
        cuts = [0.00, 0.12, 0.47, 0.62, 0.73, 0.82, 0.90, 1.00]
        names = cls.LEDGER_COLUMNS
        bands: dict[str, tuple[float, float]] = {}
        for idx, name in enumerate(names):
            bands[name] = (left + width * cuts[idx], left + width * cuts[idx + 1])
        return bands

    @classmethod
    def _line_has_strong_row_anchor(
        cls,
        line: dict,
        column_bands: dict[str, tuple[float, float]],
    ) -> bool:
        text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
        if not text:
            return False
        if not re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", text, flags=re.IGNORECASE):
            return False
        line_left = float(line.get("left", 0.0) or 0.0)
        start_band = column_bands.get("Value Date", (0.0, 99999.0))
        if start_band[0] - 16 <= line_left <= start_band[1] + 64:
            return True
        span = max(1.0, start_band[1] - start_band[0])
        return line_left <= start_band[0] + (span * 1.8)

    @classmethod
    def _is_continuation_line(
        cls,
        line: dict,
        prev_line: dict,
        column_bands: dict[str, tuple[float, float]],
    ) -> bool:
        text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
        if not text:
            return False
        if re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", text, flags=re.IGNORECASE):
            return False
        left = float(line.get("left", 0.0) or 0.0)
        desc_band = column_bands.get("Description", (0.0, 99999.0))
        ref_band = column_bands.get("Reference", (0.0, 99999.0))
        y = float(line.get("top", 0.0) or 0.0)
        py = float(prev_line.get("top", 0.0) or 0.0)
        return (desc_band[0] - 12 <= left <= ref_band[1] + 20) and (0 <= y - py <= 36)

    @classmethod
    def _estimate_tokens_with_x(cls, row_lines: list[dict]) -> list[tuple[str, float]]:
        tokens: list[tuple[str, float]] = []
        for line in row_lines or []:
            text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            if not text:
                continue
            line_left = float(line.get("left", 0.0) or 0.0)
            line_width = float(line.get("width", max(1, len(text) * 7)) or max(1, len(text) * 7))
            parts = text.split()
            if not parts:
                continue
            cursor = line_left
            step = max(line_width / max(len(text), 1), 4.0)
            for part in parts:
                token_width = max(len(part) * step, 6.0)
                center_x = cursor + token_width / 2.0
                tokens.append((part, center_x))
                cursor += token_width + step
        return tokens

    @classmethod
    def _assign_tokens_to_ledger_columns(
        cls,
        token_items: list[tuple[str, float]],
        column_bands: dict[str, tuple[float, float]],
    ) -> dict[str, str]:
        buckets: dict[str, list[str]] = {col: [] for col in cls.LEDGER_COLUMNS}
        for token, x in token_items:
            col = cls._nearest_column_for_x(x, column_bands)
            if col:
                buckets[col].append(token)
        out = {col: re.sub(r"\s+", " ", " ".join(vals)).strip() for col, vals in buckets.items()}

        # Extract references from description if needed.
        if not out.get("Reference"):
            ref_match = re.search(r"\b[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}\b", out.get("Description", ""))
            if ref_match:
                out["Reference"] = ref_match.group(0)
                out["Description"] = (
                    out.get("Description", "")[: ref_match.start()] + " " + out.get("Description", "")[ref_match.end():]
                ).strip()
        return out

    @classmethod
    def _nearest_column_for_x(
        cls,
        x: float,
        column_bands: dict[str, tuple[float, float]],
    ) -> str | None:
        best_col = None
        best_distance = None
        for col, (start, end) in column_bands.items():
            if start <= x <= end:
                return col
            center = (start + end) / 2.0
            dist = abs(x - center)
            if best_distance is None or dist < best_distance:
                best_distance = dist
                best_col = col
        return best_col

    @staticmethod
    def _stitch_split_decimal_tokens(tokens: list[str]) -> list[str]:
        stitched: list[str] = []
        idx = 0
        while idx < len(tokens):
            current = str(tokens[idx]).strip()
            if idx + 1 < len(tokens):
                nxt = str(tokens[idx + 1]).strip()
                # OCR often emits thousand separator as dot and decimal part as next token.
                # Example: -200.398 55 -> -200,398.55
                if (
                    re.fullmatch(r"[-+]?\d{1,3}\.\d{3}", current)
                    and re.fullmatch(r"\d{1,3}", nxt)
                ):
                    stitched.append(f"{current.replace('.', ',')}.{nxt}")
                    idx += 2
                    continue
                if (
                    re.fullmatch(r"[-+]?\d[\d,]*", current)
                    and re.fullmatch(r"\d{1,3}", nxt)
                    and ("," in current or len(current) >= 4)
                    and "." not in current
                ):
                    stitched.append(f"{current}.{nxt}")
                    idx += 2
                    continue
            stitched.append(current)
            idx += 1
        return stitched

    @classmethod
    def _parse_transaction_line(cls, text: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        normalized = normalized.lstrip("-").strip()

        patterns = [
            re.compile(
                r"(?P<posting>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
                r"(?P<txn>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
                r"(?P<terminal>\d+)\s+"
                r"(?P<batch>\d+)\s+"
                r"(?P<seq>\d+)\s+"
                r"(?P<card>[A-Za-z0-9xX*]+)\s+"
                r"(?P<type>[A-Z]{2})\s+"
                r"(?P<txn_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<com_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<vat_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<net_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<cback_amt>[-+]?\d[\d,]*(?:\.\d+)?)$"
            ),
            re.compile(
                r"(?P<posting>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
                r"(?P<txn>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+"
                r"(?P<terminal>\d+)\s+"
                r"(?P<batch>\d+)\s+"
                r"(?P<seq>\d+)\s+"
                r"(?P<card>[A-Za-z0-9xX*]+)\s+"
                r"(?P<type>[A-Z]{2})\s+"
                r"(?P<txn_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<vat_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<com_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<net_amt>[-+]?\d[\d,]*(?:\.\d+)?)\s+"
                r"(?P<cback_amt>[-+]?\d[\d,]*(?:\.\d+)?)$"
            ),
        ]

        for pattern in patterns:
            match = pattern.search(normalized)
            if match:
                return {
                    "PostingDate": match.group("posting"),
                    "Txn.Date": match.group("txn"),
                    "Terminal": match.group("terminal"),
                    "Batch": match.group("batch"),
                    "Seq#": match.group("seq"),
                    "Card No.": match.group("card"),
                    "Type": match.group("type"),
                    "Txn.Amount": match.group("txn_amt"),
                    "Com.Amount": match.group("com_amt"),
                    "Vat Amount": match.group("vat_amt"),
                    "Net Amount": match.group("net_amt"),
                    "Cback Amount": match.group("cback_amt"),
                }

        return None

    @classmethod
    def _parse_summary_line(cls, text: str) -> dict[str, str] | None:
        normalized = re.sub(r"\s+", " ", text).strip()
        tokens = normalized.split()
        if len(tokens) < 2:
            return None

        first = tokens[0].strip()
        normalized_first = PDFTableDetector.normalize_text(first)

        valid_label = (
            normalized_first in {"visa", "master", "benefit", "maestro", "others", "subtotal", "sub-total", "total"}
            or normalized_first.startswith("visa")
            or normalized_first.startswith("master")
        )
        if not valid_label:
            return None

        numeric_tokens = [tok for tok in tokens[1:] if PDFTableDetector.looks_like_numeric(tok)]
        if not numeric_tokens:
            return None

        return {
            "Card Type": first,
            "Count": numeric_tokens[0] if len(numeric_tokens) >= 1 else "",
            "Txn.Amount": numeric_tokens[1] if len(numeric_tokens) >= 2 else "",
            "Com.Amount": numeric_tokens[2] if len(numeric_tokens) >= 3 else "",
            "Net Amount": numeric_tokens[3] if len(numeric_tokens) >= 4 else "",
            "Cback Amount": numeric_tokens[4] if len(numeric_tokens) >= 5 else "",
            "Vat Amount": numeric_tokens[5] if len(numeric_tokens) >= 6 else "",
        }

    @classmethod
    def _merge_line_continuation_loose(cls, current_row: dict[str, str], text: str, columns: list[str]) -> dict[str, str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return current_row

        if cls._looks_like_reference_token(normalized.replace(" ", "")):
            current_row["Reference"] = f"{current_row.get('Reference', '')} {normalized}".strip()
            return current_row

        if re.fullmatch(r"[-A-Za-z0-9./ ]{1,50}", normalized):
            current_row["Description"] = f"{current_row.get('Description', '')} {normalized}".strip()
            return current_row

        numeric_tokens = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", normalized)
        alpha_tokens = re.sub(r"[-+]?\d[\d,]*(?:\.\d+)?", " ", normalized).strip()

        if alpha_tokens and not cls._line_starts_row_candidate(normalized):
            current_row["Description"] = f"{current_row.get('Description', '')} {alpha_tokens}".strip()

        if numeric_tokens:
            if not current_row.get("Balance"):
                current_row["Balance"] = cls._normalize_numeric_token(numeric_tokens[-1], "Balance")
            elif not current_row.get("Credit") and not current_row.get("Debit"):
                value = cls._normalize_numeric_token(numeric_tokens[0], "Amount")
                if value.startswith("-"):
                    current_row["Debit"] = value
                else:
                    current_row["Credit"] = value

        return current_row

    @classmethod
    def _line_starts_row_candidate(cls, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return False
        if cls._looks_like_metadata_or_header_line(normalized):
            return False
        if re.match(r"^\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\b", normalized, flags=re.IGNORECASE):
            return True
        if PDFTableDetector.is_balance_summary_line(normalized) and bool(re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", normalized)):
            return True
        return False

    @classmethod
    def _looks_like_metadata_or_header_line(cls, text: str) -> bool:
        normalized = PDFTableDetector.normalize_text(text)
        if not normalized:
            return False

        if cls._looks_like_table_header_line(text):
            return True

        if ":" in text:
            label, _value = text.split(":", 1)
            if PDFTableDetector.canonical_label_key(label):
                return True

        if re.match(r"^(from date|to date|account(?: number| no)?|customer(?: id| name)?|portfolio|statement date|currency|page)\b", normalized):
            return True

        return False

    @classmethod
    def _normalize_rows(cls, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        normalized_rows: list[dict[str, str]] = []
        for row in rows:
            clean_row: dict[str, str] = {}
            for key, value in row.items():
                clean_row[str(key).strip()] = re.sub(r"\s+", " ", str(value)).strip()
            normalized_rows.append(clean_row)
        return normalized_rows

    @classmethod
    def _consolidate_logical_rows(cls, rows: list[dict[str, str]], columns: list[str]) -> list[dict[str, str]]:
        if not rows:
            return rows

        consolidated: list[dict[str, str]] = []
        current: dict[str, str] | None = None

        for row in rows:
            if current is None:
                current = dict(row)
                continue

            if cls._is_orphan_fragment_row(row, columns):
                current = cls._merge_fragment_into_row(current, row, columns)
                continue

            consolidated.append(cls._finalize_row(current, columns))
            current = dict(row)

        if current is not None:
            consolidated.append(cls._finalize_row(current, columns))

        return consolidated

    @classmethod
    def _is_orphan_fragment_row(cls, row: dict[str, str], columns: list[str]) -> bool:
        value_date = str(row.get("Value Date", "")).strip()
        post_date = str(row.get("Post Date", "")).strip()
        ref = str(row.get("Reference", "")).strip()
        desc = str(row.get("Description", "")).strip()
        debit = str(row.get("Debit", "")).strip()
        credit = str(row.get("Credit", "")).strip()
        balance = str(row.get("Balance", "")).strip()

        if value_date or post_date:
            return False
        if PDFTableDetector.is_balance_summary_line(desc):
            return False

        text_len = len(desc.split())
        numeric_count = sum(bool(x) for x in (debit, credit, balance))

        if ref and not desc and numeric_count == 0:
            return True
        if desc and text_len <= 3 and not ref and numeric_count <= 1:
            return True
        if desc and re.fullmatch(r"[-A-Za-z0-9./ ]{1,50}", desc) and numeric_count <= 1:
            return True

        return False

    @classmethod
    def _merge_fragment_into_row(cls, base: dict[str, str], fragment: dict[str, str], columns: list[str]) -> dict[str, str]:
        merged = dict(base)

        if fragment.get("Description"):
            merged["Description"] = f"{merged.get('Description', '')} {fragment.get('Description', '')}".strip()

        if fragment.get("Reference"):
            if merged.get("Reference"):
                merged["Reference"] = f"{merged.get('Reference', '')} {fragment.get('Reference', '')}".strip()
            else:
                merged["Reference"] = fragment["Reference"]

        for key in ("Debit", "Credit", "Balance"):
            if not merged.get(key) and fragment.get(key):
                merged[key] = fragment[key]

        return merged

    @classmethod
    def _finalize_row(cls, row: dict[str, str], columns: list[str]) -> dict[str, str]:
        return {column: re.sub(r"\s+", " ", str(row.get(column, ""))).strip() for column in columns}

    @classmethod
    def _validate_block_aware_table(cls, table: TableData) -> tuple[bool, list[str], float]:
        warnings: list[str] = []
        source = str(getattr(table, "source", "") or "")
        columns = table.columns or []
        rows = table.rows or []

        if not rows or not columns:
            return False, warnings, 0.0

        normalized_cols = [PDFTableDetector.normalize_text(col) for col in columns]

        if source.startswith("opendataloader_structured"):
            populated_ratio = cls._populated_ratio(rows, columns)
            if len(rows) >= 1 and len(columns) >= 2 and populated_ratio >= 0.20:
                return True, warnings, 0.78
            return False, warnings, 0.0

        if "merchant_transactions" in source:
            header_hits = sum(
                any(token in col for token in ("postingdate", "txn.date", "terminal", "batch", "seq", "card", "amount", "type", "vat", "net", "cback", "com"))
                for col in normalized_cols
            )
            if header_hits >= 5 and len(rows) >= 1:
                return True, warnings, 0.80
            return False, warnings, 0.0

        if "merchant_batch_summary" in source or "merchant_summary" in source:
            header_hits = sum(
                any(token in col for token in ("card", "count", "txn", "com", "net", "cback", "vat", "amount", "total"))
                for col in normalized_cols
            )
            if header_hits >= 3 and len(rows) >= 1:
                return True, warnings, 0.72
            return False, warnings, 0.0

        canonical_hits = sum(
            any(token in col for token in ("value", "description", "reference", "post", "debit", "credit", "balance"))
            for col in normalized_cols
        )
        if canonical_hits >= 4:
            real_rows = sum(
                1 for row in rows
                if str(row.get("Description", "")).strip() or str(row.get("Balance", "")).strip()
            )
            if real_rows >= 2:
                return True, warnings, 0.76

        if len(rows) >= 1 and len(columns) >= 2:
            label_like_first_col = sum(
                bool(str(row.get(columns[0], "")).strip()) and not PDFTableDetector.looks_like_numeric(str(row.get(columns[0], "")).strip())
                for row in rows
            )
            if label_like_first_col >= 1:
                warnings.append("Accepted small summary-like table block with source-aware validation.")
                return True, warnings, 0.58

        return False, warnings, 0.0

    @staticmethod
    def _populated_ratio(rows: list[dict[str, str]], columns: list[str]) -> float:
        total = len(rows) * len(columns)
        if total <= 0:
            return 0.0
        populated = sum(1 for row in rows for col in columns if str(row.get(col, "")).strip())
        return populated / total

    @staticmethod
    def _normalize_summary_label(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        cleaned = re.sub(r"[-+]?\d[\d,]*(?:\.\d+)?$", "", cleaned).strip()
        return cleaned

    @staticmethod
    def _normalize_numeric_token(token: str, column_name: str) -> str:
        cleaned = str(token).strip().replace(" ", "")
        cleaned = cleaned.replace("O", "0").replace("o", "0")
        cleaned = cleaned.replace("I", "1").replace("l", "1")
        if column_name in {"Balance", "Debit", "Credit", "Amount", "Txn.Amount", "Com.Amount", "Net Amount", "Vat Amount"}:
            cleaned = cleaned.replace("B", "8")
        cleaned = re.sub(r"(?<!^)[^0-9,.\-+]", "", cleaned)
        if cleaned.count(".") > 1:
            parts = cleaned.split(".")
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        return cleaned

    @classmethod
    def _normalize_date_token(cls, token: str) -> str:
        text = re.sub(r"\s+", " ", str(token)).strip().upper()
        text = text.replace("2B", "28").replace(" O", " 0")
        match = re.match(r"^(\d{1,2})\s+([A-Z]{3,9})\s+(\d{2,4})$", text)
        if not match:
            return text
        day, month, year = match.groups()
        month = cls.MONTH_NORMALIZATION.get(month[:3], month[:3])
        return f"{day.zfill(2)} {month} {year}"

    @staticmethod
    def _looks_like_reference_token(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]{4,}", value or ""))

    @staticmethod
    def _row_has_signal(row: dict[str, str], columns: list[str]) -> bool:
        values = [str(row.get(column, "")).strip() for column in columns]
        populated = [value for value in values if value]
        if not populated:
            return False
        joined = " ".join(populated)
        if PDFTableDetector.is_balance_summary_line(joined):
            return True
        if row.get("Value Date") or row.get("Reference"):
            return True
        if row.get("Description") and any(row.get(key) for key in ("Debit", "Credit", "Balance")):
            return True
        return False
