import asyncio
import json
import time
from pathlib import Path
from .llm_client import LlamaCppClient
from .models import DebateEvent


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

    async def generate_turn(self, session, advisor, round_index, round_label, transcript_excerpt):
        selected = [self.advisors[x] for x in session.advisor_ids]
        system = advisor.llm_prompt.strip() + "\n\nYou are participating in a constructive, high-signal advisory roundtable. Stay in character. Reply in the same language as the user's topic. Use short, information-dense paragraphs. Avoid filler and repetition. Reference earlier points when useful, but do not quote excessively."
        user = f'''Meeting topic:
{session.topic}

Participants:
{self.roster_text(selected)}

Current round:
{round_label}

Support scores so far:
{json.dumps(session.scores, ensure_ascii=False)}

Recent transcript:
{transcript_excerpt or 'No previous messages yet.'}

Your task:
- Give your perspective from your persona.
- React to 1-2 concrete ideas from others if there are prior messages.
- Add one practical implication, risk, opportunity, or recommendation.
- If this is a closing turn, end with your most important takeaway.
- Prefer 2-4 short paragraphs.

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

    async def synthesize_message_audio(self, session, event):
        if not self.tts:
            return None
        advisor = self.advisors[event.advisor_id]
        try:
            audio_path = await self.tts.synthesize(
                text=event.content or '',
                voice_mode=advisor.voice_mode,
                voice_instruct=advisor.voice_instruct,
                ref_audio=advisor.ref_audio,
                ref_text=advisor.ref_text,
                prefix=f"{session.id}-{advisor.id}",
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

    async def summarize(self, session):
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
- Write in Hungarian.
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
            concat_list.write_text("\n".join([f"file '{Path(__file__).resolve().parent.parent / w}'" for w in wavs]), encoding='utf-8')
            podcast_path = out_dir / 'podcast.wav'
            proc = await asyncio.create_subprocess_exec('ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_list), '-c', 'copy', str(podcast_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
        session.final_payload = {
            'summary': summary,
            'transcript_url': f"/app/generated/debates/{session.id}/transcript.txt",
            'summary_url': f"/app/generated/debates/{session.id}/summary.md",
            'podcast_url': f"/app/generated/debates/{session.id}/podcast.wav" if podcast_path and podcast_path.exists() else None,
            'scores': session.scores,
        }

    async def run(self, session):
        session.status = 'running'
        self.tts_tasks = []
        try:
            await self.emit(session, 'status', content='A vita elindult.')
            selected = [self.advisors[x] for x in session.advisor_ids if x in self.advisors]
            total_rounds = max(1, self.settings.opening_rounds)
            stop_now = False
            for round_index in range(total_rounds):
                for advisor in selected:
                    if session.stop_requested:
                        if session.stop_budget_remaining is None:
                            session.stop_budget_remaining = self.settings.closure_extra_turns
                        if session.stop_budget_remaining <= 0:
                            stop_now = True
                            break
                    transcript_excerpt = self.transcript_text(session).split("\n\n")[-8:]
                    turn = await self.generate_turn(session, advisor, round_index + 1, 'zárókör' if session.stop_requested else f"{round_index + 1}. kör", "\n\n".join(transcript_excerpt))
                    event = await self.emit(session, 'message', content=turn, advisor=advisor, meta={'round': round_index + 1, 'closing': bool(session.stop_requested)})
                    audio_event = await self.synthesize_message_audio(session, event)
                    if audio_event:
                        await self.wait_for_client_continue(session, audio_event.meta.get('event_id'))
                    elif self.tts:
                        await self.emit(session, 'warning', content=f'Nem készült hang {advisor.name} megszólalásához, ezért a vita továbblép.')
                    if session.stop_requested and session.stop_budget_remaining is not None:
                        session.stop_budget_remaining -= 1
                if stop_now:
                    break
            await self.emit(session, 'status', content='Összegzés készül...')
            summary = await self.summarize(session)
            await self.write_artifacts(session, summary)
            await self.emit(session, 'summary', content=summary.get('overview', ''), meta={**session.final_payload, 'summary': summary})
            session.status = 'completed'
            await self.emit(session, 'complete', content='A vita lezárult.', meta=session.final_payload)
        except Exception as exc:
            session.status = 'failed'
            session.final_payload = {'error': str(exc), 'scores': session.scores}
            await self.emit(session, 'warning', content=f'Hiba történt a vita közben: {exc}')
            await self.emit(session, 'complete', content='A vita hibával leállt.', meta=session.final_payload)
