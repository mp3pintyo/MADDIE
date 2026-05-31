const state = { settings: null, advisors: [], selectedAdvisorIds: [], debateId: null, eventSource: null, scores: {} };

const els = {
  topicInput: document.getElementById('topicInput'),
  openAdvisorModalBtn: document.getElementById('openAdvisorModalBtn'),
  openSettingsBtn: document.getElementById('openSettingsBtn'),
  startBtn: document.getElementById('startBtn'),
  stopBtn: document.getElementById('stopBtn'),
  advisorGrid: document.getElementById('advisorGrid'),
  advisorStrip: document.getElementById('advisorStrip'),
  chat: document.getElementById('chat'),
  statusBadge: document.getElementById('statusBadge'),
  modelInfo: document.getElementById('modelInfo'),
  summaryPanel: document.getElementById('summaryPanel'),
  summaryBlocks: document.getElementById('summaryBlocks'),
  exportsBox: document.getElementById('exportsBox'),
  saveSettingsBtn: document.getElementById('saveSettingsBtn'),
  advisorSettingsList: document.getElementById('advisorSettingsList'),
  settingsBaseUrl: document.getElementById('settingsBaseUrl'),
  settingsModel: document.getElementById('settingsModel'),
  settingsTemp: document.getElementById('settingsTemp'),
  settingsMaxTokens: document.getElementById('settingsMaxTokens'),
  settingsVoiceSpeed: document.getElementById('settingsVoiceSpeed'),
  settingsNumStep: document.getElementById('settingsNumStep'),
};

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (!res.ok) throw new Error(await res.text());
  return res.headers.get('content-type')?.includes('application/json') ? res.json() : res.text();
}

