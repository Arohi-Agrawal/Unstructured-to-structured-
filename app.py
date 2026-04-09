from __future__ import annotations

import json
import logging
import shutil
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.format_router import FormatRouter
from services.output_writer import OutputWriter
from services.validators import Validators
from services.pdf.pdf_parser import PDFParser


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

for directory in (UPLOAD_DIR, OUTPUT_DIR, TEMPLATE_DIR, STATIC_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Document Processing App", version="3.0.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
logger = logging.getLogger("document_app")


def _json_preview(payload: dict) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _job_paths(job_id: str) -> tuple[Path, Path]:
    upload_dir = UPLOAD_DIR / job_id
    output_dir = OUTPUT_DIR / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir, output_dir


def _looks_like_pdf(file_path: Path) -> bool:
    try:
        with file_path.open("rb") as handle:
            return handle.read(8).startswith(b"%PDF")
    except Exception:
        return False


def _resolve_safe_output_path(job_id: str, file_name: str) -> Path:
    job_dir = (OUTPUT_DIR / job_id).resolve()
    candidate = (job_dir / Path(file_name).name).resolve()
    if not str(candidate).startswith(str(job_dir)):
        raise HTTPException(status_code=400, detail="Invalid file path")
    return candidate


def _load_job_payload(job_id: str) -> dict:
    payload_path = OUTPUT_DIR / job_id / "result.json"
    if not payload_path.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return json.loads(payload_path.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "supported_formats": FormatRouter.supported_format_labels(),
        },
    )


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)) -> RedirectResponse:
    job_id = uuid.uuid4().hex[:8]
    upload_dir, output_dir = _job_paths(job_id)
    upload_path = upload_dir / Validators.sanitize_filename(file.filename or "uploaded_file")

    with upload_path.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    is_valid, validation_error = Validators.validate_upload(upload_path)
    if not is_valid:
        error_payload = {
            "fileName": upload_path.name,
            "fileType": upload_path.suffix.lower().lstrip("."),
            "detectedType": "unknown",
            "parserImplemented": False,
            "parserUsed": "validation",
            "modeUsed": "validation_failed",
            "metadata": {},
            "notes": [],
            "tables": [],
            "issues": [validation_error],
            "confidence": 0.0,
            "exports": {"jsonFile": "", "csvFiles": []},
            "jobId": job_id,
        }
        with (output_dir / "result.json").open("w", encoding="utf-8") as handle:
            json.dump(error_payload, handle, indent=2, ensure_ascii=False)
        return RedirectResponse(url=f"/result/{job_id}", status_code=303)

    try:
        route = FormatRouter.resolve(upload_path)
        parser = route.parser_factory()
        parse_result = parser.parse(upload_path, route.detected_type)
        payload = OutputWriter.persist_result(output_dir, upload_path, parse_result, route)
    except ValueError as exc:
        if _looks_like_pdf(upload_path):
            try:
                parser = PDFParser()
                parse_result = parser.parse(upload_path, "pdf")
                fallback_route = FormatRouter.resolve(upload_path.with_suffix(".pdf"))
                payload = OutputWriter.persist_result(output_dir, upload_path, parse_result, fallback_route)
                payload.setdefault("notes", []).append("Format fallback: routed unknown extension by PDF signature.")
            except Exception as fallback_exc:
                logger.exception("PDF fallback parse failed for job %s", job_id)
                payload = {
                    "fileName": upload_path.name,
                    "fileType": upload_path.suffix.lower().lstrip("."),
                    "detectedType": "pdf",
                    "parserImplemented": True,
                    "parserUsed": "opendataloader_pdf",
                    "modeUsed": "processing_failed",
                    "metadata": {},
                    "notes": [],
                    "tables": [],
                    "issues": [f"Processing failed unexpectedly: {fallback_exc}"],
                    "confidence": 0.0,
                    "exports": {"jsonFile": "", "csvFiles": []},
                    "jobId": job_id,
                }
        else:
            payload = {
                "fileName": upload_path.name,
                "fileType": upload_path.suffix.lower().lstrip("."),
                "detectedType": "unknown",
                "parserImplemented": False,
                "parserUsed": "format_router",
                "modeUsed": "routing_failed",
                "metadata": {},
                "notes": [],
                "tables": [],
                "issues": [str(exc)],
                "confidence": 0.0,
                "exports": {"jsonFile": "", "csvFiles": []},
                "jobId": job_id,
            }
    except Exception as exc:
        logger.error("Unhandled upload processing error for job %s: %s\n%s", job_id, exc, traceback.format_exc())
        payload = {
            "fileName": upload_path.name,
            "fileType": upload_path.suffix.lower().lstrip("."),
            "detectedType": "unknown",
            "parserImplemented": False,
            "parserUsed": "application",
            "modeUsed": "processing_failed",
            "metadata": {},
            "notes": [],
            "tables": [],
            "issues": [f"Processing failed unexpectedly: {exc}"],
            "confidence": 0.0,
            "exports": {"jsonFile": "", "csvFiles": []},
            "jobId": job_id,
        }

    with (output_dir / "result.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    return RedirectResponse(url=f"/result/{job_id}", status_code=303)


@app.get("/result/{job_id}", response_class=HTMLResponse)
async def get_result(request: Request, job_id: str) -> HTMLResponse:
    payload = _load_job_payload(job_id)
    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "result": payload,
            "json_preview": _json_preview(payload),
        },
    )


@app.get("/download/json/{job_id}")
async def download_json(job_id: str) -> FileResponse:
    payload = _load_job_payload(job_id)
    export_name = (payload.get("exports") or {}).get("jsonFile") or ""
    if not export_name:
        raise HTTPException(status_code=404, detail="JSON export not available")
    target = _resolve_safe_output_path(job_id, export_name)
    if not target.exists():
        raise HTTPException(status_code=404, detail="JSON export not found")
    return FileResponse(path=target, filename=target.name, media_type="application/json")


@app.get("/download/csv/{job_id}/{table_name}")
async def download_csv(job_id: str, table_name: str) -> FileResponse:
    safe_name = Path(table_name).name
    payload = _load_job_payload(job_id)
    csv_files = set((payload.get("exports") or {}).get("csvFiles") or [])
    if safe_name not in csv_files:
        raise HTTPException(status_code=404, detail="CSV export not available for this job")
    target = _resolve_safe_output_path(job_id, safe_name)
    if not target.exists():
        raise HTTPException(status_code=404, detail="CSV export not found")
    return FileResponse(path=target, filename=target.name, media_type="text/csv")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
