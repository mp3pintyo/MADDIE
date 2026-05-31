from typing import Literal, Optional
from pydantic import BaseModel, Field


class Advisor(BaseModel):
    id: str
    name: str
    title: str
    description: str
    avatar: str
    accent_color: str
    llm_prompt: str
    voice_mode: Literal['instruct', 'clone'] = 'instruct'
    voice_instruct: str = ''
    voice_num_step: int | None = None
    ref_audio: str = ''
    ref_text: str = ''
    enabled: bool = True


class AppSettings(BaseModel):
    llama_base_url: str = 'http://0.0.0.0:8080'
    llama_model: str = 'Qwen3.6-27B-IQ4_NL.gguf'
    request_timeout_seconds: int = 600
    temperature: float = 0.7
    summary_temperature: float = 0.4
    max_tokens_per_turn: int = 220
    opening_rounds: int = 2
    closure_extra_turns: int = 2
    omnivoice_enabled: bool = True
    omnivoice_base_url: str = 'http://127.0.0.1:8010'
    omnivoice_model: str = 'k2-fsa/OmniVoice'
    omnivoice_language: str = 'hu'
    omnivoice_speed: float = 1.0
    omnivoice_num_step: int = 8
    omnivoice_llm_annotation_enabled: bool = True
    omnivoice_markup_enabled: bool = True
    omnivoice_english_pronunciation_enabled: bool = True
    omnivoice_device: str = 'cuda:0'
    meeting_name: str = 'MADDIE — Tanácsadói kör'


class SettingsPayload(BaseModel):
    settings: AppSettings
    advisors: list[Advisor]


class DebateStartRequest(BaseModel):
    topic: str = Field(min_length=3)
    advisor_ids: list[str] = Field(min_length=1)


class VoteRequest(BaseModel):
    advisor_id: str


class ContinueRequest(BaseModel):
    event_id: str | None = None


class DebateEvent(BaseModel):
    id: str
    type: str
    timestamp: float
    advisor_id: Optional[str] = None
    advisor_name: Optional[str] = None
    content: Optional[str] = None
    audio_url: Optional[str] = None
    meta: dict = Field(default_factory=dict)
