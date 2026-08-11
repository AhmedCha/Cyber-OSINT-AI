"""
Low-level Ollama client: connectivity check, the raw /api/chat call (with
optional live-streaming debug output), and a retrying JSON-mode wrapper
around it. Every other lib/llm/* module and llm_filter.py talk to Ollama
only through ollama_chat_json() - nothing else in this package should be
touching urllib directly.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from lib.llm.config import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)


def check_ollama_available(host: str, timeout: int = 10) -> bool:
    try:
        req = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Ollama is not reachable at {host}: {e}")
        return False


def _build_options(
    temperature: float,
    num_gpu: Optional[int],
    num_ctx: Optional[int],
    num_predict: Optional[int],
) -> Dict[str, Any]:
    options: Dict[str, Any] = {"temperature": temperature, "seed": 42}
    if num_gpu is not None:
        options["num_gpu"] = num_gpu
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    if num_predict is not None:
        options["num_predict"] = num_predict
    return options


def _post_ollama_chat(
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: Optional[Dict[str, Any]],
    options: Dict[str, Any],
    timeout: int,
    use_schema_format: bool,
    debug: bool = False,
    debug_label: str = "",
) -> str:
    """Low-level call to Ollama's /api/chat. Returns the full message content
    string. When debug=True, streams the response and prints tokens live to
    stdout as they arrive (Ollama's stream=true NDJSON API)."""
    body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": debug,
        "options": options,
    }

    if schema is not None and use_schema_format:
        body["format"] = schema
    else:
        body["format"] = "json"

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    if not debug:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        return raw.get("message", {}).get("content", "")

    # --- streaming / debug path -----------------------------------
    print(
        f"\n--- [{debug_label}] live model output "
        + "-" * max(0, 40 - len(debug_label))
    )
    full_content = []
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                print(piece, end="", flush=True)
                full_content.append(piece)
            if chunk.get("done"):
                elapsed = time.time() - start
                eval_count = chunk.get("eval_count")
                eval_duration_ns = chunk.get("eval_duration")
                rate = ""
                if eval_count and eval_duration_ns:
                    tok_per_sec = eval_count / (eval_duration_ns / 1e9)
                    rate = f" | {eval_count} tokens @ {tok_per_sec:.1f} tok/s"
                print(f"\n--- [{debug_label}] done in {elapsed:.1f}s{rate} ---\n")
    return "".join(full_content)


def ollama_chat_json(
    host: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: Optional[Dict[str, Any]] = None,
    temperature: float = 0.0,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    debug: bool = False,
    debug_label: str = "",
    num_gpu: Optional[int] = None,
    num_ctx: Optional[int] = None,
    num_predict: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Calls the local LLM and returns (parsed_json, error). Retries on
    malformed JSON by re-prompting; falls back to generic json mode if the
    schema-constrained call itself fails (older Ollama versions)."""
    use_schema_format = schema is not None
    last_error = None
    current_user_prompt = user_prompt
    options = _build_options(temperature, num_gpu, num_ctx, num_predict)

    for attempt in range(1, max_retries + 1):
        attempt_start = time.time()
        try:
            content = _post_ollama_chat(
                host,
                model,
                system_prompt,
                current_user_prompt,
                schema,
                options,
                timeout,
                use_schema_format,
                debug=debug,
                debug_label=f"{debug_label} attempt {attempt}",
            )
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            if use_schema_format and e.code == 400:
                logger.warning(
                    "Ollama rejected structured `format` schema (HTTP 400). "
                    "Falling back to generic JSON mode."
                )
                use_schema_format = False
                continue
            last_error = f"HTTP {e.code}: {e.reason} | body: {body}"
            logger.warning(
                f"[{debug_label}] Ollama call failed (attempt {attempt}/{max_retries}, "
                f"{time.time() - attempt_start:.1f}s): {last_error}"
            )
        except urllib.error.URLError as e:
            last_error = f"Connection error: {e.reason}"
            logger.warning(
                f"[{debug_label}] Ollama call failed (attempt {attempt}/{max_retries}, "
                f"{time.time() - attempt_start:.1f}s): {last_error}"
            )
        except TimeoutError as e:
            last_error = f"Timed out after {timeout}s"
            logger.warning(
                f"[{debug_label}] Ollama call timed out (attempt {attempt}/{max_retries}). "
                f"Consider raising --timeout, lowering --batch-size, or trying --num-gpu 0 "
                f"if GPU offload seems unstable on this hardware."
            )
        except Exception as e:
            last_error = str(e)
            logger.warning(
                f"[{debug_label}] Ollama call failed (attempt {attempt}/{max_retries}, "
                f"{time.time() - attempt_start:.1f}s): {last_error}"
            )
        else:
            elapsed = time.time() - attempt_start
            try:
                parsed = json.loads(content)
                logger.info(f"[{debug_label}] completed in {elapsed:.1f}s")
                return parsed, None
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON from model: {e}"
                logger.warning(
                    f"[{debug_label}] Model returned invalid JSON after {elapsed:.1f}s "
                    f"(attempt {attempt}/{max_retries}). Retrying with correction."
                )
                current_user_prompt = (
                    user_prompt
                    + "\n\nYour previous response was not valid JSON. "
                    + "Respond with ONLY valid JSON matching the schema - no other text."
                )

        time.sleep(min(2 * attempt, 6))

    return None, last_error or "Unknown error"
