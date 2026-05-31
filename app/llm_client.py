import json
import re
from urllib.parse import urlsplit, urlunsplit

import httpx


THINKING_TAG_RE = re.compile(r'<think>.*?</think>', re.IGNORECASE | re.DOTALL)


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


def _flatten_message_field(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict) and isinstance(item.get('text'), str):
                parts.append(item['text'])
        return ''.join(parts)
    return ''


def _visible_message_content(message: dict) -> str:
    content = _flatten_message_field(message.get('content'))
    content = THINKING_TAG_RE.sub('', content)
    return content.strip()


def _reasoning_message_content(message: dict) -> str:
    return _flatten_message_field(message.get('reasoning_content')).strip()


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
        token_budget = max_tokens
        saw_reasoning_only = False

        for _ in range(4):
            data = await self._chat_completion(current_messages, temperature=temperature, max_tokens=token_budget)
            choice = data['choices'][0]
            msg = choice['message']
            visible_content = _visible_message_content(msg)
            reasoning_content = _reasoning_message_content(msg)
            if visible_content:
                parts.append(visible_content)
            elif reasoning_content:
                saw_reasoning_only = True

            if choice.get('finish_reason') != 'length' and parts:
                break

            if not parts and reasoning_content:
                if token_budget < 512:
                    token_budget = 512
                    continue
                if token_budget < 1024:
                    token_budget = 1024
                    continue
                break

            if choice.get('finish_reason') != 'length':
                break

            partial = ''.join(parts)
            current_messages = [
                *messages,
                {'role': 'assistant', 'content': partial},
                {
                    'role': 'user',
                    'content': 'Continue exactly from the next missing characters. Return only the continuation, without repeating, restarting, summarizing, or showing reasoning.'
                },
            ]

        if not parts and saw_reasoning_only:
            rescue_messages = [
                *messages,
                {
                    'role': 'user',
                    'content': 'Return only the final answer. Do not include chain-of-thought, internal reasoning, or thinking text.'
                },
            ]
            rescue_budget = max(128, min(1024, max_tokens))
            rescue_data = await self._chat_completion(rescue_messages, temperature=temperature, max_tokens=rescue_budget)
            rescue_content = _visible_message_content(rescue_data['choices'][0]['message'])
            if rescue_content:
                return rescue_content

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
