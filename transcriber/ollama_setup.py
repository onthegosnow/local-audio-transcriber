"""One-click, no-root setup of a LOCAL Ollama server for the AI-summary feature.

Downloads the Ollama runtime into the user's home (``~/.local``, no sudo/root),
starts it as the current user, and pulls a model. Everything stays on the
machine — the only network traffic is fetching Ollama and the model from their
official sources.

Used by both the GUI ("Enable AI summaries" button) and install.sh
(``python -m transcriber.ollama_setup``), so there is one implementation.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import requests

DEFAULT_MODEL = "llama3.2"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
LOCAL_PREFIX = Path.home() / ".local"
HOME_BIN = LOCAL_PREFIX / "bin" / "ollama"
RELEASES_API = "https://api.github.com/repos/ollama/ollama/releases/latest"

Status = Optional[Callable[[str], None]]
Progress = Optional[Callable[[int], None]]


def _arch() -> Optional[str]:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return None


def supported() -> bool:
    """True if we can install Ollama on this OS/CPU without root."""
    return platform.system() == "Linux" and _arch() is not None


def binary_path() -> Optional[str]:
    """Path to a usable ollama binary (a system one wins), or None."""
    sys_ollama = shutil.which("ollama")
    if sys_ollama:
        return sys_ollama
    return str(HOME_BIN) if HOME_BIN.exists() else None


def is_installed() -> bool:
    return binary_path() is not None


def is_running(timeout: float = 2.0) -> bool:
    try:
        return requests.get(f"{OLLAMA_HOST}/api/tags", timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def _asset_url(arch: str) -> str:
    r = requests.get(
        RELEASES_API, timeout=15, headers={"Accept": "application/vnd.github+json"}
    )
    r.raise_for_status()
    want = f"ollama-linux-{arch}.tar.zst"
    for a in r.json().get("assets", []):
        if a.get("name") == want:
            return a["browser_download_url"]
    raise RuntimeError(f"Could not find {want} in the latest Ollama release.")


def install(on_status: Status = None, on_progress: Progress = None) -> None:
    """Download + extract the Ollama runtime into ~/.local (no root)."""
    if is_installed():
        if on_status:
            on_status("Ollama is already installed.")
        return

    arch = _arch()
    if not arch:
        raise RuntimeError(f"Unsupported CPU architecture: {platform.machine()}")

    if on_status:
        on_status("Finding the latest Ollama release…")
    url = _asset_url(arch)
    (LOCAL_PREFIX / "bin").mkdir(parents=True, exist_ok=True)

    if on_status:
        on_status("Downloading Ollama…")
    tmpdir = Path(tempfile.mkdtemp())
    archive = tmpdir / "ollama.tar.zst"
    try:
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            done = 0
            with open(archive, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(int(done * 100 / total))

        if on_status:
            on_status("Extracting Ollama…")
        import zstandard  # provided via requirements.txt

        with open(archive, "rb") as f:
            with zstandard.ZstdDecompressor().stream_reader(f) as reader:
                # Streamed (non-seekable) tar -> mode "r|". Yields
                # ~/.local/bin/ollama and ~/.local/lib/ollama/*.
                with tarfile.open(fileobj=reader, mode="r|") as tar:
                    tar.extractall(LOCAL_PREFIX)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if not is_installed():
        raise RuntimeError("Ollama binary missing after extraction.")
    try:
        os.chmod(HOME_BIN, 0o755)
    except OSError:
        pass


def start_server(on_status: Status = None, wait: int = 40) -> None:
    """Start `ollama serve` as the current user (detached) and wait for it."""
    if is_running():
        if on_status:
            on_status("Ollama is already running.")
        return
    exe = binary_path()
    if not exe:
        raise RuntimeError("Ollama is not installed.")

    if on_status:
        on_status("Starting Ollama…")
    logfile = open(Path.home() / ".ollama-serve.log", "ab")
    env = dict(os.environ)
    env["PATH"] = f"{LOCAL_PREFIX / 'bin'}:{env.get('PATH', '')}"
    subprocess.Popen(
        [exe, "serve"],
        stdout=logfile,
        stderr=logfile,
        start_new_session=True,  # survive parent exit
        env=env,
    )
    for _ in range(wait * 2):
        if is_running():
            if on_status:
                on_status("Ollama is running.")
            return
        time.sleep(0.5)
    raise RuntimeError("Ollama did not become ready in time.")


def pull_model(
    model: str = DEFAULT_MODEL, on_status: Status = None, on_progress: Progress = None
) -> None:
    """Pull a model via the Ollama HTTP API, streaming download progress."""
    if on_status:
        on_status(f"Downloading the AI model ({model})…")
    with requests.post(
        f"{OLLAMA_HOST}/api/pull",
        json={"name": model, "stream": True},
        stream=True,
        timeout=None,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in msg:
                raise RuntimeError(msg["error"])
            total, completed = msg.get("total"), msg.get("completed")
            if on_progress and total:
                on_progress(int(min(100, (completed or 0) * 100 / total)))
            if on_status and msg.get("status"):
                on_status(msg["status"])
    if on_progress:
        on_progress(100)


def setup(
    model: str = DEFAULT_MODEL, on_status: Status = None, on_progress: Progress = None
) -> None:
    """Full flow: install (if needed) -> start server -> pull model."""
    install(on_status=on_status, on_progress=on_progress)
    start_server(on_status=on_status)
    pull_model(model, on_status=on_status, on_progress=on_progress)
    if on_status:
        on_status("AI summaries are ready.")


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Set up local Ollama for AI summaries.")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    if not supported():
        print(
            f"AI setup isn't supported on this system "
            f"({platform.system()}/{platform.machine()})."
        )
        return 1

    def st(s: str) -> None:
        print(f"  {s}", flush=True)

    last = [-1]

    def pr(p: int) -> None:
        if p != last[0] and (p % 10 == 0 or p == 100):
            print(f"    {p}%", flush=True)
            last[0] = p

    try:
        setup(args.model, on_status=st, on_progress=pr)
        print("AI summaries are ready.")
        return 0
    except Exception as e:  # surface a clean message to the installer
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
