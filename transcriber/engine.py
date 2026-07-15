"""faster-whisper transcription engine.

Wraps faster-whisper so the GUI can call a single ``transcribe`` function and
receive progress + streaming segments through callbacks. Models are cached in
memory so re-running with the same settings does not reload weights.

The actual speech recognition runs 100% locally. The model files are downloaded
from Hugging Face on first use only, then cached under ~/.cache/huggingface and
reused fully offline afterwards.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

# Model sizes offered in the UI, smallest/fastest first. distil-* models are
# English-optimized and noticeably faster with near-large accuracy.
MODEL_SIZES = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3",
    "distil-large-v3",
]

# Human-friendly notes shown next to each model in the UI.
MODEL_NOTES = {
    "tiny": "~75 MB · fastest, lowest accuracy",
    "base": "~145 MB · fast",
    "small": "~485 MB · good balance",
    "medium": "~1.5 GB · high accuracy",
    "large-v3": "~3 GB · best accuracy, slowest",
    "distil-large-v3": "~1.5 GB · large-ish accuracy, ~2x faster (English)",
}

DEVICES = ["cpu", "cuda"]

# Sensible compute types per device. int8 is fast + low-memory on CPU;
# float16 is the GPU sweet spot.
DEFAULT_COMPUTE = {"cpu": "int8", "cuda": "float16"}


@dataclass
class TranscribeParams:
    audio_path: str
    model_size: str = "small"
    device: str = "cpu"
    compute_type: Optional[str] = None  # None -> DEFAULT_COMPUTE[device]
    language: Optional[str] = None      # None -> auto-detect
    beam_size: int = 5
    vad_filter: bool = True             # skip long silences, big speedup
    include_timestamps: bool = False

    def resolved_compute(self) -> str:
        return self.compute_type or DEFAULT_COMPUTE.get(self.device, "int8")


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    text: str
    segments: list = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration: float = 0.0


# Cache: (model_size, device, compute_type) -> WhisperModel
_MODEL_CACHE: dict = {}


def _get_model(model_size: str, device: str, compute_type: str):
    """Load (or reuse) a WhisperModel. Imported lazily so importing this module
    is cheap and does not require faster-whisper until the user transcribes."""
    key = (model_size, device, compute_type)
    if key not in _MODEL_CACHE:
        from faster_whisper import WhisperModel  # heavy import, done on demand

        _MODEL_CACHE[key] = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
    return _MODEL_CACHE[key]


def _format_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def transcribe(
    params: TranscribeParams,
    on_status: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    on_segment: Optional[Callable[[Segment], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> TranscriptResult:
    """Transcribe an audio file.

    Callbacks (all optional):
      on_status(str)      -- coarse status messages for the UI
      on_progress(int)    -- 0..100 based on audio position
      on_segment(Segment) -- each recognized segment as it is produced
      is_cancelled()      -- return True to stop early; raises Cancelled
    """
    if not os.path.exists(params.audio_path):
        raise FileNotFoundError(f"Audio file not found: {params.audio_path}")

    device = params.device
    compute_type = params.resolved_compute()

    if on_status:
        on_status(f"Loading model '{params.model_size}' ({device}/{compute_type})…")

    try:
        model = _get_model(params.model_size, device, compute_type)
    except Exception:
        # If the GPU path can't initialize (missing CUDA/cuDNN, wrong arch, etc.)
        # transparently fall back to CPU instead of failing the whole job.
        if device != "cuda":
            raise
        device = "cpu"
        compute_type = DEFAULT_COMPUTE["cpu"]
        if on_status:
            on_status("GPU unavailable — using CPU instead…")
        model = _get_model(params.model_size, device, compute_type)

    if on_status:
        on_status("Analyzing audio…")

    segments_iter, info = model.transcribe(
        params.audio_path,
        language=params.language,          # None => auto-detect
        beam_size=params.beam_size,
        vad_filter=params.vad_filter,
    )

    duration = float(getattr(info, "duration", 0.0) or 0.0)
    detected_lang = getattr(info, "language", "") or ""
    lang_prob = float(getattr(info, "language_probability", 0.0) or 0.0)

    if on_status:
        if params.language:
            on_status(f"Transcribing ({detected_lang})…")
        else:
            on_status(f"Transcribing — detected {detected_lang} ({lang_prob*100:.0f}%)…")

    collected: list[Segment] = []
    text_parts: list[str] = []

    for seg in segments_iter:
        if is_cancelled and is_cancelled():
            raise Cancelled("Transcription cancelled by user.")

        s = Segment(start=float(seg.start), end=float(seg.end), text=seg.text.strip())
        collected.append(s)

        if params.include_timestamps:
            text_parts.append(f"[{_format_timestamp(s.start)} -> {_format_timestamp(s.end)}] {s.text}")
        else:
            text_parts.append(s.text)

        if on_segment:
            on_segment(s)

        if on_progress and duration > 0:
            pct = int(min(100, max(0, (s.end / duration) * 100)))
            on_progress(pct)

    if on_progress:
        on_progress(100)
    if on_status:
        on_status("Done.")

    joiner = "\n" if params.include_timestamps else " "
    full_text = joiner.join(text_parts).strip()

    return TranscriptResult(
        text=full_text,
        segments=collected,
        language=detected_lang,
        language_probability=lang_prob,
        duration=duration,
    )


class Cancelled(Exception):
    """Raised when transcription is cancelled via the is_cancelled callback."""
