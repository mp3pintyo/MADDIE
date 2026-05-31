from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
STATIC_DIR = ROOT / 'app' / 'static'
GENERATED_DIR = ROOT / 'app' / 'generated'
GENERATED_AUDIO_DIR = GENERATED_DIR / 'audio'
GENERATED_DEBATES_DIR = GENERATED_DIR / 'debates'
LOGS_DIR = ROOT / 'logs'

for p in [DATA_DIR, STATIC_DIR, GENERATED_DIR, GENERATED_AUDIO_DIR, GENERATED_DEBATES_DIR, LOGS_DIR]:
    p.mkdir(parents=True, exist_ok=True)

APP_SETTINGS_PATH = DATA_DIR / 'app_settings.json'
APP_SETTINGS_EXAMPLE_PATH = DATA_DIR / 'app_settings.example.json'
ADVISORS_PATH = DATA_DIR / 'advisors.json'


def load_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
