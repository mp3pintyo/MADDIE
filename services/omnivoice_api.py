import io
import os
import tempfile

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from omnivoice import OmniVoice


def _resolve_device(device_name: str | None) -> str:
    name = (device_name or '').strip().lower()
    if not name or name == 'auto':
        if torch.cuda.is_available():
            return 'cuda'
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            return 'mps'
        return 'cpu'
    return device_name or 'cpu'


def _resolve_dtype(device_name: str, dtype_name: str | None):
    name = (dtype_name or '').strip().lower()
    if not name or name == 'auto':
        return None
    if name == 'float32':
        return torch.float32
    if name == 'bfloat16':
        return torch.bfloat16
    if name == 'float16':
        return torch.float16
    if name == 'fp32':
        return torch.float32
    if name == 'fp16':
        return torch.float16
    if name == 'bf16':
        return torch.bfloat16
    raise ValueError(f'Nem támogatott OmniVoice dtype: {dtype_name}')


def _prepare_audio_for_write(audio):
    if isinstance(audio, torch.Tensor):
        audio = audio.detach().float().cpu().numpy()
    if getattr(audio, 'ndim', 0) == 2:
        if 1 in audio.shape:
            return audio.squeeze()
        if audio.shape[0] < audio.shape[1]:
            return audio.T
    return audio


class OmniVoiceService:
    def __init__(self):
        self.model_name = os.getenv('OMNIVOICE_MODEL', 'k2-fsa/OmniVoice')
        self.device = _resolve_device(os.getenv('OMNIVOICE_DEVICE', 'auto'))
        self.dtype_name = os.getenv('OMNIVOICE_DTYPE', '')
        self.model = None

    def load(self):
        if self.model is not None:
            return self.model
        dtype = _resolve_dtype(self.device, self.dtype_name)
        load_kwargs = {
            'device_map': self.device,
        }
        if dtype is not None:
            load_kwargs['dtype'] = dtype
        self.model = OmniVoice.from_pretrained(self.model_name, **load_kwargs)
        self.device = str(getattr(self.model, 'device', self.device))
        return self.model


service = OmniVoiceService()
app = FastAPI(title='MADDIE OmniVoice Service')


@app.on_event('startup')
async def startup_event():
    service.load()


@app.get('/health')
async def health():
    service.load()
    resolved_dtype = getattr(service.model, 'dtype', None)
    return JSONResponse({
        'ok': True,
        'model': service.model_name,
        'device': service.device,
        'dtype': (
            str(resolved_dtype).replace('torch.', '')
            if resolved_dtype is not None
            else 'auto'
        ),
    })


@app.post('/synthesize')
async def synthesize(
    text: str = Form(...),
    voice_mode: str = Form('instruct'),
    voice_instruct: str = Form('female, moderate pitch'),
    ref_text: str = Form(''),
    language: str = Form('hu'),
    speed: float = Form(1.0),
    num_step: int = Form(8),
    ref_audio: UploadFile | None = File(default=None),
):
    text = text.strip()
    if not text:
        raise HTTPException(400, 'A szöveg nem lehet üres.')

    model = service.load()
    kwargs = {
        'text': text,
        'language': language,
        'speed': speed,
        'num_step': num_step,
    }

    temp_ref_path = None
    if voice_mode == 'clone':
        if ref_audio is None:
            raise HTTPException(400, 'Clone módhoz referenciahang szükséges.')
        temp_ref_path = f'/tmp/{ref_audio.filename or "ref.wav"}'
        with open(temp_ref_path, 'wb') as handle:
            handle.write(await ref_audio.read())
        kwargs['ref_audio'] = temp_ref_path
        if ref_text.strip():
            kwargs['ref_text'] = ref_text.strip()
    elif voice_instruct.strip():
        kwargs['instruct'] = voice_instruct.strip()

    try:
        audios = model.generate(**kwargs)
    except Exception as exc:
        raise HTTPException(500, f'OmniVoice generálási hiba: {exc}') from exc
    finally:
        if temp_ref_path and os.path.exists(temp_ref_path):
            os.remove(temp_ref_path)

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_wav:
        temp_wav_path = temp_wav.name

    try:
        sf.write(temp_wav_path, _prepare_audio_for_write(audios[0]), model.sampling_rate, format='WAV')
        with open(temp_wav_path, 'rb') as handle:
            return Response(handle.read(), media_type='audio/wav')
    finally:
        if os.path.exists(temp_wav_path):
            os.remove(temp_wav_path)