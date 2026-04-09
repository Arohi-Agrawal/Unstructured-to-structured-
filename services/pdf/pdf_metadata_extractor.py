from __future__ import annotations

import re

from services.parser_base import default_metadata
from services.pdf.opendataloader_runner import PDFRunResult
from services.pdf.pdf_table_detector import PDFTableDetector


class PDFMetadataExtractor:
    GENERIC_LABEL_PATTERNS = {
        "accountNumber": [
            r"(?:bank\s*account|account\s*(?:number|no)?)\s*[:\-]?\s*([A-Z0-9\-]{4,})"
        ],
        "currency": [
            r"\b(AED|USD|EUR|GBP|INR|BHD|SAR|QAR|KWD|OMR)\b",
            r"all\s+currency\s+charged\s+are\s+in\s+(AED|USD|EUR|GBP|INR|BHD|SAR|QAR|KWD|OMR)\b",
        ],
        "statementDate": [
            r"statement\s*date\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
            r"printed\s+on\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
        ],
        "periodStart": [r"from\s+date\s*[:\-]?\s*([0-9A-Za-z/\- ]+)"],
        "periodEnd": [r"to\s+date\s*[:\-]?\s*([0-9A-Za-z/\- ]+)"],
        "customerId": [r"(?:customer|client)\s*(?:id|number|no)?\s*[:\-]?\s*([A-Z0-9\-]{4,})"],
        "address": [r"address\s*[:\-]?\s*(.+)$"],
        "trn": [r"\btrn\s*[:\-]?\s*([0-9]{8,})\b"],
        "merchantCode": [r"merchant\s*code\s*[:\-]?\s*([0-9]{6,})\b"],
        "reportTakenBy": [r"(?:report\s*taken\s*by|prepared\s*by)\s*[:\-]?\s*([A-Za-z][A-Za-z .'-]{2,})"],
    }

    TITLE_BLOCK_TERMS = (
        "statement",
        "report",
        "advice",
        "transaction details",
        "tax invoice",
        "summary",
    )

    @staticmethod
    def should_use_table_first(run_result: PDFRunResult) -> bool:
        return PDFTableDetector.is_table_dominant(run_result)

    @classmethod
    def extract(cls, file_name: str, run_result: PDFRunResult, force_table_minimal: bool = False) -> dict:
        metadata = default_metadata(file_name)
        metadata["title"] = None
        metadata["trn"] = None
        metadata["merchantCode"] = None
        metadata["reportTakenBy"] = None
        metadata["merchantName"] = None

        payload = run_result.raw_json or {}
        markdown = run_result.markdown_text or ""
        ocr_lines = run_result.ocr_lines or []

        table_first = force_table_minimal or cls.should_use_table_first(run_result)
        regions = PDFTableDetector.split_line_regions(ocr_lines, run_result.ocr_words)
        branding_lines = regions.get("branding", [])
        metadata_lines = regions.get("header", [])
        footer_lines = regions.get("footer", [])
        table_lines = regions.get("table", [])

        strong_metadata_evidence = cls._has_strong_metadata_evidence(metadata_lines, footer_lines, markdown)
        effective_table_first = bool(table_first and not strong_metadata_evidence)

        region_text = "\n".join(
            line["text"] for line in metadata_lines + footer_lines + table_lines if line.get("text")
        ).strip()

        metadata["pageInfo"] = cls._extract_page_info(payload, metadata_lines, footer_lines, markdown)

        if not effective_table_first:
            metadata["title"] = cls._derive_title(metadata_lines, markdown)
            metadata["reportName"] = metadata["title"]
            metadata["institutionName"] = cls._derive_institution_name(metadata_lines, markdown)

            (
                metadata["headers"],
                metadata["footers"],
                metadata["headings"],
                metadata["paragraphs"],
            ) = cls._segment_regions(branding_lines, metadata_lines, footer_lines, table_lines)

            metadata["narrativeText"] = cls._collect_narrative(markdown, metadata_lines, footer_lines, table_lines)
            metadata["disclaimerText"] = [
                line for line in metadata["narrativeText"] if "disclaimer" in line.lower()
            ]
            metadata["summaryText"] = [
                line for line in metadata["narrativeText"] if "summary" in line.lower()
            ]
        else:
            metadata["title"] = None
            metadata["reportName"] = None
            metadata["institutionName"] = None

        metadata["rawLabelValues"] = cls._raw_label_pairs(metadata_lines + footer_lines + table_lines)

        explicit_pairs = (
            cls._extract_strict_canonical_label_values(metadata_lines + footer_lines + table_lines)
            if effective_table_first
            else cls._extract_canonical_label_values(metadata_lines + footer_lines + table_lines)
        )
        cls._apply_explicit_pairs(metadata, explicit_pairs, table_first=effective_table_first)

        cls._extract_business_fields(
            metadata,
            metadata_lines + footer_lines + table_lines,
            markdown,
        )

        if not effective_table_first:
            for field_name, patterns in cls.GENERIC_LABEL_PATTERNS.items():
                if metadata.get(field_name):
                    continue
                value = cls._match_patterns(region_text, patterns)
                if value:
                    metadata[field_name] = value

            cls._fill_date_range_from_same_line(metadata, metadata_lines + footer_lines + table_lines)
            cls._apply_page_truth_validation(metadata, metadata_lines, footer_lines, table_lines, markdown)
        else:
            cls._enforce_minimal_table_first_metadata(metadata, metadata_lines, footer_lines, table_lines)

        cls._clean_uncertain_values(metadata)
        cls._normalize_dates(metadata)
        return metadata

    @classmethod
    def _extract_business_fields(cls, metadata: dict, ocr_lines: list[dict], markdown: str) -> None:
        merged_text = "\n".join(
            str(line.get("text", "")) for line in ocr_lines if str(line.get("text", "")).strip()
        )
        merged_text = f"{merged_text}\n{markdown or ''}"
        merged_lines = [re.sub(r"\s+", " ", line).strip() for line in merged_text.splitlines() if line.strip()]

        if not metadata.get("title"):
            metadata["title"] = cls._extract_title_from_text(merged_text)
        if not metadata.get("reportName") and metadata.get("title"):
            metadata["reportName"] = metadata["title"]

        if not metadata.get("institutionName"):
            metadata["institutionName"] = cls._extract_institution_from_text(merged_text)

        key_patterns = {
            "trn": r"\bTRN\s*[:\-]?\s*([0-9]{8,})\b",
            "merchantCode": r"Merchant\s*Code\s*[:\-]?\s*([0-9]{6,})\b",
            "accountNumber": r"(?:Bank\s*Account|Account\s*(?:No|Number)?)\s*[:\-]?\s*([0-9A-Z\-]{5,})",
            "statementDate": r"(?:Statement\s*Date|Date)\s*[:\-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4})",
            "reportTakenBy": r"(?:Report\s*Taken\s*By|Prepared\s*By)\s*[:\-]?\s*([A-Za-z][A-Za-z .'-]{2,})",
        }
        for field, pattern in key_patterns.items():
            if metadata.get(field):
                continue
            match = re.search(pattern, merged_text, flags=re.IGNORECASE)
            if match:
                metadata[field] = re.sub(r"\s+", " ", match.group(1)).strip()

        if not metadata.get("merchantCode"):
            metadata["merchantCode"] = cls._extract_value_near_label(
                merged_lines,
                label_tokens=("merchant code",),
                value_pattern=r"\b[0-9]{6,}\b",
            )
        if not metadata.get("accountNumber"):
            metadata["accountNumber"] = cls._extract_value_near_label(
                merged_lines,
                label_tokens=("bank account", "account number", "account"),
                value_pattern=r"\b[0-9A-Z\-]{5,}\b",
            )

        if not metadata.get("currency"):
            ccy_match = re.search(
                r"(?:all\s+currency\s+charged\s+are\s+in|currency)\s*[:\-]?\s*(BHD|AED|USD|EUR|GBP|INR|SAR|QAR|KWD|OMR)\b",
                merged_text,
                flags=re.IGNORECASE,
            )
            if ccy_match:
                metadata["currency"] = ccy_match.group(1).upper()
            else:
                phrase_match = re.search(
                    r"all\s+currency\s+charged\s+are\s+in\s+(BHD|AED|USD|EUR|GBP|INR|SAR|QAR|KWD|OMR)\b",
                    merged_text,
                    flags=re.IGNORECASE,
                )
                if phrase_match:
                    metadata["currency"] = phrase_match.group(1).upper()

        # Better entity mapping
        raw_pairs = metadata.get("rawLabelValues", {}) if isinstance(metadata.get("rawLabelValues"), dict) else {}

        if not metadata.get("merchantName"):
            merchant_raw = raw_pairs.get("merchantname")
            if merchant_raw:
                metadata["merchantName"] = merchant_raw

        if not metadata.get("merchantCode"):
            merchant_code_raw = (
                raw_pairs.get("merchantcode")
                or raw_pairs.get("merchant code")
                or raw_pairs.get("merchant_code")
            )
            if merchant_code_raw:
                metadata["merchantCode"] = str(merchant_code_raw).strip()

        if not metadata.get("accountNumber"):
            account_raw = (
                raw_pairs.get("bank account")
                or raw_pairs.get("account")
                or raw_pairs.get("account no")
                or raw_pairs.get("account number")
            )
            if account_raw:
                metadata["accountNumber"] = str(account_raw).strip()

        if not metadata.get("currency"):
            currency_raw = raw_pairs.get("currency") or raw_pairs.get("ccy")
            if currency_raw:
                metadata["currency"] = str(currency_raw).strip().upper()

        if not metadata.get("customerName"):
            entity_name = cls._extract_entity_name_from_lines(ocr_lines)
            if entity_name:
                metadata["customerName"] = entity_name
            else:
                customer_from_text = cls._extract_customer_name_from_text_lines(merged_lines)
                if customer_from_text:
                    metadata["customerName"] = customer_from_text

        if metadata.get("institutionName") in {"NBB", "National Bank of Bahrain"}:
            merchant_raw = raw_pairs.get("merchantname")
            customer_name = str(metadata.get("customerName") or "").strip()
            normalized_customer = PDFTableDetector.normalize_text(customer_name)
            if merchant_raw and normalized_customer in {"nbb", "national bank of bahrain"}:
                metadata["customerName"] = merchant_raw

        cls._expand_compact_name_from_markdown(metadata, merged_text)

        if metadata.get("institutionName") == "NBB" and "national bank of bahrain" in PDFTableDetector.normalize_text(merged_text):
            metadata["institutionName"] = "National Bank of Bahrain"

        if metadata.get("merchantCode"):
            metadata.setdefault("rawLabelValues", {})
            metadata["rawLabelValues"]["merchant_code"] = metadata["merchantCode"]
        if metadata.get("trn"):
            metadata.setdefault("rawLabelValues", {})
            metadata["rawLabelValues"]["trn"] = metadata["trn"]

    @classmethod
    def _has_strong_metadata_evidence(cls, metadata_lines: list[dict], footer_lines: list[dict], markdown: str) -> bool:
        evidence_blob = "\n".join(
            [str(line.get("text", "")) for line in (metadata_lines + footer_lines)] + [markdown or ""]
        )
        normalized = PDFTableDetector.normalize_text(evidence_blob)
        strong_hits = 0
        for token in (
            "account",
            "currency",
            "statement date",
            "from date",
            "to date",
            "customer",
            "client",
            "institution",
            "bank",
            "trn",
            "merchant code",
        ):
            if token in normalized:
                strong_hits += 1
        return strong_hits >= 3

    @classmethod
    def _looks_like_title_heading(cls, text: str) -> bool:
        normalized = PDFTableDetector.normalize_text(text)
        return any(token in normalized for token in cls.TITLE_BLOCK_TERMS)

    @classmethod
    def _extract_title_from_text(cls, merged_text: str) -> str | None:
        lines = [re.sub(r"\s+", " ", line).strip() for line in merged_text.splitlines() if line.strip()]
        for line in lines[:20]:
            if cls._looks_like_title_heading(line) and not PDFTableDetector.looks_like_body_row(line):
                return line.lstrip("#").strip()
        return None

    @classmethod
    def _extract_institution_from_text(cls, merged_text: str) -> str | None:
        normalized_blob = PDFTableDetector.normalize_text(merged_text)
        if "national bank of bahrain" in normalized_blob:
            return "National Bank of Bahrain"

        lines = [re.sub(r"\s+", " ", line).strip() for line in merged_text.splitlines() if line.strip()]
        for line in lines[:20]:
            normalized = PDFTableDetector.normalize_text(line)
            if "national bank of bahrain" in normalized:
                return "National Bank of Bahrain"
            if normalized == "nbb":
                return "NBB"
            if "bank" in normalized and not PDFTableDetector.looks_like_body_row(line):
                return line.lstrip("#").strip()
            if any(token in normalized for token in ("institution", "branch", "authority")):
                if not PDFTableDetector.looks_like_body_row(line):
                    return line.lstrip("#").strip()
        return None

    @classmethod
    def _extract_entity_name_from_lines(cls, ocr_lines: list[dict]) -> str | None:
        for line in ocr_lines:
            text = re.sub(r"\s+", " ", str(line.get("text", ""))).strip()
            if not text:
                continue
            normalized = PDFTableDetector.normalize_text(text)

            if cls._looks_like_title_heading(text):
                continue

            if ":" in text:
                label, value = text.split(":", 1)
                norm_label = PDFTableDetector.normalize_text(label)
                if any(token in norm_label for token in ("merchant name", "customer name", "entity name", "account name")):
                    candidate = value.strip()
                    if candidate and not cls._looks_like_title_heading(candidate):
                        return candidate
                continue

            if re.search(r"[A-Za-z]", text) and not re.search(r"\d", text):
                if len(text.split()) >= 3 and not PDFTableDetector.looks_like_body_row(text):
                    if not any(token in normalized for token in ("merchant advice", "transaction details", "tax invoice")):
                        return text.lstrip("#").strip()
        return None

    @classmethod
    def _derive_title(cls, metadata_lines: list[dict], markdown: str) -> str | None:
        joined = "\n".join(line["text"] for line in metadata_lines if line.get("text"))
        title = cls._extract_title_from_text(joined)
        if title:
            return title
        return cls._extract_title_from_text(markdown or "")

    @classmethod
    def _derive_institution_name(cls, metadata_lines: list[dict], markdown: str) -> str | None:
        joined = "\n".join(line["text"] for line in metadata_lines if line.get("text"))
        inst = cls._extract_institution_from_text(joined)
        if inst:
            return inst
        return cls._extract_institution_from_text(markdown or "")

    @staticmethod
    def _extract_page_info(payload: dict, header_lines: list[dict], footer_lines: list[dict], markdown: str) -> list[str]:
        page_info: list[str] = []
        if payload.get("number of pages"):
            page_info.append(f"Page: {payload['number of pages']}")
        page_info.extend([line["text"] for line in header_lines + footer_lines if "page" in line["text"].lower()])
        page_info.extend([line.strip() for line in markdown.splitlines() if "page" in line.lower()])

        normalized: list[str] = []
        seen: set[str] = set()
        for item in page_info:
            compact = re.sub(r"\s+", " ", item).strip()
            match = re.search(r"page\s*[:\-]?\s*(\d+)", compact, flags=re.IGNORECASE)
            canonical = f"Page: {match.group(1)}" if match else compact
            if canonical and canonical.lower() not in seen:
                normalized.append(canonical)
                seen.add(canonical.lower())
        return normalized

    @staticmethod
    def _segment_regions(
        branding_lines: list[dict],
        header_lines: list[dict],
        footer_lines: list[dict],
        table_lines: list[dict],
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        headers = [
            line["text"]
            for line in branding_lines + header_lines
            if not PDFTableDetector.looks_like_generic_placeholder(line["text"])
        ]
        footers = [line["text"] for line in footer_lines]
        headings = [
            line["text"]
            for line in header_lines
            if len(line["text"].split()) <= 10 and not PDFTableDetector.looks_like_generic_placeholder(line["text"])
        ]
        paragraphs = [line["text"] for line in header_lines if line["text"] not in headings]
        paragraphs.extend([line["text"] for line in footer_lines if line["text"] not in footers])
        paragraphs = [line for line in paragraphs if line not in [item["text"] for item in table_lines]]
        return headers, footers, headings, paragraphs

    @staticmethod
    def _collect_narrative(markdown: str, header_lines: list[dict], footer_lines: list[dict], table_lines: list[dict]) -> list[str]:
        narrative = [
            line.strip()
            for line in markdown.splitlines()
            if line.strip() and "|" not in line and not line.startswith("![image ")
        ]
        narrative.extend([line["text"] for line in header_lines + footer_lines if len(line["text"].split()) > 2])
        narrative = [line for line in narrative if line not in [item["text"] for item in table_lines]]
        return list(dict.fromkeys(narrative))

    @classmethod
    def _raw_label_pairs(cls, ocr_lines: list[dict]) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for line in ocr_lines:
            text = line["text"].strip()
            if ":" not in text:
                continue
            label, value = text.split(":", 1)
            label = PDFTableDetector.normalize_text(label)
            value = value.strip()
            if not label or not value:
                continue
            if len(label) < 2:
                continue
            if not re.search(r"[a-z]{2,}", label):
                continue
            if PDFTableDetector.looks_like_body_row(f"{label}: {value}"):
                continue
            pairs[label] = value
        return pairs

    @classmethod
    def _extract_canonical_label_values(cls, ocr_lines: list[dict]) -> dict[str, str]:
        pairs: dict[str, str] = {}
        sorted_lines = sorted(ocr_lines, key=lambda line: (line["page"], line["top"], line["left"]))

        for index, line in enumerate(sorted_lines):
            text = line["text"].strip()

            if ":" in text:
                label, value = text.split(":", 1)
                key = PDFTableDetector.canonical_label_key(label)
                if key and value.strip():
                    pairs[key] = value.strip()
                continue

            key = PDFTableDetector.canonical_label_key(text)
            if key and index + 1 < len(sorted_lines):
                next_line = sorted_lines[index + 1]
                next_text = next_line["text"].strip()
                if next_text and not PDFTableDetector.canonical_label_key(next_text) and not PDFTableDetector.looks_like_body_row(next_text):
                    pairs.setdefault(key, next_text)

        return pairs

    @classmethod
    def _extract_strict_canonical_label_values(cls, ocr_lines: list[dict]) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for line in sorted(ocr_lines, key=lambda item: (item["page"], item["top"], item["left"])):
            text = line["text"].strip()
            if ":" not in text:
                continue
            label, value = text.split(":", 1)
            key = PDFTableDetector.canonical_label_key(label)
            clean_value = value.strip()
            if key and clean_value and not PDFTableDetector.looks_like_body_row(clean_value):
                pairs[key] = clean_value
        return pairs

    @staticmethod
    def _apply_explicit_pairs(metadata: dict, explicit_pairs: dict[str, str], table_first: bool = False) -> None:
        mapping = {
            "accountnumber": "accountNumber",
            "currency": "currency",
            "customername": "customerName",
            "customerid": "customerId",
            "statementdate": "statementDate",
            "periodstart": "periodStart",
            "periodend": "periodEnd",
            "address": "address",
            "institutionname": "institutionName",
            "merchantcode": "merchantCode",
            "trn": "trn",
            "reporttakenby": "reportTakenBy",
            "merchantname": "merchantName",
        }
        for pair_key, metadata_key in mapping.items():
            value = explicit_pairs.get(pair_key)
            if value:
                metadata[metadata_key] = value

        if table_first:
            raw_pairs = metadata.get("rawLabelValues", {}) if isinstance(metadata.get("rawLabelValues"), dict) else {}

            fallback_mapping = {
                "accountNumber": ("account", "account number", "account no", "bank account"),
                "merchantCode": ("merchantcode", "merchant code", "merchant_code"),
                "trn": ("trn",),
                "reportTakenBy": ("report taken by", "prepared by"),
                "merchantName": ("merchantname", "merchant name"),
                "statementDate": ("statement date",),
                "periodStart": ("from date", "period start"),
                "periodEnd": ("to date", "period end"),
            }
            for metadata_key, raw_keys in fallback_mapping.items():
                if metadata.get(metadata_key):
                    continue
                for raw_key in raw_keys:
                    candidate = str(raw_pairs.get(raw_key, "")).strip()
                    if candidate:
                        metadata[metadata_key] = candidate
                        break
            return

        raw_pairs = metadata.get("rawLabelValues", {}) if isinstance(metadata.get("rawLabelValues"), dict) else {}
        account_fallback = raw_pairs.get("account") or raw_pairs.get("account number") or raw_pairs.get("account no") or raw_pairs.get("bank account")
        if account_fallback and not metadata.get("accountNumber"):
            candidate = str(account_fallback).strip()
            if re.fullmatch(r"[A-Z0-9\-]{4,}", candidate) and not PDFTableDetector.looks_like_body_row(candidate):
                metadata["accountNumber"] = candidate

        customer_fallback = (
            raw_pairs.get("customer")
            or raw_pairs.get("customer id")
            or raw_pairs.get("client")
            or raw_pairs.get("client id")
        )
        if customer_fallback and not metadata.get("customerId") and not metadata.get("customerName"):
            candidate = str(customer_fallback).strip()
            if re.fullmatch(r"[A-Z0-9\-]{4,}", candidate) and not re.search(r"[a-z]{3,}", candidate, flags=re.IGNORECASE):
                metadata["customerId"] = candidate

    @classmethod
    def _enforce_minimal_table_first_metadata(
        cls,
        metadata: dict,
        metadata_lines: list[dict],
        footer_lines: list[dict],
        table_lines: list[dict],
    ) -> None:
        evidence_lines = metadata_lines + footer_lines + table_lines
        evidence_text = "\n".join(line.get("text", "") for line in evidence_lines)
        normalized_text = PDFTableDetector.normalize_text(evidence_text)
        raw_pairs = metadata.get("rawLabelValues", {}) if isinstance(metadata.get("rawLabelValues"), dict) else {}

        for key in (
            "title",
            "reportName",
            "institutionName",
            "customerName",
            "address",
        ):
            metadata[key] = None

        account_value = (
            metadata.get("accountNumber")
            or raw_pairs.get("bank account")
            or raw_pairs.get("account number")
            or raw_pairs.get("account no")
            or raw_pairs.get("account")
        )
        if isinstance(account_value, str):
            account_value = account_value.strip()
        if isinstance(account_value, str) and re.fullmatch(r"[A-Z0-9\-]{4,}", account_value) and not PDFTableDetector.looks_like_body_row(account_value):
            metadata["accountNumber"] = account_value
        else:
            metadata["accountNumber"] = None

        statement_value = metadata.get("statementDate") or raw_pairs.get("statement date")
        if isinstance(statement_value, str) and re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}", statement_value):
            metadata["statementDate"] = statement_value.strip()
        else:
            metadata["statementDate"] = None

        period_start = metadata.get("periodStart") or raw_pairs.get("from date") or raw_pairs.get("period start")
        period_end = metadata.get("periodEnd") or raw_pairs.get("to date") or raw_pairs.get("period end")
        metadata["periodStart"] = str(period_start).strip() if isinstance(period_start, str) and period_start.strip() else None
        metadata["periodEnd"] = str(period_end).strip() if isinstance(period_end, str) and period_end.strip() else None

        customer_value = raw_pairs.get("customer id") or raw_pairs.get("client id")
        if customer_value and re.fullmatch(r"[A-Z0-9\-]{4,}", str(customer_value).strip()):
            metadata["customerId"] = str(customer_value).strip()
        else:
            metadata["customerId"] = metadata.get("customerId")

        if metadata.get("currency") and "currency" not in normalized_text and "ccy" not in normalized_text and "all currency charged are in" not in normalized_text:
            metadata["currency"] = None

    @classmethod
    def _expand_compact_name_from_markdown(cls, metadata: dict, merged_text: str) -> None:
        merchant_name = str(metadata.get("merchantName") or "").strip()
        if not merchant_name:
            return

        compact_merchant = re.sub(r"[^A-Za-z]", "", merchant_name).lower()
        if len(compact_merchant) < 8:
            return

        candidates = []
        for raw_line in merged_text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip().lstrip("#").strip()
            if not line or len(line.split()) < 3:
                continue
            normalized = PDFTableDetector.normalize_text(line)
            if any(token in normalized for token in ("statement", "transaction", "summary", "page", "merchant code", "bank account", "trn")):
                continue
            alpha_only = re.sub(r"[^A-Za-z]", "", line).lower()
            if len(alpha_only) < 8:
                continue
            candidates.append((line, alpha_only))

        for line, alpha_only in candidates:
            if compact_merchant in alpha_only or alpha_only in compact_merchant:
                cleaned = re.sub(r"\s+", " ", line.upper()).strip()
                metadata["merchantName"] = cleaned
                if metadata.get("customerName"):
                    customer_compact = re.sub(r"[^A-Za-z]", "", str(metadata.get("customerName"))).lower()
                    if customer_compact == compact_merchant:
                        metadata["customerName"] = cleaned
                break

    @classmethod
    def _extract_value_near_label(cls, lines: list[str], label_tokens: tuple[str, ...], value_pattern: str) -> str | None:
        for idx, line in enumerate(lines):
            normalized = PDFTableDetector.normalize_text(line)
            if not any(token in normalized for token in label_tokens):
                continue
            match_same = re.search(value_pattern, line, flags=re.IGNORECASE)
            if match_same:
                return match_same.group(0).strip()
            for offset in (1, 2, -1):
                pos = idx + offset
                if pos < 0 or pos >= len(lines):
                    continue
                neighbor = lines[pos]
                match_neighbor = re.search(value_pattern, neighbor, flags=re.IGNORECASE)
                if match_neighbor:
                    return match_neighbor.group(0).strip()
        return None

    @classmethod
    def _extract_customer_name_from_text_lines(cls, lines: list[str]) -> str | None:
        for line in lines:
            cleaned = line.lstrip("#").strip()
            if len(cleaned.split()) < 3:
                continue
            normalized = PDFTableDetector.normalize_text(cleaned)
            if any(
                token in normalized
                for token in (
                    "national bank of bahrain",
                    "statement",
                    "merchant advice",
                    "transaction details",
                    "tax invoice",
                    "merchant code",
                    "bank account",
                    "trn",
                    "currency",
                    "page",
                )
            ):
                continue
            alpha_only = re.sub(r"[^A-Za-z ]", "", cleaned).strip()
            if alpha_only and alpha_only.upper() == alpha_only:
                return re.sub(r"\s+", " ", alpha_only).strip()
        return None

    @staticmethod
    def _match_patterns(text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        return None

    @classmethod
    def _fill_date_range_from_same_line(cls, metadata: dict, ocr_lines: list[dict]) -> None:
        for line in ocr_lines:
            normalized = PDFTableDetector.normalize_text(line["text"])
            if "from date" in normalized and "to date" in normalized:
                from_match = re.search(
                    r"from\s*date\s*[:\-]?\s*([0-9A-Za-z/\- ]+?)\s*to\s*date",
                    line["text"],
                    flags=re.IGNORECASE,
                )
                to_match = re.search(
                    r"to\s*date\s*[:\-]?\s*([0-9A-Za-z/\- ]+)$",
                    line["text"],
                    flags=re.IGNORECASE,
                )
                if from_match and not metadata["periodStart"]:
                    metadata["periodStart"] = from_match.group(1).strip()
                if to_match and not metadata["periodEnd"]:
                    metadata["periodEnd"] = to_match.group(1).strip()

    @classmethod
    def _apply_page_truth_validation(
        cls,
        metadata: dict,
        metadata_lines: list[dict],
        footer_lines: list[dict],
        table_lines: list[dict],
        markdown: str,
    ) -> None:
        truth_blob = "\n".join(
            [line["text"] for line in metadata_lines + footer_lines + table_lines if line.get("text")] + [markdown or ""]
        )
        normalized = PDFTableDetector.normalize_text(truth_blob)

        if metadata.get("currency") and metadata["currency"].lower() not in normalized and "currency" not in normalized:
            if "all currency charged are in" not in normalized:
                metadata["currency"] = None

        if metadata.get("customerName") and cls._looks_like_title_heading(metadata["customerName"]):
            metadata["customerName"] = None

        if metadata.get("merchantName") and cls._looks_like_title_heading(metadata["merchantName"]):
            metadata["merchantName"] = None

    @classmethod
    def _clean_uncertain_values(cls, metadata: dict) -> None:
        for key in ("title", "reportName", "institutionName", "customerName", "merchantName", "address"):
            value = metadata.get(key)
            if not isinstance(value, str):
                continue
            cleaned = re.sub(r"\s+", " ", value).strip()
            if not cleaned:
                metadata[key] = None
                continue
            if cls._looks_like_title_heading(cleaned) and key in ("customerName", "merchantName"):
                metadata[key] = None
                continue
            metadata[key] = cleaned

        for key in ("currency", "trn", "merchantCode", "accountNumber", "customerId"):
            value = metadata.get(key)
            if isinstance(value, str):
                metadata[key] = value.strip() or None

    @staticmethod
    def _normalize_dates(metadata: dict) -> None:
        def clean_date_string(value: str | None) -> str | None:
            if not isinstance(value, str):
                return value
            text = re.sub(r"\s+", " ", value).strip()
            text = text.replace("2B", "28")
            text = text.replace(" O", " 0")
            return text or None

        metadata["statementDate"] = clean_date_string(metadata.get("statementDate"))
        metadata["periodStart"] = clean_date_string(metadata.get("periodStart"))
        metadata["periodEnd"] = clean_date_string(metadata.get("periodEnd"))

        period_start = metadata.get("periodStart")
        if isinstance(period_start, str):
            split_match = re.search(
                r"^(.*?)(?:\s*to\s*date[:\-]?\s*)(.+)?$",
                period_start,
                flags=re.IGNORECASE,
            )
            if split_match:
                left = (split_match.group(1) or "").strip()
                right = (split_match.group(2) or "").strip()
                if left:
                    metadata["periodStart"] = left
                if right and not metadata.get("periodEnd"):
                    metadata["periodEnd"] = right

        period_end = metadata.get("periodEnd")
        if isinstance(period_end, str):
            metadata["periodEnd"] = re.sub(r"^\s*to\s*date[:\-]?\s*", "", period_end, flags=re.IGNORECASE).strip() or None

        for key in ("periodStart", "periodEnd"):
            value = metadata.get(key)
            if not isinstance(value, str):
                continue
            date_match = re.search(
                r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{2,4}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
                value,
                flags=re.IGNORECASE,
            )
            if date_match:
                metadata[key] = date_match.group(0).strip()

        statement = metadata.get("statementDate")
        if isinstance(statement, str) and "to date" in statement.lower():
            from_match = re.search(
                r"([0-9]{1,2}\s+[A-Za-z]{3}\s+[0-9]{4})",
                statement,
                flags=re.IGNORECASE,
            )
            to_match = re.search(
                r"to\s*date[:\-]?\s*([0-9A-Za-z ]+)$",
                statement,
                flags=re.IGNORECASE,
            )
            if from_match and not metadata.get("periodStart"):
                metadata["periodStart"] = from_match.group(1).replace("2B", "28").strip()
            if to_match:
                cleaned_end = to_match.group(1).replace("2B", "28").strip()
                metadata["periodEnd"] = cleaned_end
            if from_match:
                metadata["statementDate"] = from_match.group(1).replace("2B", "28").strip()
