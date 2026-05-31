import io
import os

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from omnivoice import OmniVoice


def _resolve_dtype(device_name: str, dtype_name: str | None):
    name = (dtype_name or '').strip().lower()
    if name == 'float32':
        return torch.float32
    if name == 'bfloat16':
        return torch.bfloat16
    if device_name.startswith('cpu'):
        return torch.float32
    return torch.float16


class OmniVoiceService:
    def __init__(self):
        self.model_name = os.getenv('OMNIVOICE_MODEL', 'k2-fsa/OmniVoice')
        self.device = os.getenv('OMNIVOICE_DEVICE', 'cpu')
        self.dtype_name = os.getenv('OMNIVOICE_DTYPE', '')
        self.model = None

    def load(self):
        if self.model is not None:
            return self.model
        dtype = _resolve_dtype(self.device, self.dtype_name)
        self.model = OmniVoice.from_pretrained(self.model_name, device_map=self.device, dtype=dtype)
        return self.model


service = OmniVoiceService()
app = FastAPI(title='MADDIE OmniVoice Service')


@app.on_event('startup')
async def startup_event():
    service.load()


@app.get('/health')
async def health():
    service.load()
    return JSONResponse({
        'ok': True,
        'model': service.model_name,
        'device': service.device,
        'dtype': str(_resolve_dtype(service.device, service.dtype_name)).replace('torch.', ''),
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

    buffer = io.BytesIO()
    sf.write(buffer, audios[0], model.sampling_rate, format='WAV')
    return Response(buffer.getvalue(), media_type='audio/wav')