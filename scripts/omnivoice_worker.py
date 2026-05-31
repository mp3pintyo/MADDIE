#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
import soundfile as sf
import torch
from omnivoice.models.omnivoice import OmniVoice


def _prepare_audio_for_write(audio):
    if isinstance(audio, torch.Tensor):
        audio = audio.detach().float().cpu().numpy()
    if getattr(audio, 'ndim', 0) == 2:
        if 1 in audio.shape:
            return audio.squeeze()
        if audio.shape[0] < audio.shape[1]:
            return audio.T
    return audio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='k2-fsa/OmniVoice')
    parser.add_argument('--language', default='hu')
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--num-step', type=int, default=8)
    parser.add_argument('--device', default='cuda:0')
    args = parser.parse_args()

    model = OmniVoice.from_pretrained(args.model, device_map=args.device, dtype=torch.float16)

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req = json.loads(raw)
        if req.get('cmd') == 'shutdown':
            return 0
        if req.get('cmd') != 'synthesize':
            continue
        request_id = req['request_id']
        try:
            output_path = Path(req['output_path']).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            language = req.get('language') or args.language
            num_step = int(req.get('num_step') or args.num_step)
            if req.get('voice_mode') == 'clone' and req.get('ref_audio'):
                audios = model.generate(
                    text=req['text'],
                    language=language,
                    ref_audio=req.get('ref_audio') or None,
                    ref_text=req.get('ref_text') or None,
                    speed=args.speed,
                    num_step=num_step,
                )
            else:
                audios = model.generate(
                    text=req['text'],
                    language=language,
                    instruct=req.get('voice_instruct') or 'female, moderate pitch',
                    speed=args.speed,
                    num_step=num_step,
                )
            sf.write(output_path, _prepare_audio_for_write(audios[0]), model.sampling_rate)
            sys.stdout.write(json.dumps({'request_id': request_id, 'status': 'ok', 'output_path': str(output_path)}, ensure_ascii=False) + '\n')
            sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(json.dumps({'request_id': request_id, 'status': 'error', 'error': str(exc)}, ensure_ascii=False) + '\n')
            sys.stdout.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
