import asyncio
import json
import uuid
from pathlib import Path
from .config import GENERATED_AUDIO_DIR


class OmniVoiceManager:
    def __init__(self, model: str, language: str, speed: float, num_step: int, device: str):
        self.model = model
        self.language = language
        self.speed = speed
        self.num_step = num_step
        self.device = device
        self.proc = None
        self.lock = asyncio.Lock()

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        root = Path(__file__).resolve().parent.parent
        worker = root / 'scripts' / 'omnivoice_worker.py'
        python_bin = Path.home() / '.hermes' / 'omnivoice' / 'venv' / 'bin' / 'python'
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
        if not self.proc:
            return
        if self.proc.stdin:
            self.proc.stdin.write(b'{"cmd":"shutdown"}\n')
            await self.proc.stdin.drain()
        await self.proc.wait()

    async def synthesize(self, text: str, voice_mode: str, voice_instruct: str, ref_audio: str, ref_text: str, prefix: str):
        if not text.strip():
            return None
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
                'ref_audio': ref_audio,
                'ref_text': ref_text,
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
