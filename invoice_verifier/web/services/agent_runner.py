from __future__ import annotations

import asyncio
import logging
import os
import time

from baca_invoice.agents.document import PAGE_NUMBERS_STATE_KEY, document_agent
from baca_invoice.agents.postprocess import apply_postprocessing
from baca_invoice.agents.segmenter import DOCUMENT_PAGES_STATE_KEY, segmenter_agent
from baca_invoice.models.authenticity import DocumentAuthenticity
from baca_invoice.models.travel_document import TravelDocumentResult
from baca_invoice.tools.authenticity import analyze_authenticity
from baca_invoice.tools.pdf import count_pages, read_pdf, scanned_page_images
from baca_invoice.tools.pdf_forensics import analyze_pdf_structure
from baca_invoice.utils.merge_documents import merge_document_results
from baca_invoice.utils.segments import format_pages_for_segmenter, parse_segments
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from web.config import (
    APP_NAME,
    IS_PRODUCTION,
    JOB_TTL_SECONDS,
    LLM_MAX_PARALLEL_CALLS,
    LLM_SEND_PAGE_IMAGES,
    MAX_CONCURRENT_JOBS,
)
from web.services.agent_io import run_agent_for_json
from web.services.job_errors import LLM_RATE_LIMITED, classify_error
from web.services.jobs import Job, JobStatus, cleanup_job_file
from web.services.result_validator import validate_agent_result

logger = logging.getLogger(__name__)

_EVICTION_INTERVAL_SECONDS = 300
# Jeda retry (detik) bila endpoint LLM membalas rate limit.
_RATE_LIMIT_BACKOFF_SECONDS = (5, 15)


