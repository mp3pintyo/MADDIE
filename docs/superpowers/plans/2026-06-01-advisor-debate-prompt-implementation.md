# Advisor Debate Prompt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make advisor debates stay on the literal topic while preserving strong advisor personality and preventing leaked meta-text from appearing in visible replies.

**Architecture:** Tighten the shared debate prompt in `app/debate_engine.py`, simplify turn-level instructions, and harden text-cleaning/reasoning-rescue helpers so internal constraint language is filtered before it reaches the debate transcript. Add focused regression tests around helper behavior and prompt assembly rather than model-dependent integration tests.

**Tech Stack:** Python 3.12, FastAPI app code, standard-library `unittest`

---

### Task 1: Add helper-level regression tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_debate_engine.py`
- Create: `tests/test_llm_client.py`
- Test: `tests/test_debate_engine.py`
- Test: `tests/test_llm_client.py`

- [ ] **Step 1: Write the failing tests**

```python
from app.debate_engine import DebateEngine
from app.llm_client import _extract_answer_from_reasoning


def test_finalize_turn_text_strips_constraint_meta_prefix():
    engine = DebateEngine(None, [], None, None)
    cleaned = engine.finalize_turn_text(
        "Review against constraints: all constraints met. No, snails are not an everyday staple in France."
    )
    assert cleaned == "No, snails are not an everyday staple in France."


def test_finalize_turn_text_keeps_spoken_sentence_after_drafting_prefix():
    engine = DebateEngine(None, [], None, None)
    cleaned = engine.finalize_turn_text(
        "Here's a version: French people do eat snails, but mostly as a special dish, not an everyday habit."
    )
    assert cleaned == "French people do eat snails, but mostly as a special dish, not an everyday habit."


def test_extract_answer_from_reasoning_rejects_constraint_checklist():
    reasoning = "1. Check constraints\\n2. Constraints met\\n3. Final output generation\\n"
    assert _extract_answer_from_reasoning(reasoning) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_debate_engine tests.test_llm_client -v`
Expected: FAIL because the cleaner currently leaves meta prefixes or the reasoning helper still extracts invalid fallback text.

- [ ] **Step 3: Write minimal implementation**

```python
# app/debate_engine.py
# Extend finalize_turn_text() with meta-prefix stripping and spoken-sentence salvage.

# app/llm_client.py
# Tighten reasoning extraction so meta-only candidates are rejected.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_debate_engine tests.test_llm_client -v`
Expected: PASS

### Task 2: Refine the debate prompt assembly

**Files:**
- Modify: `app/debate_engine.py`
- Test: `tests/test_debate_engine.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_turn_messages_include_topic_first_guardrails():
    engine = DebateEngine(settings_stub, [advisor_stub], None, None)
    system, user = engine.build_turn_messages_for_test(session_stub, advisor_stub, 1, "1. kör", "No previous messages yet.", language_context)

    assert "The literal meeting topic comes first." in system
    assert "Your persona is a way of seeing the topic" in system
    assert "Do not output meta-text" in user
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_debate_engine -v`
Expected: FAIL because no helper exists yet and the current prompt wording does not match.

- [ ] **Step 3: Write minimal implementation**

```python
# app/debate_engine.py
# Extract prompt assembly into a helper that returns system/user strings.
# Use shorter, topic-first guardrails and simpler turn rules.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_debate_engine -v`
Expected: PASS

### Task 3: Verify full targeted suite stays green

**Files:**
- Modify: `app/debate_engine.py`
- Modify: `app/llm_client.py`
- Test: `tests/test_debate_engine.py`
- Test: `tests/test_llm_client.py`

- [ ] **Step 1: Run the focused test suite**

Run: `python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 2: Review for unintended prompt regressions**

```python
# Confirm:
# - advisor persona remains in the system prompt
# - topic-first rule is explicit
# - turn instructions are shorter and less self-conscious
# - meta leakage patterns are filtered
```

- [ ] **Step 3: Update plan status in final report**

```text
Report the tested commands, passing scope, and any unverified runtime risks such as live LLM behavior not exercised in unit tests.
```
