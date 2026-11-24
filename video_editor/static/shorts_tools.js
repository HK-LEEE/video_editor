// 쇼츠 도구 (오른쪽 패널 '쇼츠 도구' 탭): 소리 보관함 · 박자 컷 · 화면 글자 · Claude 문구 · 템플릿·일괄 적용 · 엔딩
// 편집기 본체(editor.html)가 내놓은 window.__editor 를 통해서만 프로젝트를 건드립니다.
(() => {
'use strict';
const ed = window.__editor;
const root = document.getElementById('toolsDlg');
if (!ed || !root) return;
const $ = (sel, el = root) => el.querySelector(sel);
const esc = ed.escapeHtml;
const api = async (path, opt = {}) => {
  const o = { ...opt };
  if (o.json !== undefined) { o.method = o.method || 'POST'; o.headers = { 'Content-Type': 'application/json' }; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch('/api' + path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : '요청이 올바르지 않아요.');
  return j;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function job(path, body, onTick) {   // 오래 걸리는 작업: 시작하고 끝날 때까지 기다림
  const j = await api(path, { json: body });
  for (let i = 0; i < 1800; i++) {
    const s = await api(`/ai/jobs/${j.job}`);
    if (s.status === 'done') return s.result;
    if (s.status === 'error') throw new Error(s.error || '실패했어요.');
    if (onTick) onTick(i);
    await sleep(700);
  }
  throw new Error('너무 오래 걸려요.');
}
const copy = async (text, btn) => {
  try { await navigator.clipboard.writeText(text); } catch { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
  if (btn) { const o = btn.textContent; btn.textContent = '복사됨 ✓'; setTimeout(() => (btn.textContent = o), 1200); }
};
const say = (id, msg, err) => { const el = $('#' + id); if (el) { el.textContent = msg || ''; el.className = 'tmsg' + (err ? ' err' : ''); } };

// ---------------------------------------------------------------- 뼈대
const css = document.createElement('style');
css.textContent = `
#toolsDlg { padding: 8px 10px 20px; }
#toolsDlg .tsub { display: flex; flex-wrap: wrap; gap: 2px; margin-bottom: 8px; border-bottom: 1px solid var(--line); }
#toolsDlg .tsub button { border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--muted); padding: 4px 8px; font-weight: 600; }
#toolsDlg .tsub button.lit { color: var(--ink); border-bottom-color: var(--accent); background: transparent; }
#toolsDlg h3 { margin: 12px 0 4px; font-size: 13px; }
#toolsDlg p { margin: 2px 0 6px; font-size: 12px; }
#toolsDlg .row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0; }
#toolsDlg .row > input[type=text], #toolsDlg .row > input[type=password], #toolsDlg .row > select { flex: 1; min-width: 0; }
#toolsDlg textarea { width: 100%; background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--r); color: inherit; font: inherit; padding: 5px 6px; resize: vertical; }
#toolsDlg input[type=text], #toolsDlg input[type=password] { background: var(--panel2); border: 1px solid var(--line2); border-radius: var(--r); padding: 4px 6px; width: 100%; }
#toolsDlg .tmsg { min-height: 1em; font-size: 12px; white-space: pre-wrap; color: var(--muted); } #toolsDlg .tmsg.err { color: var(--rec); }
#toolsDlg .lib { display: flex; align-items: center; gap: 6px; padding: 4px 6px; border-radius: 6px; background: var(--panel2); margin-top: 3px; cursor: grab; }
#toolsDlg .lib .n { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#toolsDlg .lib small { display: block; color: var(--muted); font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; }
#toolsDlg .lib button { padding: 2px 7px; }
#toolsDlg .card { border: 1px solid var(--line2); border-radius: 8px; padding: 6px 8px; margin: 6px 0; }
#toolsDlg .card b { font-size: 12.5px; }
#toolsDlg .chip { display: inline-block; background: var(--panel3, #333); border-radius: 10px; padding: 0 7px; font-size: 11px; margin: 1px 2px 1px 0; color: var(--muted); }
#toolsDlg img.thumb { width: 100%; border-radius: 8px; margin-top: 6px; }
#toolsDlg video { width: 100%; max-height: 300px; background: #000; border-radius: 8px; margin-top: 6px; }
#toolsDlg label.ck { display: flex; gap: 6px; align-items: center; margin: 3px 0; }
#toolsDlg input[type=number] { width: 64px; } #toolsDlg input[type=color] { width: 34px; height: 24px; padding: 0; border: 1px solid var(--line2); background: none; }
`;
document.head.appendChild(css);

const TABS = [['sound', '소리'], ['beat', '박자'], ['text', '글자'], ['copy', '문구'], ['tpl', '템플릿'], ['end', '엔딩']];
let cur = 'sound';
root.innerHTML = `<div class="tsub">${TABS.map(([k, v]) => `<button data-t="${k}">${v}</button>`).join('')}</div><div id="tBody"></div>`;
root.querySelectorAll('.tsub button').forEach((b) => (b.onclick = () => go(b.dataset.t)));
function go(t) { cur = t; root.querySelectorAll('.tsub button').forEach((b) => b.classList.toggle('lit', b.dataset.t === t)); PAGES[t](); }
window.__tools = { show: () => go(cur), go };
// 타임라인에서 지우거나 되돌려도 '글자' 탭 목록이 바로 맞춰지도록
window.addEventListener('editor:change', () => { if (cur === 'text' && root.isConnected && body()) pageText(); });
const body = () => $('#tBody');
const clipName = (c) => (ed.media[c.media] || {}).name || c.media;

// ================================================================ 1·8 소리 보관함
const S = { kind: 'sfx', q: '', mood: '', items: [], ready: false, audio: null, playing: null };
function stopPreview() { if (S.audio) { S.audio.pause(); S.audio = null; S.playing = null; } }
async function loadLibrary(force) {
  try {
    S.items = await api('/library');
    if (force || S.items.some((i) => !i.ready)) {
      say('lMsg', '기본 소리를 만드는 중이에요… (처음 한 번만, 몇 초 걸려요)');
      const r = await api('/library/ensure', { method: 'POST' });
      S.items = r.items;
      await ed.loadMedia();
      say('lMsg', '');
    } else if (!S.ready) await ed.loadMedia();
    S.ready = true;
  } catch (e) { say('lMsg', e.message, true); }
  if (cur === 'sound') renderLibList();
}
function pageSound() {
  body().innerHTML = `
    <p class="muted">직접 합성한 소리라 저작권 걱정이 없어요. 목록에서 타임라인으로 <b>끌어다 놓거나</b> ＋를 누르면 재생 위치에 들어가요.</p>
    <div class="row"><button data-k="sfx">효과음</button><button data-k="bgm">배경 음악</button><input type="text" id="lQ" placeholder="검색 (뿅, 웃음, 신남…)" value="${esc(S.q)}"></div>
    <div class="row" id="lMoodRow"><select id="lMood"></select><span class="muted" id="lCount"></span></div>
    <div id="lMsg" class="tmsg"></div><div id="lList"></div>
    <h3>내 소리 올리기</h3>
    <div class="row"><input type="text" id="uName" placeholder="이름"><select id="uKind"><option value="sfx">효과음</option><option value="bgm">배경 음악</option></select></div>
    <div class="row"><input type="text" id="uTags" placeholder="태그 (쉼표로 구분)"><input type="number" id="uBpm" placeholder="BPM" title="비우면 음악은 박자를 찾아서 채워요"></div>
    <div class="row"><button id="uGo">📁 파일 골라 올리기</button><input type="file" id="uFile" accept="audio/*,video/*" hidden></div>`;
  root.querySelectorAll('[data-k]').forEach((b) => (b.onclick = () => { S.kind = b.dataset.k; S.mood = ''; stopPreview(); renderLibList(); }));
  $('#lQ').oninput = (e) => { S.q = e.target.value; renderLibList(); };
  $('#lMood').onchange = (e) => { S.mood = e.target.value; renderLibList(); };
  $('#uGo').onclick = () => $('#uFile').click();
  $('#uFile').onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f); fd.append('kind', $('#uKind').value); fd.append('name', $('#uName').value || f.name.replace(/\.[^.]+$/, ''));
    fd.append('tags', $('#uTags').value); fd.append('bpm', $('#uBpm').value || '0');
    say('lMsg', '올리는 중…');
    try { const r = await fetch('/api/library/upload', { method: 'POST', body: fd }); const j = await r.json(); if (!r.ok) throw new Error(j.detail); await loadLibrary(false); await ed.loadMedia(); S.kind = j.lib; renderLibList(); say('lMsg', `'${j.name}'을 올렸어요.`); }
    catch (err) { say('lMsg', err.message, true); }
    e.target.value = '';
  };
  renderLibList(); loadLibrary(false);
}
function renderLibList() {
  if (!$('#lList')) return;
  root.querySelectorAll('[data-k]').forEach((b) => b.classList.toggle('lit', b.dataset.k === S.kind));
  const all = S.items.filter((i) => i.kind === S.kind);
  const moods = [...new Set(all.map((i) => i.mood).filter(Boolean))];
  $('#lMoodRow').style.display = S.kind === 'bgm' ? '' : 'none';
  $('#lMood').innerHTML = '<option value="">모든 분위기</option>' + moods.map((m) => `<option ${m === S.mood ? 'selected' : ''}>${esc(m)}</option>`).join('');
  const q = S.q.trim().toLowerCase();
  const list = all.filter((i) => (!S.mood || i.mood === S.mood) && (!q || [i.name, i.mood, ...(i.tags || [])].join(' ').toLowerCase().includes(q)));
  $('#lCount').textContent = `${list.length}개`;
  $('#lList').innerHTML = list.length ? '' : '<p class="muted">없어요.</p>';
  for (const it of list) {
    const el = document.createElement('div'); el.className = 'lib'; el.draggable = it.ready;
    el.innerHTML = `<button data-a="play" title="미리듣기" ${it.ready ? '' : 'disabled'}>${S.playing === it.id ? '■' : '▶'}</button>
      <span class="n">${it.user ? '🎵 ' : ''}${esc(it.name)}<small>${it.ready ? ed.fmt(it.duration) : '준비 중…'}${it.bpm ? ` · ${Math.round(it.bpm)}BPM` : ''}${it.mood ? ` · ${esc(it.mood)}` : ''} · ${esc((it.tags || []).slice(0, 3).join(' '))}</small></span>
      <button data-a="add" title="${it.kind === 'bgm' ? '영상 길이에 맞춰 배경 음악으로 넣기' : '재생 위치에 넣기'}" ${it.ready ? '' : 'disabled'}>＋</button>${it.user ? '<button data-a="del" title="지우기">✕</button>' : ''}`;
    el.ondragstart = (e) => { e.dataTransfer.setData('text/x-media', it.id); e.dataTransfer.effectAllowed = 'copy'; ed.setDragMedia(it.id); };
    el.ondragend = () => ed.endDrag();
    el.querySelector('[data-a=play]').onclick = () => {
      if (S.playing === it.id) { stopPreview(); return renderLibList(); }
      stopPreview(); S.audio = new Audio(`/api/media/${it.id}/file`); S.playing = it.id; S.audio.onended = () => { S.playing = null; renderLibList(); };
      S.audio.play().catch(() => {}); renderLibList();
    };
    el.querySelector('[data-a=add]').onclick = () => (it.kind === 'bgm' ? placeBgm(it) : ed.placeAtPlayhead(it.id));
    const del = el.querySelector('[data-a=del]');
    if (del) del.onclick = async () => { try { await api(`/library/${it.id}`, { method: 'DELETE' }); await loadLibrary(false); await ed.loadMedia(); } catch (e) { say('lMsg', e.message, true); } };
    $('#lList').appendChild(el);
  }
}
// 배경 음악: 영상 길이만큼 이어 붙이고(짧으면 반복) 마지막은 서서히 줄입니다. 말할 때 줄이기(덕킹)는 '음악·효과음' 트랙에 이미 있어요.
function placeBgm(it) {
  const m = ed.media[it.id]; if (!m) return ed.status('소리가 아직 준비 중이에요.', true);
  const L = Math.max(ed.projectLength(), 5);
  ed.replaceProject((p) => {
    const tr = p.tracks.filter((t) => t.kind === 'audio' && !t.voice).slice(-1)[0] || (() => { const t = ed.newTrack('audio', { name: '음악·효과음', duck: 0.5 }); p.tracks.push(t); return t; })();
    for (const c of p.clips.filter((c) => c.track === tr.id && c.bgm)) p.clips.splice(p.clips.indexOf(c), 1);
    let at = 0;
    while (at < L - 0.05) {
      const len = Math.min(m.duration, L - at), last = at + len >= L - 0.05;
      const c = { id: ed.uid(), track: tr.id, media: it.id, src_in: 0, src_out: len, start: at, voice: 'original', intensity: 1, gain: 0.55, link: null, bgm: true };
      if (last && len > 2) c.vol_keys = [{ t: Math.max(0, len - 1.5), v: 1 }, { t: len, v: 0 }];
      p.clips.push(c); at += len;
    }
    return p;
  });
  ed.status(`'${it.name}'을 영상 길이(${L.toFixed(1)}초)에 맞춰 넣었어요. ⌘Z로 되돌릴 수 있어요.`);
}

// ================================================================ 3 박자 컷
const B = { clip: null, res: null, bpm: null };
const audioClips = () => ed.project.clips.filter((c) => { const t = ed.project.tracks.find((t) => t.id === c.track); return t && t.kind === 'audio'; });
const toTimeline = (c, beats) => beats.filter((b) => b >= c.src_in && b < c.src_out).map((b) => c.start + (b - c.src_in) / (c.speed || 1));
async function findBeats(hint) {
  const c = ed.clipById(B.clip); if (!c) return say('bMsg', '기준이 될 음악 클립을 골라 주세요.', true);
  say('bMsg', '박자를 찾는 중…');
  try {
    const m = ed.media[c.media] || {};
    const r = await api('/beats', { json: { media: c.media, bpm: hint || (m.bpm || null) } });
    B.res = r; B.bpm = r.bpm;
    const tl = toTimeline(c, r.beats).map((t) => t + (+($('#bOff') || { value: 0 }).value) / 1000);
    ed.setBeats(tl);
    say('bMsg', r.beats.length ? `${Math.round(r.bpm)} BPM, 박자 ${tl.length}개를 찾았어요 (타임라인 눈금에 분홍 표시).` : '박자를 못 찾았어요. 리듬이 또렷한 음악인지 확인해 주세요.', !r.beats.length);
  } catch (e) { say('bMsg', e.message, true); }
  renderBeatTools();
}
function pageBeat() {
  const ac = audioClips();
  if (!B.clip || !ed.clipById(B.clip)) { const s = ed.clipById(ed.sel.clip); B.clip = (s && ac.includes(s) ? s : ac.find((c) => (ed.media[c.media] || {}).lib === 'bgm') || ac[0] || {}).id || null; }
  body().innerHTML = `
    <p class="muted">배경 음악의 박자를 찾아서, 영상 컷과 전환을 박자에 맞춰 자동으로 배치해요. (음악 보관함의 곡은 BPM을 이미 알고 있어 더 정확해요)</p>
    <h3>1. 기준 음악</h3>
    <div class="row"><select id="bClip">${ac.length ? ac.map((c) => `<option value="${c.id}" ${c.id === B.clip ? 'selected' : ''}>${esc(clipName(c))} · ${ed.fmt(c.start)}</option>`).join('') : '<option value="">타임라인에 음악 클립이 없어요</option>'}</select></div>
    <div class="row"><button id="bFind" class="primary">박자 찾기</button><button id="bHalf" title="박자를 절반 속도로 다시 잡아요">÷2</button><button id="bDbl" title="박자를 두 배 속도로 다시 잡아요">×2</button><button id="bClear">표시 지우기</button></div>
    <div class="row"><span class="muted">박자 위치 미세 조정</span><input type="number" id="bOff" value="0" step="10" title="ms. 분홍 표시가 소리의 쿵 소리보다 빠르거나 늦으면 맞춰요"><span class="muted">ms</span></div>
    <div id="bMsg" class="tmsg"></div><div id="bTools"></div>`;
  $('#bClip').onchange = (e) => { B.clip = e.target.value; B.res = null; ed.setBeats([]); };
  $('#bFind').onclick = () => findBeats();
  $('#bHalf').onclick = () => B.bpm && findBeats(B.bpm / 2);
  $('#bDbl').onclick = () => B.bpm && findBeats(B.bpm * 2);
  $('#bClear').onclick = () => { ed.setBeats([]); say('bMsg', ''); };
  $('#bOff').onchange = () => findBeats(B.bpm);
  renderBeatTools();
}
const TR_OPTS = '<option value="none">전환 없음 (컷)</option><option value="fade">페이드</option><option value="dip">검정을 거쳐서</option><option value="slideleft">밀기 ←</option><option value="slideright">밀기 →</option><option value="wipeleft">와이프 ←</option><option value="wiperight">와이프 →</option>';
function renderBeatTools() {
  const el = $('#bTools'); if (!el) return;
  if (!ed.beats.length) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <h3>2. 클립 경계를 박자에 맞추기</h3>
    <p class="muted">영상 클립이 끝나는 곳을 가까운 박자로 줄이거나(원본이 남으면 늘려서) 뒤 클립을 이어 붙여요. 자막도 같이 따라가요.</p>
    <div class="row"><span class="muted">한 클립 최소</span><select id="sMin"><option value="1">1박</option><option value="2">2박</option><option value="4" selected>4박</option><option value="8">8박</option></select><select id="sTr">${TR_OPTS}</select></div>
    <label class="ck"><input type="checkbox" id="sStart"> 첫 클립 시작도 박자에 맞추기</label>
    <div class="row"><button id="sGo" class="primary">박자에 맞춰 이어 붙이기</button></div>
    <h3>3. 고른 클립을 N박마다 자르기</h3>
    <p class="muted">타임라인에서 영상 클립을 고르고 누르세요 (안 고르면 만들 수 없어요). 자른 곳마다 전환을 걸 수 있어요.</p>
    <div class="row"><select id="pEvery"><option value="1">1박마다</option><option value="2">2박마다</option><option value="4" selected>4박마다</option><option value="8">8박마다</option></select><select id="pTr">${TR_OPTS}</select></div>
    <div class="row"><button id="pGo" class="primary">박자마다 자르기</button></div>`;
  const run = async (mode) => {
    const p = ed.projectBody();
    let ids = [];
    if (mode === 'split') {
      const s = ed.clipById(ed.sel.clip);
      if (!s) return say('bMsg', '자를 영상 클립을 먼저 타임라인에서 골라 주세요.', true);
      const vt = new Set(ed.project.tracks.filter((t) => t.kind === 'video').map((t) => t.id));
      const head = vt.has(s.track) ? s : ed.project.clips.find((c) => c.link && c.link === s.link && vt.has(c.track));
      if (!head) return say('bMsg', '영상 클립을 골라 주세요 (고른 것이 소리 클립이에요).', true);
      ids = [head.id];
    }
    const body = { project: p, beats: ed.beats, mode, ids, every: +($('#pEvery') || {}).value || 4, min_beats: +($('#sMin') || {}).value || 1,
      align_start: !!($('#sStart') || {}).checked, transition: (mode === 'snap' ? $('#sTr') : $('#pTr')).value, transition_dur: 0.2 };
    say('bMsg', '배치하는 중…');
    try {
      const r = await api('/beats/plan', { json: body });
      ed.replaceProject((pp) => { pp.clips = r.clips; pp.subtitles = r.subtitles; return pp; });
      say('bMsg', mode === 'snap' ? '클립 경계를 박자에 맞췄어요. ⌘Z로 되돌릴 수 있어요.' : `박자마다 잘랐어요 (클립 ${r.clips.length}개). ⌘Z로 되돌릴 수 있어요.`);
    } catch (e) { say('bMsg', e.message, true); }
  };
  $('#sGo').onclick = () => run('snap'); $('#pGo').onclick = () => run('split');
}

// ================================================================ 5 화면 글자 (훅 제목·워터마크·자유 글자)
// 글자는 타임라인의 '그래픽' 줄에 막대로 올라가요. 거기서 끌어 옮기고, 양 끝을 끌어 길이를 줄이고, 미리보기에서 눌러 고르면 속성 패널에서 모양을 고쳐요.
const OV_NAME = { title: '훅 제목', watermark: '워터마크', text: '글자' };
function addOverlay(kind, text) { return ed.addOverlay(kind, text); }
function pageText() {
  const ovs = ed.project.overlays;
  body().innerHTML = `
    <p class="muted">영상 위에 얹는 글자예요. 맨 위 큰 <b>훅 제목</b>(첫 3초), 구석의 <b>워터마크</b>, 자유 글자. 모두 타임라인 <b>'그래픽' 줄</b>에 막대로 보여요 — 끌어서 옮기고, 끝을 끌어 길이를 바꾸고, 눌러서 속성 패널에서 모양을 고쳐요. 쇼츠 화면에서는 앱 UI에 가려지지 않게 안전 영역 안으로 자동으로 옮겨요.</p>
    <div class="row"><button id="oT" class="primary">＋ 훅 제목</button><button id="oW">＋ 워터마크</button><button id="oX">＋ 글자</button></div>
    <div id="oList">${ovs.length ? '' : '<p class="muted">아직 없어요. 문구 탭에서 Claude가 제목을 뽑아 줄 수도 있어요.</p>'}</div>`;
  $('#oT').onclick = () => { addOverlay('title', '여기에 훅 제목'); pageText(); };
  $('#oW').onclick = () => { addOverlay('watermark'); pageText(); };
  $('#oX').onclick = () => { addOverlay('text', '글자'); pageText(); };
  for (const o of ovs) {
    const el = document.createElement('div'); el.className = 'lib'; el.style.cursor = 'pointer';
    el.innerHTML = `<span class="chip" style="background:${{ title: '#e11d48', watermark: '#64748b', text: '#0ea5e9' }[o.kind]};color:#fff">${OV_NAME[o.kind]}</span>
      <span class="n">${esc(o.text || '(빈 글)')}<small>${ed.fmt(o.start)} ~ ${o.end == null ? '끝까지' : ed.fmt(o.end)}${o.tpl ? ' · 템플릿' : ''}</small></span><button data-a="del" title="지우기">✕</button>`;
    el.onclick = (e) => { if (e.target.closest('[data-a=del]')) return; ed.selectOv(o.id); };
    el.querySelector('[data-a=del]').onclick = () => { ed.edit(() => (ed.project.overlays = ed.project.overlays.filter((x) => x.id !== o.id))); pageText(); };
    $('#oList').appendChild(el);
  }
}

// ================================================================ 5 Claude 문구 (훅 제목 · 썸네일 · 업로드 문구)
const C = { settings: null, res: null, topic: '', note: '' };
async function loadSettings() { try { C.settings = await api('/settings'); } catch { /* 서버가 없으면 무시 */ } }
function pageCopy() {
  const s = C.settings;
  body().innerHTML = `
    <p class="muted">자막(대본)을 Claude가 읽고 <b>첫 3초 훅 제목, 썸네일 문구, 유튜브 쇼츠·인스타 릴스 업로드 문구</b>를 뽑아요. 대본 글만 보내고 영상·소리는 보내지 않아요.</p>
    <div class="card"><b>Claude 연결</b>
      <div class="row"><span class="muted">방식</span><select id="kEng"></select></div>
      <div id="kStat" class="muted"></div>
      <div id="kKeyBox"><div class="row"><input type="password" id="kIn" placeholder="sk-ant-…" autocomplete="off"><button id="kSave">저장</button><button id="kDel" title="저장한 키 지우기">지우기</button></div></div>
      <div class="row"><span class="muted">모델</span><select id="kModel"></select></div>
      <p class="muted" id="kCliHelp">기본은 이 컴퓨터의 <b>Claude Code</b>(<code>claude</code> 명령)를 내 Claude 로그인으로 불러 써요. 키가 필요 없어요. 처음이면 터미널에서 <kbd>claude</kbd> 를 한 번 실행해 로그인하세요.</p>
      <p class="muted" id="kKeyHelp">키는 <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener" style="color:var(--accent)">console.anthropic.com</a>에서 만들어요. ① 터미널에서 <kbd>export ANTHROPIC_API_KEY=sk-ant-…</kbd> 후 앱 실행, 또는 ② 위 칸에 붙여넣기 (이 컴퓨터의 <code>workspace/settings.json</code>에만 저장, 코드에는 안 들어가요).</p></div>
    <div class="row"><input type="text" id="cTopic" placeholder="채널·영상 주제 (선택, 예: 고양이 일상 브이로그)" value="${esc(C.topic)}"></div>
    <div class="row"><input type="text" id="cNote" placeholder="추가 요청 (선택, 예: 반말로, 웃기게)" value="${esc(C.note)}"></div>
    <div class="row"><button id="cGo" class="primary">대본에서 뽑기</button></div>
    <div id="cMsg" class="tmsg"></div><div id="cOut"></div>`;
  paintKey();
  $('#kSave').onclick = async () => { try { C.settings = await api('/settings', { method: 'PUT', json: { api_key: $('#kIn').value } }); $('#kIn').value = ''; paintKey(); say('cMsg', '키를 저장했어요.'); } catch (e) { say('cMsg', e.message, true); } };
  $('#kDel').onclick = async () => { C.settings = await api('/settings', { method: 'PUT', json: { api_key: '' } }); paintKey(); };
  $('#kEng').onchange = async (e) => { C.settings = await api('/settings', { method: 'PUT', json: { engine: e.target.value } }); paintKey(); };
  $('#kModel').onchange = async (e) => { C.settings = await api('/settings', { method: 'PUT', json: { model: e.target.value } }); };
  $('#cTopic').oninput = (e) => (C.topic = e.target.value); $('#cNote').oninput = (e) => (C.note = e.target.value);
  $('#cGo').onclick = genCopy;
  if (C.res) renderCopy();
  if (!C.settings) loadSettings().then(() => cur === 'copy' && paintKey());
}
function paintKey() {
  const s = C.settings; if (!s || !$('#kStat')) return;
  const viaApi = s.active_engine === 'api';
  $('#kEng').innerHTML = Object.entries(s.engines).map(([k, v]) => `<option value="${k}" ${k === s.engine ? 'selected' : ''}>${esc(v)}</option>`).join('');
  $('#kKeyBox').style.display = $('#kKeyHelp').style.display = viaApi || s.engine === 'api' ? '' : 'none';
  $('#kCliHelp').style.display = viaApi ? 'none' : '';
  $('#kStat').textContent = viaApi ? (s.has_key ? `✅ 키가 있어요 (${s.source === 'env' ? '환경변수' : '저장된 키'} ${s.masked})` : '⚠ 키가 아직 없어요. 아래에 넣어 주세요.')
    : (s.has_cli ? '✅ Claude Code를 찾았어요. 로그인되어 있으면 바로 쓸 수 있어요.' : '⚠ Claude Code(claude 명령)를 못 찾았어요. ' + s.no_cli_help);
  $('#kModel').innerHTML = Object.entries(s.models).map(([k, v]) => `<option value="${k}" ${k === s.model ? 'selected' : ''}>${esc(v)}</option>`).join('');
}
async function genCopy() {
  if (!C.settings) await loadSettings();
  if (C.settings && !C.settings.ready) return say('cMsg', C.settings.help, true);
  const cues = ed.project.subtitles.filter((c) => c.text.trim()).map((c) => ({ start: c.start, text: c.text }));
  if (!cues.length) return say('cMsg', '대본이 비어 있어요. AI 도구에서 자동 자막을 만들거나 자막을 넣어 주세요.', true);
  $('#cGo').disabled = true; say('cMsg', 'Claude가 쓰는 중… (10~60초)');
  try { C.res = await job('/ai/copy', { cues, duration: ed.projectLength(), topic: C.topic, note: C.note, model: C.settings && C.settings.model }); say('cMsg', `완성! (${C.res.model}) 마음대로 고쳐서 쓰세요.`); renderCopy(); }
  catch (e) { say('cMsg', e.message, true); }
  $('#cGo').disabled = false;
}
function renderCopy() {
  const r = C.res; if (!r || !$('#cOut')) return;
  const hashes = (a) => a.join(' ');
  $('#cOut').innerHTML = `
    <h3>① 첫 3초 훅 제목</h3>${r.hook_titles.map((h, i) => `<div class="card"><input type="text" data-h="${i}" value="${esc(h.text)}"><div class="muted" style="font-size:11px">${esc(h.why)}</div>
      <div class="row"><button data-use="${i}" class="primary">화면 위에 넣기</button><button data-cp="h${i}">복사</button></div></div>`).join('')}
    <h3>② 썸네일</h3><div class="card"><input type="text" id="tText" value="${esc(r.thumbnail.text)}"><p class="muted">💡 ${esc(r.thumbnail.idea)}</p>
      <div class="row"><span class="muted">장면 시각</span><input type="number" id="tAt" step="0.1" min="0" value="${(+r.thumbnail.best_time || 0).toFixed(1)}"><span class="muted">초</span><button id="tGo" class="primary">썸네일 만들기</button></div><div id="tOut"></div></div>
    <h3>③ 유튜브 쇼츠</h3><div class="card"><input type="text" id="yT" value="${esc(r.youtube.title)}"><textarea id="yD" rows="4">${esc(r.youtube.description)}</textarea><input type="text" id="yH" value="${esc(hashes(r.youtube.hashtags))}">
      <div class="row"><button data-cp="y">제목·설명·태그 모두 복사</button></div></div>
    <h3>④ 인스타그램 릴스</h3><div class="card"><textarea id="iC" rows="5">${esc(r.instagram.caption)}</textarea><input type="text" id="iH" value="${esc(hashes(r.instagram.hashtags))}">
      <div class="row"><button data-cp="i">캡션·태그 모두 복사</button></div></div>`;
  $('#cOut').querySelectorAll('[data-use]').forEach((b) => (b.onclick = () => {
    const txt = $(`[data-h="${b.dataset.use}"]`).value;
    ed.edit(() => { ed.project.overlays = ed.project.overlays.filter((o) => o.kind !== 'title'); });
    addOverlay('title', txt); ed.status('훅 제목을 맨 위에 넣었어요 (처음 3초). 타임라인 그래픽 줄에서 길이를 바꾸고, 눌러서 모양을 고칠 수 있어요.');
  }));
  $('#cOut').querySelectorAll('[data-cp]').forEach((b) => (b.onclick = () => {
    const k = b.dataset.cp;
    copy(k[0] === 'h' ? $(`[data-h="${k.slice(1)}"]`).value : k === 'y' ? `${$('#yT').value}\n\n${$('#yD').value}\n\n${$('#yH').value}` : `${$('#iC').value}\n\n${$('#iH').value}`, b);
  }));
  $('#tGo').onclick = async () => {
    say('cMsg', '썸네일을 만드는 중…');
    try { const j = await api('/thumbnail', { json: { project: ed.projectBody(), t: +$('#tAt').value || 0, text: $('#tText').value } }); say('cMsg', '');
      $('#tOut').innerHTML = `<img class="thumb" src="${j.url}"><div class="row"><a href="${j.url}" download style="color:var(--accent)">PNG 내려받기</a></div>`; }
    catch (e) { say('cMsg', e.message, true); }
  };
}

// ================================================================ 6 템플릿 · 일괄 적용
const T = { list: [], projects: [], endings: [], sel: new Set(), projSel: new Set(), mediaSel: new Set(), tplSel: null, last: null, channel: (() => { try { return localStorage.getItem('voxshift.channel') || ''; } catch { return ''; } })() };
async function loadTpl() {
  try { [T.list, T.projects, T.endings] = await Promise.all([api('/templates'), api('/projects'), api('/endings').then((r) => r.items)]); } catch (e) { say('mMsg', e.message, true); }
  if (cur === 'tpl') paintTpl();
}
const videoMedia = () => Object.values(ed.media).filter((m) => m.kind === 'video' && !m.lib && !m.hidden).sort((a, b) => a.name.localeCompare(b.name));
const chn = (tpl) => `<span class="chn" data-tpl="${esc(tpl)}">${esc(tpl.replace('{channel}', T.channel || '내 채널'))}</span>`;   // 채널 이름을 적으면 그 자리만 바로 바뀜
const summary = (t) => [t.subtitle_style && '자막 스타일', t.hook && '훅 제목 모양', t.intro && `인트로 ${esc((ed.media[t.intro] || {}).name || '')}`, t.intro_card && '인트로 카드', t.outro && `아웃트로 ${esc((ed.media[t.outro] || {}).name || '')}`, t.outro_card && '아웃트로 카드',
  t.watermark && `워터마크 ${chn(t.watermark.text)}`, t.ending && `엔딩 ${esc((T.endings.find((e) => e.id === t.ending) || {}).name || '')}`, t.ending_card && '엔딩 애니메이션'].filter(Boolean);
// 템플릿 모양을 작은 세로 화면으로 미리 보여줘요 (훅 제목 바 · 자막 · 워터마크)
function mock(t) {
  const st = t.subtitle_style, h = t.hook, w = t.watermark, ic = t.intro_card;
  const rgba = (hex, a) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;
  const bg = ic ? `linear-gradient(${ic.bg1},${ic.bg2})` : 'linear-gradient(#2a2f45,#12141f)';
  return `<div style="position:relative;flex:none;width:54px;height:96px;border-radius:6px;overflow:hidden;background:${bg};border:1px solid var(--line2)">
    ${h ? `<div style="position:absolute;left:50%;top:${h.y * 100}%;transform:translate(-50%,-50%);white-space:nowrap;font-size:6px;font-weight:700;padding:2px 4px;border-radius:2px;color:${h.color};background:${rgba(h.bg_color, h.bg_opacity)};${h.bg_opacity < 0.1 ? '-webkit-text-stroke:.4px #000;' : ''}">훅 제목</div>` : ''}
    ${st ? `<div style="position:absolute;left:50%;top:${Math.min(88, st.y * 100)}%;transform:translate(-50%,-50%);white-space:nowrap;font-size:6.5px;font-weight:${st.bold ? 700 : 400};padding:${st.bg_opacity > 0 ? '1.5px 3px' : '0'};color:${st.karaoke ? st.hl_color : st.color};background:${st.bg_opacity > 0 ? rgba(st.bg_color, st.bg_opacity) : 'transparent'};-webkit-text-stroke:${st.outline > 0 ? Math.max(0.3, st.outline * 2.2) + 'px ' + st.outline_color : '0'};paint-order:stroke fill">자막 예시</div>` : ''}
    ${w ? `<div style="position:absolute;left:${w.x * 100}%;top:${w.y * 100}%;transform:translate(-50%,-50%);white-space:nowrap;font-size:4px;opacity:${w.opacity};color:${w.color}">${chn(w.text)}</div>` : ''}
  </div>`;
}
function pageTpl() {
  body().innerHTML = `
    <p class="muted">자막 스타일·훅 제목·워터마크·인트로·아웃트로·구독 엔딩을 한 벌로 묶은 <b>템플릿</b>이에요. <b>샘플 5종</b>을 눌러 바로 입혀 보세요. 다시 입혀도 겹치지 않고 새로 바뀌고, 입힌 훅 제목·워터마크·엔딩은 모두 타임라인에 막대·클립으로 보여서 따로 고칠 수 있어요.</p>
    <div class="row"><span class="muted">채널 이름</span><input type="text" id="tChannel" placeholder="워터마크·인트로에 들어가요 (비우면 '내 채널')" value="${esc(T.channel)}" maxlength="30"></div>
    <div id="tList"></div><div id="mMsg" class="tmsg"></div>
    <h3>새 템플릿 저장</h3><div class="card">
      <input type="text" id="nName" placeholder="템플릿 이름 (예: 내 채널 기본)">
      <label class="ck"><input type="checkbox" id="nSub" checked> 지금 자막 스타일 담기</label>
      <label class="ck"><input type="checkbox" id="nHook"> 지금 훅 제목 모양 담기</label>
      <div class="row"><span class="muted">인트로</span><select id="nIntro"></select></div>
      <div class="row"><span class="muted">아웃트로</span><select id="nOutro"></select></div>
      <div class="row"><span class="muted">워터마크</span><input type="text" id="nWm" placeholder="비우면 없음 (예: @내채널)"></div>
      <div class="row"><span class="muted">엔딩</span><select id="nEnd"></select></div>
      <label class="ck"><input type="checkbox" id="nGlobal"> 모든 워크스페이스 공용 (끄면 '${esc(ed.workspace.space_name || '이')}' 워크스페이스 전용)</label>
      <div class="row"><button id="nSave" class="primary">템플릿 저장</button></div></div>
    <h3>일괄 적용 대상</h3>
    <p class="muted">여러 프로젝트에 흩어진 보관본도 한꺼번에 골라 입힐 수 있어요. ① 보관한 프로젝트를 고르거나 ② 보관함 영상을 골라 한 번에 새 프로젝트로 만들어요.</p>
    <div class="row"><button id="pSave">현재 프로젝트 보관하기</button><input type="text" id="pName" placeholder="보관 이름"></div>
    <div id="pList"></div>
    <div class="row"><b style="font-size:12px">보관함 영상으로 새 프로젝트 만들기</b></div><div id="vList"></div>
    <div class="row"><button id="bApply" class="primary">선택한 대상에 일괄 적용</button></div>
    <div class="row"><select id="bFmt"><option value="shorts">YouTube 쇼츠 1080×1920</option><option value="reels">Instagram 릴스 1080×1920</option><option value="full">원본 비율</option></select><button id="bExp">선택한 프로젝트 모두 내보내기</button></div>
    <div id="bRes"></div>`;
  $('#tChannel').oninput = (e) => { T.channel = e.target.value; try { localStorage.setItem('voxshift.channel', T.channel); } catch {} root.querySelectorAll('.chn').forEach((el) => (el.textContent = el.dataset.tpl.replace('{channel}', T.channel || '내 채널'))); };
  $('#nSave').onclick = saveTpl; $('#pSave').onclick = saveProj; $('#bApply').onclick = batchApply; $('#bExp').onclick = batchExport;
  paintTpl(); loadTpl();
}
function paintTpl() {
  if (!$('#tList')) return;
  $('#tList').innerHTML = '';
  const section = (title) => { const h = document.createElement('h3'); h.textContent = title; $('#tList').appendChild(h); };
  const mine = T.list.filter((t) => !t.builtin);
  if (T.list.some((t) => t.builtin)) section('샘플 템플릿');
  for (const t of [...T.list.filter((t) => t.builtin), ...(mine.length ? [null] : []), ...mine]) {
    if (t === null) { section('내 템플릿'); continue; }
    const el = document.createElement('div'); el.className = 'card';
    el.innerHTML = `<div style="display:flex;gap:8px">${t.builtin ? mock(t) : ''}<div style="flex:1;min-width:0">
      <div class="row" style="margin-top:0"><label class="ck" style="flex:1;margin:0"><input type="radio" name="tsel" value="${t.id}" ${T.tplSel === t.id ? 'checked' : ''} title="일괄 적용 대상으로 쓸 템플릿"><b>${esc(t.name)}</b>${t.builtin ? '' : `<span class="chip">${t.scope === 'global' ? '🌐 공용' : '이 워크스페이스'}</span>`}</label>${t.builtin ? '' : '<button data-a="del">✕</button>'}</div>
      ${t.desc ? `<div class="muted" style="font-size:11px;margin:2px 0">${esc(t.desc)}</div>` : ''}
      <div>${summary(t).map((x) => `<span class="chip">${x}</span>`).join('') || '<span class="muted">(비어 있음)</span>'}</div>
      <div class="row"><button data-a="apply" class="primary">지금 프로젝트에 입히기</button></div></div></div>`;
    el.querySelector('input').onchange = () => (T.tplSel = t.id);
    el.querySelector('[data-a=apply]').onclick = async () => {
      say('mMsg', '입히는 중… (인트로·엔딩 애니메이션을 만들어요, 몇 초 걸려요)');
      try {
        const np = await api('/template/apply', { json: { project: ed.projectBody(), template_id: t.id, channel: T.channel.trim(), hook_text: '' } });
        ed.replaceProject((p) => ({ ...p, clips: np.clips, tracks: np.tracks, subtitles: np.subtitles, subtitle_style: np.subtitle_style, overlays: np.overlays })); await ed.loadMedia();
        say('mMsg', `'${t.name}'을 입혔어요. 훅 제목·워터마크는 타임라인 그래픽 줄, 인트로·엔딩은 영상 줄에 보여요. 훅 제목 글은 눌러서 고치세요. ⌘Z로 되돌릴 수 있어요.`);
      } catch (e) { say('mMsg', e.message, true); }
    };
    const del = el.querySelector('[data-a=del]');
    if (del) del.onclick = async () => { await api(`/templates/${t.id}`, { method: 'DELETE' }); T.list = T.list.filter((x) => x.id !== t.id); paintTpl(); };
    $('#tList').appendChild(el);
  }
  const vm = videoMedia(), opt = (list, none) => `<option value="">${none}</option>` + list.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join('');
  const keep = ['nIntro', 'nOutro', 'nEnd'].map((id) => ($('#' + id) || {}).value);
  $('#nIntro').innerHTML = opt(vm, '없음'); $('#nOutro').innerHTML = opt(vm, '없음');
  $('#nEnd').innerHTML = opt(T.endings, '없음');
  ['nIntro', 'nOutro', 'nEnd'].forEach((id, i) => { if (keep[i]) $('#' + id).value = keep[i]; });
  $('#pList').innerHTML = T.projects.length ? '' : '<p class="muted">보관한 프로젝트가 없어요.</p>';
  for (const p of T.projects) {
    const el = document.createElement('div'); el.className = 'lib'; el.style.cursor = 'default';
    el.innerHTML = `<input type="checkbox" ${T.projSel.has(p.id) ? 'checked' : ''}><span class="n">${esc(p.name)}<small>${p.current ? '' : `📁 ${esc(p.workspace_name)} · `}클립 ${p.clips}개 · ${p.length.toFixed(1)}초</small></span><button data-a="open">${p.current ? '열기' : '그 프로젝트로'}</button><button data-a="del">✕</button>`;
    el.querySelector('input').onchange = (e) => (e.target.checked ? T.projSel.add(p.id) : T.projSel.delete(p.id));
    el.querySelector('[data-a=open]').onclick = async () => { try { if (!p.current) { if (confirm(`'${p.name}'은 '${p.workspace_name}' 프로젝트의 보관본이에요.\n그 프로젝트로 바꿀까요?`)) await window.__wsPicker.openProject(p.workspace); return; } const r = await api(`/projects/${p.id}`); ed.replaceProject(() => r.project); await ed.loadMedia(); say('mMsg', `'${r.name}'을 열었어요.`); } catch (e) { say('mMsg', e.message, true); } };
    el.querySelector('[data-a=del]').onclick = async () => { await api(`/projects/${p.id}`, { method: 'DELETE' }); T.projSel.delete(p.id); loadTpl(); };
    $('#pList').appendChild(el);
  }
  $('#vList').innerHTML = vm.length ? '' : '<p class="muted">보관함에 영상이 없어요.</p>';
  for (const m of vm) {
    const el = document.createElement('label'); el.className = 'ck'; el.innerHTML = `<input type="checkbox" ${T.mediaSel.has(m.id) ? 'checked' : ''}> ${esc(m.name)} <span class="muted">${ed.fmt(m.duration)}</span>`;
    el.querySelector('input').onchange = (e) => (e.target.checked ? T.mediaSel.add(m.id) : T.mediaSel.delete(m.id));
    $('#vList').appendChild(el);
  }
}
async function saveTpl() {
  const name = $('#nName').value.trim(); if (!name) return say('mMsg', '템플릿 이름을 적어 주세요.', true);
  const t = { name, intro: $('#nIntro').value || null, outro: $('#nOutro').value || null, ending: $('#nEnd').value || null, scope: $('#nGlobal').checked ? 'global' : 'space' };
  if ($('#nSub').checked) t.subtitle_style = ed.project.subtitle_style;
  const wm = $('#nWm').value.trim();
  if (wm) { const w = ed.project.overlays.find((o) => o.kind === 'watermark'); t.watermark = w ? { text: wm, x: w.x, y: w.y, size: w.size, opacity: w.opacity, color: w.color } : { text: wm }; }
  if ($('#nHook').checked) {
    const h = ed.project.overlays.find((o) => o.kind === 'title');
    if (!h) return say('mMsg', '훅 제목이 아직 없어요. 글자 탭에서 먼저 만들어 주세요.', true);
    t.hook = { y: h.y, size: h.size, color: h.color, bg_color: h.bg_color, bg_opacity: h.bg_opacity, outline: h.outline, start: h.start, end: h.end };
  }
  try { await api('/templates', { json: t }); $('#nName').value = ''; say('mMsg', `'${name}' 템플릿을 저장했어요.`); loadTpl(); } catch (e) { say('mMsg', e.message, true); }
}
async function saveProj() {
  try { const name = $('#pName').value.trim() || `프로젝트 ${T.projects.length + 1}`; await api('/projects', { json: { name, project: ed.projectBody() } }); $('#pName').value = ''; say('mMsg', `'${name}'을 보관했어요.`); loadTpl(); }
  catch (e) { say('mMsg', e.message, true); }
}
function results(el, rs) {
  el.innerHTML = rs.map((r) => `<div class="lib" style="cursor:default"><span class="n">${r.ok ? '✅' : '⚠'} ${esc(r.name)}<small>${r.ok ? (r.url ? `<a href="${r.url}" style="color:var(--accent)">내려받기</a>` : '완료') + (r.path ? ` · <button data-reveal="${esc(r.path)}">Finder에서 열기</button><br><code style="word-break:break-all">${esc(r.path)}</code>` : '') : esc(r.error || '')}</small></span></div>`).join('');
}
async function batchApply() {
  if (!T.tplSel) return say('mMsg', '위 목록에서 입힐 템플릿을 먼저 골라 주세요 (동그라미).', true);
  if (!T.projSel.size && !T.mediaSel.size) return say('mMsg', '대상 프로젝트나 영상을 하나 이상 골라 주세요.', true);
  $('#bApply').disabled = true; say('mMsg', '일괄 적용 중…');
  try {
    const r = await job('/template/batch', { template_id: T.tplSel, project_ids: [...T.projSel], media_ids: [...T.mediaSel], name_prefix: '쇼츠 ', channel: T.channel.trim() });
    results($('#bRes'), r.results); T.mediaSel.clear(); say('mMsg', `${r.results.filter((x) => x.ok).length}/${r.results.length}개에 적용했어요.`); await loadTpl();
    for (const x of r.results) if (x.ok) T.projSel.add(x.id);
    paintTpl();
  } catch (e) { say('mMsg', e.message, true); }
  $('#bApply').disabled = false;
}
async function batchExport() {
  if (!T.projSel.size) return say('mMsg', '내보낼 보관한 프로젝트를 골라 주세요.', true);
  $('#bExp').disabled = true; say('mMsg', '내보내는 중… (프로젝트마다 몇십 초)');
  try { const r = await job('/batch/export', { project_ids: [...T.projSel], format: $('#bFmt').value }); results($('#bRes'), r.results); say('mMsg', '내보내기가 끝났어요.'); }
  catch (e) { say('mMsg', e.message, true); }
  $('#bExp').disabled = false;
}

// ================================================================ 7 구독·좋아요 엔딩
const E = { kinds: {}, defaults: {}, items: [], draft: null };
async function loadEndings() { try { const r = await api('/endings'); E.kinds = r.kinds; E.defaults = r.defaults; E.items = r.items; if (!E.draft) E.draft = { name: '', ...r.defaults }; } catch (e) { say('eMsg', e.message, true); } if (cur === 'end') paintEnd(); }
function pageEnd() { body().innerHTML = '<div id="eBox"><p class="muted">불러오는 중…</p></div>'; E.draft ? paintEnd() : null; loadEndings(); }
function paintEnd() {
  const d = E.draft; if (!d || !$('#eBox')) return;
  $('#eBox').innerHTML = `
    <p class="muted">영상 맨 끝에 붙는 <b>구독·좋아요 애니메이션</b> (직접 그린 원본 디자인). 모양과 문구를 고쳐 프리셋으로 저장하고, 템플릿에 넣어서 여러 영상에 한 번에 붙여요.</p>
    <div class="card">
      <div class="row"><span class="muted">모양</span><select id="eKind">${Object.entries(E.kinds).map(([k, v]) => `<option value="${k}" ${k === d.kind ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
      <div class="row"><span class="muted">큰 글자</span><input type="text" id="eText" value="${esc(d.text)}" maxlength="30"></div>
      <div class="row"><span class="muted">작은 글자</span><input type="text" id="eSub" value="${esc(d.sub)}" maxlength="30"></div>
      <div class="row"><span class="muted">포인트 색</span><input type="color" id="eAcc" value="${d.accent}"><span class="muted">배경</span><input type="color" id="eB1" value="${d.bg1}"><input type="color" id="eB2" value="${d.bg2}"><span class="muted">길이</span><input type="number" id="eDur" step="0.5" min="1.5" max="8" value="${d.duration}"><span class="muted">초</span></div>
      <div class="row"><button id="ePrev">▶ 미리보기</button><input type="text" id="eName" placeholder="프리셋 이름" value="${esc(d.name)}"><button id="eSave" class="primary">프리셋 저장</button></div>
      <label class="ck"><input type="checkbox" id="eGlobal" ${d.scope === 'global' ? 'checked' : ''}> 모든 워크스페이스 공용</label>
      <div id="ePrevBox"></div></div>
    <div id="eMsg" class="tmsg"></div>
    <h3>저장한 엔딩</h3><div id="eList">${E.items.length ? '' : '<p class="muted">아직 없어요.</p>'}</div>`;
  const read = () => { d.kind = $('#eKind').value; d.text = $('#eText').value; d.sub = $('#eSub').value; d.accent = $('#eAcc').value; d.bg1 = $('#eB1').value; d.bg2 = $('#eB2').value; d.duration = +$('#eDur').value || 3; d.name = $('#eName').value; d.scope = $('#eGlobal').checked ? 'global' : 'space'; };
  root.querySelectorAll('#eBox input, #eBox select').forEach((i) => i.addEventListener('change', read));
  $('#ePrev').onclick = async () => {
    read(); say('eMsg', '미리보기를 만드는 중…');
    try { const r = await job('/endings/preview', { ...d, name: d.name || 'x' }); say('eMsg', ''); $('#ePrevBox').innerHTML = `<video src="${r.url}" controls autoplay loop muted playsinline></video>`; }
    catch (e) { say('eMsg', e.message, true); }
  };
  $('#eSave').onclick = async () => {
    read(); if (!d.name.trim()) return say('eMsg', '프리셋 이름을 적어 주세요.', true);
    try { await api('/endings', { json: d }); say('eMsg', `'${d.name}' 프리셋을 저장했어요.`); loadEndings(); } catch (e) { say('eMsg', e.message, true); }
  };
  for (const e of E.items) {
    const el = document.createElement('div'); el.className = 'card';
    el.innerHTML = `<div class="row"><b style="flex:1">${esc(e.name)}</b><span class="muted">${e.scope === 'global' ? '🌐 공용 · ' : ''}${esc(E.kinds[e.kind] || e.kind)} · ${e.duration}초</span></div><div class="row"><button data-a="add" class="primary">영상 끝에 붙이기</button><button data-a="edit">불러와 고치기</button><button data-a="del">✕</button></div>`;
    el.querySelector('[data-a=add]').onclick = async () => {
      say('eMsg', '엔딩을 만들어 붙이는 중…');
      try { const np = await api('/endings/apply', { json: { project: ed.projectBody(), ending_id: e.id } }); ed.replaceProject((p) => ({ ...p, clips: np.clips, tracks: np.tracks })); await ed.loadMedia(); say('eMsg', `'${e.name}'을 영상 끝에 붙였어요. ⌘Z로 되돌릴 수 있어요.`); }
      catch (err) { say('eMsg', err.message, true); }
    };
    el.querySelector('[data-a=edit]').onclick = () => { E.draft = { ...E.defaults, ...e }; paintEnd(); };
    el.querySelector('[data-a=del]').onclick = async () => { await api(`/endings/${e.id}`, { method: 'DELETE' }); loadEndings(); };
    $('#eList').appendChild(el);
  }
}

const PAGES = { sound: pageSound, beat: pageBeat, text: pageText, copy: pageCopy, tpl: pageTpl, end: pageEnd };

})();
