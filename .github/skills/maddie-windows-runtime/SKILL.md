---
name: maddie-windows-runtime
description: "Use when: debugging or changing Windows runtime support for MADDIE, PowerShell startup, llama.cpp endpoint configuration, docker compose, OmniVoice service URL, or app_settings.json defaults. Keywords: Windows, PowerShell, 0.0.0.0:8080, run.ps1, Docker, OmniVoice."
---

# MADDIE Windows Runtime

Use this skill when the task is about starting, configuring, or debugging the app on Windows.

## Primary files

- `run.ps1`
- `docker-compose.yml`
- `docker/omnivoice/Dockerfile`
- `data/app_settings.json`
- `app/llm_client.py`
- `README.md`

## Runtime facts

- The configured llama.cpp URL may be `http://0.0.0.0:8080`, but the Python client normalizes it to loopback for actual requests.
- The recommended Windows path is: llama.cpp on the host, OmniVoice in Docker, FastAPI app in the host venv.
- The default OmniVoice HTTP service runs on `http://127.0.0.1:8010`.

## Safe workflow

1. Check `data/app_settings.json` before changing runtime code.
2. If TTS is failing, verify `docker compose up omnivoice` and `GET /health` on port `8010`.
3. If LLM calls fail, verify `/v1/models` on the configured llama.cpp host.
4. Prefer fixing URL normalization or startup scripts over hardcoding machine-specific paths.