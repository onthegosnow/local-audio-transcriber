"""Local Ollama post-processing.

Talks to a locally running Ollama server (default http://localhost:11434) over
its HTTP API so we don't add another Python dependency beyond `requests`.
Everything stays on the machine — no cloud calls.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

import requests

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

# Steering applied to EVERY task (built-in and custom). This is what keeps the
# output clean and app-like instead of chatty ("Here is a summary of the…").
SYSTEM_PROMPT = (
    "You are a transcript post-processing assistant. Follow the user's instruction "
    "exactly and return ONLY the requested result — no preamble, no sign-off, no "
    "'Here is…' line, and do not restate the task. Base everything strictly on the "
    "provided transcript and never invent facts, names, dates, or numbers that are "
    "not present in it."
)

# Built-in post-processing tasks. The key is shown in the UI; the value is the
# instruction prepended to the transcript. Keep each instruction terse — the
# SYSTEM_PROMPT above enforces the "output only, no preamble" behavior.
TASKS: dict[str, str] = {
    "Summary": (
        "Summarize the transcript in a few clear paragraphs, capturing the key "
        "points, decisions, and any action items."
    ),
    "Bullet points": (
        "Rewrite the most important points of the transcript as a concise "
        "bulleted list. Keep each bullet short and factual."
    ),
    "Clean up": (
        "Clean up the raw transcript: fix punctuation and capitalization, remove "
        "filler words (um, uh, you know), and break it into readable paragraphs. "
        "Preserve the original meaning and wording. Output only the cleaned text."
    ),
    "Action items": (
        "Extract a checklist of concrete action items from the transcript, each "
        "with the responsible person and due date when they are mentioned. If "
        "there are none, output exactly: No action items found."
    ),
    "Meeting minutes": (
        "Write structured meeting minutes from the transcript with these sections: "
        "Attendees (only if identifiable), Summary, Decisions, and Action Items."
    ),
}


class OllamaError(RuntimeError):
    pass


def is_available(timeout: float = 2.0) -> bool:
    """True if an Ollama server responds at OLLAMA_HOST."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def list_models(timeout: float = 5.0) -> list[str]:
    """Return installed model names (e.g. ['llama3.2:latest', 'mistral:latest'])."""
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout)
        r.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(f"Could not reach Ollama at {OLLAMA_HOST}: {e}") from e
    data = r.json()
    return sorted(m.get("name", "") for m in data.get("models", []) if m.get("name"))


def process(
    text: str,
    model: str,
    instruction: str,
    on_token: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    timeout: float = 600.0,
) -> str:
    """Run `text` through `model` with `instruction`, streaming tokens.

    Returns the full response. If on_token is given, it receives each chunk as
    it arrives so the UI can stream output live.
    """
    if not text.strip():
        raise OllamaError("Nothing to process — the transcript is empty.")

    prompt = f"{instruction}\n\n----- TRANSCRIPT -----\n{text}\n----- END TRANSCRIPT -----"
    payload = {
        "model": model,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": True,
    }

    pieces: list[str] = []
    try:
        with requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            stream=True,
            timeout=timeout,
        ) as resp:
            if resp.status_code == 404:
                raise OllamaError(
                    f"Model '{model}' is not installed. Run:  ollama pull {model}"
                )
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if is_cancelled and is_cancelled():
                    raise OllamaError("Cancelled by user.")
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    raise OllamaError(chunk["error"])
                token = chunk.get("response", "")
                if token:
                    pieces.append(token)
                    if on_token:
                        on_token(token)
                if chunk.get("done"):
                    break
    except requests.RequestException as e:
        raise OllamaError(f"Ollama request failed: {e}") from e

    return "".join(pieces).strip()
