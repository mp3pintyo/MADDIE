# Advisor Debate Prompt Design

## Goal

The debate should feel like an intelligent conversation about the literal topic the user entered.

Each advisor should keep a strong character, but that character should appear mainly through:
- point of view
- priorities
- vocabulary and rhythm

The character should not replace the topic, hijack the discussion into role-jargon, or invent a fictional workplace frame.

## Problem Statement

The current debate behavior has two main failure modes:

1. Advisors over-index on persona and drift into generic profession-shaped monologues instead of discussing the literal topic.
2. Internal reasoning or constraint-check text can leak into the visible reply, producing lines like "Review against constraints" inside the debate.

These failures make the conversation feel unnatural, repetitive, and disconnected from the user prompt.

## Desired Outcome

After the change:

- advisors stay on the literal topic
- advisors still sound distinct from each other
- replies feel conversational rather than essay-like
- replies react to the immediate previous turn
- meta-instructions, constraint checks, and drafting artifacts do not appear in visible output

## Recommended Approach

Use a combined `prompt + turn-guard + output-cleaning` design.

This keeps the existing advisor architecture, but strengthens the behavioral boundaries around it.

## Design

### 1. Shared system behavior becomes topic-first

The shared system prompt in `app/debate_engine.py` should explicitly establish this hierarchy:

1. The literal topic is primary.
2. The advisor persona is a lens on the topic.
3. Style is allowed, but topic replacement is not.

The shared prompt should strongly forbid:

- turning a simple topic into a fake product, roadmap, model, audience, or strategy exercise
- answering with internal review language
- drifting into abstract metaphor when the topic is concrete

The shared prompt should also explicitly encourage:

- direct answers
- concrete reactions to what was just said
- natural short spoken turns
- disagreement, clarification, and follow-up questions when useful

### 2. Advisor persona remains strong but scoped

The per-advisor `llm_prompt` fields in `data/advisors.json` should remain short and characterful.

Their job is to define:

- what the advisor tends to notice
- what kind of reasoning they prefer
- what kind of language flavor they bring

Their job is not to define a whole replacement scenario.

Implementation implication:

- keep advisor prompts concise
- rely on the shared system layer to enforce topic discipline
- do not over-expand each advisor into a long roleplay block

### 3. Turn instructions should be simplified

The current turn prompt contains many good ideas, but too many overlapping micro-rules can cause the model to latch onto instruction wording instead of the topic.

Replace the current long turn rule block with a smaller set of hard rules:

- reply to the previous speaker when one exists
- if the previous speaker asked a question, answer it first
- stay on the literal topic
- make one main point per turn
- keep the turn natural and spoken
- do not output meta-text, drafting text, or constraint-check language

The randomized turn-shape and speech-act machinery can stay, but its wording should be simpler and less self-conscious.

It should shape delivery without sounding like an acting exercise.

### 4. Output cleaning should aggressively strip meta leakage

`finalize_turn_text()` should be strengthened to detect and remove common internal-generation artifacts such as:

- "check constraints"
- "constraints met"
- "review against constraints"
- "final output"
- "draft"
- "here's a version"

If the model returns a mixed answer where meta-text appears before a plausible spoken sentence, the cleaner should keep the spoken sentence.

If the whole output is unrecoverably meta, return an empty string and let generation retry logic handle recovery in a future improvement.

### 5. Reasoning rescue should prefer safe visible output

`app/llm_client.py` already contains reasoning-rescue logic for models that hide visible output.

That logic should be tightened so that extracted fallback text is rejected if it still looks like internal process language.

The client should prefer:

- visible assistant content
- quoted final-answer candidates that look like real spoken output

The client should reject:

- checklist language
- self-instruction
- "the user wants me..." style text
- constraint validation fragments

### 6. Testing strategy

Add focused tests for the new behavior instead of trying to fully simulate the model.

Test targets:

- `finalize_turn_text()` removes leaked meta phrases
- `finalize_turn_text()` preserves a valid spoken sentence after leaked meta prefixes
- reasoning extraction helpers in `app/llm_client.py` reject internal constraint text
- prompt assembly keeps the topic-first rules present

Where possible, tests should use deterministic helper-level inputs rather than model calls.

## Scope

In scope:

- `app/debate_engine.py`
- `app/llm_client.py`
- tests for prompt cleaning and reasoning extraction

Out of scope:

- changing the UI
- changing the advisor roster model
- changing TTS behavior except where it depends on cleaned reply text
- redesigning the summary pipeline

## Risks

1. Over-constraining the prompt could flatten advisor personality.
2. Aggressive output cleaning could accidentally trim valid content.
3. Simplifying the turn rules too much could reduce conversational energy.

## Risk Mitigation

- keep persona wording strong in advisor prompts
- constrain topic drift at the shared layer rather than by weakening personas
- add unit tests around text cleaning edge cases
- preserve the existing turn-shape randomness, but simplify its instructions

## Implementation Outline

1. Refactor the shared prompt text in `app/debate_engine.py`.
2. Simplify the user turn-instruction block in `app/debate_engine.py`.
3. Harden `finalize_turn_text()` against leaked meta output.
4. Harden reasoning candidate extraction in `app/llm_client.py`.
5. Add regression tests for meta leakage and topic-first prompt assembly.

## Spec Review

This spec is intentionally narrow.

It does not attempt a full persona-system redesign.
It aims to produce a noticeably more natural debate with minimal structural change to the current app.
