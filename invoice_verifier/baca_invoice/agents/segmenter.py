"""Agent pemetaan halaman → dokumen untuk PDF multi-halaman.

Dipakai `web.services.agent_runner` sebelum ekstraksi: tiap dokumen lalu
diekstrak terpisah supaya transaksi tidak terlewat ketika satu file berisi
banyak dokumen.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field
from web.config import OPENAI_BASE_URL

from ..utils.schema_inline import inline_json_schema_refs
from .document import _agent_model
from .prompts import SEGMENTER_PROMPT

DOCUMENT_PAGES_STATE_KEY = "document_pages"


class DocumentPages(BaseModel):
    pages: list[int] = Field(description="Nomor halaman (mulai 1) milik dokumen ini.")


class SegmentPlan(BaseModel):
    documents: list[DocumentPages] = Field(description="Dokumen terpisah, urut halaman.")


segmenter_agent = LlmAgent(
    model=_agent_model(),
    name="segmenter_agent",
    description="Memetakan halaman PDF ke dokumen/transaksi terpisah.",
    instruction=SEGMENTER_PROMPT,
    # Structured output: provider dipaksa mengembalikan JSON sesuai skema.
    output_schema=inline_json_schema_refs(SegmentPlan) if OPENAI_BASE_URL else SegmentPlan,
)
