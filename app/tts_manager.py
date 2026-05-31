import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path

import httpx

from .config import GENERATED_AUDIO_DIR, ROOT


_SUPPORTED_NON_VERBAL_TAGS = (
    'laughter',
    'sigh',
    'confirmation-en',
    'question-en',
    'question-ah',
    'question-oh',
    'question-ei',
    'question-yi',
    'surprise-ah',
    'surprise-oh',
    'surprise-wa',
    'surprise-yo',
    'dissatisfaction-hnn',
)
SUPPORTED_NON_VERBAL_TAGS = _SUPPORTED_NON_VERBAL_TAGS
_SUPPORTED_NON_VERBAL_TAG_LOOKUP = {tag.casefold(): tag for tag in SUPPORTED_NON_VERBAL_TAGS}
_NON_VERBAL_TAG_PREFIX_RE = re.compile(
    r'^\[(?:' + '|'.join(re.escape(tag) for tag in _SUPPORTED_NON_VERBAL_TAGS) + r')\]\s*',
    re.IGNORECASE,
)
_SENTENCE_BREAK_RE = re.compile(r'(?<=[.!?])(?P<gap>\s+)')
_BRACKETED_SEGMENT_RE = re.compile(r'(\[[^\]]+\])')
_ARPABET_TOKEN_RE = re.compile(r'^[A-Z]{1,3}\d?$')
_QUESTION_END_RE = re.compile(r'[?？]\s*(?:["\')\]]+)?$')
_LAUGHTER_CUE_RE = re.compile(r'\b(?:ha(?:ha)+|ha-ha|hehe|heh|lol|lmao)\b', re.IGNORECASE)
_SIGH_CUE_RE = re.compile(r'\b(?:unfortunately|sadly|regrettably|sajnos)\b', re.IGNORECASE)
_SURPRISE_CUE_RE = re.compile(r'^\s*(?:wow|whoa|oh|ah|huh|no way|ez meglep[őo]|milyen meglep[őo])\b', re.IGNORECASE)
_CONFIRMATION_CUE_RE = re.compile(r'^\s*(?:yes|yeah|yep|absolutely|exactly|indeed|certainly|right)\b', re.IGNORECASE)
_ENGLISH_PRONUNCIATION_OVERRIDES = {
    'AI': '[EY1 AY1]',
    'API': '[EY1 P IY1 AY1]',
    'APIS': '[EY1 P IY1 AY1 Z]',
    'CPU': '[S IY1 P IY1 Y UW1]',
    'CPUS': '[S IY1 P IY1 Y UW1 Z]',
    'GPU': '[JH IY1 P IY1 Y UW1]',
    'GPUS': '[JH IY1 P IY1 Y UW1 Z]',
    'JSON': '[JH EY1 S AH0 N]',
    'KPI': '[K EY1 P IY1 AY1]',
    'KPIS': '[K EY1 P IY1 AY1 Z]',
    'LLM': '[EH1 L EH1 L EH1 M]',
    'LLMS': '[EH1 L EH1 L EH1 M Z]',
    'MVP': '[EH1 M V IY1 P IY1]',
    'PM': '[P IY1 EH1 M]',
    'PMS': '[P IY1 EH1 M Z]',
    'ROI': '[AA1 R OW1 AY1]',
    'SQL': '[EH1 S K Y UW1 EH1 L]',
    'UI': '[Y UW1 AY1]',
    'UIS': '[Y UW1 AY1 Z]',
    'UX': '[Y UW1 EH1 K S]',
    'YAML': '[Y AE1 M AH0 L]',
}
_ENGLISH_PRONUNCIATION_RE = re.compile(
    r'\b(?:' + '|'.join(sorted((re.escape(key) for key in _ENGLISH_PRONUNCIATION_OVERRIDES), key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)


def _normalize_language_code(language: str | None) -> str:
    return (language or '').strip().lower().split('-', 1)[0]


def split_tts_sentences(text: str) -> list[tuple[str, str]]:
    parts = _SENTENCE_BREAK_RE.split(text)
    sentences = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        separator = parts[index + 1] if index + 1 < len(parts) else ''
        sentences.append((sentence, separator))
    return sentences


def _canonicalize_non_verbal_tag(tag: str) -> str | None:
    cleaned = (tag or '').strip().strip('[]').casefold()
    return _SUPPORTED_NON_VERBAL_TAG_LOOKUP.get(cleaned)


def _apply_sentence_tag(sentence: str, tag: str) -> str:
    stripped = sentence.lstrip()
    if not stripped or _NON_VERBAL_TAG_PREFIX_RE.match(stripped):
        return sentence
    leading_ws = sentence[:len(sentence) - len(stripped)]
    return f'{leading_ws}[{tag}] {stripped}'


def _is_valid_arpabet(value: str) -> bool:
    tokens = value.split()
    return bool(tokens) and all(_ARPABET_TOKEN_RE.fullmatch(token) for token in tokens)


def _replace_nth_unbracketed_match(text: str, source: str, replacement: str, occurrence: int) -> str:
    if not source or occurrence < 1:
        return text

    pattern = re.compile(rf'(?<!\w){re.escape(source)}(?!\w)', re.IGNORECASE)
    segments = _BRACKETED_SEGMENT_RE.split(text)
    seen = 0

    for index, segment in enumerate(segments):
        if index % 2 == 1:
            continue

        def replace(match):
            nonlocal seen
            seen += 1
            if seen == occurrence:
                return replacement
            return match.group(0)

        segments[index] = pattern.sub(replace, segment)
        if seen >= occurrence:
            break

    return ''.join(segments)


def apply_omnivoice_annotation_plan(text: str, language: str | None, sentence_tags: list[dict] | None = None, pronunciations: list[dict] | None = None) -> str:
    prepared = text
    language_code = _normalize_language_code(language)

    if language_code == 'en':
        for item in pronunciations or []:
            if not isinstance(item, dict):
                continue
            source_text = str(item.get('text', '')).strip()
            arpabet = str(item.get('arpabet', '')).strip().upper()
            occurrence = item.get('occurrence', 1)
            if not isinstance(occurrence, int) or occurrence < 1:
                occurrence = 1
            if not source_text or not _is_valid_arpabet(arpabet):
                continue
            prepared = _replace_nth_unbracketed_match(prepared, source_text, f'[{arpabet}]', occurrence)

    sentences = [[sentence, separator] for sentence, separator in split_tts_sentences(prepared)]
    for item in sentence_tags or []:
        if not isinstance(item, dict):
            continue
        sentence_index = item.get('sentence_index')
        tag = _canonicalize_non_verbal_tag(str(item.get('tag', '')))
        if not isinstance(sentence_index, int) or sentence_index < 0 or sentence_index >= len(sentences) or not tag:
            continue
        sentences[sentence_index][0] = _apply_sentence_tag(sentences[sentence_index][0], tag)

    return ''.join(sentence + separator for sentence, separator in sentences)


def _select_non_verbal_tag(sentence: str, language_code: str) -> str | None:
    if _LAUGHTER_CUE_RE.search(sentence):
        return 'laughter'
    if _QUESTION_END_RE.search(sentence):
        return 'question-en' if language_code == 'en' else 'question-oh'
    if language_code == 'en' and _CONFIRMATION_CUE_RE.match(sentence):
        return 'confirmation-en'
    if _SURPRISE_CUE_RE.match(sentence):
        return 'surprise-oh'
    if _SIGH_CUE_RE.search(sentence):
        return 'sigh'
    return None


def _inject_non_verbal_tags(text: str, language_code: str) -> str:
    parts = [[sentence, separator] for sentence, separator in split_tts_sentences(text)]
    for index, item in enumerate(parts):
        sentence = item[0]
        stripped = sentence.lstrip()
        if not stripped or _NON_VERBAL_TAG_PREFIX_RE.match(stripped):
            continue
        tag = _select_non_verbal_tag(stripped, language_code)
        if not tag:
            continue
        item[0] = _apply_sentence_tag(sentence, tag)
    return ''.join(sentence + separator for sentence, separator in parts)


def _apply_english_pronunciation_overrides(text: str) -> str:
    segments = _BRACKETED_SEGMENT_RE.split(text)
    for index, segment in enumerate(segments):
        if index % 2 == 1:
            continue
        segments[index] = _ENGLISH_PRONUNCIATION_RE.sub(
            lambda match: _ENGLISH_PRONUNCIATION_OVERRIDES[match.group(0).upper()],
            segment,
        )
    return ''.join(segments)


def _prepare_omnivoice_text(text: str, language: str | None, markup_enabled: bool, pronunciation_enabled: bool) -> str:
    prepared = text.strip()
    if not prepared:
        return ''

    language_code = _normalize_language_code(language)
    if markup_enabled:
        prepared = _inject_non_verbal_tags(prepared, language_code)
    if pronunciation_enabled and language_code == 'en':
        prepared = _apply_english_pronunciation_overrides(prepared)
    return prepared


def _normalize_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    cleaned = base_url.strip().rstrip('/')
    return cleaned.replace('://0.0.0.0', '://127.0.0.1', 1)


def _resolve_ref_audio_path(ref_audio: str) -> Path | None:
    if not ref_audio:
        return None
    candidate = Path(ref_audio).expanduser()
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    rooted = ROOT / candidate
    return rooted if rooted.exists() else None


def _resolve_ref_text(ref_audio: str, ref_text: str) -> str:
    explicit = (ref_text or '').strip()
    if explicit:
        return explicit

    resolved_ref_audio = _resolve_ref_audio_path(ref_audio)
    if not resolved_ref_audio:
        return ''

    candidates = [resolved_ref_audio.with_suffix('.txt')]
    simplified_stem = resolved_ref_audio.stem
    for suffix in ('_24k_mono', '_mono', '_24k'):
        if simplified_stem.endswith(suffix):
            simplified_stem = simplified_stem[:-len(suffix)]
            break
    candidates.append(resolved_ref_audio.with_name(f'{simplified_stem}.txt'))

    seen = set()
    for candidate in candidates:
        normalized = candidate.resolve()
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.exists():
            return candidate.read_text(encoding='utf-8').strip()
    return ''


def _resolve_python_bin() -> Path:
    candidates = []
    configured = os.getenv('OMNIVOICE_LOCAL_PYTHON', '').strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(Path(sys.executable).resolve())
    candidates.append(Path.home() / '.hermes' / 'omnivoice' / 'venv' / 'bin' / 'python')
    candidates.append(Path.home() / '.hermes' / 'omnivoice' / 'venv' / 'Scripts' / 'python.exe')

    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError('Nem található OmniVoice Python interpreter. Állítsd be az OMNIVOICE_LOCAL_PYTHON változót vagy használd a HTTP-s OmniVoice szolgáltatást.')


class OmniVoiceManager:
    def __init__(self, model: str, language: str, speed: float, num_step: int, device: str, base_url: str | None = None, timeout_seconds: int = 600, markup_enabled: bool = True, english_pronunciation_enabled: bool = True):
        self.model = model
        self.language = language
        self.speed = speed
        self.num_step = num_step
        self.device = device
        self.base_url = _normalize_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.markup_enabled = markup_enabled
        self.english_pronunciation_enabled = english_pronunciation_enabled
        self.proc = None
        self.lock = asyncio.Lock()

    async def start(self):
        if self.base_url:
            return
        if self.proc and self.proc.returncode is None:
            return
        worker = ROOT / 'scripts' / 'omnivoice_worker.py'
        python_bin = _resolve_python_bin()
        self.proc = await asyncio.create_subprocess_exec(
            str(python_bin), str(worker),
            '--model', self.model,
            '--language', self.language,
            '--speed', str(self.speed),
            '--num-step', str(self.num_step),
            '--device', self.device,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self):
        if not self.proc or not self.proc.stderr:
            return
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break

    async def stop(self):
        if self.base_url:
            return
        if not self.proc:
            return
        if self.proc.stdin:
            self.proc.stdin.write(b'{"cmd":"shutdown"}\n')
            await self.proc.stdin.drain()
        await self.proc.wait()

    async def _synthesize_via_service(self, text: str, voice_mode: str, voice_instruct: str, ref_audio: str, ref_text: str, prefix: str, language: str | None = None, num_step: int | None = None):
        req_id = str(uuid.uuid4())
        out_path = GENERATED_AUDIO_DIR / f'{prefix}-{req_id}.wav'
        resolved_ref_audio = _resolve_ref_audio_path(ref_audio)
        resolved_ref_text = _resolve_ref_text(ref_audio, ref_text)
        if voice_mode == 'clone' and ref_audio and not resolved_ref_audio:
            raise RuntimeError(f'Referenciahang nem található: {ref_audio}')

        data = {
            'text': text,
            'voice_mode': voice_mode,
            'voice_instruct': voice_instruct,
            'ref_text': resolved_ref_text,
            'language': language or self.language,
            'speed': str(self.speed),
            'num_step': str(num_step if num_step is not None else self.num_step),
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=30.0)
        ref_handle = None
        files = None
        try:
            if resolved_ref_audio:
                ref_handle = resolved_ref_audio.open('rb')
                files = {'ref_audio': (resolved_ref_audio.name, ref_handle, 'audio/wav')}

            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f'{self.base_url}/synthesize', data=data, files=files)
                response.raise_for_status()

            out_path.write_bytes(response.content)
            return str(out_path)
        finally:
            if ref_handle:
                ref_handle.close()

    async def synthesize(self, text: str, voice_mode: str, voice_instruct: str, ref_audio: str, ref_text: str, prefix: str, language: str | None = None, num_step: int | None = None):
        resolved_language = language or self.language
        prepared_text = _prepare_omnivoice_text(
            text,
            resolved_language,
            self.markup_enabled,
            self.english_pronunciation_enabled,
        )
        if not prepared_text:
            return None
        if self.base_url:
            return await self._synthesize_via_service(prepared_text, voice_mode, voice_instruct, ref_audio, ref_text, prefix, language=resolved_language, num_step=num_step)

        resolved_ref_audio = _resolve_ref_audio_path(ref_audio)
        resolved_ref_text = _resolve_ref_text(ref_audio, ref_text)
        if voice_mode == 'clone' and ref_audio and not resolved_ref_audio:
            raise RuntimeError(f'Referenciahang nem található: {ref_audio}')

        await self.start()
        async with self.lock:
            req_id = str(uuid.uuid4())
            out_path = GENERATED_AUDIO_DIR / f'{prefix}-{req_id}.wav'
            payload = {
                'cmd': 'synthesize',
                'request_id': req_id,
                'text': prepared_text,
                'output_path': str(out_path),
                'voice_mode': voice_mode,
                'voice_instruct': voice_instruct,
                'ref_audio': str(resolved_ref_audio) if resolved_ref_audio else '',
                'ref_text': resolved_ref_text,
                'language': resolved_language,
                'num_step': num_step if num_step is not None else self.num_step,
            }
            self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
            await self.proc.stdin.drain()
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RuntimeError('OmniVoice worker exited unexpectedly')
                result = json.loads(line.decode('utf-8'))
                if result.get('request_id') != req_id:
                    continue
                if result.get('status') == 'ok':
                    return result['output_path']
                raise RuntimeError(result.get('error', 'Unknown OmniVoice worker error'))
