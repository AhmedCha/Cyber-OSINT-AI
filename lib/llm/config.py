"""
Shared configuration for the LLM filter stage (lib/llm/*).

Anything genuinely cross-category lives here: Ollama connection defaults,
retry/timeout/batch-size defaults, and the anti-hallucination system prompt
every category-specific instruction block gets appended to. Category-only
constants (e.g. EMAIL_TIERS, USABILITY_LEVELS) live in their own category
module instead - see lib/llm/emails.py, lib/llm/documents.py.
"""

import os

DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1:8b")
DEFAULT_TIMEOUT = 600  # generous default for CPU/iGPU-only hardware
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_DOC_CHARS = (
    4000  # kept conservative relative to a likely 4096-token context window
)
DEFAULT_BATCH_SIZE = 8  # records per LLM call for list-based filter passes

SUPPORTED_DOC_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


# =====================================================================
# SYSTEM PROMPT (shared anti-hallucination contract)
# =====================================================================

BASE_SYSTEM_PROMPT = """You are a precise OSINT data-triage assistant. You are given data that has \
ALREADY been collected by automated reconnaissance tools (domain enumeration, \
email pattern generation, LinkedIn scraping, breach lookups, document \
discovery). Your only job is to judge and summarize what you are given.

STRICT RULES - violating any of these makes your output useless and unsafe:
1. NEVER invent, guess, assume, or infer any fact that is not explicitly \
present in the data given to you in this message. If something is unknown, \
say so or leave it out - do not fill gaps with plausible-sounding guesses.
2. NEVER fabricate new identifiers (emails, domains, names, filenames, URLs). \
You may only refer to identifiers that appear verbatim in the input below.
3. NEVER change, "correct", normalize, or reformat identifiers (emails, \
domains, filenames) - copy them exactly as given.
4. Output ONLY valid JSON matching the requested schema. No markdown code \
fences, no prose before or after the JSON, no explanations outside the JSON \
fields themselves.
5. If you are not confident about a judgment, prefer the more conservative \
label (e.g. "low" usability, "speculative" tier, keep=true) rather than \
discarding data - a human will review your output before anything is \
published in a report.
6. You have no internet access and cannot verify anything beyond the text \
given to you in this message.
"""
