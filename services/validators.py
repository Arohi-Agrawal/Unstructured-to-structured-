from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class Validators:
    ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".docx", ".doc", ".pdf", ".txt", ".940", ".mt940"}
    MAX_FILE_SIZE_MB = 100

    @classmethod
    def validate_upload(cls, file_path: Path) -> tuple[bool, str | None]:
        if not file_path.exists() or not file_path.is_file():
            return False, "Uploaded file could not be saved."
        if file_path.suffix.lower() not in cls.ALLOWED_EXTENSIONS:
            return False, f"Unsupported file type: {file_path.suffix}"
        size_mb = file_path.stat().st_size / (1024 * 1024)
        if size_mb > cls.MAX_FILE_SIZE_MB:
            return False, f"File is too large ({size_mb:.2f} MB). Limit is {cls.MAX_FILE_SIZE_MB} MB."
        if not os.access(file_path, os.R_OK):
            return False, "Uploaded file is not readable."
        return True, None

    @staticmethod
    def sanitize_filename(name: str) -> str:
        cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name).strip(" .")
        return cleaned[:120] or "file"

    @staticmethod
    def check_java_version() -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["java", "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, "Java 11+ is required for PDF parsing, but Java was not found on PATH."

        output = (result.stderr or "") + (result.stdout or "")
        match = re.search(r'version "(\d+)', output)
        if not match:
            return False, "Java was found, but the version could not be determined."
        major = int(match.group(1))
        if major < 11:
            return False, f"Java {major} detected. Java 11+ is required for OpenDataLoader PDF."
        return True, f"Java {major}"

    @staticmethod
    def check_python_module(module_name: str, install_hint: str) -> tuple[bool, str | None]:
        try:
            __import__(module_name)
        except ImportError:
            return False, install_hint
        return True, None
