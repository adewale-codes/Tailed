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
from app.api.uploads import router as uploads_router
from app.config import get_settings
from app.services.cleanup import fail_interrupted_checks, run_cleanup
from app.services.errors import MESSAGES, ApiError
from app.services.limits import has_proxy_secret, reserve_ai_call
from app.services.uploads import MAX_CHUNK_BYTES

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


MIN_PROXY_SECRET_LENGTH = 32


def check_proxy_secret_configured() -> None:
    """Production refuses to start without a strong WEB_PROXY_SECRET: every request except
    GET /health needs it, so without one the API would either refuse everything or, worse, be
    open to direct calls that dodge the daily limit."""
    s = get_settings()
    if s.is_production and len(s.web_proxy_secret.strip()) < MIN_PROXY_SECRET_LENGTH:
        log.error("startup.refused reason=web_proxy_secret_missing_or_short")
        raise RuntimeError(f"WEB_PROXY_SECRET must be set to at least {MIN_PROXY_SECRET_LENGTH} "
                           "random characters in production (see DEPLOY.md).")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    check_proxy_secret_configured()  # uvicorn exits before binding $PORT if this raises
    set_guard(reserve_ai_call)
    # Checks the previous process was running can't finish: fail them now and refund the visitor.
    try:
        await run_in_threadpool(fail_interrupted_checks)
    except Exception as exc:  # never stop the API from starting
        log.error("startup.interrupted_checks_error type=%s", type(exc).__name__)
    task = None
    if get_settings().cleanup_interval_minutes > 0:
        task = asyncio.create_task(_cleanup_loop(get_settings().cleanup_interval_minutes))
    yield
    if task:
        task.cancel()


# The interactive docs list every endpoint; useful locally, not needed in production.
_docs = {} if not settings.is_production else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Tallied API", version="0.3.0", lifespan=lifespan, **_docs)

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
    if request.method == "POST" and request.url.path.startswith("/uploads/"):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_CHUNK_BYTES + 64 * 1024:
            return _error("chunk_too_large")
    return await call_next(request)


# Declared last, so it runs first (outermost), before any other middleware or route.
@app.middleware("http")
async def require_web_app(request: Request, call_next):
    """In production, only the web app may call the API: every request except GET /health must
    carry X-Tallied-Proxy-Secret. Heroku's router reaches the dyno from changing internal addresses,
    so without this, direct callers would get a fresh daily limit on almost every request.
    Optional in development, so tests and local runs work without a secret."""
    if (get_settings().is_production
            and not (request.method == "GET" and request.url.path == "/health")
            and not has_proxy_secret(request)):
        return _error("forbidden")
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
app.include_router(uploads_router)
