from contextlib import asynccontextmanager
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ADVISORS_PATH, APP_SETTINGS_EXAMPLE_PATH, APP_SETTINGS_PATH, ROOT, load_json, save_json
from .debate_engine import DebateEngine
from .llm_client import LlamaCppClient
from .models import Advisor, AppSettings, SettingsPayload, DebateStartRequest, VoteRequest, ContinueRequest
from .state import SessionRegistry
from .tts_manager import OmniVoiceManager


def default_advisors():
    return [
        {
            'id': 'ai-engineer', 'name': 'AI-mérnök', 'title': 'Rendszer és megvalósítás', 'description': 'Skálázás, modellek, kockázatok, technikai megvalósíthatóság.',
            'avatar': '/app/static/avatars/ai-engineer.svg', 'accent_color': '#5b8def', 'voice_mode': 'instruct', 'voice_instruct': 'male, low pitch',
            'llm_prompt': 'Te egy tapasztalt AI-mérnök vagy. Rendszerszinten gondolkodsz, szereted a trade-offokat, a megbízhatóságot, a mérést és a fokozatos bevezetést.'
        },
        {
            'id': 'artist', 'name': 'Művész', 'title': 'Esztétika és élmény', 'description': 'Hangulat, jelentés, forma, kulturális rezonancia.',
            'avatar': '/app/static/avatars/artist.svg', 'accent_color': '#d971ff', 'voice_mode': 'instruct', 'voice_instruct': 'female, moderate pitch',
            'llm_prompt': 'Te egy művész vagy. Az érzelmi hatást, szimbolikát, formát, ritmust és emberi élményt hangsúlyozod.'
        },
        {
            'id': 'content-creator', 'name': 'Tartalomkészítő', 'title': 'Közönség és kommunikáció', 'description': 'Világos üzenet, figyelem, elérés, narratíva.',
            'avatar': '/app/static/avatars/content-creator.svg', 'accent_color': '#ff7b54', 'voice_mode': 'instruct', 'voice_instruct': 'female, high pitch',
            'llm_prompt': 'Te egy tapasztalt tartalomkészítő vagy. A közérthetőségre, storytellingre, közönségfigyelemre és terjeszthetőségre figyelsz.'
        },
        {
            'id': 'anthropologist', 'name': 'Antropológus', 'title': 'Kultúra és emberi viselkedés', 'description': 'Szokások, csoportdinamika, jelentések, társadalmi következmények.',
            'avatar': '/app/static/avatars/anthropologist.svg', 'accent_color': '#6ac47e', 'voice_mode': 'instruct', 'voice_instruct': 'male, middle-aged, moderate pitch',
            'llm_prompt': 'Te antropológus vagy. A kulturális kontextust, emberi rítusokat, normákat és társadalmi mellékhatásokat keresed.'
        },
        {
            'id': 'product-manager', 'name': 'Termékmenedzser', 'title': 'Prioritás és döntés', 'description': 'Felhasználói érték, scope, mérőszámok, roadmap.',
            'avatar': '/app/static/avatars/product-manager.svg', 'accent_color': '#f2c14e', 'voice_mode': 'instruct', 'voice_instruct': 'female, low pitch',
            'llm_prompt': 'Te egy termékmenedzser vagy. A felhasználói problémát, prioritást, scope-ot, validációt és kockázatcsökkentést hangsúlyozod.'
        },
        {
            'id': 'greek-philosopher', 'name': 'Ókori görög filozófus', 'title': 'Alapelv és bölcsesség', 'description': 'Első elvek, erények, logika, jó élet.',
            'avatar': '/app/static/avatars/greek-philosopher.svg', 'accent_color': '#8c7ae6', 'voice_mode': 'instruct', 'voice_instruct': 'elderly, male, very low pitch',
            'llm_prompt': 'Te egy ókori görög filozófus vagy. Első elvekből, logikából és erényetikai nézőpontból érvelsz, röviden de mélyen.'
        },
        {
            'id': 'behavioral-economist', 'name': 'Viselkedési közgazdász', 'title': 'Ösztönzők és torzítások', 'description': 'Döntési torzítások, incentive-ek, valós emberi reakciók.',
            'avatar': '/app/static/avatars/behavioral-economist.svg', 'accent_color': '#00b894', 'voice_mode': 'instruct', 'voice_instruct': 'male, young adult, moderate pitch',
            'llm_prompt': 'Te viselkedési közgazdász vagy. A döntési torzításokat, ösztönzőket, incentive-hibákat és nem várt reakciókat keresed.'
        },
        {
            'id': 'futurist', 'name': 'Jövőkutató', 'title': 'Trendek és hosszú táv', 'description': 'Másodrendű hatások, forgatókönyvek, stratégiai időtáv.',
            'avatar': '/app/static/avatars/futurist.svg', 'accent_color': '#00cec9', 'voice_mode': 'instruct', 'voice_instruct': 'female, young adult, moderate pitch',
            'llm_prompt': 'Te jövőkutató vagy. Trendeket, másodrendű következményeket, forgatókönyveket és stratégiai időtávokat vizsgálsz.'
        },
    ]


