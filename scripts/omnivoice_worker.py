#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
import soundfile as sf
import torch
from omnivoice.models.omnivoice import OmniVoice


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
            if req.get('voice_mode') == 'clone' and req.get('ref_audio'):
                audios = model.generate(
                    text=req['text'],
                    language=args.language,
                    ref_audio=req.get('ref_audio') or None,
                    ref_text=req.get('ref_text') or None,
                    speed=args.speed,
                    num_step=args.num_step,
                )
            else:
                audios = model.generate(
                    text=req['text'],
                    language=args.language,
                    instruct=req.get('voice_instruct') or 'female, moderate pitch',
                    speed=args.speed,
                    num_step=args.num_step,
                )
            sf.write(output_path, audios[0], model.sampling_rate)
            sys.stdout.write(json.dumps({'request_id': request_id, 'status': 'ok', 'output_path': str(output_path)}, ensure_ascii=False) + '\n')
            sys.stdout.flush()
        except Exception as exc:
            sys.stdout.write(json.dumps({'request_id': request_id, 'status': 'error', 'error': str(exc)}, ensure_ascii=False) + '\n')
            sys.stdout.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
