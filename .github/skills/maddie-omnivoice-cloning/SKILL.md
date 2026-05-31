---
name: maddie-omnivoice-cloning
description: "Use when: changing OmniVoice integration, voice cloning, reference audio handling, clone/instruct voice modes, or the separate TTS service. Keywords: OmniVoice, clone voice, ref_audio, ref_text, services/omnivoice_api.py, tts_manager.py, advisors.json."
---

# MADDIE OmniVoice Cloning

Use this skill when the task touches speech generation or voice cloning behavior.

## Primary files

- `app/tts_manager.py`
- `services/omnivoice_api.py`
- `scripts/omnivoice_worker.py`
- `data/advisors.json`
- `source/02_omnivoice/`

## Integration facts

- Relative `ref_audio` paths are resolved from the repo root on the app side.
- In service mode, the app uploads the reference WAV to the OmniVoice container instead of passing a host path.
- The checked-in sample voice is `source/02_omnivoice/sajat_hang_v1-8sec_24k_mono.wav`.
- `ref_text` can stay empty; OmniVoice can auto-transcribe when needed.

## Safe workflow

1. Preserve the HTTP service contract unless both app and service are updated together.
2. Validate clone mode with an existing WAV under `source/02_omnivoice/` before changing advisor defaults.
3. Keep generated audio under `app/generated/audio/` so podcast export keeps working unchanged.