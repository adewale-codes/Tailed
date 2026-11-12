import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartParser

from app.ai_guard import set_guard
from app.api.checks import router as checks_router
from app.config import get_settings
from app.services.cleanup import run_cleanup
from app.services.errors import MESSAGES, ApiError
from app.services.limits import reserve_ai_call

# Logging policy: only check IDs, timings and error types. Never document
# contents, transaction lines, names or amounts.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("tallied")

settings = get_settings()

# Keep accepted uploads in memory until we write them into our own temp folder, instead of
# Starlette spooling anything over 1 MB to the system temp directory.
MultiPartParser.spool_max_size = settings.max_file_mb * 1024 * 1024 + 1


def max_request_bytes() -> int:
    s = get_settings()
    return s.max_files * s.max_file_mb * 1024 * 1024 + 1024 * 1024  # plus form overhead


async def _cleanup_loop(minutes: int) -> None:
    while True:
        try:
            await run_in_threadpool(run_cleanup)
        except Exception as exc:
            log.error("cleanup.error type=%s", type(exc).__name__)
        await asyncio.sleep(minutes * 60)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    set_guard(reserve_ai_call)
    task = None
    if get_settings().cleanup_interval_minutes > 0:
        task = asyncio.create_task(_cleanup_loop(get_settings().cleanup_interval_minutes))
    yield
    if task:
        task.cancel()


app = FastAPI(title="Tallied API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _error(code: str, status: int | None = None, message: str | None = None) -> JSONResponse:
    default_status, default_message = MESSAGES.get(code, MESSAGES["internal_error"])
    return JSONResponse(status_code=status or default_status,
                        content={"error": {"code": code, "message": message or default_message}})


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/checks":
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_request_bytes():
            s = get_settings()
            return _error("upload_too_large", message=MESSAGES["upload_too_large"][1].format(
                max_files=s.max_files, max_file_mb=s.max_file_mb))
    return await call_next(request)


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return _error(exc.code, exc.status, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.endswith("/feedback"):
        return _error("invalid_feedback")
    return _error("invalid_request")


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "invalid_request")
    return _error(code, exc.status_code)


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    # No stack trace or document details in the response or the log line.
    log.error("unhandled_error type=%s", type(exc).__name__)
    return _error("internal_error")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(checks_router)
