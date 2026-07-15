# Local Audio Transcriber

An **offline** desktop app for Ubuntu/Linux that transcribes audio locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and (optionally)
post-processes the transcript with a **local LLM via [Ollama](https://ollama.com)**.

Nothing leaves your machine. Audio is transcribed on-device; the LLM step runs
against a local Ollama server. The only network access is a one-time download of
the chosen Whisper model on first use.

![app](icon.svg)

## Features

- 🎙️ Drag-and-drop any common audio/video file (mp3, wav, m4a, flac, ogg, opus,
  mp4, mkv, mov, …)
- ⚡ faster-whisper engine — CPU (int8) by default, optional NVIDIA GPU (CUDA)
- 🌍 Auto language detection or pick a language
- ⏱️ Optional timestamps, silence-skipping (VAD) for speed
- 📝 Live streaming transcript as it's recognized
- 🤖 One-click local-LLM post-processing: Summary, Bullet points, Clean up,
  Action items, Meeting minutes, or a custom instruction
- 📋 Copy / 💾 Save transcript and LLM output

## Install (Ubuntu / Debian)

```bash
cd audio-transcriber
./install.sh
```

That creates a self-contained `.venv`, installs the Python deps, and adds a
**Local Audio Transcriber** entry to your application menu.

If you're on a minimal install and the GUI won't start (Qt `xcb` platform
error), install the Qt runtime libs:

```bash
sudo apt install libxcb-cursor0 libxkbcommon0 libegl1 libgl1
```

You also need Python's venv module (Ubuntu ships it separately):

```bash
sudo apt install python3 python3-venv
```

## Run

From the app menu (**Local Audio Transcriber**) or a terminal:

```bash
./run.sh
```

## Optional: local LLM post-processing (Ollama)

The transcription works without this. To enable the right-hand LLM panel:

```bash
# 1. Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# 2. Pull a model (any chat model works)
ollama pull llama3.2        # small & fast
# or: ollama pull mistral   /  ollama pull qwen2.5

# 3. Make sure it's running (systemd usually starts it automatically)
ollama serve &
```

Then click **↻** in the app to refresh the model list. Ollama is auto-detected
at `http://localhost:11434` (override with the `OLLAMA_HOST` env var).

## How it works

```
audio file ──► faster-whisper (local) ──► transcript
                                              │
                                              ▼
                                 Ollama LLM (local) ──► summary / cleanup / …
```

- **Models** download to `~/.cache/huggingface` on first use, then run fully
  offline. `small` is a good default; `large-v3` is most accurate but slower;
  `distil-large-v3` is a fast, near-large English model.
- **GPU:** set Device = *NVIDIA GPU (CUDA)*. Requires the CUDA + cuDNN runtime.
  If the libraries are missing you'll get a clear error — just switch back to
  CPU.
- No `ffmpeg` binary needed — faster-whisper decodes audio via bundled PyAV.

## Project layout

```
audio-transcriber/
├── install.sh                    # one-shot Ubuntu installer
├── update.sh                     # one-command updater (pulls latest release)
├── run.sh                        # launcher (uses .venv)
├── package.sh                    # (dev) build a sendable release tarball
├── release.sh                    # (dev) publish a new GitHub release
├── requirements.txt
├── icon.svg
├── local-transcriber.desktop.in  # app-menu launcher template
└── transcriber/
    ├── __main__.py               # `python -m transcriber`
    ├── engine.py                 # faster-whisper wrapper
    ├── llm.py                    # Ollama HTTP client
    └── gui.py                    # PySide6 GUI
```

## Updating

When a new version is published, update in place — your virtualenv, settings,
and downloaded models are kept:

```bash
./update.sh
```

Or from the app: **Help → Check for updates…** It tells you if a newer version
is available and to run `./update.sh`.

## Publishing an update (developer)

Fix something, then publish a new release in one command:

```bash
./release.sh 1.0.1 "What changed"
```

This bumps the version, builds the tarball, tags the commit, and creates a
GitHub Release with the archive attached. Everyone's `./update.sh` picks it up.
Releases are published to `github.com/onthegosnow/local-audio-transcriber`.

## Uninstall

```bash
rm -rf audio-transcriber/.venv
rm ~/.local/share/applications/local-transcriber.desktop
```

## Note

Speech-to-text is done by **Whisper** (an open speech-recognition model), which
is the correct tool for turning audio into text. A general chat LLM can't
transcribe audio, but it *can* refine the transcript afterward — that's the
optional Ollama step.
