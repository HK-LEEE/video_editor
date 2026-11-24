// 워크스페이스 · 프로젝트 화면: 왼쪽은 워크스페이스 목록, 오른쪽은 고른 워크스페이스의 프로젝트 표 (탐색기 '자세히 보기'처럼).
// editor.html 에서는 위쪽 막대의 두 버튼(#spaceBtn, #projBtn)과 window.__WS(열린 워크스페이스·프로젝트)만 씁니다.
(() => {
'use strict';
const WSI = window.__WS || { id: '', name: '', space: '', space_name: '' };
const $ = (id) => document.getElementById(id);
const spaceBtn = $('spaceBtn'), projBtn = $('projBtn');
if (!spaceBtn || !projBtn) return;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
spaceBtn.querySelector('.nm').textContent = WSI.space_name || '워크스페이스';
projBtn.querySelector('.nm').textContent = WSI.name || '프로젝트';

const api = async (path, opt = {}) => {
  const o = { ...opt };
  if (o.json !== undefined) { o.method = o.method || 'POST'; o.headers = { 'Content-Type': 'application/json' }; o.body = JSON.stringify(o.json); delete o.json; }
  const r = await fetch('/api' + path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(typeof j.detail === 'string' ? j.detail : '요청이 올바르지 않아요.'); e.status = r.status; throw e; }
  return j;
};
const q = (space) => (space ? `?space=${encodeURIComponent(space)}` : '');

const css = document.createElement('style');
css.textContent = `
dialog#wsDlg { background: var(--panel); color: var(--ink); border: 1px solid var(--line2); border-radius: 12px; padding: 0; width: min(1020px, 96vw); height: min(640px, 88vh); box-shadow: 0 24px 60px #000b; overflow: hidden; }
dialog#wsDlg[open] { display: flex; flex-direction: column; }
dialog#wsDlg::backdrop { background: rgba(0,0,0,.6); }
#wsDlg .wh { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--line); } #wsDlg .wh b { font-size: 15px; flex: 1; }
#wsDlg .wbody { flex: 1; min-height: 0; display: grid; grid-template-columns: 250px minmax(0, 1fr); }
#wsDlg .wl { border-right: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; background: var(--bg); }
#wsDlg .wr { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
#wsDlg .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; padding: 7px 10px; border-bottom: 1px solid var(--line); }
#wsDlg .bar .t { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--accent2); flex: 1; min-width: 80px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#wsDlg .bar button { padding: 3px 9px; }
#wsDlg .list { flex: 1; overflow: auto; padding: 4px; }
#wsDlg .si { display: flex; align-items: center; gap: 6px; padding: 7px 9px; border-radius: 8px; cursor: default; border: 1px solid transparent; user-select: none; }
#wsDlg .si:hover { background: var(--panel2); } #wsDlg .si.sel { background: var(--panel3, #2a2f3b); border-color: var(--line2); }
#wsDlg .si .n { flex: 1; min-width: 0; } #wsDlg .si .n b { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
#wsDlg .si small, #wsDlg .muted2 { color: var(--muted); font-size: 11px; }
#wsDlg .si .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); flex: none; } #wsDlg .si .dot.off { background: transparent; }
#wsDlg .tbl { flex: 1; overflow: auto; }
#wsDlg table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
#wsDlg th { position: sticky; top: 0; background: var(--panel); text-align: left; font-weight: 600; color: var(--muted); padding: 6px 10px; border-bottom: 1px solid var(--line); cursor: pointer; white-space: nowrap; user-select: none; }
#wsDlg th:hover { color: var(--ink); } #wsDlg th .ar { color: var(--accent); }
#wsDlg th.r, #wsDlg td.r { text-align: right; font-variant-numeric: tabular-nums; }
#wsDlg td { padding: 6px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; cursor: default; user-select: none; }
#wsDlg td.nm { max-width: 320px; overflow: hidden; text-overflow: ellipsis; }
#wsDlg tr.pr:hover td { background: var(--panel2); } #wsDlg tr.pr.sel td { background: var(--panel3, #2a2f3b); } #wsDlg tr.pr.cur td.nm { color: var(--accent2); font-weight: 600; }
#wsDlg .stat { display: flex; gap: 12px; padding: 6px 12px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); }
#wsDlg .stat #wsMsg { flex: 1; white-space: pre-wrap; } #wsDlg .stat #wsMsg.err { color: #ff7b7b; }
#wsDlg .empty { padding: 24px; text-align: center; color: var(--muted); }
#wsMenu { position: fixed; z-index: 1000; background: var(--panel); border: 1px solid var(--line2); border-radius: 9px; padding: 4px; min-width: 180px; box-shadow: 0 12px 34px #000a; }
#wsMenu button { display: flex; width: 100%; border: 0; background: transparent; padding: 6px 10px; border-radius: 6px; text-align: left; } #wsMenu button:hover { background: var(--panel3, #333); }
#wsMenu hr { border: 0; border-top: 1px solid var(--line); margin: 4px 0; } #wsMenu .h { padding: 3px 10px; font-size: 11px; color: var(--muted); }
@media (max-width: 760px) { #wsDlg .wbody { grid-template-columns: 1fr; grid-template-rows: 180px 1fr; } #wsDlg .wl { border-right: 0; border-bottom: 1px solid var(--line); } }
`;
document.head.appendChild(css);

const dlg = document.createElement('dialog');
dlg.id = 'wsDlg';
dlg.innerHTML = `
  <div class="wh"><b>워크스페이스 · 프로젝트</b><span class="muted2">워크스페이스 = 채널·클라이언트·시리즈 묶음, 프로젝트 = 그 안의 작업 하나</span><span style="flex:1"></span><button id="wsClose">닫기 (Esc)</button></div>
  <div class="wbody">
    <div class="wl">
      <div class="bar"><span class="t">워크스페이스</span><button id="spNew" class="primary" title="새 워크스페이스">+ 새로 만들기</button></div>
      <div class="list" id="spList"></div>
      <div class="bar" style="border-top:1px solid var(--line);border-bottom:0"><button id="spRen">이름 바꾸기</button><button id="spOpen">열기</button><button id="spDel">지우기</button></div>
    </div>
    <div class="wr">
      <div class="bar"><span class="t" id="prHead">프로젝트</span>
        <button id="prOpen" class="primary">열기</button><button id="prNew">+ 새 프로젝트</button><button id="prRen">이름 바꾸기</button><button id="prDup">복제</button><button id="prMv">옮기기…</button><button id="prDel">지우기</button></div>
      <div class="tbl"><table><thead><tr id="prHeadRow"></tr></thead><tbody id="prBody"></tbody></table><div class="empty" id="prEmpty" hidden>프로젝트가 없어요. '+ 새 프로젝트'를 눌러 만들어 보세요.</div></div>
    </div>
  </div>
  <div class="stat"><span id="wsMsg"></span><span id="wsCount"></span></div>`;
document.body.appendChild(dlg);

const COLS = [
  ['name', '이름', '', (p) => p.name.toLowerCase(), (p) => `${esc(p.name)}${p.active && S.viewActive ? ' · 열려 있음' : ''}`],
  ['updated', '수정한 날짜', '', (p) => p.updated || p.created || 0, (p) => ago(p.updated || p.created)],
  ['length', '길이', 'r', (p) => p.length || 0, (p) => (p.length ? dur(p.length) : '—')],
  ['clips', '클립', 'r', (p) => p.clips || 0, (p) => p.clips || '—'],
  ['media', '미디어', 'r', (p) => p.media || 0, (p) => p.media],
  ['saved', '보관본', 'r', (p) => p.saved || 0, (p) => p.saved],
  ['bytes', '용량', 'r', (p) => p.bytes || 0, (p) => size(p.bytes || 0)],
];
const S = { spaces: [], view: WSI.space, active: WSI.space, viewActive: true, rows: [], sel: null, sort: ['updated', -1], activeProject: WSI.id };
const pad = (n) => String(n).padStart(2, '0');
const ago = (t) => { if (!t) return '—'; const d = new Date(t * 1000); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const dur = (t) => `${pad(Math.floor(t / 60))}:${pad(Math.floor(t % 60))}`;
const size = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b >= 1e6 ? (b / 1e6).toFixed(1) + ' MB' : b >= 1e3 ? Math.round(b / 1e3) + ' KB' : b + ' B');
const say = (msg, err) => { $('wsMsg').textContent = msg || ''; $('wsMsg').className = err ? 'err' : ''; };
const viewSpace = () => S.spaces.find((x) => x.id === S.view) || {};
const selRow = () => S.rows.find((p) => p.id === S.sel);

const reload = () => location.reload();
async function guarded(fn) {   // 오래 걸리는 작업이 돌고 있으면 물어보고, 그래도 하면 강제로
  try { return await fn(false); } catch (e) {
    if (e.status === 409 && e.message.includes('강제로') && confirm('오래 걸리는 작업(AI 변환·받아쓰기 등)이 아직 돌고 있어요.\n\n그래도 바꿀까요? (돌고 있는 작업은 이전 프로젝트가 아니라 새로 연 프로젝트 폴더에 결과를 남길 수 있어요)')) return fn(true);
    say(e.message, true);
  }
}
const openProject = (pid, space = S.view) => guarded((force) => api('/workspace/spaces/open', { json: { id: space, project: pid, force } }).then(reload));
const openSpace = (sid) => guarded((force) => api('/workspace/spaces/open', { json: { id: sid, force } }).then(reload));

async function loadSpaces() {
  const w = await api('/workspace');
  S.spaces = w.spaces; S.active = w.space;
  if (!S.spaces.some((x) => x.id === S.view)) S.view = S.active;
  paintSpaces();
  await loadRows();
}
function paintSpaces() {
  $('spList').innerHTML = S.spaces.map((s) => `<div class="si ${s.id === S.view ? 'sel' : ''}" data-id="${esc(s.id)}"><span class="dot ${s.active ? '' : 'off'}" title="${s.active ? '열려 있는 워크스페이스' : ''}"></span><span class="n"><b>${esc(s.name)}</b><small>프로젝트 ${s.projects}개${s.active ? ' · 열려 있음' : ''}</small></span></div>`).join('');
  $('spList').querySelectorAll('.si').forEach((el) => {
    const id = el.dataset.id;
    el.onclick = () => { S.view = id; S.sel = null; paintSpaces(); loadRows(); };
    el.ondblclick = () => openSpace(id);
    el.oncontextmenu = (e) => { e.preventDefault(); S.view = id; paintSpaces(); loadRows(); menu(e, spaceMenu(S.spaces.find((x) => x.id === id))); };
  });
  const v = viewSpace();
  $('prHead').textContent = `${v.name || ''} › 프로젝트`;
  $('spOpen').disabled = S.view === S.active;
}
async function loadRows() {
  const view = S.view, keep = S.rowsView === S.view ? S.sel : null;
  S.viewActive = view === S.active;
  S.rows = []; S.sel = null; paintRows();        // 다른 워크스페이스를 눌렀을 때 이전 표가 남아 엉뚱한 곳을 열지 않게 먼저 비웁니다
  let r;
  try { r = await api(`/workspace/spaces/${encodeURIComponent(view)}/projects`); } catch (e) { return say(e.message, true); }
  if (view !== S.view) return;                   // 그 사이 다른 워크스페이스로 바뀌었으면 늦게 온 답은 버립니다
  S.rows = r.projects;
  S.activeProject = r.active;
  S.rows.forEach((p) => { p.active = p.id === r.active; });
  S.rowsView = view;
  S.sel = S.rows.some((p) => p.id === keep) ? keep : (S.rows.find((p) => p.active) || S.rows[0] || {}).id || null;
  paintRows();
}
function paintRows() {
  const [key, dir] = S.sort, col = COLS.find((c) => c[0] === key);
  const rows = [...S.rows].sort((a, b) => { const x = col[3](a), y = col[3](b); return (x < y ? -1 : x > y ? 1 : 0) * dir; });
  $('prHeadRow').innerHTML = COLS.map(([k, label, cls]) => `<th class="${cls}" data-k="${k}">${label}${k === key ? ` <span class="ar">${dir > 0 ? '▲' : '▼'}</span>` : ''}</th>`).join('');
  $('prHeadRow').querySelectorAll('th').forEach((th) => (th.onclick = () => { S.sort = [th.dataset.k, th.dataset.k === S.sort[0] ? -S.sort[1] : (th.dataset.k === 'name' ? 1 : -1)]; paintRows(); }));
  $('prBody').innerHTML = rows.map((p) => `<tr class="pr ${p.id === S.sel ? 'sel' : ''} ${p.active && S.viewActive ? 'cur' : ''}" data-id="${esc(p.id)}">${COLS.map(([k, , cls, , cell]) => `<td class="${cls} ${k === 'name' ? 'nm' : ''}">${cell(p)}</td>`).join('')}</tr>`).join('');
  $('prEmpty').hidden = rows.length > 0;
  $('prBody').querySelectorAll('tr').forEach((tr) => {
    const id = tr.dataset.id;
    tr.onclick = () => { S.sel = id; paintRows(); };
    tr.ondblclick = () => openProject(id);
    tr.oncontextmenu = (e) => { e.preventDefault(); S.sel = id; paintRows(); menu(e, projMenu()); };
  });
  $('wsCount').textContent = `항목 ${rows.length}개${S.sel ? ' · 1개 선택' : ''}`;
  const has = !!selRow();
  ['prOpen', 'prRen', 'prDup', 'prMv', 'prDel'].forEach((id) => ($(id).disabled = !has));
  $('prMv').disabled = !has || S.spaces.length < 2;
}

// ---- 동작
async function act(fn) { try { await fn(); } catch (e) { say(e.message, true); } }
async function spNewAct() {
  const n = prompt('새 워크스페이스 이름', ''); if (n === null) return;
  await act(async () => { await guarded((force) => api('/workspace/spaces', { json: { name: n.trim() || '새 워크스페이스' } }).then(reload)); });
}
async function spRenAct(s = viewSpace()) {
  const n = prompt('새 이름', s.name); if (!n || !n.trim() || n.trim() === s.name) return;
  await act(async () => { await api(`/workspace/spaces/${s.id}`, { method: 'PATCH', json: { name: n.trim() } }); if (s.active) spaceBtn.querySelector('.nm').textContent = n.trim(); say(`'${n.trim()}'(으)로 바꿨어요.`); await loadSpaces(); });
}
async function spDelAct(s = viewSpace()) {
  const n = prompt(`'${s.name}' 워크스페이스를 지울까요?\n안에 있는 프로젝트 ${s.projects}개(미디어·AI 결과·내보낸 영상·보관본)가 모두 들어 있어요. 폴더째 workspace/_trash로 옮겨 두니 직접 되돌릴 수 있어요.\n\n지우려면 워크스페이스 이름을 똑같이 적어 주세요.`);
  if (n === null) return;
  await act(async () => { const r = await api(`/workspace/spaces/${s.id}/delete`, { json: { confirm: n } }); if (s.active) return reload(); say(`지웠어요. (${r.trash})`); await loadSpaces(); });
}
async function prNewAct() {
  const n = prompt(`'${viewSpace().name}'에 만들 새 프로젝트 이름`, ''); if (n === null) return;
  await act(async () => { await api(`/workspace/projects?open=false${S.view ? '&space=' + encodeURIComponent(S.view) : ''}`, { json: { name: n.trim() || '새 프로젝트' } }); say('만들었어요. 더블클릭해서 열어요.'); await loadSpaces(); });
}
async function prRenAct(p = selRow()) {
  if (!p) return; const n = prompt('새 이름', p.name); if (!n || !n.trim() || n.trim() === p.name) return;
  await act(async () => { await api(`/workspace/projects/${p.id}${q(S.view)}`, { method: 'PATCH', json: { name: n.trim() } }); if (p.active && S.viewActive) projBtn.querySelector('.nm').textContent = n.trim(); await loadRows(); });
}
async function prDupAct(p = selRow()) {
  if (!p) return; say('복제하는 중…');
  await act(async () => { const d = await api(`/workspace/projects/${p.id}/duplicate${q(S.view)}`, { json: { name: '' } }); say(`'${d.name}'을 만들었어요. (내보낸 영상은 복제하지 않아요)`); await loadSpaces(); });
}
async function prMvAct(target, p = selRow()) {
  if (!p || !target) return; say('옮기는 중…');
  await act(async () => { await guarded((force) => api(`/workspace/projects/${p.id}/move${q(S.view)}`, { json: { space: target.id } })); if (p.active && S.viewActive) return reload(); say(`'${p.name}'을 '${target.name}'(으)로 옮겼어요.`); await loadSpaces(); });
}
async function prDelAct(p = selRow()) {
  if (!p) return;
  const n = prompt(`'${p.name}' 프로젝트를 지울까요?\n미디어·AI 결과·내보낸 영상·보관본이 들어 있어요. 폴더째 그 워크스페이스의 _trash로 옮겨 두니 직접 되돌릴 수 있어요.\n\n지우려면 프로젝트 이름을 똑같이 적어 주세요.`);
  if (n === null) return;
  await act(async () => { const r = await api(`/workspace/projects/${p.id}/delete${q(S.view)}`, { json: { confirm: n } }); if (p.active && S.viewActive) return reload(); say(`지웠어요. (${r.trash})`); await loadSpaces(); });
}
function pickSpace(p) {   // 옮길 워크스페이스 고르기 (버튼 쪽)
  const others = S.spaces.filter((x) => x.id !== S.view);
  const pick = prompt(`'${p.name}'을 어느 워크스페이스로 옮길까요? 번호를 적어 주세요.\n` + others.map((x, k) => `${k + 1}. ${x.name}`).join('\n') + '\n\n미디어·AI 결과·보관본이 함께 옮겨가요. 이전 워크스페이스 전용 템플릿 인트로·아웃트로 파일은 복사돼요.');
  return others[(parseInt(pick, 10) || 0) - 1];
}

// ---- 오른쪽 클릭 메뉴
function menu(e, items) {
  closeMenu();
  const m = document.createElement('div'); m.id = 'wsMenu';
  for (const it of items) {
    if (it === '-') { m.appendChild(document.createElement('hr')); continue; }
    if (it.head) { const h = document.createElement('div'); h.className = 'h'; h.textContent = it.head; m.appendChild(h); continue; }
    const b = document.createElement('button'); b.textContent = it.label; b.disabled = !!it.disabled;
    b.onclick = () => { closeMenu(); it.fn(); }; m.appendChild(b);
  }
  dlg.appendChild(m);
  m.style.left = Math.min(e.clientX, innerWidth - 210) + 'px'; m.style.top = Math.min(e.clientY, innerHeight - m.offsetHeight - 8) + 'px';
}
const closeMenu = () => { const m = $('wsMenu'); if (m) m.remove(); };
dlg.addEventListener('click', closeMenu);
const projMenu = () => {
  const p = selRow(), others = S.spaces.filter((x) => x.id !== S.view);
  return [{ label: '열기', fn: () => openProject(p.id) }, '-', { label: '이름 바꾸기  (F2)', fn: () => prRenAct(p) }, { label: '복제', fn: () => prDupAct(p) },
    ...(others.length ? ['-', { head: '다른 워크스페이스로 옮기기' }, ...others.map((o) => ({ label: `→ ${o.name}`, fn: () => prMvAct(o, p) }))] : []),
    '-', { label: '지우기  (Delete)', fn: () => prDelAct(p) }];
};
const spaceMenu = (s) => [{ label: '열기', disabled: s.active, fn: () => openSpace(s.id) }, '-', { label: '이름 바꾸기', fn: () => spRenAct(s) }, { label: '지우기', fn: () => spDelAct(s) }];

// ---- 연결
window.__wsPicker = { openProject: (pid) => openProject(pid, WSI.space) };   // 쇼츠 도구의 '그 프로젝트로' 버튼이 씁니다
function show() { say(''); closeMenu(); S.view = S.active = WSI.space; if (!dlg.open) dlg.showModal(); loadSpaces().catch((e) => say(e.message, true)); }
spaceBtn.onclick = show; projBtn.onclick = show;
$('wsClose').onclick = () => dlg.close();
dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
$('spNew').onclick = spNewAct; $('spRen').onclick = () => spRenAct(); $('spDel').onclick = () => spDelAct(); $('spOpen').onclick = () => openSpace(S.view);
$('prOpen').onclick = () => selRow() && openProject(S.sel); $('prNew').onclick = prNewAct; $('prRen').onclick = () => prRenAct(); $('prDup').onclick = () => prDupAct();
$('prDel').onclick = () => prDelAct();
$('prMv').onclick = () => { const p = selRow(); if (!p) return; const t = pickSpace(p); if (t) prMvAct(t, p); };
dlg.addEventListener('keydown', (e) => {
  if (e.target.closest && e.target.closest('input, textarea')) return;
  e.stopPropagation();
  if (e.key === 'Escape') { if ($('wsMenu')) { e.preventDefault(); closeMenu(); } return; }
  const rows = [...document.querySelectorAll('#prBody tr')];
  const i = rows.findIndex((r) => r.dataset.id === S.sel);
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); const n = rows[Math.max(0, Math.min(rows.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))]; if (n) { S.sel = n.dataset.id; paintRows(); document.querySelector(`#prBody tr[data-id="${CSS.escape(S.sel)}"]`)?.scrollIntoView({ block: 'nearest' }); } }
  else if (e.key === 'Enter' && S.sel) { e.preventDefault(); openProject(S.sel); }
  else if (e.key === 'F2' && S.sel) { e.preventDefault(); prRenAct(); }
  else if (e.key === 'Delete' && S.sel) { e.preventDefault(); prDelAct(); }
});

// 다른 탭에서 워크스페이스·프로젝트를 바꿨으면 알려 줘요 (이 탭의 보관함이 다른 프로젝트 것이 되어 버리니까요)
async function checkActive() {
  try {
    const w = await api('/workspace');
    if ((w.active !== WSI.id || w.space !== WSI.space) && !$('projBanner')) {
      const b = document.createElement('div'); b.id = 'projBanner';
      b.style.cssText = 'position:fixed;left:50%;top:10px;transform:translateX(-50%);z-index:2000;background:var(--accent);color:#1a1208;padding:8px 14px;border-radius:9px;font-weight:600;cursor:pointer;box-shadow:0 6px 20px #0008';
      b.textContent = '다른 창에서 프로젝트를 바꿨어요 — 눌러서 새로고침'; b.onclick = reload; document.body.appendChild(b);
    }
  } catch {}
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkActive(); });
})();
