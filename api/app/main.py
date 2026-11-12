import logging

from fastapi import FastAPI, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.reader import ReaderError, ReadResult, read_document

# Logging policy: only check IDs, timings and error types. Never document
# contents, transaction lines, names or amounts.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("tallied")

settings = get_settings()

app = FastAPI(title="Tallied API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(ReaderError)
async def reader_error_handler(_: Request, exc: ReaderError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": {"code": exc.code, "message": exc.message}})


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # No stack trace or document details in the response or the log line.
    log.error("unhandled_error type=%s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": {
        "code": "internal_error", "message": "Something went wrong on our side. Please try again."}})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/documents/read", response_model=ReadResult)
async def read_uploaded_document(file: UploadFile) -> ReadResult:
    """Read one document. Nothing is stored; the upload is discarded when the request ends."""
    try:
        limit = settings.max_upload_mb * 1024 * 1024
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise ReaderError("file_too_large", limit_mb=settings.max_upload_mb)
        return await run_in_threadpool(read_document, data)
    finally:
        await file.close()
