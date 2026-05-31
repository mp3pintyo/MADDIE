import asyncio
import json
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

    async def generate_turn(self, session, advisor, round_index, round_label, transcript_excerpt, language_context):
        selected = [self.advisors[x] for x in session.advisor_ids]
        message_events = self.message_events(session)
        own_history = self.format_message_list([evt for evt in message_events if evt.advisor_id == advisor.id][-2:], 'You have not spoken yet.')
        others_history = self.format_message_list([evt for evt in message_events if evt.advisor_id != advisor.id][-6:], 'No one else has spoken yet.')
        system = advisor.llm_prompt.strip() + f"\n\nYou are participating in a live, high-signal advisory roundtable. Stay in character. The meeting language is {language_context['prompt_name']}. Speak to the other advisors, not into the void. Maintain memory of what you already said and what the others already said. Be concise, direct, and conversational."
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

Recent transcript:
{transcript_excerpt or 'No previous messages yet.'}

Your task:
- Give your perspective from your persona.
- If others have already spoken, explicitly react to at least one named advisor and say whether you agree, disagree, refine, or question their point.
- Remember your own earlier position and stay consistent unless you explain why you changed your mind.
- Add one practical implication, risk, opportunity, or recommendation.
- If this is a closing turn, end with your most important takeaway.
- Use 1 to 4 sentences total. One-word answers are allowed if they are genuinely enough.
- Do not exceed 4 sentences.
- No bullet list, no markdown, no stage directions.

Return only your spoken contribution.'''
        messages = [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]
        try:
            return await self.llm.chat(
                messages=messages,
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens_per_turn,
            )
        except Exception:
            return await self.llm.chat(
                messages=messages,
                temperature=min(self.settings.temperature, 0.6),
                max_tokens=max(120, int(self.settings.max_tokens_per_turn * 0.65)),
            )

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
            audio_path = await self.tts.synthesize(
                text=tts_text,
                voice_mode=advisor.voice_mode,
                voice_instruct=advisor.voice_instruct,
                ref_audio=advisor.ref_audio,
                ref_text=advisor.ref_text,
                prefix=f"{session.id}-{advisor.id}",
                language=language_context['tts_code'],
                num_step=advisor.voice_num_step,
            )
            if audio_path:
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
                    transcript_excerpt = self.transcript_text(session).split("\n\n")[-8:]
                    turn = await self.generate_turn(session, advisor, round_index, 'zárókör' if session.stop_requested else f"{round_index}. kör", "\n\n".join(transcript_excerpt), language_context)
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
