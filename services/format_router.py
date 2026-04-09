from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import zipfile

from services.csv.csv_parser import CSVParser
from services.docx.docx_parser import DOCXParser
from services.mt940.mt940_parser import MT940Parser
from services.pdf.pdf_parser import PDFParser
from services.txt.txt_parser import TXTParser
from services.xlsx.xlsx_parser import XLSXParser


@dataclass(frozen=True)
class RouteResult:
    format_type: str
    detected_type: str
    implemented: bool
    parser_factory: Callable[[], object]


class FormatRouter:
    EXTENSION_MAP = {
        ".csv": ("csv", "csv", True, CSVParser),
        ".xlsx": ("xlsx", "xlsx", True, XLSXParser),
        ".xls": ("xls", "xls", True, XLSXParser),
        ".docx": ("docx", "docx", True, DOCXParser),
        ".doc": ("docx", "docx", True, DOCXParser),
        ".pdf": ("pdf", "pdf", True, PDFParser),
        ".txt": ("txt", "txt", True, TXTParser),
        ".940": ("mt940", "mt940", True, MT940Parser),
        ".mt940": ("mt940", "mt940", True, MT940Parser),
    }

    @classmethod
    def resolve(cls, file_path: Path) -> RouteResult:
        suffix = file_path.suffix.lower()
        if cls._looks_like_pdf(file_path):
            return RouteResult("pdf", "pdf", True, PDFParser)

        if suffix == ".txt" and cls._looks_like_mt940(file_path):
            return RouteResult("mt940", "mt940-as-txt", True, MT940Parser)

        if suffix not in cls.EXTENSION_MAP and cls._looks_like_zip_container(file_path):
            guessed = cls._guess_zip_office_type(file_path)
            if guessed in {"docx", "xlsx"}:
                route = cls.EXTENSION_MAP[f".{guessed}"]
                return RouteResult(*route)

        if suffix not in cls.EXTENSION_MAP and cls._looks_like_ole_compound(file_path):
            return RouteResult("xls", "xls", True, XLSXParser)

        if suffix == ".txt":
            detected_type = cls._detect_txt_variant(file_path)
            return RouteResult("txt", detected_type, True, TXTParser)

        if suffix not in cls.EXTENSION_MAP:
            raise ValueError(f"Unsupported file format: {suffix or 'unknown'}")

        format_type, detected_type, implemented, parser_class = cls.EXTENSION_MAP[suffix]
        return RouteResult(format_type, detected_type, implemented, parser_class)

    @staticmethod
    def _detect_txt_variant(file_path: Path) -> str:
        preview = file_path.read_text(encoding="utf-8", errors="ignore")[:2000]
        if any(tag in preview for tag in (":20:", ":25:", ":61:", ":86:")):
            return "mt940-as-txt"
        if "EP" in preview[:120] and "|" in preview:
            return "ep-txt"
        return "txt"

    @classmethod
    def supported_format_labels(cls) -> list[str]:
        return [
            "pdf",
            "scanned pdf",
            "csv",
            "xlsx / xls",
            "docx",
            "mt940 / 940",
            "txt",
            "ep txt",
        ]

    @staticmethod
    def _looks_like_pdf(file_path: Path) -> bool:
        try:
            with file_path.open("rb") as handle:
                signature = handle.read(8)
            return signature.startswith(b"%PDF")
        except Exception:
            return False

    @staticmethod
    def _looks_like_zip_container(file_path: Path) -> bool:
        try:
            with file_path.open("rb") as handle:
                return handle.read(4) == b"PK\x03\x04"
        except Exception:
            return False

    @staticmethod
    def _looks_like_ole_compound(file_path: Path) -> bool:
        try:
            with file_path.open("rb") as handle:
                return handle.read(8) == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        except Exception:
            return False

    @staticmethod
    def _guess_zip_office_type(file_path: Path) -> str | None:
        try:
            with zipfile.ZipFile(file_path) as archive:
                names = archive.namelist()
            joined = " ".join(names).lower()
            if "word/" in joined:
                return "docx"
            if "xl/" in joined:
                return "xlsx"
        except Exception:
            return None
        return None

    @staticmethod
    def _looks_like_mt940(file_path: Path) -> bool:
        try:
            preview = file_path.read_text(encoding="utf-8", errors="ignore")[:4000]
        except Exception:
            return False
        tags = (":20:", ":25:", ":28C:", ":60F:", ":61:", ":86:")
        score = sum(1 for tag in tags if tag in preview)
        return score >= 2
