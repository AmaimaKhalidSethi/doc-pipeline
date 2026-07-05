"""
Processes the 5 files in sample_documents/ through the full pipeline
(parse -> extract -> validate) and prints a summary table.

By default runs in --mock mode (no network/API key needed) using a
rule-free stand-in extractor that just confirms the parse+validate steps
work end to end. Pass --live (with GROQ_API_KEY set) to run the real
extraction agent and see actual model output.

Usage:
    python tests/run_demo.py            # mock mode (default)
    python tests/run_demo.py --live      # real Groq API, needs GROQ_API_KEY
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = str(Path(__file__).parent.parent / "backend")
sys.path.insert(0, BACKEND_DIR)

SAMPLE_DIR = Path(__file__).parent.parent / "sample_documents"


class MockAgent:
    """Confirms the parse -> extract -> validate plumbing without a live LLM call."""

    def extract(self, text: str):
        from schemas import DocumentExtraction

        first_line = text.strip().splitlines()[0] if text.strip() else "(empty document)"
        return DocumentExtraction(
            summary=f"[mock] Document begins: {first_line[:80]}",
            entities=[],
            key_dates=[],
            key_terms=[],
            action_items=[],
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use the real Groq extraction agent")
    args = parser.parse_args()

    from parsers import extract_text

    if args.live:
        from extraction import ExtractionAgent
        agent = ExtractionAgent()
    else:
        agent = MockAgent()

    files = sorted(SAMPLE_DIR.iterdir())
    if not files:
        print(f"No files found in {SAMPLE_DIR}")
        return 1

    print(f"Mode: {'LIVE (real Groq API)' if args.live else 'MOCK (offline plumbing check)'}")
    print(f"Processing {len(files)} documents from {SAMPLE_DIR}\n")

    for f in files:
        text = extract_text(f.name, f.read_bytes())
        try:
            extraction = agent.extract(text)
        except Exception as exc:  # noqa: BLE001
            print(f"=== {f.name} ===\nERROR: {exc}\n")
            continue
        print(f"=== {f.name} ({len(text)} chars extracted) ===")
        print(f"Summary: {extraction.summary}")
        print(f"Entities: {extraction.entities}")
        print(f"Key dates: {extraction.key_dates}")
        print(f"Key terms: {extraction.key_terms}")
        print(f"Action items: {len(extraction.action_items)}")
        for item in extraction.action_items:
            print(f"  - {item.description} (owner={item.owner}, due={item.due_date})")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
