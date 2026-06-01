import asyncio
import json
import random
import re
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from .config import LOGS_DIR
from .llm_client import LlamaCppClient
from .models import DebateEvent
from .tts_manager import SUPPORTED_NON_VERBAL_TAGS, apply_omnivoice_annotation_plan, split_tts_sentences


class DebateEngine:
    def __init__(self, settings, advisors, llm: LlamaCppClient, tts):
        self.settings = settings
        self.advisors = {a.id: a for a in advisors}
        self.llm = llm
        self.tts = tts
        self.tts_tasks = []

    async def emit(self, session, type_, content=None, advisor=None, meta=None, audio_url=None):
        event = DebateEvent(
            id=f"evt-{len(session.events)+1}",
            type=type_,
            timestamp=time.time(),
            advisor_id=advisor.id if advisor else None,
            advisor_name=advisor.name if advisor else None,
            content=content,
            audio_url=audio_url,
            meta=meta or {},
        )
        session.events.append(event)
        await session.queue.put(event)
        return event

    def transcript_text(self, session):
        lines = []
        for evt in session.events:
            if evt.type == 'message' and evt.content:
                lines.append(f"{evt.advisor_name}: {evt.content}")
        return "\n\n".join(lines)

    def roster_text(self, advisors):
        return "\n".join([f"- {a.name} ({a.title}): {a.description}" for a in advisors])

    def message_events(self, session):
        return [evt for evt in session.events if evt.type == 'message' and evt.content]

    def format_message_list(self, events, fallback: str):
        if not events:
            return fallback
        return "\n".join([f"- {evt.advisor_name}: {evt.content}" for evt in events])

    def conversation_timeline_text(self, session):
        timeline = []
        for evt in self.message_events(session):
            round_index = evt.meta.get('round') if isinstance(evt.meta, dict) else None
            round_prefix = f"[{round_index}. kör] " if round_index else ''
            timeline.append(f"{round_prefix}{evt.advisor_name}: {evt.content}")
        return "\n".join(timeline) if timeline else 'No previous messages yet.'

    def resolve_tts_voice_profile(self, session, advisor, reference_text: str):
        if advisor.voice_mode != 'instruct':
            return {
                'voice_mode': advisor.voice_mode,
                'voice_instruct': advisor.voice_instruct,
                'ref_audio': advisor.ref_audio,
                'ref_text': advisor.ref_text,
            }

        cached_reference = session.voice_references.get(advisor.id)
        if not cached_reference:
            return {
                'voice_mode': advisor.voice_mode,
                'voice_instruct': advisor.voice_instruct,
                'ref_audio': advisor.ref_audio,
                'ref_text': advisor.ref_text,
            }

        return {
            'voice_mode': 'clone',
            'voice_instruct': advisor.voice_instruct,
            'ref_audio': cached_reference.get('ref_audio', ''),
            'ref_text': cached_reference.get('ref_text') or reference_text,
        }

    def remember_generated_voice_reference(self, session, advisor, audio_path: str, reference_text: str):
        if advisor.voice_mode != 'instruct' or advisor.id in session.voice_references or not audio_path:
            return

        session.voice_references[advisor.id] = {
            'ref_audio': audio_path,
            'ref_text': (reference_text or '').strip(),
        }

    def choose_turn_profile(self, session, advisor, round_index, message_events):
        closing_turn = bool(session.stop_requested)
        others_spoke = any(evt.advisor_id != advisor.id for evt in message_events)

        if closing_turn:
            profiles = [
                {
                    'label': '1 short closing sentence',
                    'instruction': 'End with one decisive line if that is enough.',
                    'max_tokens': 48,
                    'temperature_delta': 0.05,
                },
                {
                    'label': '2 short closing sentences',
                    'instruction': 'Keep the close brief and pointed.',
                    'max_tokens': 72,
                    'temperature_delta': 0.05,
                },
                {
                    'label': '3 short closing sentences',
                    'instruction': 'Only add a second or third sentence if it genuinely adds something new.',
                    'max_tokens': 110,
                    'temperature_delta': 0.0,
                },
                {
                    'label': 'up to 4 short closing sentences',
                    'instruction': 'Use four sentences only if a compressed conclusion truly needs it.',
                    'max_tokens': 150,
                    'temperature_delta': -0.05,
                },
            ]
            weights = [30, 40, 20, 10]
        elif round_index == 1 and not others_spoke:
            profiles = [
                {
                    'label': '1 sharp opening sentence',
                    'instruction': 'Open with a single sharp angle if that is enough.',
                    'max_tokens': 48,
                    'temperature_delta': 0.1,
                },
                {
                    'label': '2 short opening sentences',
                    'instruction': 'State one core point and stop. Do not pre-explain everything.',
                    'max_tokens': 72,
                    'temperature_delta': 0.1,
                },
                {
                    'label': '3 short opening sentences',
                    'instruction': 'Use a third sentence only if you need one concrete implication.',
                    'max_tokens': 110,
                    'temperature_delta': 0.0,
                },
                {
                    'label': 'up to 4 short opening sentences',
                    'instruction': 'Only use four sentences if the topic truly needs a slightly fuller opening.',
                    'max_tokens': 150,
                    'temperature_delta': -0.05,
                },
            ]
            weights = [20, 45, 25, 10]
        else:
            profiles = [
                {
                    'label': '1 to 4 words or 1 short sentence',
                    'instruction': 'Treat this like live back-and-forth. A quick agreement, objection, question, or correction is ideal.',
                    'max_tokens': 48,
                    'temperature_delta': 0.2,
                },
                {
                    'label': '1 short sentence',
                    'instruction': 'Make one point cleanly and stop.',
                    'max_tokens': 60,
                    'temperature_delta': 0.15,
                },
                {
                    'label': '2 short sentences',
                    'instruction': 'Add a second sentence only if it sharpens or challenges the first.',
                    'max_tokens': 80,
                    'temperature_delta': 0.1,
                },
                {
                    'label': '3 short sentences',
                    'instruction': 'Keep it compact and spoken, never essay-like.',
                    'max_tokens': 110,
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'up to 4 short sentences',
                    'instruction': 'Use four sentences only when the exchange would otherwise become unclear.',
                    'max_tokens': 150,
                    'temperature_delta': 0.0,
                },
            ]
            weights = [20, 35, 25, 15, 5]

        return random.choices(profiles, weights=weights, k=1)[0]

    def choose_turn_speech_act(self, session, advisor, round_index, message_events):
        closing_turn = bool(session.stop_requested)
        others_spoke = any(evt.advisor_id != advisor.id for evt in message_events)

        if closing_turn:
            acts = [
                {
                    'label': 'decisive imperative takeaway',
                    'instruction': 'Give a brief recommendation, warning, or decision in imperative form.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'brief reaction then directive',
                    'instruction': 'React briefly to the debate, then tell the group what to do, test, compare, or stop doing.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'closing challenge question',
                    'instruction': 'Leave one sharp unresolved question on the table. End with a question mark.',
                    'temperature_delta': 0.1,
                },
                {
                    'label': 'plain decisive closing line',
                    'instruction': 'State one final line cleanly and stop. Do not turn it into a recap paragraph.',
                    'temperature_delta': 0.0,
                },
            ]
            weights = [35, 25, 15, 25]
        elif round_index == 1 and not others_spoke:
            acts = [
                {
                    'label': 'provocative opening question',
                    'instruction': 'Open by challenging the room with a direct question. End with a question mark.',
                    'temperature_delta': 0.1,
                },
                {
                    'label': 'agenda-setting imperative',
                    'instruction': 'Open by telling the room what to examine, compare, or avoid. Use imperative wording.',
                    'temperature_delta': 0.1,
                },
                {
                    'label': 'sharp opening claim',
                    'instruction': 'Open with one sharp claim, objection, or framing move.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'skeptical challenge',
                    'instruction': 'Open with a short skeptical pushback or challenge, not a polished explanation.',
                    'temperature_delta': 0.1,
                },
            ]
            weights = [28, 24, 28, 20]
        elif others_spoke:
            acts = [
                {
                    'label': 'direct question to another advisor',
                    'instruction': 'Ask one advisor a short direct question, request for evidence, or challenge. End with a question mark.',
                    'temperature_delta': 0.12,
                },
                {
                    'label': 'brief reaction then question',
                    'instruction': 'Start with a quick reaction, then ask a follow-up question.',
                    'temperature_delta': 0.1,
                },
                {
                    'label': 'imperative push',
                    'instruction': 'Use a short imperative or suggestion to push the discussion somewhere: test, compare, drop, verify, or decide something.',
                    'temperature_delta': 0.08,
                },
                {
                    'label': 'brief reaction then directive',
                    'instruction': 'Start with a quick reaction, then tell the group or one advisor what to test, compare, or decide next.',
                    'temperature_delta': 0.08,
                },
                {
                    'label': 'interrupting correction or objection',
                    'instruction': 'Use a short correction, objection, or skeptical fragment. Do not over-explain.',
                    'temperature_delta': 0.08,
                },
                {
                    'label': 'concise plain statement',
                    'instruction': 'A plain statement is allowed, but keep it spoken, interruptible, and clearly in response to the room.',
                    'temperature_delta': 0.0,
                },
            ]
            weights = [24, 20, 20, 16, 12, 8]
        else:
            acts = [
                {
                    'label': 'sharp claim',
                    'instruction': 'Make one strong spoken point and stop.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'provocative question',
                    'instruction': 'Frame your point as a direct question to the room. End with a question mark.',
                    'temperature_delta': 0.1,
                },
                {
                    'label': 'imperative suggestion',
                    'instruction': 'Push one concrete move in imperative form.',
                    'temperature_delta': 0.08,
                },
            ]
            weights = [40, 35, 25]

        return random.choices(acts, weights=weights, k=1)[0]

    def immediate_previous_turn(self, advisor, message_events):
        for evt in reversed(message_events):
            if evt.advisor_id == advisor.id:
                continue
            content = re.sub(r'\s+', ' ', (evt.content or '').strip())
            return {
                'advisor_name': evt.advisor_name or 'Another advisor',
                'content': content,
                'asked_question': bool(re.search(r'[?？]', content)),
            }
        return None

    def choose_turn_reply_mode(self, previous_turn):
        if not previous_turn:
            return {
                'label': 'opening move',
                'instruction': 'You are opening the discussion. Frame one sharp angle and invite a response.',
                'temperature_delta': 0.05,
            }

        if previous_turn['asked_question']:
            modes = [
                {
                    'label': 'direct answer',
                    'instruction': 'Answer the previous advisor\'s actual question first. Do not dodge it.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'answer then challenge',
                    'instruction': 'Give a direct answer first, then briefly challenge the assumption behind the question.',
                    'temperature_delta': 0.08,
                },
                {
                    'label': 'answer then follow-up question',
                    'instruction': 'Answer briefly, then ask one sharper follow-up question that moves the debate forward.',
                    'temperature_delta': 0.1,
                },
                {
                    'label': 'partial answer with consequence',
                    'instruction': 'Answer the question, then state the most important consequence or trade-off.',
                    'temperature_delta': 0.06,
                },
            ]
            weights = [40, 20, 25, 15]
        else:
            modes = [
                {
                    'label': 'direct reply to the last claim',
                    'instruction': 'Respond to the previous advisor\'s actual claim, not just to the general topic.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'agreement or disagreement with reason',
                    'instruction': 'State clearly whether you agree or disagree with the previous turn, then give the reason.',
                    'temperature_delta': 0.06,
                },
                {
                    'label': 'build on one concrete point',
                    'instruction': 'Pick one concrete point from the previous turn and extend it forward.',
                    'temperature_delta': 0.05,
                },
                {
                    'label': 'correction or rebuttal',
                    'instruction': 'Correct or rebut one specific part of the previous turn. Do not restart the topic from zero.',
                    'temperature_delta': 0.08,
                },
                {
                    'label': 'acknowledge then redirect',
                    'instruction': 'You may redirect, but only after explicitly acknowledging what the previous speaker just said.',
                    'temperature_delta': 0.07,
                },
            ]
            weights = [34, 24, 18, 16, 8]

        return random.choices(modes, weights=weights, k=1)[0]

    def finalize_turn_text(self, text: str) -> str:
        cleaned = re.sub(r'\s+', ' ', (text or '').strip())
        cleaned = re.sub(r'^[-*•]+\s*', '', cleaned)
        if re.match(r'^(wait|ok|okay|actually|let\'s|lets|here\'s|heres|i\'d|id|try this|better|more)\b', cleaned, re.IGNORECASE):
            quoted_candidates = [candidate.strip() for candidate in re.findall(r'["“](.+?)["”]', cleaned) if candidate.strip()]
            if quoted_candidates:
                cleaned = max(quoted_candidates, key=len)
        cleaned = cleaned.strip('"“”\' ')
        if not cleaned:
            return ''

        trimmed_sentences = []
        for sentence, separator in split_tts_sentences(cleaned):
            chunk = re.sub(r'^[-*•]+\s*', '', f'{sentence}{separator}'.strip())
            if not chunk:
                continue
            trimmed_sentences.append(chunk)
            if len(trimmed_sentences) >= 4:
                break

        if trimmed_sentences:
            return ' '.join(trimmed_sentences).strip()
        return cleaned

    def detect_topic_language(self, text: str):
        if re.search(r'[\u4e00-\u9fff]', text):
            return {'prompt_name': 'Chinese', 'summary_name': 'Chinese', 'tts_code': 'zh'}

        lowered = f" {text.casefold()} "
        hungarian_markers = [' és ', ' hogy ', ' nem ', ' az ', ' egy ', ' mi ', ' mit ', ' miért ', ' legyen ', ' fontos ']
        if any(ch in lowered for ch in 'áéíóöőúüű') or any(marker in lowered for marker in hungarian_markers):
            return {'prompt_name': 'Hungarian', 'summary_name': 'Hungarian', 'tts_code': 'hu'}

        english_markers = [' the ', ' and ', ' why ', ' what ', ' should ', ' important ', ' is ', ' are ', ' in ']
        if any(marker in lowered for marker in english_markers):
            return {'prompt_name': 'English', 'summary_name': 'English', 'tts_code': 'en'}

        fallback_code = self.settings.omnivoice_language or 'hu'
        fallback_name = {'hu': 'Hungarian', 'en': 'English', 'zh': 'Chinese'}.get(fallback_code, 'the same language as the meeting topic')
        return {'prompt_name': fallback_name, 'summary_name': fallback_name, 'tts_code': fallback_code}

    def format_error(self, exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return message
        return f'{exc.__class__.__name__} (nincs részletes hibaüzenet)'

    def write_error_log(self, session, exc: Exception) -> Path:
        log_path = LOGS_DIR / f'debate-{session.id}.log'
        details = traceback.format_exc()
        log_path.write_text(details, encoding='utf-8')
        return log_path

    def podcast_concat_entry(self, relative_audio_path: str) -> str:
        audio_path = (Path(__file__).resolve().parent.parent / relative_audio_path).resolve()
        return f"file '{audio_path.as_posix()}'"

    def export_podcast_audio(self, concat_list: Path, podcast_path: Path):
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path:
            raise FileNotFoundError('ffmpeg nincs a PATH-ban.')

        proc = subprocess.run(
            [
                ffmpeg_path,
                '-y',
                '-f',
                'concat',
                '-safe',
                '0',
                '-i',
                str(concat_list),
                '-c',
                'copy',
                str(podcast_path),
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            check=False,
        )
        if proc.returncode != 0:
            stderr_text = (proc.stderr or '').strip()
            stderr_line = stderr_text.splitlines()[-1] if stderr_text else f'ffmpeg exit code {proc.returncode}'
            raise RuntimeError(stderr_line)

    async def generate_turn(self, session, advisor, round_index, round_label, conversation_history, language_context):
        selected = [self.advisors[x] for x in session.advisor_ids]
        message_events = self.message_events(session)
        own_history = self.format_message_list([evt for evt in message_events if evt.advisor_id == advisor.id], 'You have not spoken yet.')
        others_history = self.format_message_list([evt for evt in message_events if evt.advisor_id != advisor.id], 'No one else has spoken yet.')
        previous_turn = self.immediate_previous_turn(advisor, message_events)
        turn_profile = self.choose_turn_profile(session, advisor, round_index, message_events)
        turn_speech_act = self.choose_turn_speech_act(session, advisor, round_index, message_events)
        turn_reply_mode = self.choose_turn_reply_mode(previous_turn)
        turn_temperature = min(1.1, max(0.35, self.settings.temperature + turn_profile['temperature_delta'] + turn_speech_act['temperature_delta'] + turn_reply_mode['temperature_delta']))
        turn_max_tokens = min(self.settings.max_tokens_per_turn, turn_profile['max_tokens'])
        system = advisor.llm_prompt.strip() + f"\n\nYou are participating in a live, interruptible advisory debate. Stay in character. The meeting language is {language_context['prompt_name']}. Speak to the other advisors, not into the void. Your persona is a lens for analyzing the literal meeting topic, not a reason to replace the topic with your profession or favorite jargon. Stay grounded in the real-world situation named in the topic. Do not recast the discussion as a software product, AI model, startup, content strategy, artwork, UX problem, or abstract thought experiment unless the topic itself is actually about that. Never invent an imaginary 'our model', 'our product', 'our users', 'our roadmap', 'our audience', or similar workplace framing unless that framing is explicitly present in the topic or conversation. If you need technical or professional language, tie it to the real systems inside the scenario itself. Maintain memory of what you already said and what the others already said. Use the full conversation record below to remember who said what across the entire debate, not just the last exchange. Sound like a real person in a fast back-and-forth conversation, not a polished panelist. It is normal to answer with one word, a fragment, a short question, a short command, or one sharp sentence when that is enough. Do not default to polished declarative statements. Questions, imperatives, objections, fragments, and quick follow-ups should be common across the debate. When another advisor has just spoken, treat your turn as a reply to that exact line, not as a separate mini-monologue."
        user = f'''Meeting topic:
{session.topic}

Participants:
{self.roster_text(selected)}

Current round:
{round_label}

Support scores so far:
{json.dumps(session.scores, ensure_ascii=False)}

Your earlier statements:
{own_history}

Other advisors already said:
{others_history}

Immediate turn you are replying to:
{f"{previous_turn['advisor_name']}: {previous_turn['content']}" if previous_turn else 'You are opening the conversation, so there is no previous turn yet.'}

Full conversation so far (entire discussion, oldest to newest):
{conversation_history or 'No previous messages yet.'}

Your task:
- Respond like natural spoken conversation, not a mini-essay.
- Stay inside the literal topic. Your role is only a perspective on the topic, not a new topic.
- Keep the discussion about the actual people, place, institutions, material conditions, and consequences implied by the meeting topic.
- In most turns, mention or clearly imply at least one concrete element of the scenario: affected people, water, food, disease, migration, conflict, infrastructure, government response, time pressure, or another real consequence relevant to this topic.
- Avoid imaginary workplace framing. Do not talk about a fictional product, roadmap, feature scope, user segment, audience strategy, or model-training task unless the topic literally calls for it.
- If you use a metric, threshold, system, or trade-off, make it a real one from the scenario itself.
- Start from the immediate previous turn above whenever one exists.
- If the previous speaker asked a question, answer that question before anything else.
- Explicitly react to one concrete claim, question, risk, metric, or assumption from the immediate previous turn.
- Also stay consistent with the full conversation so far: remember who said what, what you already argued, and what has already been answered.
- If another advisor drifts into generic role jargon or an unrelated metaphor, pull the discussion back to the literal scenario.
- Any metric, recommendation, philosophical point, or aesthetic point must still connect back to the real situation in the topic.
- Do not branch into a parallel monologue or restart from the topic statement.
- Never narrate your drafting process or style adjustment. Do not output lines like 'wait', 'let\'s make it more...', 'here\'s a version', or quotes around your answer.
- Make one natural conversational move: agree, disagree, refine, challenge, ask, warn, conclude, redirect, or push for action.
- Usually cover only one idea and stop.
- Direct address is good when natural: yes, no, wait, exactly, then answer them.
- Stay consistent with your earlier stance unless you briefly explain a change.
- If this is a closing turn, focus on your clearest final takeaway instead of reopening the whole debate.
- Plain declarative statements should be the exception, not the default.
- Prefer sentence moods that feel alive in conversation: question, imperative, interruption, correction, or challenge.
- Reply mode for this reply: {turn_reply_mode['label']}.
- {turn_reply_mode['instruction']}
- Turn shape for this reply: {turn_profile['label']}.
- {turn_profile['instruction']}
- Primary speech act for this reply: {turn_speech_act['label']}.
- {turn_speech_act['instruction']}
- One-word answers are valid when enough.
- Hard cap: 4 sentences.
- No bullet list, no markdown, no stage directions.

Return only your spoken contribution.'''
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]
        try:
            response = await self.llm.chat(
                messages=messages,
                temperature=turn_temperature,
                max_tokens=turn_max_tokens,
            )
            return self.finalize_turn_text(response)
        except Exception:
            response = await self.llm.chat(
                messages=messages,
                temperature=min(turn_temperature, 0.65),
                max_tokens=max(48, int(turn_max_tokens * 0.85)),
            )
            return self.finalize_turn_text(response)

    def should_annotate_tts_text(self, language_context) -> bool:
        if not self.tts or not self.settings.omnivoice_llm_annotation_enabled:
            return False
        if self.settings.omnivoice_markup_enabled:
            return True
        return self.settings.omnivoice_english_pronunciation_enabled and language_context['tts_code'] == 'en'

    async def annotate_tts_text(self, text: str, language_context) -> str:
        stripped = (text or '').strip()
        if not stripped or not self.should_annotate_tts_text(language_context):
            return stripped

        sentence_lines = []
        for index, (sentence, _) in enumerate(split_tts_sentences(stripped)):
            sentence_lines.append(f'{index}: {sentence.strip() or "[empty]"}')

        allow_pronunciation = self.settings.omnivoice_english_pronunciation_enabled and language_context['tts_code'] == 'en'
        system = 'You create structured OmniVoice delivery annotations. Return only valid JSON.'
        user = f'''Language code: {language_context['tts_code']}
Language name: {language_context['prompt_name']}

Original text:
{stripped}

Sentence list (0-based):
{chr(10).join(sentence_lines)}

Supported non-verbal tags:
{', '.join(f'[{tag}]' for tag in SUPPORTED_NON_VERBAL_TAGS)}

Return a JSON object with this exact schema:
{{
  "sentence_tags": [
    {{"sentence_index": 0, "tag": "question-en", "reason": "short reason"}}
  ],
  "pronunciations": [
    {{"text": "AI", "arpabet": "EY1 AY1", "occurrence": 1, "reason": "short reason"}}
  ]
}}

Rules:
- Do not rewrite, summarize, translate, or paraphrase the original text.
- Use sentence_tags only when the delivery clearly benefits.
- Use at most 2 non-verbal tags total.
- Only use supported tags.
- If no non-verbal tag is needed, return an empty sentence_tags array.
- {'Pronunciations are allowed because the speech is English. Use them only for ambiguous or technical words.' if allow_pronunciation else 'Pronunciations are disabled here. Return an empty pronunciations array.'}
- For pronunciations, text must be an exact substring from the original text.
- occurrence is 1-based and counts whole-word matches in reading order.
- If nothing needs annotation, return both arrays empty.
'''

        try:
            annotation_plan = await self.llm.chat_json(
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                temperature=0.0,
                max_tokens=260,
            )
        except Exception:
            return stripped

        if not isinstance(annotation_plan, dict):
            return stripped

        annotated = apply_omnivoice_annotation_plan(
            stripped,
            language_context['tts_code'],
            sentence_tags=annotation_plan.get('sentence_tags'),
            pronunciations=annotation_plan.get('pronunciations'),
        )
        return annotated or stripped

    async def synthesize_message_audio(self, session, event, language_context):
        if not self.tts:
            return None
        advisor = self.advisors[event.advisor_id]
        try:
            tts_text = await self.annotate_tts_text(event.content or '', language_context)
            reference_text = (event.content or '').strip() or tts_text.strip()
            voice_profile = self.resolve_tts_voice_profile(session, advisor, reference_text)
            audio_path = await self.tts.synthesize(
                text=tts_text,
                voice_mode=voice_profile['voice_mode'],
                voice_instruct=voice_profile['voice_instruct'],
                ref_audio=voice_profile['ref_audio'],
                ref_text=voice_profile['ref_text'],
                prefix=f"{session.id}-{advisor.id}",
                language=language_context['tts_code'],
                num_step=advisor.voice_num_step,
            )
            if audio_path:
                self.remember_generated_voice_reference(session, advisor, audio_path, reference_text)
                rel = Path(audio_path).resolve().relative_to(Path(__file__).resolve().parent.parent)
                return await self.emit(session, 'audio_ready', advisor=advisor, meta={'event_id': event.id}, audio_url='/' + str(rel).replace('\\', '/'))
        except Exception as exc:
            await self.emit(session, 'warning', content=f"OmniVoice hiba {advisor.name} hangjánál: {exc}")
        return None

    async def wait_for_client_continue(self, session, event_id: str | None):
        session.waiting_for_client = True
        session.waiting_event_id = event_id
        session.continue_event.clear()
        await self.emit(session, 'awaiting_continue', content='Várakozás a lejátszás végére…', meta={'event_id': event_id})
        await session.continue_event.wait()
        session.waiting_for_client = False
        session.waiting_event_id = None

    async def summarize(self, session, language_context):
        selected = [self.advisors[x] for x in session.advisor_ids]
        transcript = self.transcript_text(session)
        system = 'You are an expert discussion synthesizer. Output only valid JSON.'
        user = f'''Topic: {session.topic}

Participants:
{self.roster_text(selected)}

Transcript:
{transcript}

Create a JSON object with this exact schema:
{{
  "advisor_summaries": [
    {{"advisor": "name", "summary": "short paragraph", "strongest_ideas": ["idea1", "idea2"]}}
  ],
  "overview": "1 short paragraph",
  "consensus": ["..."],
  "disagreements": ["..."],
  "final_evaluations": ["..."],
  "recommended_next_steps": ["..."]
}}

Requirements:
- Write in {language_context['summary_name']}.
- Be concrete, not generic.
- Capture what each advisor actually emphasized.
- Keep strongest_ideas concise but specific.
- In final_evaluations mention which viewpoints were most decision-useful and why.'''
        return await self.llm.chat_json(
            messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            temperature=self.settings.summary_temperature,
            max_tokens=1200,
        )

    async def write_artifacts(self, session, summary):
        out_dir = session.ensure_dir()
        export_warnings = []
        transcript_path = out_dir / 'transcript.txt'
        transcript_path.write_text(self.transcript_text(session), encoding='utf-8')
        summary_path = out_dir / 'summary.md'
        md = [f"# {self.settings.meeting_name}", '', f"Téma: {session.topic}", '']
        md.append('## Áttekintés')
        md.append(summary.get('overview', ''))
        md.append('')
        md.append('## Tanácsadónkénti összegzés')
        for item in summary.get('advisor_summaries', []):
            md.append(f"### {item.get('advisor', 'Ismeretlen')}")
            md.append(item.get('summary', ''))
            strongest = item.get('strongest_ideas', [])
            if strongest:
                md.append('Erős gondolatok:')
                for idea in strongest:
                    md.append(f"- {idea}")
            md.append('')
        for key, title in [('consensus', 'Konszenzus'), ('disagreements', 'Nézeteltérések'), ('final_evaluations', 'Végső értékelések'), ('recommended_next_steps', 'Javasolt lépések')]:
            md.append(f"## {title}")
            for item in summary.get(key, []):
                md.append(f"- {item}")
            md.append('')
        summary_path.write_text("\n".join(md), encoding='utf-8')

        wavs = []
        for evt in session.events:
            if evt.type == 'audio_ready' and evt.audio_url:
                wavs.append(evt.audio_url.lstrip('/'))
        podcast_path = None
        if wavs:
            concat_list = out_dir / 'podcast_concat.txt'
            concat_list.write_text("\n".join([self.podcast_concat_entry(w) for w in wavs]), encoding='utf-8')
            podcast_path = out_dir / 'podcast.wav'
            try:
                await asyncio.to_thread(self.export_podcast_audio, concat_list, podcast_path)
            except Exception as exc:
                export_warnings.append(f'Podcast export hiba: {self.format_error(exc)}')
                if podcast_path.exists():
                    podcast_path.unlink()
                podcast_path = None
        session.final_payload = {
            'summary': summary,
            'transcript_url': f"/app/generated/debates/{session.id}/transcript.txt",
            'summary_url': f"/app/generated/debates/{session.id}/summary.md",
            'podcast_url': f"/app/generated/debates/{session.id}/podcast.wav" if podcast_path and podcast_path.exists() else None,
            'scores': session.scores,
        }
        return export_warnings

    async def run(self, session):
        session.status = 'running'
        self.tts_tasks = []
        try:
            await self.emit(session, 'status', content='A vita elindult.')
            selected = [self.advisors[x] for x in session.advisor_ids if x in self.advisors]
            language_context = self.detect_topic_language(session.topic)
            round_index = 0
            stop_now = False
            while True:
                round_index += 1
                for advisor in selected:
                    if session.stop_requested:
                        if session.stop_budget_remaining is None:
                            session.stop_budget_remaining = self.settings.closure_extra_turns
                        if session.stop_budget_remaining <= 0:
                            stop_now = True
                            break
                    conversation_history = self.conversation_timeline_text(session)
                    turn = await self.generate_turn(session, advisor, round_index, 'zárókör' if session.stop_requested else f"{round_index}. kör", conversation_history, language_context)
                    event = await self.emit(session, 'message', content=turn, advisor=advisor, meta={'round': round_index, 'closing': bool(session.stop_requested)})
                    audio_event = await self.synthesize_message_audio(session, event, language_context)
                    if audio_event:
                        await self.wait_for_client_continue(session, audio_event.meta.get('event_id'))
                    elif self.tts:
                        await self.emit(session, 'warning', content=f'Nem készült hang {advisor.name} megszólalásához, ezért a vita továbblép.')
                    if session.stop_requested and session.stop_budget_remaining is not None:
                        session.stop_budget_remaining -= 1
                if stop_now:
                    break
            await self.emit(session, 'status', content='Összegzés készül...')
            summary = await self.summarize(session, language_context)
            export_warnings = await self.write_artifacts(session, summary)
            for warning in export_warnings:
                await self.emit(session, 'warning', content=warning)
            await self.emit(session, 'summary', content=summary.get('overview', ''), meta={**session.final_payload, 'summary': summary})
            session.status = 'completed'
            await self.emit(session, 'complete', content='A vita lezárult.', meta=session.final_payload)
        except Exception as exc:
            error_message = self.format_error(exc)
            error_log_path = self.write_error_log(session, exc)
            session.status = 'failed'
            session.final_payload = {'error': error_message, 'scores': session.scores, 'error_log': str(error_log_path.relative_to(Path(__file__).resolve().parent.parent))}
            await self.emit(session, 'warning', content=f'Hiba történt a vita közben: {error_message}. Részletek: {session.final_payload["error_log"]}')
            await self.emit(session, 'complete', content='A vita hibával leállt.', meta=session.final_payload)
