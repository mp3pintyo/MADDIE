import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from .config import GENERATED_DEBATES_DIR
from .models import DebateEvent


@dataclass
class DebateSession:
    id: str
    topic: str
    advisor_ids: list[str]
    created_at: float = field(default_factory=time.time)
    status: str = 'queued'
    stop_requested: bool = False
    stop_budget_remaining: int | None = None
    scores: dict[str, int] = field(default_factory=dict)
    events: list[DebateEvent] = field(default_factory=list)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    final_payload: dict | None = None
    artifact_dir: Path | None = None
    voice_references: dict[str, dict[str, str]] = field(default_factory=dict)
    waiting_for_client: bool = False
    waiting_event_id: str | None = None
    continue_event: asyncio.Event = field(default_factory=asyncio.Event)

    def ensure_dir(self):
        if self.artifact_dir is None:
            self.artifact_dir = GENERATED_DEBATES_DIR / self.id
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
        return self.artifact_dir


class SessionRegistry:
    def __init__(self):
        self.sessions = {}

    def create(self, topic: str, advisor_ids: list[str]):
        session = DebateSession(id=str(uuid.uuid4()), topic=topic, advisor_ids=advisor_ids)
        session.scores = {advisor_id: 0 for advisor_id in advisor_ids}
        session.ensure_dir()
        self.sessions[session.id] = session
        return session

    def get(self, session_id: str):
        return self.sessions[session_id]
