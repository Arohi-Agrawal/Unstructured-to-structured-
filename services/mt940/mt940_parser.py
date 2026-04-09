from __future__ import annotations

from pathlib import Path

import mt940

from services.parser_base import BaseParser, ParserResult, TableData, default_metadata


class MT940Parser(BaseParser):
    parser_name = "mt940_parser"
    implemented = True

    def __init__(self) -> None:
        self._encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1"]

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        lower = file_path.lower()
        return detected_type == "mt940" or lower.endswith(".mt940") or lower.endswith(".940")

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        metadata = default_metadata(file_path.name)
        notes: list[str] = []
        issues: list[str] = []
        validation_warnings: list[str] = []

        raw_text = self._read_text(file_path)
        try:
            parsed = mt940.parse(raw_text)
        except Exception as exc:
            return ParserResult(
                status="error",
                message="MT940 parsing failed.",
                implemented=True,
                parser_used="mt940",
                detected_type=detected_type,
                mode_used="swift_failed",
                metadata=metadata,
                notes=notes,
                issues=[f"Failed to parse MT940 content: {exc}"],
                confidence=0.0,
            )

        statements = self._normalize_statements(parsed)
        if not statements:
            issues.append("No MT940 statements detected in file.")

        rows: list[dict[str, str]] = []
        metadata_snapshots: list[dict[str, str]] = []

        for statement_index, statement in enumerate(statements, start=1):
            statement_data = getattr(statement, "data", {}) or {}
            txs = getattr(statement, "transactions", []) or []
            statement_id = str(statement_data.get("transaction_reference", "") or f"statement_{statement_index}")

            metadata_snapshots.append(
                {
                    "statementId": statement_id,
                    "accountNumber": str(statement_data.get("account_identification", "") or ""),
                    "statementNumber": str(statement_data.get("statement_number", "") or ""),
                    "sequenceNumber": str(statement_data.get("sequence_number", "") or ""),
                    "openingBalance": str(getattr(statement_data.get("opening_balance"), "amount", "") or ""),
                    "closingBalance": str(getattr(statement_data.get("closing_balance"), "amount", "") or ""),
                }
            )

            if not txs:
                validation_warnings.append(f"No transactions found for {statement_id}.")
                continue

            for entry in txs:
                entry_data = getattr(entry, "data", {}) or {}
                amount_obj = entry_data.get("amount")
                amount = getattr(amount_obj, "amount", amount_obj)
                currency = getattr(amount_obj, "currency", "") or ""

                rows.append(
                    {
                        "Statement Id": statement_id,
                        "Date": str(entry_data.get("date", "") or ""),
                        "Entry Date": str(entry_data.get("entry_date", "") or ""),
                        "Amount": str(amount or ""),
                        "Currency": str(currency or ""),
                        "Transaction Type": str(entry_data.get("id", "") or ""),
                        "Reference": str(entry_data.get("customer_reference", "") or ""),
                        "Bank Reference": str(entry_data.get("bank_reference", "") or ""),
                        "Description": str(entry_data.get("transaction_details", "") or "").replace("\n", " ").strip(),
                        "Raw Tag Data": str(entry_data or ""),
                    }
                )

        first_statement = statements[0] if statements else None
        first_data = getattr(first_statement, "data", {}) if first_statement else {}
        first_data = first_data or {}
        closing_balance = first_data.get("final_closing_balance") or first_data.get("closing_balance")
        opening_balance = first_data.get("final_opening_balance") or first_data.get("opening_balance")

        account = str(first_data.get("account_identification", "") or "")
        statement_ref = str(first_data.get("transaction_reference", "") or "")
        currency = ""
        if opening_balance and getattr(opening_balance, "currency", None):
            currency = str(opening_balance.currency)
        elif closing_balance and getattr(closing_balance, "currency", None):
            currency = str(closing_balance.currency)

        metadata["reportName"] = file_path.stem
        metadata["title"] = "MT940 Statement"
        metadata["accountNumber"] = account or None
        metadata["currency"] = currency or None
        metadata["statementDate"] = str(getattr(closing_balance, "date", "") or "")
        metadata["openingBalance"] = str(getattr(opening_balance, "amount", "") or "") or None
        metadata["closingBalance"] = str(getattr(closing_balance, "amount", "") or "") or None
        metadata["rawLabelValues"] = {
            "transactionReference": statement_ref,
            "openingBalance": str(getattr(opening_balance, "amount", "") or ""),
            "closingBalance": str(getattr(closing_balance, "amount", "") or ""),
            "statementCount": str(len(statements)),
            "transactionCount": str(len(rows)),
            "statements": metadata_snapshots,
        }
        if not metadata.get("currency"):
            currencies = [str(row.get("Currency", "")).strip().upper() for row in rows if str(row.get("Currency", "")).strip()]
            unique_currencies = sorted(set(currencies))
            if len(unique_currencies) == 1:
                metadata["currency"] = unique_currencies[0]
            elif len(unique_currencies) > 1:
                validation_warnings.append("Multiple currencies detected across transactions.")
        if metadata.get("openingBalance") in {"", "None"}:
            metadata["openingBalance"] = None
        if metadata.get("closingBalance") in {"", "None"}:
            metadata["closingBalance"] = None
        metadata["summaryText"] = [f"Statements: {len(statements)}", f"Transactions: {len(rows)}"]
        if validation_warnings:
            metadata["validationWarnings"] = list(dict.fromkeys(validation_warnings))

        table = TableData(
            table_id="table_001",
            name="table_001_transactions",
            columns=[
                "Statement Id",
                "Date",
                "Entry Date",
                "Amount",
                "Currency",
                "Transaction Type",
                "Description",
                "Reference",
                "Bank Reference",
                "Raw Tag Data",
            ],
            rows=rows,
            source="mt940_transactions",
            confidence=0.93 if rows else 0.6,
        )

        return ParserResult(
            status="success",
            message="MT940 parsed successfully.",
            implemented=True,
            parser_used="mt940",
            detected_type=detected_type,
            mode_used="swift_mt940",
            metadata=metadata,
            notes=notes + [f"Parsed {len(statements)} MT940 statement block(s)."],
            tables=[table] if rows else [],
            issues=issues,
            confidence=0.93 if rows else 0.6,
        )

    @staticmethod
    def _normalize_statements(parsed) -> list:
        if parsed is None:
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if item is not None]
        if hasattr(parsed, "transactions"):
            return [parsed]
        return []

    def _read_text(self, file_path: Path) -> str:
        for encoding in self._encodings:
            try:
                return file_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return file_path.read_text(encoding="utf-8", errors="replace")
