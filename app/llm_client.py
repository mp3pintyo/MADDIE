import json
from urllib.parse import urlsplit, urlunsplit

import httpx


def _normalize_loopback_base_url(base_url: str) -> str:
    parts = urlsplit(base_url.rstrip('/'))
    if parts.hostname != '0.0.0.0':
        return base_url.rstrip('/')

    netloc = '127.0.0.1'
    if parts.port:
        netloc = f'{netloc}:{parts.port}'
    if parts.username:
        auth = parts.username
        if parts.password:
            auth = f'{auth}:{parts.password}'
        netloc = f'{auth}@{netloc}'
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)).rstrip('/')


def _extract_json_candidate(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith('```'):
        lines = cleaned.splitlines()[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()
    if cleaned.startswith('{') and cleaned.endswith('}'):
        return cleaned
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1 and end > start:
        return cleaned[start:end+1]
    return cleaned


class LlamaCppClient:
    def __init__(self, base_url: str, model: str, timeout_seconds: int = 600):
        self.base_url = _normalize_loopback_base_url(base_url)
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def _chat_completion(self, messages, temperature: float, max_tokens: int):
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
            'chat_template_kwargs': {'enable_thinking': False},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)) as client:
            r = await client.post(f'{self.base_url}/v1/chat/completions', json=payload)
            r.raise_for_status()
            return r.json()

    async def list_models(self):
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)) as client:
            r = await client.get(f'{self.base_url}/v1/models')
            r.raise_for_status()
            return r.json()

    async def chat(self, messages, temperature: float, max_tokens: int) -> str:
        current_messages = list(messages)
        parts = []

        for _ in range(3):
            data = await self._chat_completion(current_messages, temperature=temperature, max_tokens=max_tokens)
            choice = data['choices'][0]
            msg = choice['message']
            content = msg.get('content') or msg.get('reasoning_content') or ''
            if content:
                parts.append(content)

            if choice.get('finish_reason') != 'length':
                break

            partial = ''.join(parts)
            current_messages = [
                *messages,
                {'role': 'assistant', 'content': partial},
                {
                    'role': 'user',
                    'content': 'Continue exactly from the next missing characters. Return only the continuation, without repeating, restarting, or summarizing.'
                },
            ]

        return ''.join(parts).strip()

    async def chat_json(self, messages, temperature: float, max_tokens: int):
        text = await self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        cleaned = _extract_json_candidate(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            repair_messages = [
                {
                    'role': 'system',
                    'content': 'You repair malformed JSON. Return only valid JSON matching the original intended structure. Do not add commentary.'
                },
                {
                    'role': 'user',
                    'content': f'Make this valid JSON and preserve the content as much as possible:\n\n{cleaned}'
                }
            ]
            repaired = await self.chat(repair_messages, temperature=0.0, max_tokens=max_tokens)
            return json.loads(_extract_json_candidate(repaired))
