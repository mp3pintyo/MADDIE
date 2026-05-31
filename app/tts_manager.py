import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

from .config import GENERATED_AUDIO_DIR, ROOT


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
    def __init__(self, model: str, language: str, speed: float, num_step: int, device: str, base_url: str | None = None, timeout_seconds: int = 600):
        self.model = model
        self.language = language
        self.speed = speed
        self.num_step = num_step
        self.device = device
        self.base_url = _normalize_base_url(base_url)
        self.timeout_seconds = timeout_seconds
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

    async def _synthesize_via_service(self, text: str, voice_mode: str, voice_instruct: str, ref_audio: str, ref_text: str, prefix: str):
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
            'language': self.language,
            'speed': str(self.speed),
            'num_step': str(self.num_step),
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

    async def synthesize(self, text: str, voice_mode: str, voice_instruct: str, ref_audio: str, ref_text: str, prefix: str):
        if not text.strip():
            return None
        if self.base_url:
            return await self._synthesize_via_service(text, voice_mode, voice_instruct, ref_audio, ref_text, prefix)

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
                'text': text,
                'output_path': str(out_path),
                'voice_mode': voice_mode,
                'voice_instruct': voice_instruct,
                'ref_audio': str(resolved_ref_audio) if resolved_ref_audio else '',
                'ref_text': resolved_ref_text,
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
