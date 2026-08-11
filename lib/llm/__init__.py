"""
lib.llm - one module per OSINT category for the LLM filter stage
(llm_filter.py). Mirrors the top-level lib/ package's role for the other
stage scripts: shared, importable pieces rather than one large file.

  config.py         - shared constants + the anti-hallucination system prompt
  client.py         - low-level Ollama HTTP client (chat/retry/streaming)
  filter_pass.py    - the generic batched keep/exclude engine every list
                       category runs through (run_filter_pass)
  utils.py          - small helpers shared across category modules
  documents.py       ─┐
  domains.py          │
  emails.py           │ one module per OSINT category: compact_fn,
  employees.py        │ verdict schema, instructions, and any
  breaches.py         │ category-specific deterministic backstop
  darkweb.py          │ (e.g. emails.py's catch-all/tier enforcement)
  infrastructure.py  ─┘
  output.py         - build_output_template(), the fixed llm_filtered.json shape

llm_filter.py (top-level) is the thin CLI entry point: argument parsing,
main() orchestration (calling each category's compact/schema/instructions
through run_filter_pass), and the final summary print.
"""