function esc(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function safeUrl(value) {
  const str = String(value ?? '');
  if (str.startsWith('/')) return str;
  if (str.startsWith('http://') || str.startsWith('https://')) return str;
  return '#';
}

function openPanel(id) { document.getElementById(id).classList.remove('hidden'); }
function closePanel(id) { document.getElementById(id).classList.add('hidden'); }
document.querySelectorAll('[data-close]').forEach(btn => btn.addEventListener('click', () => closePanel(btn.dataset.close)));
els.openAdvisorModalBtn.addEventListener('click', () => openPanel('advisorModal'));
els.openSettingsBtn.addEventListener('click', () => openPanel('settingsDrawer'));

function renderAdvisorGrid() {
  els.advisorGrid.innerHTML = '';
  state.advisors.filter(a => a.enabled).forEach(advisor => {
    const card = document.createElement('div');
    card.className = 'advisor-card' + (state.selectedAdvisorIds.includes(advisor.id) ? ' selected' : '');
    card.innerHTML = `
      <img src="${safeUrl(advisor.avatar)}" alt="${esc(advisor.name)}">
      <div><strong>${esc(advisor.name)}</strong><div>${esc(advisor.title)}</div></div>
      <p>${esc(advisor.description)}</p>
      <small>Hang: ${advisor.voice_mode === 'clone' ? 'clone' : esc(advisor.voice_instruct)}</small>`;
    card.addEventListener('click', () => {
      if (state.selectedAdvisorIds.includes(advisor.id)) state.selectedAdvisorIds = state.selectedAdvisorIds.filter(x => x !== advisor.id);
      else state.selectedAdvisorIds.push(advisor.id);
      renderAdvisorGrid(); renderAdvisorStrip();
    });
    els.advisorGrid.appendChild(card);
  });
}

function renderAdvisorStrip(speakingId = null) {
  els.advisorStrip.innerHTML = '';
  const selected = state.advisors.filter(a => state.selectedAdvisorIds.includes(a.id));
  selected.forEach(advisor => {
    const chip = document.createElement('div');
    chip.className = 'advisor-chip' + (speakingId === advisor.id ? ' speaking' : '');
    chip.style.borderColor = advisor.accent_color + '55';
    chip.innerHTML = `
      <img src="${safeUrl(advisor.avatar)}" alt="${esc(advisor.name)}">
      <div><strong>${esc(advisor.name)}</strong><div class='muted'>${esc(advisor.title)}</div></div>
      <div class='score'>Pontok: ${state.scores[advisor.id] ?? 0}</div>`;
    chip.addEventListener('click', async () => {
      if (!state.debateId) return;
      const res = await api(`/api/debates/${state.debateId}/vote`, { method: 'POST', body: JSON.stringify({ advisor_id: advisor.id }) });
      state.scores = res.scores; renderAdvisorStrip(speakingId);
    });
    els.advisorStrip.appendChild(chip);
  });
}

function appendStatus(text) {
  const div = document.createElement('div'); div.className = 'status-note'; div.textContent = text; els.chat.appendChild(div); els.chat.scrollTop = els.chat.scrollHeight;
}

function appendMessage(evt) {
  const tpl = document.getElementById('messageTemplate');
  const node = tpl.content.firstElementChild.cloneNode(true);
  const advisor = state.advisors.find(a => a.id === evt.advisor_id);
  node.dataset.eventId = evt.id;
  node.querySelector('.avatar').src = advisor?.avatar || '';
  node.querySelector('.speaker').textContent = evt.advisor_name || 'Tanácsadó';
  node.querySelector('.meta').textContent = evt.meta?.closing ? 'zárókör' : `kör ${evt.meta?.round ?? ''}`;
  node.querySelector('.bubble').textContent = evt.content || '';
  els.chat.appendChild(node); renderAdvisorStrip(evt.advisor_id); els.chat.scrollTop = els.chat.scrollHeight;
}

async function notifyPlaybackFinished(eventId) {
  if (!state.debateId) return;
  try {
    await api(`/api/debates/${state.debateId}/continue`, { method: 'POST', body: JSON.stringify({ event_id: eventId }) });
  } catch (err) {
    console.error('continue failed', err);
  }
}

function attachAudio(evt) {
  const node = [...els.chat.querySelectorAll('.message')].find(x => x.dataset.eventId === evt.meta?.event_id);
  if (!node) return;
  const audio = node.querySelector('.audio-player');
  audio.src = evt.audio_url;
  audio.hidden = false;
  audio.autoplay = true;
  audio.onended = () => notifyPlaybackFinished(evt.meta?.event_id);
  audio.onplay = () => renderAdvisorStrip(evt.advisor_id);
  audio.play().catch(err => {
    console.warn('autoplay blocked', err);
    appendStatus('Az automatikus lejátszás blokkolva lett. Indítsd el a hangot kézzel; a vita a hang végén folytatódik.');
  });
}

function block(title, html) { return `<div class='summary-block'><h3>${esc(title)}</h3>${html}</div>`; }
function renderSummary(meta) {
  els.summaryPanel.classList.remove('hidden');
  const summary = meta.summary || {}; const blocks = [];
  if (summary.overview) blocks.push(block('Áttekintés', `<p>${esc(summary.overview)}</p>`));
  if (summary.advisor_summaries?.length) blocks.push(block('Tanácsadónként', summary.advisor_summaries.map(item => `<div class='summary-block-inner'><h4>${esc(item.advisor)}</h4><p>${esc(item.summary)}</p><ul>${(item.strongest_ideas || []).map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`).join('')));
  for (const [key, title] of [['consensus','Konszenzus'],['disagreements','Nézeteltérések'],['final_evaluations','Végső értékelések'],['recommended_next_steps','Javasolt lépések']]) {
    if (summary[key]?.length) blocks.push(block(title, `<ul>${summary[key].map(x => `<li>${esc(x)}</li>`).join('')}</ul>`));
  }
  els.summaryBlocks.innerHTML = blocks.join('');
  const links = [];
  if (meta.transcript_url) links.push(`<a href='${safeUrl(meta.transcript_url)}' target='_blank' rel='noopener noreferrer'>Átirat</a>`);
  if (meta.summary_url) links.push(`<a href='${safeUrl(meta.summary_url)}' target='_blank' rel='noopener noreferrer'>Összegzés</a>`);
  if (meta.podcast_url) links.push(`<a href='${safeUrl(meta.podcast_url)}' target='_blank' rel='noopener noreferrer'>Podcast</a>`);
  els.exportsBox.classList.remove('empty'); els.exportsBox.innerHTML = links.join(' · ');
}

function renderAdvisorSettings() {
  els.advisorSettingsList.innerHTML = '';
  state.advisors.forEach(advisor => {
    const card = document.createElement('div'); card.className = 'advisor-settings-card';
    card.innerHTML = `
      <strong>${esc(advisor.name)}</strong>
      <div class='row'>
        <label>Név<input data-field='name' value="${esc(advisor.name)}"></label>
        <label>Cím<input data-field='title' value="${esc(advisor.title)}"></label>
      </div>
      <label>Leírás<textarea data-field='description'>${esc(advisor.description)}</textarea></label>
      <label>Persona prompt<textarea data-field='llm_prompt'>${esc(advisor.llm_prompt)}</textarea></label>
      <div class='row'>
        <label>Voice mode<input data-field='voice_mode' value="${esc(advisor.voice_mode)}"></label>
        <label>Voice instruct<input data-field='voice_instruct' value="${esc(advisor.voice_instruct)}"></label>
      </div>
      <div class='row'>
        <label>Ref audio<input data-field='ref_audio' value="${esc(advisor.ref_audio || '')}"></label>
        <label>Ref text<input data-field='ref_text' value="${esc(advisor.ref_text || '')}"></label>
      </div>`;
    card.querySelectorAll('[data-field]').forEach(input => input.addEventListener('input', () => { advisor[input.dataset.field] = input.value; }));
    els.advisorSettingsList.appendChild(card);
  });
}

async function loadSettings() {
  const data = await api('/api/settings');
  state.settings = data.settings; state.advisors = data.advisors;
  if (!state.selectedAdvisorIds.length) state.selectedAdvisorIds = state.advisors.filter(a => a.enabled).slice(0, 4).map(a => a.id);
  els.settingsBaseUrl.value = state.settings.llama_base_url;
  els.settingsModel.value = state.settings.llama_model;
  els.settingsTemp.value = state.settings.temperature;
  els.settingsMaxTokens.value = state.settings.max_tokens_per_turn;
  els.settingsVoiceSpeed.value = state.settings.omnivoice_speed;
  els.settingsNumStep.value = state.settings.omnivoice_num_step;
  els.modelInfo.textContent = `${state.settings.llama_model} @ ${state.settings.llama_base_url}`;
  renderAdvisorGrid(); renderAdvisorStrip(); renderAdvisorSettings();
}

els.saveSettingsBtn.addEventListener('click', async () => {
  state.settings.llama_base_url = els.settingsBaseUrl.value.trim();
  state.settings.llama_model = els.settingsModel.value.trim();
  state.settings.temperature = Number(els.settingsTemp.value);
  state.settings.max_tokens_per_turn = Number(els.settingsMaxTokens.value);
  state.settings.omnivoice_speed = Number(els.settingsVoiceSpeed.value);
  state.settings.omnivoice_num_step = Number(els.settingsNumStep.value);
  await api('/api/settings', { method: 'POST', body: JSON.stringify({ settings: state.settings, advisors: state.advisors }) });
  closePanel('settingsDrawer'); await loadSettings();
});

els.startBtn.addEventListener('click', async () => {
  if (!els.topicInput.value.trim()) return alert('Adj meg egy témát.');
  if (!state.selectedAdvisorIds.length) return alert('Válassz legalább 1 tanácsadót.');
  els.startBtn.disabled = true;
  els.chat.innerHTML = ''; els.summaryPanel.classList.add('hidden');
  try {
    const res = await api('/api/debates', { method: 'POST', body: JSON.stringify({ topic: els.topicInput.value.trim(), advisor_ids: state.selectedAdvisorIds }) });
    state.debateId = res.debate_id; state.scores = Object.fromEntries(state.selectedAdvisorIds.map(x => [x, 0])); renderAdvisorStrip();
    els.stopBtn.disabled = false; els.statusBadge.textContent = 'Folyamatban'; els.statusBadge.className = 'badge live';
    if (state.eventSource) state.eventSource.close();
    state.eventSource = new EventSource(`/api/debates/${state.debateId}/stream`);
    state.eventSource.onmessage = (msg) => {
      const evt = JSON.parse(msg.data);
      if (evt.type === 'status' || evt.type === 'warning') appendStatus(evt.content || '');
      if (evt.type === 'message') appendMessage(evt);
      if (evt.type === 'audio_ready') attachAudio(evt);
      if (evt.type === 'summary') renderSummary(evt.meta || {});
      if (evt.type === 'complete') { appendStatus(evt.content || ''); els.stopBtn.disabled = true; els.startBtn.disabled = false; els.statusBadge.textContent = 'Lezárva'; els.statusBadge.className = 'badge idle'; state.eventSource?.close(); }
    };
    state.eventSource.onerror = () => { els.startBtn.disabled = false; };
  } catch (err) {
    els.startBtn.disabled = false;
    appendStatus(`Indítási hiba: ${err.message || err}`);
    throw err;
  }
});

els.stopBtn.addEventListener('click', async () => {
  if (!state.debateId) return; await api(`/api/debates/${state.debateId}/stop`, { method: 'POST' }); appendStatus('Lezárás kérve. Legfeljebb még 2 hozzászólás jön.');
});

loadSettings().catch(err => { console.error(err); els.modelInfo.textContent = 'Hiba a beállítások betöltésekor.'; });
