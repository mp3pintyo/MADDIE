import json
import httpx


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
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def list_models(self):
        async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=30.0)) as client:
            r = await client.get(f'{self.base_url}/v1/models')
            r.raise_for_status()
            return r.json()

    async def chat(self, messages, temperature: float, max_tokens: int) -> str:
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
            data = r.json()
        msg = data['choices'][0]['message']
        content = (msg.get('content') or '').strip()
        if content:
            return content
        return (msg.get('reasoning_content') or '').strip()

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
