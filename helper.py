#!/usr/bin/env python3
"""
Native macOS helper for reading a selected screen region, asking a generic LLM
endpoint for terse answers, and cycling slash-separated answers through pbcopy.

No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import getpass
import html
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"

BLOCKING_HTTP_CODES = {400, 401, 403, 429}
DEFAULT_SOUND = "/System/Library/Sounds/Tink.aiff"
DEFAULT_MODEL = "gpt-4o-mini"
KEY_CACHE_PATH = os.path.join(tempfile.gettempdir(), f"slimste_llm_api_key_{os.getuid()}")
PUZZLE_HISTORY: list[str] = []
PUZZLE_PENDING: list[str] = []
PROTECTED_SLASH_ANSWERS = [
    "AC/DC",
]
RISKY_TERMS = [
    "aller tijden",
    "beste",
    "eerst",
    "grootste",
    "hoogste",
    "jongste",
    "langste",
    "meeste",
    "minister",
    "oudste",
    "record",
    "schaal",
    "topscorer",
    "uit welk",
    "van wie",
    "wanneer",
    "waar",
    "welk",
    "welke",
    "wie",
]
KNOWN_ANSWERS = [
    (
        ("topscorer", "aller tijden", "nederlandse eredivisie"),
        "Willy van der Kuijlen",
    ),
    (
        ("mevrouw stemband",),
        "De Grote Meneer Kaktus Show",
    ),
    (
        ("antropofaag",),
        "Mensenvlees",
    ),
    (
        ("suède", "nubuck", "nappa"),
        "Leer",
    ),
    (
        ("suede", "nubuck", "nappa"),
        "Leer",
    ),
    (
        ("tropisch bos", "oerwoud", "jungle"),
        "Rimboe",
    ),
    (
        ("looney tunes", "miep"),
        "Roadrunner",
    ),
    (
        ("1 april", "kikker in je"),
        "Bil",
    ),
    (
        ("verschillend gevormde blokjes", "goede plek", "vallen"),
        "Tetris",
    ),
    (
        ("victory boogie woogie",),
        "Piet Mondriaan",
    ),
    (
        ("gondels", "venetië", "kleur"),
        "Zwart",
    ),
    (
        ("gondels", "venetie", "kleur"),
        "Zwart",
    ),
    (
        ("gevonden ei", "koningin", "commissaris", "friesland"),
        "Kievit",
    ),
    (
        ("kaarsjes", "taart", "uitblaast"),
        "Een wens doen",
    ),
    (
        ("dubbele waterijsje", "twee voor de prijs van één"),
        "Dubbellikker",
    ),
    (
        ("dubbele waterijsje", "twee voor de prijs van een"),
        "Dubbellikker",
    ),
    (
        ("waterijsje", "amerika", "nederland", "bekend"),
        "Dubbellikker",
    ),
    (
        ("rowwen heze",),
        "Limburg/America/Bestel Mar/Jack Poels",
    ),
    (
        ("cocktail", "champagne", "crème de cassis", "krantenjongen", "miljonair"),
        "Kir Royal/Slumdog Millionaire/All men are created equal",
    ),
    (
        ("cocktail", "champagne", "creme de cassis", "krantenjongen", "miljonair"),
        "Kir Royal/Slumdog Millionaire/All men are created equal",
    ),
    (
        ("volle", "hardrock", "site", "winnares the voice", "shop", "australië", "highway to hell", "angus"),
        "AC/DC/Web/Maan",
    ),
    (
        ("volle", "hardrock", "site", "winnares the voice", "shop", "australie", "highway to hell", "angus"),
        "AC/DC/Web/Maan",
    ),
]
UI_WORDS = [
    "pas",
    "seconds",
    "seconden",
    "secondes",
    "finale",
    "ingelijst",
    "open deur",
    "slimste mens",
    "de slimste mens",
    "3-6-9",
    "3 6 9",
    "vraag",
    "antwoord",
]


class AbortAPIError(RuntimeError):
    pass


class CaptureCancelled(RuntimeError):
    pass


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CaptureCancelled("Capture cancelled")


def read_key_cache() -> str:
    try:
        stat = os.stat(KEY_CACHE_PATH)
        if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            return ""
        with open(KEY_CACHE_PATH, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def write_key_cache(api_key: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(KEY_CACHE_PATH, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(api_key.strip())


def clear_key_cache() -> None:
    try:
        os.unlink(KEY_CACHE_PATH)
    except FileNotFoundError:
        pass


def read_azure_keyvault_secret(vault_name: str, secret_name: str) -> str:
    if not vault_name or not secret_name:
        return ""
    proc = subprocess.run(
        [
            "az",
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            vault_name,
            "--name",
            secret_name,
            "--query",
            "value",
            "-o",
            "tsv",
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Could not read Azure Key Vault secret")
    return proc.stdout.strip()


def run_text(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def active_app_name() -> str:
    script = 'tell application "System Events" to name of first process whose frontmost is true'
    proc = run_text(["osascript", "-e", script])
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def app_window_bounds(app_name: str) -> tuple[int, int, int, int]:
    direct_script = (
        f'tell application "System Events" to tell process "{app_name}"\n'
        "set winPos to position of front window\n"
        "set winSize to size of front window\n"
        "return (item 1 of winPos as text) & \",\" & (item 2 of winPos as text) & \",\" & "
        "(item 1 of winSize as text) & \",\" & (item 2 of winSize as text)\n"
        "end tell"
    )
    proc = run_text(["osascript", "-e", direct_script])
    if proc.returncode != 0:
        title_script = (
            'tell application "System Events"\n'
            "repeat with proc in (processes whose visible is true)\n"
            "repeat with win in windows of proc\n"
            f'if (name of win as text) contains "{app_name}" then\n'
            "set winPos to position of win\n"
            "set winSize to size of win\n"
            "return (item 1 of winPos as text) & \",\" & (item 2 of winPos as text) & \",\" & "
            "(item 1 of winSize as text) & \",\" & (item 2 of winSize as text)\n"
            "end if\n"
            "end repeat\n"
            "end repeat\n"
            "end tell"
        )
        proc = run_text(["osascript", "-e", title_script])
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(proc.stderr.strip() or f"Could not read {app_name} window bounds")
    parts = [int(float(part.strip())) for part in proc.stdout.strip().split(",")]
    if len(parts) != 4:
        raise RuntimeError(f"Unexpected window bounds for {app_name}: {proc.stdout.strip()}")
    return parts[0], parts[1], parts[2], parts[3]


def parse_region(region: str) -> tuple[int, int, int, int]:
    parts = [int(float(part.strip())) for part in region.split(",")]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise ValueError("Region must be x,y,width,height")
    return parts[0], parts[1], parts[2], parts[3]


def question_region_from_window(bounds: tuple[int, int, int, int], ratios: str) -> tuple[int, int, int, int]:
    left, top, width, height = bounds
    parts = [float(part.strip()) for part in ratios.split(",")]
    if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
        raise ValueError("Game question ratios must be x_ratio,y_ratio,width_ratio,height_ratio")
    x_ratio, y_ratio, w_ratio, h_ratio = parts
    return (
        int(left + width * x_ratio),
        int(top + height * y_ratio),
        int(width * w_ratio),
        int(height * h_ratio),
    )


def capture_interactive(image_path: str) -> bool:
    proc = subprocess.run(["screencapture", "-i", image_path])
    return proc.returncode == 0 and os.path.exists(image_path) and os.path.getsize(image_path) > 0


def capture_region(image_path: str, region: tuple[int, int, int, int]) -> bool:
    x, y, width, height = region
    proc = subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{width},{height}", image_path])
    return proc.returncode == 0 and os.path.exists(image_path) and os.path.getsize(image_path) > 0


def capture_image(args: argparse.Namespace, image_path: str) -> bool:
    if args.capture_mode == "interactive":
        print(f"{YELLOW}Select the question area with the crosshairs. Press Esc to cancel.{RESET}")
        return capture_interactive(image_path)
    if args.capture_mode == "region":
        region = parse_region(args.region)
        print(f"{YELLOW}Capturing fixed region {region}.{RESET}")
        return capture_region(image_path, region)

    bounds = app_window_bounds(args.game_app)
    region = question_region_from_window(bounds, args.game_question_ratios)
    print(f"{YELLOW}Capturing {args.game_app} question region {region}.{RESET}")
    return capture_region(image_path, region)


def run_ocr(ocr_binary: str, image_path: str) -> str:
    proc = run_text([ocr_binary, image_path])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "OCR command failed")
    return " ".join(proc.stdout.split())


def clean_query(text: str) -> str:
    cleaned = text
    for word in UI_WORDS:
        cleaned = re.sub(rf"\b{re.escape(word)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{1,2}\s*(?:s|sec|secs|second(?:s)?|seconden)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s?'’\".,:;!()/+-]", " ", cleaned, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:;,.")
    return cleaned or text.strip()


def remove_answer_terms(text: str, answers: list[str]) -> str:
    cleaned = text
    for answer in answers:
        terms = [answer]
        terms.extend(word for word in re.findall(r"[\w'’+-]+", answer) if len(word) >= 4)
        for term in sorted(set(terms), key=len, reverse=True):
            cleaned = re.sub(rf"\b{re.escape(term)}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([?!.,:;])", r"\1", cleaned)
    return cleaned.strip(" -:;,.")


def strip_tags(fragment: str) -> str:
    fragment = re.sub(r"<script\b.*?</script>", " ", fragment, flags=re.IGNORECASE | re.DOTALL)
    fragment = re.sub(r"<style\b.*?</style>", " ", fragment, flags=re.IGNORECASE | re.DOTALL)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def duckduckgo_context(query: str, timeout: float = 6.0, max_results: int = 5) -> list[str]:
    params = urllib.parse.urlencode({"q": query})
    url = f"https://html.duckduckgo.com/html/?{params}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) NativeSlimsteHelper/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"{YELLOW}Search context unavailable: {exc}{RESET}", file=sys.stderr)
        return []

    context: list[str] = []
    result_blocks = re.findall(
        r'<div[^>]+class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*</div>',
        body,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in result_blocks:
        title_match = re.search(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet_match = re.search(
            r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>|'
            r'<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        parts = []
        if title_match:
            parts.append(strip_tags(title_match.group(1)))
        if snippet_match:
            parts.append(strip_tags(snippet_match.group(1) or snippet_match.group(2)))
        item = " - ".join(part for part in parts if part)
        if item and item not in context:
            context.append(item)
        if len(context) >= max_results:
            return context

    patterns = [
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        r'<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
    ]
    for pattern in patterns:
        for match in re.findall(pattern, body, flags=re.IGNORECASE | re.DOTALL):
            snippet = strip_tags(match)
            if snippet and snippet not in context:
                context.append(snippet)
            if len(context) >= max_results:
                return context
    return context[:max_results]


def search_queries(query: str, ocr_text: str) -> list[str]:
    base = query.strip()
    variants = [base]
    if base:
        variants.append(f'"{base}"')
        variants.append(f"{base} antwoord")

    lower = ocr_text.lower()
    if any(word in lower for word in ("wie", "welke", "welk", "waar", "wanneer", "hoe heet")):
        variants.append(f"{base} quiz")
    if "de slimste mens" not in lower:
        variants.append(f"{base} de slimste mens")

    unique = []
    seen = set()
    for item in variants:
        normalized = item.casefold()
        if item and normalized not in seen:
            seen.add(normalized)
            unique.append(item)
    return unique


def gather_context(args: argparse.Namespace, query: str, ocr_text: str, extra_queries: list[str] | None = None) -> list[str]:
    queries = search_queries(query, ocr_text)
    if extra_queries:
        queries.extend(extra_queries)
    context: list[str] = []
    for item in queries[: args.search_rounds]:
        before = len(context)
        context.extend(duckduckgo_context(item, timeout=args.search_timeout, max_results=args.search_results))
        if args.search_fail_fast and len(context) == before:
            break
    return unique_context(context)[: args.max_context_items]


def detect_round_type(text: str) -> str:
    lower = text.lower()
    if "open deur" in lower:
        return "open_deur"
    if "ingelijst" in lower:
        return "ingelijst"
    if "finale" in lower:
        return "finale"
    if "puzzel" in lower:
        return "puzzel"
    return "default"


def round_instruction(round_type: str) -> str:
    if round_type == "open_deur":
        return "Return the 4 most likely keywords, separated by slash characters (/)."
    if round_type == "ingelijst":
        return "Return the 10 most likely answers, separated by slash characters (/)."
    if round_type == "finale":
        return "Return the 5 most likely keywords or associations, separated by slash characters (/)."
    if round_type == "puzzel":
        return "Return the 3 most likely remaining puzzle answers, separated by slash characters (/)."
    return "Return exactly one best direct answer. Do not return alternatives for normal questions."


def known_answer(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text.lower())
    for required_terms, answer in KNOWN_ANSWERS:
        if all(term in normalized for term in required_terms):
            return answer
    return None


def should_verify(args: argparse.Namespace, ocr_text: str, answer: str) -> bool:
    if args.verify_mode == "never":
        return False
    if args.verify_mode == "always":
        return True
    if "/" in answer:
        return True

    normalized = re.sub(r"\s+", " ", ocr_text.lower())
    if re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", normalized):
        return True
    return any(term in normalized for term in RISKY_TERMS)


def answer_supported_by_context(answer: str, snippets: list[str]) -> bool:
    answer_words = [
        word
        for word in re.findall(r"[\w'’+-]+", answer.casefold())
        if len(word) >= 4
    ]
    if not answer_words:
        return True

    context_text = " ".join(snippets).casefold()
    if answer.casefold() in context_text:
        return True

    meaningful_words = answer_words[:4]
    hits = sum(1 for word in meaningful_words if word in context_text)
    return hits >= max(1, min(2, len(meaningful_words)))


def build_prompt(
    ocr_text: str,
    cleaned_query: str,
    snippets: list[str],
    excluded_answers: list[str] | None = None,
) -> str:
    context = "\n".join(f"- {snippet}" for snippet in snippets) or "- No external search snippets available."
    excluded = ", ".join(excluded_answers or []) or "none"
    return (
        "You are a fast answer helper for the Dutch/Belgian quiz show De Slimste Mens.\n"
        f"{round_instruction(detect_round_type(ocr_text))}\n"
        "For normal quiz questions, choose the canonical accepted quiz answer, not a list of plausible alternatives.\n"
        "For Puzzel rounds, infer hidden connector answers from the visible grid and output multiple remaining answers separated by /.\n"
        "For Puzzel rounds, each answer must connect at least two visible clues; prefer answers that connect three or four clues.\n"
        "For Puzzel rounds, do not output visible clue words themselves and do not output lower-level examples when an umbrella answer fits the grid.\n"
        "For Puzzel rounds, never repeat answers that are already visible as accepted answers or listed as excluded.\n"
        "Use the provided search context when it conflicts with weak memory.\n"
        "Do not include thoughts, reasoning, XML tags, Markdown, introductions, explanations, or conversational filler.\n"
        "Output ONLY the raw answer text, using / as the separator when multiple answers are requested.\n\n"
        f"OCR text: {ocr_text}\n"
        f"Cleaned search query: {cleaned_query}\n"
        f"Excluded answers: {excluded}\n"
        f"Search context:\n{context}\n"
    )


def build_retry_prompt(
    ocr_text: str,
    cleaned_query: str,
    bad_answer: str,
    excluded_answers: list[str],
    snippets: list[str],
    needed_count: int = 3,
) -> str:
    context = "\n".join(f"- {snippet}" for snippet in snippets) or "- No external search snippets available."
    excluded = ", ".join(excluded_answers) or "none"
    return (
        "Your previous answer for a De Slimste Mens Puzzel round was incomplete or repeated an old answer.\n"
        "Find different hidden connector answers from the visible grid.\n"
        f"Output exactly {needed_count} different remaining answers separated by / if possible.\n"
        "Each answer must connect multiple visible clues. Do not output visible clue words themselves.\n"
        "Do not output the repeated answer or any excluded answer.\n"
        "Output ONLY raw answers, no explanation.\n\n"
        f"OCR text: {ocr_text}\n"
        f"Cleaned search query: {cleaned_query}\n"
        f"Repeated answer: {bad_answer}\n"
        f"Excluded answers: {excluded}\n"
        f"Search context:\n{context}\n"
    )


def unique_context(items: list[str]) -> list[str]:
    seen = set()
    unique = []
    for item in items:
        normalized = item.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(item)
    return unique


def build_verification_prompt(
    ocr_text: str,
    cleaned_query: str,
    proposed_answer: str,
    snippets: list[str],
) -> str:
    context = "\n".join(f"- {snippet}" for snippet in snippets) or "- No external search snippets available."
    return (
        "You are verifying a De Slimste Mens quiz answer.\n"
        "Check whether the proposed answer is factually the best canonical answer to the question.\n"
        "Do not assume the proposed answer is correct; it may be a plausible wrong answer.\n"
        "If it is correct, output the proposed answer exactly.\n"
        "If it is wrong or too broad, output only the corrected answer.\n"
        "Do not output alternatives, reasoning, explanations, Markdown, XML tags, or filler.\n\n"
        f"Question OCR: {ocr_text}\n"
        f"Cleaned search query: {cleaned_query}\n"
        f"Proposed answer: {proposed_answer}\n"
        f"Search context:\n{context}\n"
    )


def build_independent_prompt(ocr_text: str, cleaned_query: str, snippets: list[str]) -> str:
    context = "\n".join(f"- {snippet}" for snippet in snippets) or "- No external search snippets available."
    return (
        "You are independently answering a De Slimste Mens quiz question.\n"
        f"{round_instruction(detect_round_type(ocr_text))}\n"
        "For normal quiz questions, output exactly one canonical accepted answer.\n"
        "Prefer the search context over weak memory. Be careful with record, sports, history, geography, and entertainment facts.\n"
        "Output ONLY the raw answer text. No reasoning, no alternatives, no punctuation unless part of the answer.\n\n"
        f"OCR text: {ocr_text}\n"
        f"Cleaned search query: {cleaned_query}\n"
        f"Search context:\n{context}\n"
    )


def build_tiebreak_prompt(
    ocr_text: str,
    cleaned_query: str,
    answer_a: str,
    answer_b: str,
    snippets: list[str],
) -> str:
    context = "\n".join(f"- {snippet}" for snippet in snippets) or "- No external search snippets available."
    return (
        "You are resolving two conflicting candidate answers for a De Slimste Mens quiz question.\n"
        "Choose the single best canonical accepted answer. If both are wrong, output the corrected answer.\n"
        "Use search context over memory. Output ONLY the answer, no explanation.\n\n"
        f"Question OCR: {ocr_text}\n"
        f"Cleaned search query: {cleaned_query}\n"
        f"Candidate A: {answer_a}\n"
        f"Candidate B: {answer_b}\n"
        f"Search context:\n{context}\n"
    )


def build_evidence_prompt(
    ocr_text: str,
    cleaned_query: str,
    answer: str,
    snippets: list[str],
) -> str:
    context = "\n".join(f"- {snippet}" for snippet in snippets) or "- No external search snippets available."
    return (
        "You are doing a final factual sanity check for a De Slimste Mens quiz answer.\n"
        "The current answer may be unsupported by search snippets. Verify it against the question and context.\n"
        "If the current answer is still the best canonical answer, output it exactly.\n"
        "If it is wrong, output only the corrected canonical answer.\n"
        "No reasoning, no alternatives, no punctuation unless part of the answer.\n\n"
        f"Question OCR: {ocr_text}\n"
        f"Cleaned search query: {cleaned_query}\n"
        f"Current answer: {answer}\n"
        f"Search context:\n{context}\n"
    )


def provider_headers(provider: str, api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if provider == "anthropic":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")
    elif api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_payload(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    model = args.model
    max_tokens = args.max_tokens
    temperature = args.temperature
    if args.provider == "anthropic":
        return {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
    if args.provider == "openai-responses":
        payload = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_tokens,
            "reasoning": {"effort": args.reasoning_effort},
            "text": {"verbosity": "low"},
        }
        if not model.startswith("gpt-5"):
            payload["temperature"] = temperature
        return payload
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def extract_answer(provider: str, data: dict[str, Any]) -> str:
    if provider == "anthropic":
        blocks = data.get("content", [])
        text_parts = [block.get("text", "") for block in blocks if isinstance(block, dict)]
        return " ".join(part for part in text_parts if part).strip()

    if provider == "openai-responses":
        if isinstance(data.get("output_text"), str):
            return data["output_text"].strip()
        pieces: list[str] = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    pieces.append(content["text"])
        return " ".join(pieces).strip()

    choices = data.get("choices", [])
    if choices:
        first = choices[0]
        if isinstance(first.get("message"), dict):
            return str(first["message"].get("content", "")).strip()
        return str(first.get("text", "")).strip()
    return ""


def call_llm(args: argparse.Namespace, prompt: str, timeout: float | None = None) -> str:
    payload = build_payload(args, prompt)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        args.endpoint,
        data=body,
        headers=provider_headers(args.provider, args.api_key),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or args.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        message = f"HTTP {exc.code}: {error_body or exc.reason}"
        if exc.code in BLOCKING_HTTP_CODES:
            raise AbortAPIError(message)
        raise RuntimeError(message)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API returned non-JSON response: {raw[:500]}") from exc

    if data.get("status") == "incomplete":
        reason = data.get("incomplete_details", {}).get("reason", "unknown")
        raise RuntimeError(
            f"API response incomplete: {reason}. Try higher --max-tokens or lower --reasoning-effort."
        )

    answer = extract_answer(args.provider, data)
    if not answer:
        raise RuntimeError(f"Could not extract answer from API response: {raw[:500]}")
    return sanitize_answer(answer)


def sanitize_answer(answer: str) -> str:
    answer = re.sub(r"<[^>]+>", " ", answer)
    answer = answer.replace("\n", " ")
    answer = re.sub(r"\s+", " ", answer)
    return answer.strip(" .;-")


def split_answers(answer: str) -> list[str]:
    protected: dict[str, str] = {}
    protected_answer = answer
    for index, item in enumerate(PROTECTED_SLASH_ANSWERS):
        token = f"__PROTECTED_SLASH_{index}__"
        protected[token] = item
        protected_answer = re.sub(re.escape(item), token, protected_answer, flags=re.IGNORECASE)

    parts = []
    for part in protected_answer.split("/"):
        restored = part.strip()
        for token, item in protected.items():
            restored = restored.replace(token, item)
        if restored:
            parts.append(restored)
    return parts


def answer_key(answer: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", answer.casefold())


def filter_new_answers(answer: str, excluded_answers: list[str]) -> list[str]:
    excluded = {answer_key(item) for item in excluded_answers}
    fresh: list[str] = []
    seen = set()
    for item in split_answers(answer):
        key = answer_key(item)
        if not key or key in excluded or key in seen:
            continue
        seen.add(key)
        fresh.append(item)
    return fresh


def answer_visible_in_text(answer: str, text: str) -> bool:
    return answer_key(answer) in answer_key(text)


def remember_puzzle_answers(answers: list[str]) -> None:
    known = {answer_key(item) for item in PUZZLE_HISTORY}
    for item in answers:
        key = answer_key(item)
        if key and key not in known:
            PUZZLE_HISTORY.append(item)
            known.add(key)
    del PUZZLE_HISTORY[:-12]


def queue_puzzle_answers(answers: list[str]) -> None:
    known = {answer_key(item) for item in PUZZLE_PENDING}
    for item in answers:
        key = answer_key(item)
        if key and key not in known:
            PUZZLE_PENDING.append(item)
            known.add(key)
    del PUZZLE_PENDING[:-12]


def update_puzzle_state_from_ocr(ocr_text: str) -> None:
    if not PUZZLE_HISTORY and not PUZZLE_PENDING:
        return

    confirmed = [answer for answer in PUZZLE_PENDING if answer_visible_in_text(answer, ocr_text)]
    if confirmed:
        remember_puzzle_answers(confirmed)
        confirmed_keys = {answer_key(answer) for answer in confirmed}
        PUZZLE_PENDING[:] = [answer for answer in PUZZLE_PENDING if answer_key(answer) not in confirmed_keys]

    visible_confirmed = any(answer_visible_in_text(answer, ocr_text) for answer in PUZZLE_HISTORY)
    visible_pending = any(answer_visible_in_text(answer, ocr_text) for answer in PUZZLE_PENDING)
    if PUZZLE_HISTORY and not visible_confirmed and not visible_pending:
        PUZZLE_HISTORY.clear()
        PUZZLE_PENDING.clear()


def maybe_reset_puzzle_history(ocr_text: str) -> None:
    if not PUZZLE_HISTORY:
        return
    normalized = ocr_text.casefold()
    if not any(answer.casefold() in normalized for answer in PUZZLE_HISTORY):
        PUZZLE_HISTORY.clear()


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, check=True)


def play_sound(sound_path: str) -> None:
    if sound_path and os.path.exists(sound_path):
        subprocess.Popen(["afplay", sound_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_paste_trigger_loop(args: argparse.Namespace) -> int:
    if not os.path.exists(args.keywatch_binary):
        print(f"{RED}Keywatch binary not found: {args.keywatch_binary}{RESET}", file=sys.stderr)
        print("Compile it first with: swiftc keywatch.swift -o keywatch", file=sys.stderr)
        return 2

    print("Paste trigger mode: Cmd+V arms next capture. Press Esc to cancel a running scan. Press Ctrl+C here to quit.")
    proc = subprocess.Popen(
        [args.keywatch_binary],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    events: queue.Queue[str] = queue.Queue()
    cancel_event: threading.Event | None = None
    worker: threading.Thread | None = None

    def read_keywatch() -> None:
        if proc.stdout is None:
            return
        for output_line in proc.stdout:
            event = output_line.strip()
            if event:
                events.put(event)

    def run_capture_worker(active_cancel_event: threading.Event) -> None:
        try:
            process_capture(args, cancel_event=active_cancel_event)
            if active_cancel_event.is_set():
                print(f"{YELLOW}Scan cancelled. Waiting for next Cmd+V.{RESET}")
            else:
                print(f"{YELLOW}Ready: paste the answer in the game; next Cmd+V will trigger again.{RESET}")
        except CaptureCancelled:
            print(f"{YELLOW}Scan cancelled. Waiting for next Cmd+V.{RESET}")
        except AbortAPIError as exc:
            print(f"{RED}API error, aborting without retry: {exc}{RESET}", file=sys.stderr)
            events.put("fatal")
        except Exception as exc:
            if active_cancel_event.is_set():
                print(f"{YELLOW}Scan cancelled. Waiting for next Cmd+V.{RESET}")
            else:
                print(f"{RED}{exc}{RESET}", file=sys.stderr)

    reader = threading.Thread(target=read_keywatch, daemon=True)
    reader.start()
    try:
        while True:
            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue

            if event == "fatal":
                return 1
            if event == "escape":
                if worker is not None and worker.is_alive() and cancel_event is not None:
                    cancel_event.set()
                    print(f"{YELLOW}Escape detected. Cancelling current scan...{RESET}")
                else:
                    print(f"{YELLOW}Escape detected. Waiting for next Cmd+V.{RESET}")
                continue
            if event != "paste":
                continue
            if worker is not None and worker.is_alive():
                print(f"{YELLOW}Cmd+V detected, but a scan is still running. Press Esc to cancel it.{RESET}")
                continue

            cancel_event = threading.Event()
            print(f"{YELLOW}Cmd+V detected. Capturing next question in {args.paste_trigger_delay:.1f}s...{RESET}")

            def delayed_worker(active_cancel_event: threading.Event) -> None:
                time.sleep(args.paste_trigger_delay)
                run_capture_worker(active_cancel_event)

            worker = threading.Thread(target=delayed_worker, args=(cancel_event,), daemon=True)
            worker.start()
        stderr = proc.stderr.read() if proc.stderr else ""
        if stderr.strip():
            print(f"{RED}{stderr.strip()}{RESET}", file=sys.stderr)
        return proc.wait()
    except KeyboardInterrupt:
        print("\nBye.")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()


def conveyor(answer: str, delay: float, sound_path: str) -> None:
    answers = split_answers(answer)
    if not answers:
        print(f"{YELLOW}No answer text to copy.{RESET}")
        return

    for index, item in enumerate(answers, start=1):
        copy_to_clipboard(item)
        play_sound(sound_path)
        print(f"{GREEN}[{index}/{len(answers)}] In clipboard: '{item}' (Press Cmd+V + Enter){RESET}")
        if index < len(answers):
            time.sleep(delay)


def process_capture(args: argparse.Namespace, cancel_event: threading.Event | None = None) -> bool:
    fd, image_path = tempfile.mkstemp(prefix="slimste_", suffix=".png")
    os.close(fd)
    try:
        os.unlink(image_path)
        check_cancel(cancel_event)
        if not capture_image(args, image_path):
            print(f"{YELLOW}Screenshot failed or cancelled.{RESET}")
            return True

        check_cancel(cancel_event)
        ocr_text = run_ocr(args.ocr_binary, image_path)
        if not ocr_text:
            print(f"{YELLOW}No text recognized.{RESET}")
            return True

        check_cancel(cancel_event)
        print(f"{CYAN}OCR: {ocr_text}{RESET}")
        override = known_answer(ocr_text)
        if override:
            if detect_round_type(ocr_text) == "puzzel":
                fresh_override = filter_new_answers(override, PUZZLE_HISTORY)
                queue_puzzle_answers(fresh_override)
                override = "/".join(fresh_override) or override
            check_cancel(cancel_event)
            print(f"{GREEN}Answer: {override} (known correction){RESET}")
            conveyor(override, args.conveyor_delay, args.sound)
            return True

        round_type = detect_round_type(ocr_text)
        if round_type == "puzzel":
            update_puzzle_state_from_ocr(ocr_text)
        excluded_answers = PUZZLE_HISTORY[:] if round_type == "puzzel" else []
        if excluded_answers:
            print(f"{YELLOW}Puzzle excludes: {', '.join(excluded_answers)}{RESET}")
        reasoning_text = remove_answer_terms(ocr_text, excluded_answers) if excluded_answers else ocr_text
        if reasoning_text != ocr_text:
            print(f"{YELLOW}Puzzle reasoning OCR: {reasoning_text}{RESET}")

        check_cancel(cancel_event)
        query = clean_query(reasoning_text)
        print(f"Search query: {query}")
        started = time.monotonic()
        if round_type == "puzzel" and not args.puzzle_search:
            snippets = []
            print(f"{YELLOW}Puzzle mode: search skipped.{RESET}")
        else:
            snippets = gather_context(args, query, reasoning_text)
        if snippets:
            print(f"{YELLOW}Context items: {len(snippets)}{RESET}")
        check_cancel(cancel_event)
        prompt = build_prompt(reasoning_text, query, snippets, excluded_answers=excluded_answers)
        answer = call_llm(args, prompt)
        check_cancel(cancel_event)
        if round_type == "puzzel":
            fresh_answers = filter_new_answers(answer, excluded_answers)
            if len(fresh_answers) < args.puzzle_answer_count:
                print(
                    f"{YELLOW}Puzzle returned {len(fresh_answers)}/{args.puzzle_answer_count} answers, asking for the rest...{RESET}"
                )
                retry_excludes = excluded_answers + fresh_answers
                retry_prompt = build_retry_prompt(
                    reasoning_text,
                    query,
                    answer,
                    retry_excludes,
                    snippets,
                    needed_count=args.puzzle_answer_count - len(fresh_answers),
                )
                retry_answer = call_llm(args, retry_prompt)
                check_cancel(cancel_event)
                retry_answers = filter_new_answers(retry_answer, retry_excludes)
                fresh_answers = (fresh_answers + retry_answers)[: args.puzzle_answer_count]
                if fresh_answers:
                    answer = "/".join(fresh_answers)
            elif len(fresh_answers) != len(split_answers(answer)):
                answer = "/".join(fresh_answers)
            queue_puzzle_answers(fresh_answers)
        if round_type == "default" and should_verify(args, ocr_text, answer):
            print(f"{YELLOW}Verifying answer...{RESET}")
            remaining = args.max_total_seconds - (time.monotonic() - started)
            if remaining <= args.min_verify_seconds:
                print(f"{YELLOW}Skipping verification to stay fast.{RESET}")
            else:
                verification_context = unique_context(snippets)
                if args.verify_style == "independent":
                    verification_prompt = build_independent_prompt(ocr_text, query, verification_context)
                else:
                    verification_prompt = build_verification_prompt(ocr_text, query, answer, verification_context)
                verify_timeout = min(args.timeout, max(args.min_verify_seconds, remaining))
                verified = call_llm(args, verification_prompt, timeout=verify_timeout)
                check_cancel(cancel_event)
                if verified and verified != answer:
                    print(f"{YELLOW}Disagreement: {answer} <> {verified}{RESET}")
                    remaining = args.max_total_seconds - (time.monotonic() - started)
                    if args.tiebreak and remaining > args.min_verify_seconds:
                        tiebreak_context = verification_context
                        if args.tiebreak_search:
                            extra_queries = [
                                f"{query} {answer}",
                                f"{query} {verified}",
                                f'"{answer}" "{verified}" {query}',
                            ]
                            tiebreak_context = gather_context(args, query, ocr_text, extra_queries=extra_queries)
                            print(f"{YELLOW}Tiebreak context items: {len(tiebreak_context)}{RESET}")
                        tiebreak_prompt = build_tiebreak_prompt(ocr_text, query, answer, verified, tiebreak_context)
                        answer = call_llm(args, tiebreak_prompt, timeout=min(args.timeout, remaining))
                        check_cancel(cancel_event)
                        print(f"{YELLOW}Tiebreak: {answer}{RESET}")
                    else:
                        answer = verified
                elif args.evidence_check and not answer_supported_by_context(answer, verification_context):
                    remaining = args.max_total_seconds - (time.monotonic() - started)
                    if remaining > args.min_verify_seconds:
                        print(f"{YELLOW}Evidence check: answer not found in context.{RESET}")
                        evidence_prompt = build_evidence_prompt(ocr_text, query, answer, verification_context)
                        checked = call_llm(args, evidence_prompt, timeout=min(args.timeout, remaining))
                        check_cancel(cancel_event)
                        if checked and checked != answer:
                            print(f"{YELLOW}Evidence corrected: {answer} -> {checked}{RESET}")
                            answer = checked
        elif round_type == "default":
            print(f"{YELLOW}Fast path: verification skipped.{RESET}")
        check_cancel(cancel_event)
        print(f"{GREEN}Answer: {answer}{RESET}")
        conveyor(answer, args.conveyor_delay, args.sound)
        return True
    finally:
        try:
            if os.path.exists(image_path):
                os.unlink(image_path)
        except OSError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Native macOS OCR + LLM clipboard helper.")
    parser.add_argument("--endpoint", default=os.environ.get("LLM_ENDPOINT", "https://api.openai.com/v1/chat/completions"))
    parser.add_argument("--api-key", default=os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")))
    parser.add_argument("--no-key-cache", action="store_true", help="Do not read or write the temporary API key cache.")
    parser.add_argument("--clear-key-cache", action="store_true", help="Delete the cached API key and exit.")
    parser.add_argument("--azure-keyvault-name", default=os.environ.get("AZURE_KEYVAULT_NAME", ""))
    parser.add_argument("--azure-keyvault-secret-name", default=os.environ.get("AZURE_KEYVAULT_SECRET_NAME", ""))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--provider",
        choices=["openai-chat", "openai-responses", "anthropic"],
        default=os.environ.get("LLM_PROVIDER", "openai-chat"),
    )
    parser.add_argument("--ocr-binary", default=os.environ.get("OCR_BINARY", "./ocr"))
    parser.add_argument("--keywatch-binary", default=os.environ.get("KEYWATCH_BINARY", "./keywatch"))
    parser.add_argument("--terminal-name", default=os.environ.get("TERMINAL_NAME", "Terminal"))
    parser.add_argument(
        "--trigger-mode",
        choices=["focus", "paste"],
        default=os.environ.get("TRIGGER_MODE", "focus"),
        help="focus captures when Terminal becomes frontmost; paste captures after Cmd+V is pressed.",
    )
    parser.add_argument("--paste-trigger-delay", type=float, default=2.5)
    parser.add_argument("--game-app", default=os.environ.get("GAME_APP", "Slimste Mens"))
    parser.add_argument(
        "--capture-mode",
        choices=["interactive", "game-window", "region"],
        default=os.environ.get("CAPTURE_MODE", "interactive"),
        help="interactive uses crosshairs; game-window auto-captures the question area; region captures x,y,w,h.",
    )
    parser.add_argument("--region", default=os.environ.get("CAPTURE_REGION", "0,0,100,100"))
    parser.add_argument(
        "--game-question-ratios",
        default=os.environ.get("GAME_QUESTION_RATIOS", "0.02,0.12,0.96,0.55"),
        help="Relative x,y,width,height inside the game window for automatic question capture.",
    )
    parser.add_argument("--poll-delay", type=float, default=0.2)
    parser.add_argument("--conveyor-delay", type=float, default=2.2)
    parser.add_argument("--puzzle-answer-count", type=int, default=3)
    parser.add_argument("--puzzle-search", action="store_true", help="Allow web search context for Puzzel rounds.")
    parser.add_argument("--timeout", type=float, default=18.0)
    parser.add_argument("--search-timeout", type=float, default=3.0)
    parser.add_argument("--search-rounds", type=int, default=3)
    parser.add_argument("--search-results", type=int, default=5)
    parser.add_argument("--max-context-items", type=int, default=12)
    parser.add_argument(
        "--no-search-fail-fast",
        dest="search_fail_fast",
        action="store_false",
        help="Keep trying all search variants even when earlier searches return no context.",
    )
    parser.add_argument("--max-total-seconds", type=float, default=7.0)
    parser.add_argument("--min-verify-seconds", type=float, default=2.0)
    parser.add_argument("--max-tokens", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--reasoning-effort",
        choices=["none", "low", "medium", "high", "xhigh"],
        default=os.environ.get("REASONING_EFFORT", "low"),
        help="Reasoning effort for OpenAI Responses models such as gpt-5.5.",
    )
    parser.add_argument("--sound", default=DEFAULT_SOUND)
    parser.add_argument(
        "--verify-mode",
        choices=["smart", "always", "never"],
        default=os.environ.get("VERIFY_MODE", "smart"),
        help="smart verifies risky normal questions only; always verifies all normal questions; never skips verification.",
    )
    parser.add_argument(
        "--verify-style",
        choices=["proposed", "independent"],
        default=os.environ.get("VERIFY_STYLE", "independent"),
        help="independent asks a fresh second answer; proposed asks whether the first answer is correct.",
    )
    parser.add_argument(
        "--no-tiebreak",
        dest="tiebreak",
        action="store_false",
        help="Do not make a third model call when the first two answers disagree.",
    )
    parser.add_argument(
        "--no-tiebreak-search",
        dest="tiebreak_search",
        action="store_false",
        help="Do not do extra candidate-specific search before a tiebreak.",
    )
    parser.add_argument(
        "--no-evidence-check",
        dest="evidence_check",
        action="store_false",
        help="Skip the extra sanity check when an agreed answer is not present in search context.",
    )
    parser.set_defaults(tiebreak=True, tiebreak_search=True, evidence_check=True, search_fail_fast=True)
    parser.add_argument(
        "--no-verify",
        dest="verify_mode",
        action="store_const",
        const="never",
        help="Alias for --verify-mode never.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Capture immediately once, then exit. For the game, prefer running without --once.",
    )
    args = parser.parse_args()
    if args.clear_key_cache:
        clear_key_cache()
        print("Cached API key cleared.")
        raise SystemExit(0)

    if not args.api_key and not args.endpoint.startswith(("http://localhost", "http://127.0.0.1")):
        if not args.no_key_cache:
            args.api_key = read_key_cache()
        if args.api_key:
            print(f"{YELLOW}Using cached API key for this user.{RESET}")
        else:
            if args.azure_keyvault_name and args.azure_keyvault_secret_name:
                print(f"{YELLOW}Reading API key from Azure Key Vault '{args.azure_keyvault_name}'.{RESET}")
                args.api_key = read_azure_keyvault_secret(
                    args.azure_keyvault_name,
                    args.azure_keyvault_secret_name,
                )
                if args.api_key and not args.no_key_cache:
                    write_key_cache(args.api_key)
                    print(f"{YELLOW}API key cached for future helper starts.{RESET}")
            else:
                print(f"{YELLOW}No API key found in this Terminal session.{RESET}")
                args.api_key = getpass.getpass("Paste API key: ").strip()
                if args.api_key and not args.no_key_cache:
                    write_key_cache(args.api_key)
                    print(f"{YELLOW}API key cached for future helper starts.{RESET}")
        if not args.api_key:
            parser.error("No API key entered.")
    return args


def main() -> int:
    args = parse_args()
    if not os.path.exists(args.ocr_binary):
        print(f"{RED}OCR binary not found: {args.ocr_binary}{RESET}", file=sys.stderr)
        print("Compile it first with: swiftc ocr.swift -o ocr", file=sys.stderr)
        return 2

    if args.once:
        try:
            return 0 if process_capture(args) else 1
        except AbortAPIError as exc:
            print(f"{RED}API error, aborting without retry: {exc}{RESET}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print()
            return 130
        except Exception as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1

    if args.trigger_mode == "paste":
        return run_paste_trigger_loop(args)

    print("Continuous mode: leave this running. Click the game, then click Terminal to capture. Press Ctrl+C to quit.")
    was_terminal = active_app_name() == args.terminal_name
    try:
        while True:
            current_app = active_app_name()
            is_terminal = current_app == args.terminal_name
            if is_terminal and not was_terminal:
                try:
                    process_capture(args)
                    print(f"{YELLOW}Ready for next question: click the game, then click Terminal again.{RESET}")
                except AbortAPIError as exc:
                    print(f"{RED}API error, aborting without retry: {exc}{RESET}", file=sys.stderr)
                    return 1
                except Exception as exc:
                    print(f"{RED}{exc}{RESET}", file=sys.stderr)
            was_terminal = is_terminal
            time.sleep(args.poll_delay)
    except KeyboardInterrupt:
        print("\nBye.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
