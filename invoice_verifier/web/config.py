import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent

# Konfigurasi dari .env di root repository (lihat .env.example). Environment
# variable proses tetap diutamakan karena python-dotenv tidak menimpanya.
load_dotenv(REPOSITORY_ROOT / ".env")


def _path_from_env(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    path = Path(raw_value) if raw_value else default
    return path if path.is_absolute() else PROJECT_ROOT / path


APP_ENV: Literal["development", "production"] = os.getenv(  # type: ignore[assignment]
    "APP_ENV",
    "development",
)
IS_PRODUCTION: bool = APP_ENV == "production"

ENABLE_DOCS: bool = os.getenv("ENABLE_DOCS", "1").lower() not in ("0", "false", "no")

UPLOAD_DIR: Path = _path_from_env("UPLOAD_DIR", PROJECT_ROOT / "tmp_uploads")
APP_NAME: str = "invoice_verifier"
MAX_UPLOAD_MB: int = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "10"))
# Batas panggilan LLM yang berjalan bersamaan untuk SEMUA job (PDF multi-dokumen
# diekstrak per dokumen secara paralel). Sesuaikan dengan rate limit endpoint.
LLM_MAX_PARALLEL_CALLS: int = max(int(os.getenv("LLM_MAX_PARALLEL_CALLS", "4")), 1)
# Kirim gambar halaman hasil scan/foto ke LLM (model harus mendukung vision).
LLM_SEND_PAGE_IMAGES: bool = os.getenv("LLM_SEND_PAGE_IMAGES", "true").lower() in (
    "1", "true", "yes",
)
JOB_TTL_SECONDS: int = int(os.getenv("JOB_TTL_SECONDS", "3600"))

SQLITE_DB_PATH: str = str(
    _path_from_env("SQLITE_DB_PATH", PROJECT_ROOT / "data" / "invoice_verifier.db")
)

PINTER_API_KEY: str | None = os.getenv("PINTER_API_KEY") or None
PINTER_TRX_TTL_DAYS: int = int(os.getenv("PINTER_TRX_TTL_DAYS", "7"))


def _normalize_base_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value.strip())
    normalized_path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, normalized_path, parsed.query, parsed.fragment)
    )


# LLM provider settings. Gemini remains the default; setting OPENAI_BASE_URL
# switches the agents to an OpenAI-compatible endpoint through LiteLLM.
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_BASE_URL: str | None = _normalize_base_url(os.getenv("OPENAI_BASE_URL"))
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY") or None
OPENAI_MODEL: str | None = os.getenv("OPENAI_MODEL") or None
# Tingkat reasoning model (low/medium/high), hanya untuk endpoint
# OpenAI-compatible. Model reasoning seperti GLM menghabiskan sebagian besar
# waktu untuk "berpikir"; "low" ~2-4x lebih cepat dengan akurasi yang sama pada
# dokumen uji. Set kosong ("") bila model tidak mendukung parameter ini.
LLM_REASONING_EFFORT: str | None = os.getenv("LLM_REASONING_EFFORT", "low") or None
