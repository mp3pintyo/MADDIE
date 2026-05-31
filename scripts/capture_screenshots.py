import json
from pathlib import Path
from urllib import request
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'
OUT = Path('/home/hermes/projects/2026-05-31_150040_multi-advisors-debate-webapp/04_artifacts/exports/screenshots')
OUT.mkdir(parents=True, exist_ok=True)


def get_json(path: str):
    with request.urlopen(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))


def post_json(path: str, payload: dict):
    data = json.dumps(payload).encode('utf-8')
    req = request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))


def main():
    current = get_json('/api/settings')
    original = current['settings'].copy()
    advisors = current['advisors']
    tweaked = current['settings'].copy()
    tweaked['opening_rounds'] = 1
    tweaked['max_tokens_per_turn'] = 120
    post_json('/api/settings', {'settings': tweaked, 'advisors': advisors})

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path='/snap/bin/chromium', args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1600, 'height': 1200})
            page.goto(BASE, wait_until='networkidle', timeout=120000)
            page.screenshot(path=str(OUT / '01-home.png'), full_page=True)

            page.click('#openAdvisorModalBtn')
            page.wait_for_selector('#advisorModal:not(.hidden)', timeout=10000)
            page.screenshot(path=str(OUT / '02-advisor-modal.png'), full_page=True)
            page.click("#advisorModal [data-close='advisorModal']")

            page.fill('#topicInput', 'Melyik legyen a MADDIE első MVP-funkciója?')
            page.click('#startBtn')
            page.wait_for_selector('.message', timeout=180000)
            page.wait_for_timeout(8000)
            page.screenshot(path=str(OUT / '03-live-debate.png'), full_page=True)

            page.wait_for_selector('#summaryPanel:not(.hidden)', timeout=240000)
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT / '04-summary.png'), full_page=True)
            browser.close()
    finally:
        post_json('/api/settings', {'settings': original, 'advisors': advisors})


if __name__ == '__main__':
    main()
