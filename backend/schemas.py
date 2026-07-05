"""
Extraction schemas for the Intelligent Document Processing Pipeline.

Written for: pydantic 2.13.4
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ActionItem(BaseModel):
    """A single actionable task found in a document."""

    description: str = Field(..., description="What needs to be done")
    owner: Optional[str] = Field(None, description="Person or team responsible, if stated")
    due_date: Optional[str] = Field(
        None, description="Due date in ISO 8601 (YYYY-MM-DD) if stated or clearly inferable"
    )


class DocumentExtraction(BaseModel):
    """Structured information extracted from one document."""

    summary: str = Field(..., description="A concise 2-4 sentence summary of the document")
    entities: list[str] = Field(
        default_factory=list,
        description="Named people, organizations, and places mentioned in the document",
    )
    key_dates: list[str] = Field(
        default_factory=list,
        description="Significant dates mentioned in the document, as written or normalized to ISO 8601",
    )
    key_terms: list[str] = Field(
        default_factory=list,
        description="Important domain-specific terms, topics, or keywords from the document",
    )
    action_items: list[ActionItem] = Field(
        default_factory=list,
        description="Concrete action items, tasks, or follow-ups mentioned in the document",
    )


class DocumentRecord(BaseModel):
    """A row from the documents table, returned by the API."""

    id: int
    filename: str
    file_type: str
    uploaded_at: str
    char_count: int


class DocumentDetail(DocumentRecord):
    """Full detail view: document metadata + its extraction."""

    text_preview: str
    extraction: DocumentExtraction
