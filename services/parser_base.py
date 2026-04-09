from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def default_metadata(file_name: str) -> dict[str, Any]:
    return {
        "title": file_name,
        "reportName": None,
        "institutionName": None,
        "accountNumber": None,
        "currency": None,
        "statementDate": None,
        "periodStart": None,
        "periodEnd": None,
        "customerName": None,
        "address": None,
        "pageInfo": [],
        "headers": [],
        "footers": [],
        "headings": [],
        "paragraphs": [],
        "narrativeText": [],
        "disclaimerText": [],
        "summaryText": [],
        "rawLabelValues": {},
    }


@dataclass
class TableData:
    table_id: str
    name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    source: str = "structured"
    page_numbers: list[int] = field(default_factory=list)
    confidence: float = 0.0
    file_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParserResult:
    status: str
    message: str
    implemented: bool
    parser_used: str
    detected_type: str
    mode_used: str
    metadata: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    confidence: float = 0.0


class BaseParser(ABC):
    parser_name = "base"
    implemented = False

    @abstractmethod
    def can_handle(self, file_path: str, detected_type: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        raise NotImplementedError


class PlaceholderParser(BaseParser):
    parser_name = "placeholder"

    def __init__(self, format_name: str) -> None:
        self.format_name = format_name

    def can_handle(self, file_path: str, detected_type: str) -> bool:
        return True

    def parse(self, file_path: Path, detected_type: str) -> ParserResult:
        return ParserResult(
            status="not_implemented",
            message=f"{self.format_name.upper()} parser not implemented yet.",
            implemented=False,
            parser_used=f"{self.format_name}_parser_placeholder",
            detected_type=detected_type,
            mode_used="not_implemented",
            metadata=default_metadata(file_path.name),
            notes=[f"{self.format_name.upper()} routing is ready, but parsing is not implemented yet."],
            issues=[f"{self.format_name.upper()} parser not implemented yet."],
            confidence=0.0,
        )
