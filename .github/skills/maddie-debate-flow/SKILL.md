---
name: maddie-debate-flow
description: "Use when: modifying the debate loop, advisor turn order, streaming events, stop/continue behavior, summaries, transcript export, or podcast generation. Keywords: debate_engine, EventSource, summary, transcript, podcast, continue."
---

# MADDIE Debate Flow

Use this skill when the task changes debate orchestration or exported artifacts.

## Primary files

- `app/debate_engine.py`
- `app/state.py`
- `app/main.py`
- `app/static/app.js`

## Behavioural facts

- Each advisor turn is sequential: text first, then audio, then explicit continue.
- `audio_ready` events unblock the next turn only after the client posts `/continue`.
- Final exports are written under `app/generated/debates/<debate_id>/`.
- `podcast.wav` is built from the locally saved audio files, so TTS changes must keep local WAV persistence intact.

## Safe workflow

1. If you change event types or payload fields, update both backend emitters and frontend handlers in the same change.
2. Preserve `waiting_for_client` and `waiting_event_id` semantics unless the playback protocol is intentionally redesigned.
3. Run a focused validation on debate creation and summary export after any orchestration change.