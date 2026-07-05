"""
Extraction agent: turns raw document text into a validated DocumentExtraction.

Written for: groq==1.1.1

Same research findings as the Week 2 Smart Form Filler project apply here:
- llama-3.3-70b-versatile / llama-3.1-8b-instant are deprecated on Groq;
  this uses openai/gpt-oss-120b instead.
- Groq's strict structured-outputs mode is only honored on the gpt-oss
  models, and requires every property in "required" (nullability via type
  union) plus "additionalProperties": false at *every* object level --
  including nested models. DocumentExtraction has a nested list[ActionItem],
  which the Smart Form Filler's flat-schema builder didn't need to handle,
  so build_strict_schema() here is written to walk $defs recursively.
"""
from __future__ import annotations

import json
import logging
import os
import random
import time

from groq import APIConnectionError, APIStatusError, Groq, RateLimitError
from pydantic import BaseModel, ValidationError

from schemas import DocumentExtraction

logger = logging.getLogger("doc_pipeline")

DEFAULT_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = (
    "You are a document analysis assistant. Given the text of a document, "
    "extract: a concise summary, named entities (people/organizations/places), "
    "key dates, key terms/topics, and concrete action items. Only extract "
    "what is actually present in the text -- do not invent entities, dates, "
    "or action items that aren't there. If a category has nothing relevant, "
    "return an empty list for it."
)

# Groq has a context window; very long documents are truncated to keep the
# extraction call reliable and fast. Long-document chunking is a natural
# extension point (see README) but out of scope for this pass.
MAX_INPUT_CHARS = 24_000


def _patch_object_schema(obj_schema: dict) -> None:
    """Make one JSON-Schema object node strict-mode compliant, in place."""
    if "properties" not in obj_schema:
        return
    props = obj_schema["properties"]
    for prop in props.values():
        prop.pop("default", None)
        prop.pop("title", None)
    obj_schema["required"] = list(props.keys())
    obj_schema["additionalProperties"] = False


def build_strict_schema(model: type[BaseModel]) -> dict:
    """
    Convert a Pydantic v2 model's JSON Schema (including nested models
    surfaced under $defs) into Groq's strict structured-outputs shape.
    """
    schema = model.model_json_schema()
    for def_schema in schema.get("$defs", {}).values():
        _patch_object_schema(def_schema)
    _patch_object_schema(schema)
    schema.pop("title", None)
    schema.pop("description", None)
    return schema


class ExtractionAgent:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        self.client = Groq(api_key=api_key or os.environ.get("GROQ_API_KEY"))
        self.model = model
        self.max_retries = max_retries
        self.base_delay = base_delay

    def _call_with_backoff(self, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("groq_call attempt=%d model=%s", attempt, self.model)
                response = self.client.chat.completions.create(**kwargs)
                logger.info("groq_call attempt=%d outcome=success", attempt)
                return response
            except RateLimitError as exc:
                last_exc = exc
                delay = self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning("groq_call attempt=%d outcome=rate_limited retry_in=%.2fs", attempt, delay)
                time.sleep(delay)
            except (APIConnectionError, APIStatusError) as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    logger.error("groq_call attempt=%d outcome=client_error status=%s", attempt, status)
                    raise
                delay = self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                logger.warning(
                    "groq_call attempt=%d outcome=transient_error retry_in=%.2fs err=%s", attempt, delay, exc
                )
                time.sleep(delay)
        logger.error("groq_call outcome=exhausted_retries")
        raise last_exc  # type: ignore[misc]

    def extract(self, document_text: str) -> DocumentExtraction:
        truncated = document_text[:MAX_INPUT_CHARS]
        schema = build_strict_schema(DocumentExtraction)
        response = self._call_with_backoff(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": truncated},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "DocumentExtraction",
                    "strict": True,
                    "schema": schema,
                },
            },
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        try:
            return DocumentExtraction.model_validate(data)
        except ValidationError:
            logger.error("extract outcome=validation_failed raw=%s", raw)
            raise