def default_settings():
    return AppSettings().model_dump()


if not APP_SETTINGS_PATH.exists():
    save_json(APP_SETTINGS_PATH, default_settings())
if not APP_SETTINGS_EXAMPLE_PATH.exists():
    save_json(APP_SETTINGS_EXAMPLE_PATH, default_settings())
if not ADVISORS_PATH.exists():
    save_json(ADVISORS_PATH, default_advisors())


class AppContainer:
    def __init__(self):
        self.registry = SessionRegistry()
        self.reload()

    def reload(self):
        self.settings = AppSettings(**load_json(APP_SETTINGS_PATH, default_settings()))
        self.advisors = [Advisor(**item) for item in load_json(ADVISORS_PATH, default_advisors())]
        self.llm = LlamaCppClient(self.settings.llama_base_url, self.settings.llama_model, self.settings.request_timeout_seconds)
        self.tts = OmniVoiceManager(
            model=self.settings.omnivoice_model,
            language=self.settings.omnivoice_language,
            speed=self.settings.omnivoice_speed,
            num_step=self.settings.omnivoice_num_step,
            device=self.settings.omnivoice_device,
        ) if self.settings.omnivoice_enabled else None


container = AppContainer()


def get_session_or_404(debate_id: str):
    try:
        return container.registry.get(debate_id)
    except KeyError:
        raise HTTPException(404, 'Ismeretlen vitaazonosító.')


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if container.tts:
        await container.tts.stop()


app = FastAPI(title='MADDIE', lifespan=lifespan)
app.mount('/app/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')
app.mount('/app/generated', StaticFiles(directory=ROOT / 'app' / 'generated'), name='generated')


@app.get('/', response_class=HTMLResponse)
async def home():
    return FileResponse(ROOT / 'app' / 'static' / 'index.html')


@app.get('/api/settings')
async def get_settings():
    container.reload()
    return {'settings': container.settings.model_dump(), 'advisors': [a.model_dump() for a in container.advisors]}


@app.post('/api/settings')
async def update_settings(payload: SettingsPayload):
    save_json(APP_SETTINGS_PATH, payload.settings.model_dump())
    save_json(ADVISORS_PATH, [a.model_dump() for a in payload.advisors])
    if container.tts:
        await container.tts.stop()
    container.reload()
    return {'ok': True}


@app.get('/api/models')
async def models():
    return await container.llm.list_models()


@app.post('/api/debates')
async def create_debate(req: DebateStartRequest):
    enabled = {a.id for a in container.advisors if a.enabled}
    advisor_ids = [x for x in req.advisor_ids if x in enabled]
    if not advisor_ids:
        raise HTTPException(400, 'Nincs kiválasztott aktív tanácsadó.')
    session = container.registry.create(req.topic, advisor_ids)
    engine = DebateEngine(container.settings, container.advisors, container.llm, container.tts)
    asyncio.create_task(engine.run(session))
    return {'debate_id': session.id}


@app.get('/api/debates/{debate_id}')
async def get_debate(debate_id: str):
    session = get_session_or_404(debate_id)
    return {
        'id': session.id,
        'topic': session.topic,
        'advisor_ids': session.advisor_ids,
        'status': session.status,
        'scores': session.scores,
        'final_payload': session.final_payload,
        'waiting_for_client': session.waiting_for_client,
        'waiting_event_id': session.waiting_event_id,
    }


@app.get('/api/debates/{debate_id}/stream')
async def stream_debate(debate_id: str):
    session = get_session_or_404(debate_id)

    async def event_stream():
        try:
            for evt in session.events:
                yield f"data: {evt.model_dump_json()}\n\n"
            while True:
                evt = await session.queue.get()
                yield f"data: {evt.model_dump_json()}\n\n"
                if evt.type == 'complete':
                    break
        except asyncio.CancelledError:
            return
    return StreamingResponse(event_stream(), media_type='text/event-stream')


@app.post('/api/debates/{debate_id}/vote')
async def vote(debate_id: str, req: VoteRequest):
    session = get_session_or_404(debate_id)
    if req.advisor_id not in session.scores:
        raise HTTPException(404, 'Ismeretlen advisor az adott vitában.')
    session.scores[req.advisor_id] += 1
    return {'scores': session.scores}


@app.post('/api/debates/{debate_id}/continue')
async def continue_debate(debate_id: str, req: ContinueRequest):
    session = get_session_or_404(debate_id)
    if not session.waiting_for_client:
        return {'ok': True, 'waiting': False}
    if req.event_id and session.waiting_event_id and req.event_id != session.waiting_event_id:
        raise HTTPException(409, 'Másik lejátszási eseményre vár a szerver.')
    session.continue_event.set()
    return {'ok': True, 'waiting': True}


@app.post('/api/debates/{debate_id}/stop')
async def stop_debate(debate_id: str):
    session = get_session_or_404(debate_id)
    session.stop_requested = True
    return {'ok': True, 'message': 'Lezárás kérve; legfeljebb még 2 hozzászólás jön.'}
