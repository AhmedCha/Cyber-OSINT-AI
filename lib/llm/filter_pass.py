"""
The generic "filter pass" engine every list-based category (domains, emails,
employees, breaches, darkweb, infrastructure) runs through. This is the one
place that owns the batching / grounding / fail-open contract described in
llm_filter.py's module docstring - category modules only ever supply a
compact_fn, a verdict schema, and an instructions string; they never touch
batching or hallucination-grounding themselves.
"""

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from lib.llm.client import ollama_chat_json
from lib.llm.config import DEFAULT_BATCH_SIZE

logger = logging.getLogger(__name__)


def _chunked(items: List[Any], size: int) -> List[List[Any]]:
    if size <= 0:
        return [items]
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_filter_pass(
    category: str,
    records: List[Dict[str, Any]],
    identifier_field: str,
    compact_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    verdict_item_schema: Dict[str, Any],
    system_prompt: str,
    instructions: str,
    host: str,
    model: str,
    temperature: float,
    timeout: int,
    warnings: List[str],
    batch_size: int = DEFAULT_BATCH_SIZE,
    debug: bool = False,
    num_gpu: Optional[int] = None,
    num_ctx: Optional[int] = None,
    num_predict: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Sends compact versions of `records` to the LLM in batches of
    `batch_size`, gets back a keep/exclude verdict per identifier, grounds
    every verdict against the real input set, and merges the verdict onto
    the ORIGINAL (untouched) record. Fails open: any record the model
    doesn't mention is kept with a note.

    Returns (kept, excluded) - both lists contain full original records plus
    an added `_llm_verdict` block.
    """
    if not records:
        return [], []

    valid_ids = {str(r.get(identifier_field, "")).strip().lower() for r in records}

    schema = {
        "type": "object",
        "properties": {"verdicts": {"type": "array", "items": verdict_item_schema}},
        "required": ["verdicts"],
    }

    verdict_map: Dict[str, Dict[str, Any]] = {}
    batches = _chunked(records, batch_size)

    for batch_num, batch in enumerate(batches, 1):
        compact_records = [compact_fn(r) for r in batch]
        label = f"{category} batch {batch_num}/{len(batches)}"

        user_prompt = f"""{instructions}

Input records (JSON array, {len(compact_records)} items):
{json.dumps(compact_records, ensure_ascii=False, indent=2)}

Respond with JSON: {{"verdicts": [ ... one object per input record, using the exact identifier field value from the input above ... ]}}
You must return exactly one verdict object per input record. Do not add records that are not in the input above."""

        result, error = ollama_chat_json(
            host,
            model,
            system_prompt,
            user_prompt,
            schema,
            temperature,
            timeout=timeout,
            debug=debug,
            debug_label=label,
            num_gpu=num_gpu,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )

        if result is None:
            warnings.append(
                f"[{category}] Batch {batch_num}/{len(batches)} LLM filter pass failed ({error}). "
                f"Keeping those {len(batch)} record(s) unfiltered."
            )
            continue

        raw_verdicts = result.get("verdicts", [])
        if not isinstance(raw_verdicts, list):
            warnings.append(
                f"[{category}] Batch {batch_num}/{len(batches)} response had no valid 'verdicts' array. "
                f"Keeping those {len(batch)} record(s) unfiltered."
            )
            continue

        for v in raw_verdicts:
            if not isinstance(v, dict):
                continue
            vid = str(v.get(identifier_field, "")).strip().lower()
            if not vid:
                # Either the model omitted the identifier field, or
                # identifier_field doesn't match the key name actually used
                # in the schema/compact_fn for this category - both are bugs
                # worth surfacing rather than silently dropping the verdict.
                warnings.append(
                    f"[{category}] Batch {batch_num}/{len(batches)}: verdict item missing "
                    f"'{identifier_field}' field (got keys: {sorted(v.keys())}). Dropped."
                )
                continue
            if vid not in valid_ids:
                warnings.append(
                    f"[{category}] Dropped hallucinated identifier not present in input: {v.get(identifier_field)!r}"
                )
                continue
            verdict_map[vid] = v

    kept: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for record in records:
        rid = str(record.get(identifier_field, "")).strip().lower()
        verdict = verdict_map.get(rid)

        if verdict is None:
            annotated = dict(record)
            annotated["_llm_verdict"] = {
                "keep": True,
                "note": "not_evaluated_by_model_fail_open",
            }
            kept.append(annotated)
            continue

        keep = verdict.get("keep", True)
        if not isinstance(keep, bool):
            keep = True

        annotated = dict(record)
        annotated["_llm_verdict"] = {
            k: v for k, v in verdict.items() if k != identifier_field
        }
        (kept if keep else excluded).append(annotated)

    return kept, excluded
