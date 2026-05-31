from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'
DEBATE_ID = '6aa45a02-b301-497d-83b6-29e570e8ec9f'
OUT = Path('/home/hermes/projects/2026-05-31_150040_multi-advisors-debate-webapp/04_artifacts/exports/screenshots')
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='/snap/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1800, 'height': 1800})
    page.goto(BASE, wait_until='networkidle', timeout=120000)
    page.evaluate(
        """
        async (debateId) => {
          const res = await fetch(`/api/debates/${debateId}`);
          const data = await res.json();
          const meta = data.final_payload || {};
          const summary = meta.summary || {};
          const panel = document.getElementById('summaryPanel');
          const blocks = document.getElementById('summaryBlocks');
          panel.classList.remove('hidden');
          const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
          const sec = [];
          if (summary.overview) sec.push(`<div class='summary-block'><h3>Áttekintés</h3><p>${esc(summary.overview)}</p></div>`);
          if (summary.advisor_summaries?.length) {
            sec.push(`<div class='summary-block'><h3>Tanácsadónkénti összegzés</h3>${summary.advisor_summaries.map(item => `<div class='summary-block-inner'><h4>${esc(item.advisor)}</h4><p>${esc(item.summary)}</p><ul>${(item.strongest_ideas||[]).map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`).join('')}</div>`);
          }
          for (const [key,title] of [['consensus','Konszenzus'],['disagreements','Nézeteltérések'],['final_evaluations','Végső értékelések'],['recommended_next_steps','Javasolt lépések']]) {
            if (summary[key]?.length) sec.push(`<div class='summary-block'><h3>${title}</h3><ul>${summary[key].map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`);
          }
          blocks.innerHTML = sec.join('');
          document.getElementById('exportsBox').innerHTML = `Kész vita: ${debateId}`;
          document.querySelector('.main').style.width = '100%';
          document.querySelector('.main').style.maxWidth = '1400px';
        }
        """,
        DEBATE_ID,
    )
    page.wait_for_timeout(1200)
    page.locator('#summaryBlocks').screenshot(path=str(OUT / '04-summary.png'))
    browser.close()