class AgentRunnerService:
    """Mengelola siklus hidup job ekstraksi dokumen via ADK Runner.

    Job dibuat oleh route handler, dijalankan async dengan batas concurrency,
    lalu di-evict dari memori setelah TTL.
    """

    def __init__(self) -> None:
        _clear_broken_local_proxy()
        self._session_service = InMemorySessionService()
        self._runner = Runner(
            app_name=APP_NAME,
            agent=document_agent,
            session_service=self._session_service,
        )
        self._segmenter_runner = Runner(
            app_name=APP_NAME,
            agent=segmenter_agent,
            session_service=self._session_service,
        )
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)
        # Global untuk semua job: total panggilan LLM bersamaan dibatasi.
        self._llm_slots = asyncio.Semaphore(LLM_MAX_PARALLEL_CALLS)

    def create_job(self, job_id: str, file_path: str, filename: str) -> None:
        self._jobs[job_id] = Job(job_id=job_id, filename=filename, file_path=file_path)

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    @property
    def active_job_count(self) -> int:
        return sum(
            1
            for job in self._jobs.values()
            if job.status in (JobStatus.PENDING, JobStatus.RUNNING)
        )

    async def run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return

        async with self._semaphore:
            job.status = JobStatus.RUNNING
            try:
                job.result = await self._extract_document(job)
                job.status = JobStatus.DONE
            except Exception as exc:
                logger.exception("Job %s failed", job_id)
                job.status = JobStatus.ERROR
                job.error = _user_facing_error(exc)
                job.error_code = classify_error(exc)
            finally:
                cleanup_job_file(job)

    async def _extract_document(self, job: Job) -> dict:
        """PDF 1 halaman: satu kali ekstraksi. Multi-halaman: halaman dipetakan
        ke dokumen oleh segmenter, tiap dokumen diekstrak terpisah (paralel),
        lalu digabung & dijumlahkan di kode supaya transaksi tidak terlewat."""
        groups = None
        content: dict = {}
        if await asyncio.to_thread(count_pages, job.file_path) > 1:
            content = await asyncio.to_thread(read_pdf, job.file_path)
            if content.get("success"):
                groups = await self._segment_pages(job, content["pages"])

        if not groups or len(groups) < 2:
            return await self._extract_pages(job, None, "all")
        started = time.monotonic()

        results = await asyncio.gather(
            *(self._extract_pages(job, pages, f"doc{i}") for i, pages in enumerate(groups))
        )
        logger.info(
            "Job %s: %d dokumen dari halaman %s diekstrak dalam %.1f detik",
            job.job_id, len(groups), groups, time.monotonic() - started,
        )

        merged = TravelDocumentResult.model_validate(merge_document_results(list(results)))
        # Keaslian dinilai dari keseluruhan file (metadata + seluruh teks).
        authenticity = DocumentAuthenticity.model_validate(
            analyze_authenticity(
                content.get("metadata") or {},
                content.get("full_text", ""),
                await asyncio.to_thread(analyze_pdf_structure, job.file_path),
            )
        )
        final = apply_postprocessing(merged, authenticity_override=authenticity)
        return validate_agent_result(final.model_dump())

    async def _segment_pages(self, job: Job, pages: list[dict]) -> list[list[int]] | None:
        page_numbers = [page["page"] for page in pages]
        started = time.monotonic()
        try:
            data = await self._run(
                self._segmenter_runner,
                job,
                "segment",
                {DOCUMENT_PAGES_STATE_KEY: format_pages_for_segmenter(pages)},
                "Petakan halaman ke dokumen.",
            )
        except Exception:
            logger.warning("Job %s: segmentasi halaman gagal, ekstraksi sekaligus.", job.job_id)
            return None
        groups = parse_segments(data, page_numbers)
        if groups is None:
            logger.warning("Job %s: hasil segmentasi tidak valid: %s", job.job_id, data)
        logger.info("Job %s: segmentasi %.1f detik", job.job_id, time.monotonic() - started)
        return groups

    async def _extract_pages(self, job: Job, pages: list[int] | None, suffix: str) -> dict:
        state = {"file_path": job.file_path}
        if pages:
            state[PAGE_NUMBERS_STATE_KEY] = pages
        images = []
        if LLM_SEND_PAGE_IMAGES:
            images = await asyncio.to_thread(scanned_page_images, job.file_path, pages)
        raw_result = await self._run(
            self._runner, job, suffix, state, f"file_path: {job.file_path}", images
        )
        return validate_agent_result(raw_result)

    async def _run(
        self,
        runner: Runner,
        job: Job,
        suffix: str,
        state: dict,
        text: str,
        images: list[tuple[str, bytes]] | None = None,
    ) -> dict:
        user_id = f"user_{job.job_id}"
        parts = [genai_types.Part(text=text)]
        for label, jpeg in images or []:
            parts.append(genai_types.Part(text=label))
            parts.append(genai_types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"))
        message = genai_types.Content(role="user", parts=parts)
        for attempt in range(len(_RATE_LIMIT_BACKOFF_SECONDS) + 1):
            session_id = f"session_{job.job_id}_{suffix}_{attempt}"
            await self._session_service.create_session(
                app_name=APP_NAME, user_id=user_id, session_id=session_id, state=dict(state)
            )
            try:
                async with self._llm_slots:
                    return await run_agent_for_json(
                        runner=runner, user_id=user_id, session_id=session_id, message=message
                    )
            except Exception as exc:
                if (
                    classify_error(exc) != LLM_RATE_LIMITED
                    or attempt == len(_RATE_LIMIT_BACKOFF_SECONDS)
                ):
                    raise
                delay = _RATE_LIMIT_BACKOFF_SECONDS[attempt]
                logger.warning("Job %s: rate limit LLM, ulangi dalam %d detik.", job.job_id, delay)
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    async def eviction_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_EVICTION_INTERVAL_SECONDS)
                await self._evict_expired_jobs()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Eviction loop error")

    async def _evict_expired_jobs(self) -> None:
        now = time.monotonic()
        async with self._lock:
            expired = [
                job_id
                for job_id, job in self._jobs.items()
                if job.status in (JobStatus.DONE, JobStatus.ERROR)
                and now - job.created_at > JOB_TTL_SECONDS
            ]
        for job_id in expired:
            job = self._jobs.pop(job_id, None)
            logger.info("Evicted job %s", job_id)
            if job:
                cleanup_job_file(job)


def _clear_broken_local_proxy() -> None:
    """Hapus proxy env yang menunjuk ke localhost:9xxx (sering di-set akibat
    misconfig di Windows) supaya google-genai bisa mencapai endpoint Google."""
    broken_markers = ("127.0.0.1:9", "localhost:9", "[::1]:9")
    proxy_keys = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    )
    for key in proxy_keys:
        value = os.environ.get(key, "")
        if any(marker in value for marker in broken_markers):
            os.environ.pop(key, None)


def _user_facing_error(exc: Exception) -> str:
    message = "Terjadi kesalahan saat memproses dokumen."
    if not IS_PRODUCTION:
        return f"{message} Detail: {exc}"
    return message
