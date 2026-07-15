"""Hardware detection: choose CPU vs GPU for transcription, and report clearly.

The tricky reality this encodes:
  * faster-whisper's GPU engine (ctranslate2) ships CUDA **only in x86_64
    wheels**. On aarch64 (e.g. Grace Blackwell / DGX) the wheel is CPU-only, so
    GPU *transcription* isn't available via pip — but those ARM CPUs are fast.
  * Ollama (AI summaries) does its own GPU detection at runtime, so it will use
    an NVIDIA GPU when present regardless of this module.

So we only claim "GPU transcription" when ctranslate2 can actually see a CUDA
device; everything else falls back to CPU, which still works everywhere.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from functools import lru_cache


def arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(m, m)


def is_x86() -> bool:
    return arch() == "x86_64"


def is_arm() -> bool:
    return arch() == "aarch64"


@lru_cache(maxsize=1)
def nvidia_gpus() -> list:
    """Names of NVIDIA GPUs visible via nvidia-smi (driver present), else []."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if out.returncode == 0:
            return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    except Exception:
        pass
    return []


def has_nvidia_gpu() -> bool:
    return len(nvidia_gpus()) > 0


@lru_cache(maxsize=1)
def cuda_transcription_available() -> bool:
    """True only if ctranslate2 can actually use a CUDA device for transcription.

    Requires x86_64 (CUDA is not in the aarch64 ctranslate2 wheel) AND a working
    CUDA runtime that ctranslate2 can see.
    """
    if not is_x86():
        return False
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def recommended_device() -> str:
    """Device the UI should default to."""
    return "cuda" if cuda_transcription_available() else "cpu"


def report() -> dict:
    """A structured summary of what transcription/summary acceleration to expect."""
    gpus = nvidia_gpus()
    gpu_name = gpus[0] if gpus else ""
    cuda_tx = cuda_transcription_available()

    if cuda_tx:
        transcription = f"GPU / CUDA ({gpu_name})"
    elif gpus and is_arm():
        transcription = (
            f"CPU — a GPU is present ({gpu_name}) but faster-whisper has no ARM "
            "CUDA build; this ARM CPU is fast"
        )
    elif gpus:
        transcription = (
            f"CPU — a GPU is present ({gpu_name}) but CUDA isn't usable "
            "(driver/libs); run install.sh again after installing the NVIDIA driver"
        )
    else:
        transcription = "CPU (no NVIDIA GPU detected)"

    if gpus:
        summaries = f"GPU ({gpu_name}) via Ollama, when supported"
    else:
        summaries = "CPU via Ollama"

    return {
        "arch": arch(),
        "gpus": gpus,
        "cuda_transcription": cuda_tx,
        "transcription": transcription,
        "summaries": summaries,
    }


def _cli() -> int:
    r = report()
    gpu = ", ".join(r["gpus"]) if r["gpus"] else "none detected"
    print(f"Architecture : {r['arch']}")
    print(f"NVIDIA GPU   : {gpu}")
    print(f"Transcription: {r['transcription']}")
    print(f"AI summaries : {r['summaries']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
