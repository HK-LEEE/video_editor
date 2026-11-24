// 타임라인 프로: 드래그 선택(마키) · ⌘/Shift 선택 · 여러 클립 한꺼번에 편집 · 도구(선택/칼/손/슬립/슬라이드/리플/롤링)
// · 마커 · 시작/끝점(I/O) · 자석(스냅) · 재생 위치에 붙이기(삽입/덮어쓰기) · 마그네틱 · 타임라인 이전 버전 복원
// 편집기 본체(editor.html)가 내놓은 window.__editor.tl 로만 프로젝트를 건드립니다. 모든 편집은 실행 취소 한 단계예요.
(() => {
'use strict';
const ed = window.__editor, T = ed && ed.tl;
if (!T) return;
const $ = (id) => document.getElementById(id);
const { canvas, tlBox, RULER, FRAME, MAC } = T;
const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
const r3 = (x) => Math.round(x * 1000) / 1000;
const P = () => ed.project, UI = () => T.ui, S = () => T.sel;
const say = (m, err) => T.setStatus(m, err);
const len = T.len, end = T.end;
const kHint = (s) => T.hintKey(s);
const EPS = 1e-3;
const clipsOf = (ids) => [...ids].map((id) => T.clipById(id)).filter(Boolean);
const locked = (c) => T.isLocked(T.trackOf(c));
const unlockedClips = () => P().clips.filter((c) => !locked(c));
const dur = (c) => (ed.media[c.media] || {}).duration || c.src_out;

// ================================================================ 선택
let tool = 'select';
let snapAt = null;     // 끌 때 붙은 자리 (세로 선)
let hoverX = null, hoverY = null;
function expand(list, alt) {   // 연결(link)·그룹(grp)으로 묶인 클립을 함께
  const out = new Map();
  for (const c of list) {
    out.set(c.id, c);
    if (alt) continue;
    for (const k of T.linked(c)) out.set(k.id, k);
    if (c.grp) for (const k of P().clips) if (k.grp === c.grp) out.set(k.id, k);
  }
  return [...out.values()].filter((k) => !locked(k));
}
function setSelection(list, primary) {
  const ids = list.map((c) => c.id);
  T.setSel({ clip: ids.length ? (primary ? primary.id : ids[0]) : null, multi: new Set(ids) });
  T.renderHeads(); T.renderInspector();
}
const selClips = () => clipsOf(T.selIds());
function selectAll() { const l = unlockedClips(); setSelection(l); say(l.length ? `클립 ${l.length}개를 모두 골랐어요.` : '고를 클립이 없어요.'); }
function selectAfter(t = ed.playhead()) { const l = unlockedClips().filter((c) => end(c) > t + EPS); setSelection(l); say(`재생 위치 뒤의 클립 ${l.length}개를 골랐어요.`); }
function selectTracks(trackIds, after) {
  const set = new Set(trackIds);
  const l = unlockedClips().filter((c) => set.has(c.track) && (after == null || end(c) > after + EPS));
  setSelection(l); say(`클립 ${l.length}개를 골랐어요.`);
}
function toggleSel(c, alt) {
  const cur = new Map(selClips().map((k) => [k.id, k])), grp = expand([c], alt), had = cur.has(c.id);
  for (const k of grp) had ? cur.delete(k.id) : cur.set(k.id, k);
  const arr = [...cur.values()]; setSelection(arr, had ? arr[arr.length - 1] : c);
}
function rangeSel(c, alt) {
  const anchor = T.clipById(S().clip);
  if (!anchor) return setSelection(expand([c], alt), c);
  const rs = T.rows(), idx = (id) => rs.findIndex((r) => r.track.id === id);
  const a = idx(anchor.track), b = idx(c.track), r0 = Math.min(a, b), r1 = Math.max(a, b);
  const t0 = Math.min(anchor.start, c.start), t1 = Math.max(end(anchor), end(c));
  const hit = P().clips.filter((k) => { const i = idx(k.track); return i >= r0 && i <= r1 && k.start < t1 - EPS && end(k) > t0 + EPS && !locked(k); });
  setSelection(expand(hit, alt), c);
}

// ================================================================ 도구
const TOOLS = [
  ['select', 'V', '선택', '선택 도구: 끌어서 여러 클립 고르기 · ⌘클릭 하나씩 더하기·빼기 · Shift+클릭 범위 · 가장자리 ⇧끌기 리플 · ⌘끌기 롤링'],
  ['blade', 'B', '칼', '칼 도구: 클립을 누른 자리에서 자르기 (⇧: 모든 트랙, ⌥: 연결 무시)'],
  ['hand', 'H', '손', '손 도구: 끌어서 타임라인 옮겨 보기'],
  ['slip', 'Y', '슬립', '슬립: 클립 길이·위치는 그대로, 안의 영상·소리 구간만 옮기기'],
  ['slide', 'U', '슬라이드', '슬라이드: 클립을 옮기면서 앞뒤 클립이 대신 늘고 줄어요'],
  ['ripple', 'Q', '리플', '리플 트림: 가장자리를 끌면 뒤 클립이 따라와요'],
  ['roll', 'W', '롤링', '롤링 편집: 붙어 있는 두 클립의 경계를 함께 옮겨요'],
];
function setTool(name) {
  tool = name;
  canvas.classList.remove(...TOOLS.map(([k]) => 'tl-tool-' + k)); canvas.classList.add('tl-tool-' + name);
  refreshBar();
  if (name !== 'select') say(`${TOOLS.find((t) => t[0] === name)[2]} 도구 (V: 선택 도구로 돌아가기)`);
}

// ================================================================ 마커 · 시작/끝점
const MCOL = ['#f5b301', '#ef4444', '#22c55e', '#38bdf8', '#a78bfa', '#f472b6'];
const markerAt = (x) => { const s = T.pps(); return P().markers.find((m) => Math.abs(m.t * s - x) <= 6) || null; };
function addMarker(t = ed.playhead(), label = '') {
  T.edit(() => { P().markers.push({ id: ed.uid(), t: r3(Math.max(0, t)), label, color: '' }); P().markers.sort((a, b) => a.t - b.t); });
  say(`마커를 넣었어요 (${ed.fmt(t)}). 이름은 마커를 두 번 누르거나 ⇧M.`);
}
function delMarker(m) { T.edit(() => { P().markers = P().markers.filter((k) => k !== m && k.id !== m.id); }); }
function editLabel(m) {
  const old = document.getElementById('mkEdit'); if (old) old.remove();
  const b = canvas.getBoundingClientRect(), inp = document.createElement('input');
  inp.id = 'mkEdit'; inp.type = 'text'; inp.value = m.label || ''; inp.placeholder = '마커 이름'; inp.maxLength = 60;
  inp.style.cssText = `position:fixed;z-index:60;left:${Math.max(4, b.left + m.t * T.pps() + 6)}px;top:${b.top + 3}px;width:150px;font-size:12px;padding:3px 6px`;
  document.body.appendChild(inp); inp.focus(); inp.select();
  let done = false;
  const fin = (ok) => { if (done) return; done = true; const v = inp.value.trim(); inp.remove(); if (ok && v !== (m.label || '')) T.edit(() => { const mm = P().markers.find((k) => k.id === m.id); if (mm) mm.label = v; }); };
  inp.addEventListener('keydown', (e) => { e.stopPropagation(); if (e.key === 'Enter') fin(true); else if (e.key === 'Escape') fin(false); });
  inp.addEventListener('blur', () => fin(true));
}
const IO = () => UI().inout || (UI().inout = { in: null, out: null });
function setIO(which, t = ed.playhead()) {
  const io = IO(); io[which] = r3(Math.max(0, t));
  if (io.in != null && io.out != null && io.out < io.in) [io.in, io.out] = [io.out, io.in];
  T.save(); say(`${which === 'in' ? '시작점' : '끝점'} ${ed.fmt(t)}`);
}
function clearIO(which) { const io = IO(); if (which === 'both') { io.in = null; io.out = null; } else io[which] = null; T.save(); say('범위를 지웠어요.'); }
const range = () => { const io = IO(); if (io.in == null && io.out == null) return null; const a = io.in ?? 0, b = io.out ?? Math.max(ed.projectLength(), a + 0.1); return b - a > 0.02 ? [a, b] : null; };

// ================================================================ 편집 도구들 (edit 안에서 호출)
// 구간 [a,b]를 비웁니다. 걸친 클립은 자르고 안쪽은 지워요. 지운 클립 수를 돌려줘요.
function cutRange(a, b, pool) {
  const crosses = (t) => pool().filter((c) => c.start + 0.02 < t && t < end(c) - 0.02);
  const A = crosses(a); if (A.length) T.splitTargets(A, a);
  const B = crosses(b); if (B.length) T.splitTargets(B, b);
  const gone = new Set(pool().filter((c) => c.start >= a - EPS && end(c) <= b + EPS).map((c) => c.id));
  P().clips = P().clips.filter((c) => !gone.has(c.id));
  return gone.size;
}
function shiftAfter(t, d, pool, extras = true) {   // t 이후 시작하는 클립(과 자막·글자·마커)을 d만큼 밀거나 당김
  for (const c of pool()) if (c.start >= t - EPS) c.start = Math.max(0, r3(c.start + d));
  if (!extras) return;
  for (const q of P().subtitles) { if (q.start >= t - EPS) { q.start = Math.max(0, q.start + d); q.end = Math.max(q.start + 0.1, q.end + d); } else if (q.end > t + EPS && d > 0) q.end += d; }
  for (const o of P().overlays || []) { if (o.start >= t - EPS) { o.start = Math.max(0, o.start + d); if (o.end != null) o.end += d; } else if (o.end != null && o.end > t + EPS && d > 0) o.end += d; }
  for (const m of P().markers) if (m.t >= t - EPS) m.t = Math.max(0, r3(m.t + d));
}
function liftRange() {
  const r = range(); if (!r) { say('먼저 I / O 로 시작점·끝점을 정해 주세요.', true); return; }
  T.edit(() => cutRange(r[0], r[1], unlockedClips)); say('범위를 비웠어요 (빈 칸이 남아요).');
}
function extractRange() {
  const r = range(); if (!r) { say('먼저 I / O 로 시작점·끝점을 정해 주세요.', true); return; }
  T.edit(() => { cutRange(r[0], r[1], unlockedClips); shiftAfter(r[1], -(r[1] - r[0]), unlockedClips); });
  clearIO('both'); say('범위를 지우고 뒤를 당겼어요.');
}
function selectRange() {
  const r = range(); if (!r) { say('먼저 I / O 로 범위를 정해 주세요.', true); return; }
  const l = unlockedClips().filter((c) => c.start < r[1] - EPS && end(c) > r[0] + EPS); setSelection(l); say(`범위 안의 클립 ${l.length}개를 골랐어요.`);
}
function gapAt(trackId, t) {   // t가 빈 칸 안이면 {a,b}, 아니면 null (b = 다음 클립 시작)
  const cs = P().clips.filter((c) => c.track === trackId).sort((x, y) => x.start - y.start);
  const next = cs.find((c) => c.start > t + EPS); if (!next) return null;
  if (cs.some((c) => c.start <= t + EPS && end(c) > t + EPS)) return null;
  const prev = [...cs].reverse().find((c) => end(c) <= t + EPS);
  return { a: prev ? end(prev) : 0, b: next.start };
}
function closeGap(trackId, t, allTracks) {
  const g = gapAt(trackId, t); if (!g || g.b - g.a < 0.02) { say('여기는 빈 칸이 아니에요.', true); return; }
  const d = g.a - g.b;
  T.edit(() => { const pool = allTracks ? unlockedClips : () => P().clips.filter((c) => c.track === trackId); shiftAfter(g.b, d, pool, !!allTracks); });
  say(allTracks ? '모든 트랙의 뒤를 당겨 빈 칸을 메웠어요.' : '빈 칸을 메웠어요.');
}
function closeAllGaps(trackIds) {   // 클립 사이의 빈 칸을 전부 메워요 (맨 앞 여백은 그대로)
  let n = 0;
  T.edit(() => {
    for (const tid of trackIds) {
      const cs = P().clips.filter((c) => c.track === tid).sort((x, y) => x.start - y.start);
      let edge = null;
      for (const c of cs) { if (edge !== null && c.start > edge + 0.02) { c.start = r3(edge); n++; } edge = end(c); }
    }
  });
  say(n ? `빈 칸 ${n}곳을 메웠어요. (다른 트랙과 싱크가 어긋날 수 있으니 확인하세요)` : '메울 빈 칸이 없어요.');
}
function nudgeSel(d) {
  const g = T.selectedGroup().filter((c) => !locked(c)); if (!g.length) return;
  d = Math.max(d, -Math.min(...g.map((c) => c.start)));
  if (!d) return;
  T.edit(() => g.forEach((c) => (c.start = r3(c.start + d))));
}
function pullToPlayhead(toEnd) {
  const g = T.selectedGroup().filter((c) => !locked(c)); if (!g.length) { say('먼저 클립을 고르세요.', true); return; }
  const ph = ed.playhead(), d = toEnd ? ph - Math.max(...g.map(end)) : ph - Math.min(...g.map((c) => c.start));
  const dd = Math.max(d, -Math.min(...g.map((c) => c.start)));
  T.edit(() => g.forEach((c) => (c.start = r3(c.start + dd))));
  say(toEnd ? '선택한 클립의 끝을 재생 위치에 맞췄어요.' : '선택한 클립을 재생 위치로 당겨 붙였어요.');
}
function toggleDisabled() {
  const g = T.selectedGroup(); if (!g.length) { say('먼저 클립을 고르세요.', true); return; }
  const off = g.some((c) => !c.disabled);
  T.edit(() => g.forEach((c) => (c.disabled = off)));
  say(off ? `클립 ${g.length}개를 껐어요 (소리·화면에서 빠져요).` : `클립 ${g.length}개를 켰어요.`);
}
function linkSel(on) {
  const g = T.selectedGroup(); if (g.length < (on ? 2 : 1)) { say('연결할 클립을 두 개 이상 고르세요.', true); return; }
  const id = ed.uid(); T.edit(() => g.forEach((c) => (c.link = on ? id : null)));
  say(on ? '선택한 클립을 연결했어요 (함께 움직이고 지워요).' : '연결을 풀었어요.');
}
function groupSel(on) {
  const ids = T.selIds(); const g = on ? clipsOf(ids) : T.selectedGroup();
  if (on && g.length < 2) { say('묶을 클립을 두 개 이상 고르세요.', true); return; }
  const id = ed.uid();
  T.edit(() => { if (on) g.forEach((c) => (c.grp = id)); else { const gs = new Set(g.map((c) => c.grp).filter(Boolean)); P().clips.forEach((c) => { if (gs.has(c.grp)) delete c.grp; }); } });
  say(on ? '클립을 그룹으로 묶었어요 (하나를 누르면 같이 골라져요).' : '그룹을 풀었어요.');
}
function bladeAt(c, t, alt, all) {
  const crosses = (k) => k.start + 0.02 < t && t < end(k) - 0.02;
  const targets = all ? unlockedClips().filter(crosses) : (alt ? [c] : [c, ...T.linked(c)]).filter((k) => crosses(k) && !locked(k));
  if (!targets.length) return;
  T.edit(() => T.splitTargets(targets, t));
}

// ---- 보관함 에셋을 재생 위치에 붙이기: 자동(빈 트랙) · 삽입(뒤를 밀기) · 덮어쓰기
const unlockedTracks = () => P().tracks.filter((t) => !T.isLocked(t));
function makeRoom(at, L) {
  const crossing = unlockedClips().filter((c) => c.start + 0.02 < at && at < end(c) - 0.02);
  if (crossing.length) T.splitTargets(crossing, at);
  shiftAfter(at, L, unlockedClips);
}
function pickTrack(m, t, L) {
  const fits = (tr) => tr && !T.isLocked(tr) && !P().clips.some((c) => c.track === tr.id && c.start < t + L - EPS && end(c) > t + EPS);
  if (m.kind === 'video') return P().tracks.find((tr) => tr.kind === 'video' && !T.isLocked(tr)) || null;
  const pref = m.recorded ? P().tracks.find((tr) => tr.id === UI().armed && tr.kind === 'audio') || P().tracks.find((tr) => tr.kind === 'audio' && tr.voice) : P().tracks.find((tr) => tr.kind === 'audio' && !tr.voice);
  const tr = (fits(pref) && pref) || T.freeAudioTrack(t, t + L, pref);
  if (tr) return tr;
  const n = P().tracks.filter((k) => k.kind === 'audio').length + 1, nt = T.newTrack('audio', { name: `A${n}` }); P().tracks.push(nt); return nt;
}
function placeAssets(mids, mode, at = ed.playhead()) {
  mids = mids.filter((id) => ed.media[id]);
  if (!mids.length) { say('붙일 파일을 보관함에서 먼저 고르세요.', true); return; }
  mode = mode || UI().placeMode || 'free';
  if (mode === 'free' && UI().magnetic) mode = 'insert';
  const total = mids.reduce((a, id) => a + (ed.media[id].duration || 0), 0);
  T.batch(() => {
    if (mode === 'insert') makeRoom(at, total);
    else if (mode === 'overwrite') {
      const ids = new Set();
      const v = P().tracks.find((tr) => tr.kind === 'video' && !T.isLocked(tr)), a = P().tracks.find((tr) => tr.kind === 'audio' && !tr.voice && !T.isLocked(tr)) || P().tracks.find((tr) => tr.kind === 'audio' && !T.isLocked(tr));
      for (const id of mids) { const m = ed.media[id]; if (m.kind === 'video') { if (v) ids.add(v.id); if (m.has_audio && a) ids.add(a.id); } else { const p = pickTrack(m, at, 0); if (p) ids.add(p.id); } }
      cutRange(at, at + total, () => P().clips.filter((c) => ids.has(c.track)));
    }
    let t = at;
    for (const id of mids) { const m = ed.media[id], tr = pickTrack(m, t, m.duration); if (tr) T.placeMedia(id, tr, t); t += m.duration; }
  });
  say({ insert: '재생 위치에 끼워 넣고 뒤를 밀었어요.', overwrite: '재생 위치에 덮어썼어요.', free: '재생 위치에 붙였어요.' }[mode] || '붙였어요.');
}
const binIds = () => { const sel = T.binSel; return T.binOrder.filter((id) => sel.has(id)); };

// ================================================================ 마우스
let D = null, lastE = null, mqTimer = null;
const pt = (e) => T.pos(e);
function startDrag(e, o, begin = true) { canvas.setPointerCapture(e.pointerId); if (begin) T.begin(); D = { ...o, moved: false }; e.stopImmediatePropagation(); }
// 부모(#timeline)의 캡처 단계에서 받아서, 기본 핸들러보다 항상 먼저 처리해요
const on = (type, fn) => tlBox.addEventListener(type, (e) => { if (e.target === canvas) fn(e); }, true);
function cancelDrag() { if (D && D.kind === 'marquee') clearInterval(mqTimer); D = null; snapAt = null; }

on('pointerdown', (e) => {
  if (e.button !== 0 || (MAC && e.ctrlKey)) return;
  const [x, y] = pt(e), s = T.pps();
  if (y < RULER) {   // 눈금: 마커를 누르면 그 마커를 끌 수 있어요 (그 밖은 기본 동작: 재생 위치 옮기기)
    const m = markerAt(x); if (!m || y < 12) return;
    startDrag(e, { kind: 'marker', m, x0: x, t0: m.t }); return;
  }
  if (T.inSubRow(y) || T.inOvRow(y)) return;
  const ko = T.kfOwner(), kd = ko && T.kfDot(ko, x, y); if (kd) return;
  if (tool === 'hand') { canvas.setPointerCapture(e.pointerId); D = { kind: 'pan', sx: e.clientX, sy: e.clientY, sl: tlBox.scrollLeft, st: tlBox.scrollTop }; canvas.style.cursor = 'grabbing'; e.stopImmediatePropagation(); return; }
  let h = T.hit(x, y);
  if (h && h.c && T.isLocked(h.row.track)) { say('잠긴 트랙이에요. 머리의 자물쇠를 눌러 풀어 주세요.'); h = { row: h.row }; }
  const meta = e.metaKey || e.ctrlKey, shift = e.shiftKey, alt = e.altKey;
  if (!h || !h.c) {   // 빈 곳: 끌면 사각형 선택, 그냥 누르면 선택 해제 + 재생 위치 이동
    const base = meta || shift ? new Set(T.selIds()) : new Set();
    startDrag(e, { kind: 'marquee', x0: x, y0: y, base, add: meta || shift, alt, row: h && h.row }, false);   // 선택은 프로젝트를 바꾸지 않아서 실행 취소에 안 쌓여요
    lastE = e;
    return;
  }
  const c = h.c, edge = h.mode !== 'move';
  if (tool === 'blade') {
    const xs = T.snapTargets(new Set()); const [t] = T.snapTime(x / s, xs);
    bladeAt(c, t, alt, shift); e.stopImmediatePropagation(); return;
  }
  if (edge && (tool === 'roll' || (meta && tool === 'select'))) return trimStart(e, h, 'roll', x);
  if (edge && (tool === 'ripple' || (shift && tool === 'select') || (UI().magnetic && !meta && !alt))) return trimStart(e, h, 'ripple', x);
  if (!edge && meta) { toggleSel(c, alt); e.stopImmediatePropagation(); return; }
  if (!edge && shift) { rangeSel(c, alt); e.stopImmediatePropagation(); return; }
  if (!edge && tool === 'slip') return slipStart(e, h, x);
  if (!edge && tool === 'slide') return slideStart(e, h, x);
  const ids = T.selIds();
  const many = ids.size > 1 && ids.has(c.id);
  if (many || (c.grp && !alt && P().clips.filter((k) => k.grp === c.grp).length > 1)) {
    if (!many) setSelection(expand([c], alt), c);
    const cs = many ? T.selectedGroup().filter((k) => !locked(k)) : expand([c], alt);
    if (edge) return trimStart(e, h, 'normal', x, cs);
    return moveStart(e, h, cs, x, y);
  }
  if (edge && alt) return;   // 기본 동작 (연결 무시하고 다듬기)
  // 그 밖: 하나만 고른 클립은 기본 동작이 처리
});

function moveStart(e, h, cs, x, y) {
  const kinds = new Set(cs.map((k) => (T.trackOf(k) || {}).kind));
  startDrag(e, { kind: 'move', cs, primary: h.c, x0: x, y0: y, orig: cs.map((k) => ({ start: k.start, track: k.track })), sameKind: kinds.size === 1 ? [...kinds][0] : null, start: { x, y } });
}
function trimStart(e, h, variant, x, cs) {
  const c = h.c, alt = e.altKey;
  const group = cs || (alt ? [c] : [c, ...T.linked(c)]).filter((k) => !locked(k));
  const side = h.mode;
  const orig = group.map((k) => ({ ...k, vol_keys: (k.vol_keys || []).map((v) => ({ ...v })) }));
  const ids = new Set(group.map((k) => k.id));
  const o = { kind: 'trim', variant, side, c, group, orig, ids, x0: x };
  if (variant === 'ripple') {   // 같은 트랙에서 이 클립 뒤에 있는 것들이 따라와요
    o.later = [];
    for (const g of orig) for (const k of P().clips) if (!ids.has(k.id) && k.track === g.track && k.start >= end(g) - EPS) o.later.push({ k, s0: k.start });
  } else if (variant === 'roll') {
    o.pairs = orig.map((g, i) => {
      const nb = P().clips.find((k) => !ids.has(k.id) && k.track === g.track && (side === 'right' ? Math.abs(k.start - end(g)) < 0.05 : Math.abs(end(k) - g.start) < 0.05));
      return nb ? { i, n: nb, on: { ...nb, vol_keys: (nb.vol_keys || []).map((v) => ({ ...v })) } } : null;
    }).filter(Boolean);
    if (!o.pairs.length) { say('붙어 있는 이웃 클립이 없어서 일반 다듬기로 할게요.'); o.variant = 'normal'; }
  }
  startDrag(e, o);
}
function slipStart(e, h, x) {
  const cs = (e.altKey ? [h.c] : T.selectedGroup().length > 1 && T.selIds().has(h.c.id) ? T.selectedGroup() : [h.c, ...T.linked(h.c)]).filter((k) => !locked(k));
  startDrag(e, { kind: 'slip', cs, orig: cs.map((k) => ({ ...k })), x0: x });
}
function slideStart(e, h, x) {
  const cs = [h.c, ...(e.altKey ? [] : T.linked(h.c))].filter((k) => !locked(k)), ids = new Set(cs.map((k) => k.id));
  const nbs = cs.map((k) => ({
    k, o: { ...k },
    L: P().clips.find((q) => !ids.has(q.id) && q.track === k.track && Math.abs(end(q) - k.start) < 0.05),
    R: P().clips.find((q) => !ids.has(q.id) && q.track === k.track && Math.abs(q.start - end(k)) < 0.05),
  }));
  for (const n of nbs) { if (n.L) n.oL = { ...n.L }; if (n.R) n.oR = { ...n.R }; }
  startDrag(e, { kind: 'slide', nbs, x0: x });
}

on('pointermove', (e) => {
  const [x, y] = pt(e);
  hoverX = x; hoverY = y;
  if (!D) return;
  e.stopImmediatePropagation();
  lastE = e;
  if (D.kind === 'pan') { tlBox.scrollLeft = D.sl - (e.clientX - D.sx); tlBox.scrollTop = D.st - (e.clientY - D.sy); return; }
  if (D.kind === 'marquee') { marquee(e); return; }
  if (D.kind === 'marker') { const xs = T.snapTargets(new Set()); const [t, line] = T.snapTime(Math.max(0, x / T.pps()), xs); D.m.t = r3(t); snapAt = line; D.moved = true; return; }
  D.moved = true; snapAt = null;
  const s = T.pps();
  if (D.kind === 'move') moveDrag(x, y, s);
  else if (D.kind === 'trim') trimDrag(x, s);
  else if (D.kind === 'slip') slipDrag(x, s);
  else if (D.kind === 'slide') slideDrag(x, s);
});

function moveDrag(x, y, s) {
  let d = (x - D.x0) / s;
  const xs = T.snapTargets(new Set(D.cs.map((k) => k.id)));
  let best = null;
  D.cs.forEach((k, i) => {
    const a = D.orig[i].start + d, b = a + len(k);
    for (const v of [a, b]) { const [sv, line] = T.snapTime(v, xs); if (line !== null) { const dl = sv - v; if (best === null || Math.abs(dl) < Math.abs(best.dl)) best = { dl, line }; } }
  });
  if (best) { d += best.dl; snapAt = best.line; }
  d = Math.max(d, -Math.min(...D.orig.map((o) => o.start)));
  D.cs.forEach((k, i) => (k.start = r3(D.orig[i].start + d)));
  if (D.sameKind) {   // 위아래로 끌면 같은 종류의 다른 트랙으로 (선택 전체가 같은 만큼)
    const list = T.rows().filter((r) => r.track.kind === D.sameKind && !T.isLocked(r.track)), idx = (id) => list.findIndex((r) => r.track.id === id);
    const r = T.rowAt(y), i0 = idx(D.orig[D.cs.indexOf(D.primary)].track);
    if (r && r.track.kind === D.sameKind && i0 >= 0 && idx(r.track.id) >= 0) {
      const ids = D.orig.map((o) => idx(o.track)); let di = idx(r.track.id) - i0;
      if (ids.every((v) => v >= 0)) { di = clamp(di, -Math.min(...ids), list.length - 1 - Math.max(...ids)); D.cs.forEach((k, i) => (k.track = list[ids[i] + di].track.id)); }
    }
  }
}
function trimDrag(x, s) {
  const { orig, group, side, variant } = D, dt = (x - D.x0) / s;
  const xs = T.snapTargets(D.ids);
  const pi = group.indexOf(D.c), o0 = orig[pi < 0 ? 0 : pi], sp = (o) => o.speed || 1;
  const L = (o) => (o.src_out - o.src_in) / sp(o);
  let d;
  if (side === 'left') {
    let ns = o0.start + dt; const [a, la] = T.snapTime(ns, xs); if (la !== null) { ns = a; snapAt = la; }
    d = ns - o0.start;
    for (const o of orig) d = clamp(d, Math.max(-o.src_in / sp(o), variant === 'ripple' ? -Infinity : -o.start), L(o) - 0.05);
    if (variant === 'roll') for (const p of D.pairs) { const g = orig[p.i], n = p.on; d = clamp(d, 0.05 - L(n), (dur(n) - n.src_out) / sp(n)); void g; }
  } else {
    let ne = end(o0) + dt; const [a, la] = T.snapTime(ne, xs); if (la !== null) { ne = a; snapAt = la; }
    d = ne - end(o0);
    for (const o of orig) d = clamp(d, 0.05 - L(o), (dur(o) - o.src_out) / sp(o));
    if (variant === 'roll') for (const p of D.pairs) d = clamp(d, -p.on.src_in / sp(p.on), L(p.on) - 0.05);
  }
  group.forEach((k, i) => {
    const o = orig[i];
    if (side === 'left') {
      k.src_in = o.src_in + d * sp(o);
      k.start = variant === 'ripple' ? o.start : r3(o.start + d);
      if (o.vol_keys.length) k.vol_keys = T.shiftKeys(o.vol_keys, d);
    } else k.src_out = o.src_out + d * sp(o);
  });
  if (variant === 'ripple') { const sh = side === 'right' ? d : -d; for (const l of D.later) l.k.start = Math.max(0, r3(l.s0 + sh)); }
  if (variant === 'roll') for (const p of D.pairs) {
    const n = p.n, on = p.on;
    if (side === 'right') { n.src_in = on.src_in + d * sp(on); n.start = r3(on.start + d); if (on.vol_keys.length) n.vol_keys = T.shiftKeys(on.vol_keys, d); }
    else n.src_out = on.src_out + d * sp(on);
  }
}
function slipDrag(x, s) {
  let d = (x - D.x0) / s;
  for (const o of D.orig) d = clamp(d, -(dur(o) - o.src_out) / (o.speed || 1), o.src_in / (o.speed || 1));
  D.cs.forEach((k, i) => { const o = D.orig[i], v = d * (o.speed || 1); k.src_in = o.src_in - v; k.src_out = o.src_out - v; });
}
function slideDrag(x, s) {
  let d = (x - D.x0) / s;
  const xs = T.snapTargets(new Set(D.nbs.flatMap((n) => [n.k.id, n.L && n.L.id, n.R && n.R.id].filter(Boolean))));
  const p0 = D.nbs[0].o, [a, la] = T.snapTime(p0.start + d, xs), [b, lb] = T.snapTime(end(p0) + d, xs);
  if (la !== null) { d = a - p0.start; snapAt = la; } else if (lb !== null) { d = b - end(p0); snapAt = lb; }
  for (const n of D.nbs) {
    d = Math.max(d, -n.o.start);
    if (n.L) d = clamp(d, 0.05 - len(n.oL), (dur(n.oL) - n.oL.src_out) / (n.oL.speed || 1));
    if (n.R) d = clamp(d, -n.oR.src_in / (n.oR.speed || 1), len(n.oR) - 0.05);
  }
  for (const n of D.nbs) {
    n.k.start = r3(n.o.start + d);
    if (n.L) n.L.src_out = n.oL.src_out + d * (n.oL.speed || 1);
    if (n.R) { n.R.src_in = n.oR.src_in + d * (n.oR.speed || 1); n.R.start = r3(n.oR.start + d); }
  }
}
function marquee(e) {
  const [x, y] = pt(e);
  if (!D.moved && Math.hypot(x - D.x0, y - D.y0) < 4) return;
  if (!D.moved) { D.moved = true; mqTimer = setInterval(() => { if (D && D.kind === 'marquee' && lastE) { autoScroll(lastE); marqueeUpdate(lastE); } }, 40); if (!D.add) T.setSel({ clip: null, multi: new Set() }); }
  autoScroll(e); marqueeUpdate(e);
}
function autoScroll(e) {
  const b = tlBox.getBoundingClientRect(), m = 28;
  if (e.clientX > b.right - m) tlBox.scrollLeft += 18; else if (e.clientX < b.left + 200 + m) tlBox.scrollLeft -= 18;
  if (e.clientY > b.bottom - m) tlBox.scrollTop += 14; else if (e.clientY < b.top + m) tlBox.scrollTop -= 14;
}
function marqueeUpdate(e) {
  const [x, y] = pt(e), s = T.pps();
  D.x1 = x; D.y1 = y;
  const xa = Math.min(D.x0, x), xb = Math.max(D.x0, x), ya = Math.min(D.y0, y), yb = Math.max(D.y0, y);
  const rowOf = Object.fromEntries(T.rows().map((r) => [r.track.id, r]));
  const hit = P().clips.filter((c) => { const r = rowOf[c.track]; if (!r || T.isLocked(r.track)) return false; return c.start * s < xb && end(c) * s > xa && r.y + 2 < yb && r.y + r.h - 3 > ya; });
  const set = new Map(clipsOf(D.base).map((c) => [c.id, c]));
  for (const c of expand(hit, D.alt)) set.set(c.id, c);
  const arr = [...set.values()], cur = S();
  cur.multi = new Set(arr.map((c) => c.id)); cur.clip = arr.length ? (arr.find((c) => c.id === cur.clip) || arr[arr.length - 1]).id : null;
  cur.track = null; cur.cue = null; cur.subs = false; cur.ov = null;
}

function finish(e) {
  if (!D) return;
  const d = D; cancelDrag();
  if (d.kind === 'pan') { canvas.style.cursor = ''; return; }
  if (d.kind === 'marquee') {
    if (!d.moved) {   // 그냥 눌렀어요: 선택을 풀고 그 자리로 재생 위치 이동 (기본 동작과 같게)
      if (!d.add) { T.setSel({ clip: null, track: d.row ? d.row.track.id : null, multi: new Set() }); }
      if (e && !d.add) ed.seek(d.x0 / T.pps());
    }
    T.renderHeads(); T.renderInspector();
    if (d.moved && S().clip) say(`클립 ${T.selIds().size}개 선택`);
    return;
  }
  if (d.kind === 'move' && !d.moved) { const c = d.primary; setSelection(expand([c], false), c); }
  T.commit();
  if (d.kind === 'marker' && !d.moved) ed.seek(d.m.t);
}
on('pointerup', (e) => { if (D) { e.stopImmediatePropagation(); finish(e); } });
on('pointercancel', () => { if (D) finish(null); });

on('dblclick', (e) => {
  const [x, y] = pt(e);
  if (y < RULER) { const m = markerAt(x); if (m) { e.stopImmediatePropagation(); editLabel(m); } }
});

// ================================================================ 오른쪽 클릭 메뉴
const sep = { sep: true };
function multiItems() {
  const ph = ed.playhead(), g = T.selectedGroup(), n = g.length, anyOff = g.some((c) => c.disabled), grouped = g.some((c) => c.grp), linkedAny = g.some((c) => c.link);
  return [
    { head: true, label: `클립 ${n}개 선택됨` },
    { label: '재생 위치에서 자르기', hint: 'S', fn: () => T.splitAt(ed.playhead()) },
    { label: '모든 트랙 자르기', hint: '⇧⌘K', fn: () => T.splitAt(ed.playhead(), true) },
    sep,
    { label: '복사', hint: '⌘C', fn: T.copyClips }, { label: '잘라내기', hint: '⌘X', fn: T.cutClips },
    { label: '재생 위치에 붙여넣기', hint: '⌘V', fn: () => T.pasteClips(ed.playhead()) },
    { label: '복제', hint: '⌘D', fn: T.duplicateSelected },
    sep,
    { label: '지우기', hint: 'Delete', danger: true, fn: () => T.deleteSelected(true) },
    { label: '리플 삭제 (뒤 클립 당기기)', hint: '⇧Delete', danger: true, fn: T.rippleDelete },
    sep,
    { label: '재생 위치로 당겨 붙이기 (시작)', hint: 'G', fn: () => pullToPlayhead(false) },
    { label: '재생 위치에 끝 맞추기', hint: '⇧G', fn: () => pullToPlayhead(true) },
    { label: anyOff ? '클립 켜기' : '클립 끄기 (소리·화면에서 빼기)', hint: 'E', fn: toggleDisabled },
    sep,
    { label: '그룹으로 묶기', hint: '⌘G', disabled: n < 2, fn: () => groupSel(true) },
    { label: '그룹 풀기', hint: '⇧⌘G', disabled: !grouped, fn: () => groupSel(false) },
    { label: '연결 (영상+소리처럼 같이)', hint: '⌥⌘L', disabled: n < 2, fn: () => linkSel(true) },
    { label: '연결 해제', hint: '⇧⌥⌘L', disabled: !linkedAny, fn: () => linkSel(false) },
    sep,
    { label: '같은 트랙의 클립 전부 선택', hint: '⇧A', fn: () => selectTracks([...new Set(g.map((c) => c.track))]) },
    { label: '재생 위치 뒤 전부 선택', hint: 'A', fn: () => selectAfter(ph) },
    { label: '선택 해제', hint: 'Esc', fn: () => setSelection([]) },
  ];
}
T.menuHooks.clip = (c, items, tc) => {
  const multi = T.selIds().size > 1 || (c.grp && T.selectedGroup().length > 1);
  if (multi) return multiItems();
  return [...items, sep,
    { label: c.disabled ? '클립 켜기' : '클립 끄기 (소리·화면에서 빼기)', hint: 'E', fn: () => T.edit(() => { for (const k of T.selectedGroup()) k.disabled = !c.disabled; }) },
    { label: '재생 위치로 당겨 붙이기', hint: 'G', fn: () => pullToPlayhead(false) },
    { label: '같은 트랙의 클립 전부 선택', hint: '⇧A', fn: () => selectTracks([c.track]) },
    { label: '여기부터 뒤 전부 선택', fn: () => selectTracks(P().tracks.filter((t) => !T.isLocked(t)).map((t) => t.id), c.start - EPS) },
    { label: '마커 여기에 추가', hint: 'M', fn: () => addMarker(tc ?? ed.playhead()) },
  ];
};
T.menuHooks.track = (t, items, tc) => {
  const extra = [sep];
  if (tc !== null && tc !== undefined) {
    const g = gapAt(t.id, tc);
    if (g && g.b - g.a >= 0.02) extra.push({ label: `빈 칸 메우기 (${(g.b - g.a).toFixed(2)}초)`, fn: () => closeGap(t.id, tc, false) },
      { label: '빈 칸 메우기 (모든 트랙 같이 당기기)', fn: () => closeGap(t.id, tc, true) });
    extra.push({ label: '여기에 마커 추가', hint: 'M', fn: () => addMarker(tc) },
      { label: '이 트랙에서 여기부터 뒤 전부 선택', fn: () => selectTracks([t.id], tc - EPS) });
  }
  extra.push({ label: '이 트랙 클립 전부 선택', hint: '⇧A', fn: () => selectTracks([t.id]) },
    { label: '이 트랙 모든 빈 칸 메우기', fn: () => closeAllGaps([t.id]) });
  return [...items, ...extra];
};
T.menuHooks.ruler = (tc, items) => {
  const r = range();
  return [...items, sep,
    { label: '마커 여기에 추가', hint: 'M', fn: () => addMarker(tc) },
    { label: '시작점을 여기로', hint: 'I', fn: () => setIO('in', tc) },
    { label: '끝점을 여기로', hint: 'O', fn: () => setIO('out', tc) },
    { label: '범위 해제', hint: '⌥X', disabled: !r && IO().in == null && IO().out == null, fn: () => clearIO('both') },
    { label: '범위 안의 클립 선택', disabled: !r, fn: selectRange },
    { label: '범위 비우기 (빈 칸 남김)', hint: ';', disabled: !r, fn: liftRange },
    { label: '범위 지우고 뒤 당기기', hint: "'", danger: true, disabled: !r, fn: extractRange }];
};
T.menuHooks.bin = (m, items) => {
  const ids = T.binSel.has(m.id) && T.binSel.size > 1 ? binIds() : [m.id];
  return [{ label: ids.length > 1 ? `재생 위치에 ${ids.length}개 붙이기 (자동)` : '재생 위치에 붙이기 (자동·빈 트랙)', hint: 'Enter', fn: () => placeAssets(ids, UI().placeMode === 'free' ? 'free' : UI().placeMode) },
    { label: '재생 위치에 끼워 넣기 (뒤를 밀기)', hint: '⌥Enter', fn: () => placeAssets(ids, 'insert') },
    { label: '재생 위치에 덮어쓰기', hint: '⇧Enter', fn: () => placeAssets(ids, 'overwrite') },
    sep, ...items.slice(1)];
};
on('contextmenu', (e) => {   // 눈금의 마커
  const [x, y] = pt(e);
  if (y >= RULER || y < 12) return;
  const m = markerAt(x); if (!m) return;
  e.stopImmediatePropagation(); T.popup(e, [
    { label: m.label ? `마커 · ${m.label}` : '마커', head: true },
    { label: '여기로 이동', fn: () => ed.seek(m.t) },
    { label: '이름 바꾸기', fn: () => editLabel(m) },
    { label: '색', sub: MCOL.map((c, i) => ({ label: ['노랑', '빨강', '초록', '파랑', '보라', '분홍'][i], check: (m.color || MCOL[0]) === c, fn: () => T.edit(() => { const mm = P().markers.find((k) => k.id === m.id); if (mm) mm.color = c; }) })) },
    { label: '지우기', danger: true, fn: () => delMarker(m) }]);
});

// ================================================================ 키보드
document.addEventListener('keydown', (e) => {
  if ($('keysDlg') && $('keysDlg').open) return;
  const tg = e.target;
  if (tg && tg.matches && tg.matches('input:not([type=checkbox]):not([type=range]), select, textarea, [contenteditable=true]')) return;
  if (document.querySelector('dialog[open]')) return;
  const mod = e.metaKey || e.ctrlKey, alt = e.altKey, sh = e.shiftKey, c = e.code, k = e.key;
  const done = () => { e.preventDefault(); e.stopImmediatePropagation(); };
  const inBin = tg && tg.closest && tg.closest('#mediaList');
  if (mod && !alt) {
    if (c === 'KeyA' && !sh && !inBin) { done(); selectAll(); return; }
    if (c === 'KeyK') { done(); T.splitAt(ed.playhead(), sh); return; }
    if (c === 'KeyG') { done(); groupSel(!sh); return; }
    return;
  }
  if (mod && alt && c === 'KeyL') { done(); linkSel(!sh); return; }
  if (mod) return;
  if (alt && !sh) {
    if (c === 'KeyI') { done(); clearIO('in'); return; }
    if (c === 'KeyO') { done(); clearIO('out'); return; }
    if (c === 'KeyX') { done(); clearIO('both'); return; }
    if (k === 'Enter' && binIds().length) { done(); placeAssets(binIds(), 'insert'); return; }
    return;
  }
  if (alt) return;
  const hasSel = T.selIds().size > 0 && !S().cue && !S().ov;
  if (k === 'Enter' && binIds().length && (!tg || tg === document.body || inBin)) { done(); placeAssets(binIds(), sh ? 'overwrite' : undefined); return; }
  if (!sh) {
    const t = { KeyV: 'select', KeyB: 'blade', KeyH: 'hand', KeyY: 'slip', KeyU: 'slide', KeyQ: 'ripple', KeyW: 'roll' }[c];
    if (t) { done(); setTool(t); return; }
    if (c === 'KeyI') { done(); setIO('in'); return; }
    if (c === 'KeyO') { done(); setIO('out'); return; }
    if (c === 'KeyM') { done(); addMarker(); return; }
    if (c === 'KeyN') { done(); UI().snap = !UI().snap; $('snapChk').checked = UI().snap; T.save(); say(UI().snap ? '자석(스냅) 켬' : '자석(스냅) 끔'); return; }
    if (c === 'KeyA') { done(); selectAfter(); return; }
    if (c === 'KeyG' && hasSel) { done(); pullToPlayhead(false); return; }
    if (c === 'KeyE' && hasSel) { done(); toggleDisabled(); return; }
    if (c === 'Backslash') { done(); T.zoomFit(); return; }
    if (c === 'Semicolon') { done(); liftRange(); return; }
    if (c === 'Quote') { done(); extractRange(); return; }
  } else {
    if (c === 'KeyA') { done(); const ids = S().track ? [S().track] : [...new Set(T.selectedGroup().map((x) => x.track))]; if (!ids.length) say('먼저 트랙이나 클립을 고르세요.', true); else selectTracks(ids); return; }
    if (c === 'KeyM') { done(); const m = P().markers.find((x) => Math.abs(x.t - ed.playhead()) < 0.05); if (m) editLabel(m); else { addMarker(); const mm = P().markers.find((x) => Math.abs(x.t - ed.playhead()) < 0.05); if (mm) editLabel(mm); } return; }
    if (c === 'KeyN') { done(); setMagnetic(!UI().magnetic); return; }
    if (c === 'KeyG' && hasSel) { done(); pullToPlayhead(true); return; }
    if (c === 'Backslash') { done(); zoomSelection(); return; }
  }
  if ((k === 'ArrowLeft' || k === 'ArrowRight') && hasSel) { done(); nudgeSel((k === 'ArrowLeft' ? -1 : 1) * (sh ? 1 : FRAME)); return; }
  if ((k === 'Delete' || k === 'Backspace') && UI().magnetic && !sh && hasSel) { done(); T.rippleDelete(); return; }
}, true);

function zoomSelection() {
  const g = T.selectedGroup(); if (!g.length) { T.zoomFit(); return; }
  const a = Math.min(...g.map((c) => c.start)), b = Math.max(...g.map(end));
  T.setZoom(clamp((tlBox.clientWidth - 280) / Math.max(b - a, 0.5), 5, 800), null); tlBox.scrollLeft = Math.max(0, a * T.pps() - 20);
}

// 목록에서 더블클릭: 도구 막대의 붙이기 방식(자동·삽입·덮어쓰기)을 따라요
$('mediaList').addEventListener('dblclick', (e) => {
  const it = e.target.closest && e.target.closest('.item'); if (!it) return;
  e.stopImmediatePropagation(); T.setMonTab(false);
  placeAssets([it.dataset.mid], e.shiftKey ? 'overwrite' : e.altKey ? 'insert' : undefined);
}, true);

// ================================================================ 그리기 (선택 사각형 · 마커 · 범위 · 칼 · 붙은 자리)
T.drawHooks.push((g, { s, W, H, rs }) => {
  const sx0 = tlBox.scrollLeft - 40, sx1 = tlBox.scrollLeft + tlBox.clientWidth + 40;
  const r = range(), io = IO();
  if (r) { g.fillStyle = 'rgba(80,160,255,.09)'; g.fillRect(r[0] * s, RULER, (r[1] - r[0]) * s, H - RULER); g.fillStyle = 'rgba(80,160,255,.55)'; g.fillRect(r[0] * s, RULER - 5, (r[1] - r[0]) * s, 4); }
  g.strokeStyle = '#6cb4ff'; g.lineWidth = 2;
  for (const [v, dir] of [[io.in, 1], [io.out, -1]]) if (v != null) { const x = v * s; g.beginPath(); g.moveTo(x + dir * 7, RULER - 12); g.lineTo(x, RULER - 12); g.lineTo(x, RULER - 1); g.lineTo(x + dir * 7, RULER - 1); g.stroke(); }
  g.lineWidth = 1;
  g.font = '10px sans-serif';
  for (const m of P().markers) {
    const x = Math.round(m.t * s) + 0.5; if (x < sx0 || x > sx1) continue;
    const col = m.color || MCOL[0];
    g.save(); g.strokeStyle = col; g.globalAlpha = 0.28; g.setLineDash([3, 4]); g.beginPath(); g.moveTo(x, RULER); g.lineTo(x, H); g.stroke(); g.restore();
    g.fillStyle = col; g.beginPath(); g.moveTo(x - 5, RULER - 14); g.lineTo(x + 5, RULER - 14); g.lineTo(x + 5, RULER - 5); g.lineTo(x, RULER - 1); g.lineTo(x - 5, RULER - 5); g.closePath(); g.fill();
    if (m.label) { const w = g.measureText(m.label).width + 8; g.fillStyle = col; g.globalAlpha = .92; g.fillRect(x + 6, RULER - 15, w, 12); g.globalAlpha = 1; g.fillStyle = '#17120a'; g.fillText(m.label, x + 10, RULER - 5.5); }
  }
  if (snapAt !== null) { g.strokeStyle = '#ffd24a'; g.beginPath(); g.moveTo(snapAt * s + .5, RULER); g.lineTo(snapAt * s + .5, H); g.stroke(); }
  if (tool === 'blade' && hoverX != null && hoverY != null && hoverY > RULER && !D) {
    const row = T.rowAt(hoverY), t = hoverX / s; const [st] = T.snapTime(t, T.snapTargets(new Set()));
    g.strokeStyle = '#ff5d5d'; g.lineWidth = 1.5; g.beginPath();
    if (row) { g.moveTo(st * s + .5, row.y); g.lineTo(st * s + .5, row.y + row.h); } g.stroke(); g.lineWidth = 1;
  }
  if (D && D.kind === 'marquee' && D.moved && D.x1 != null) {
    const x = Math.min(D.x0, D.x1), y = Math.min(D.y0, D.y1), w = Math.abs(D.x1 - D.x0), h = Math.abs(D.y1 - D.y0);
    g.fillStyle = 'rgba(122,162,255,.16)'; g.fillRect(x, y, w, h); g.strokeStyle = '#7aa2ff'; g.setLineDash([4, 3]); g.strokeRect(x + .5, y + .5, w, h); g.setLineDash([]);
  }
  void W; void rs;
});

// ================================================================ 도구 막대 · 스타일
const css = document.createElement('style');
css.textContent = `
  #tlTools button.tltool { min-width: 0; padding: 3px 8px; }
  #tlTools button.tltool.on, #tlBar .tbtn.on { border-color: var(--accent); color: var(--accent2); background: color-mix(in srgb, var(--accent) 14%, var(--panel)); }
  #tlBar .tbtn { padding: 3px 8px; white-space: nowrap; }
  #tlBar select.tsel { padding: 3px 4px; max-width: 120px; }
  canvas.tl-tool-blade { cursor: crosshair !important; } canvas.tl-tool-hand { cursor: grab !important; }
  #histDlg { max-width: 560px; width: calc(100vw - 40px); border: 1px solid var(--line2); border-radius: 10px; background: var(--panel); color: var(--text); padding: 14px 16px; }
  #histDlg::backdrop { background: rgba(0,0,0,.5); }
  #histDlg .hrow { display: flex; gap: 8px; align-items: center; padding: 7px 0; border-top: 1px solid var(--line); font-size: 12.5px; }
  #histDlg .hrow .m { flex: 1; } #histDlg .hrow small { color: var(--muted); display: block; }
  #saveSt { cursor: pointer; }
`;
document.head.appendChild(css);
const bar = $('tlBar');
const tools = document.createElement('div'); tools.className = 'grp'; tools.id = 'tlTools';
tools.innerHTML = TOOLS.map(([k, key, label, tip]) => `<button class="tltool" data-tool="${k}" title="${tip} (${key})">${label}</button>`).join('');
bar.insertBefore(tools, bar.firstChild);
const opts = document.createElement('div'); opts.className = 'grp';
opts.innerHTML = `
  <button class="tbtn" id="tbMag" title="마그네틱 (⇧N): 지우거나 끝을 다듬거나 붙일 때 생기는 빈틈을 자동으로 메워요 (지우기=리플 삭제, 다듬기=리플 트림, 붙이기=끼워 넣기)">마그네틱</button>
  <button class="tbtn" id="tbFollow" title="재생 위치를 따라 타임라인이 같이 움직여요">따라가기</button>
  <select class="tsel" id="tbMode" title="보관함 파일을 재생 위치에 붙이는 방식 (더블클릭·Enter). ⌥Enter: 삽입, ⇧Enter: 덮어쓰기"><option value="free">붙이기: 자동(빈 트랙)</option><option value="insert">붙이기: 삽입(뒤를 밀기)</option><option value="overwrite">붙이기: 덮어쓰기</option></select>
  <button class="tbtn" id="tbPlace" title="보관함에서 고른 파일을 재생 위치에 지금 붙여요 (Enter)">⤓ 재생 위치에 붙이기</button>`;
bar.insertBefore(opts, tools.nextSibling);
const marks = document.createElement('div'); marks.className = 'grp';
marks.innerHTML = `
  <button class="tbtn" id="tbMark" title="재생 위치에 마커 (M) · 두 번 누르면 이름 바꾸기">⚑ 마커</button>
  <button class="tbtn" id="tbMarks" title="마커 목록 (누르면 그 자리로)">▾</button>
  <button class="tbtn" id="tbIn" title="시작점 (I)">[ 시작</button><button class="tbtn" id="tbOut" title="끝점 (O)">끝 ]</button>
  <button class="tbtn" id="tbHist" title="자동으로 쌓이는 타임라인 이전 버전에서 되돌리기">🕘 이전 버전</button>`;
bar.insertBefore(marks, opts.nextSibling);
function refreshBar() {
  tools.querySelectorAll('.tltool').forEach((b) => b.classList.toggle('on', b.dataset.tool === tool));
  $('tbMag').classList.toggle('on', !!UI().magnetic); $('tbFollow').classList.toggle('on', UI().follow !== false); $('tbMode').value = UI().placeMode || 'free';
}
function setMagnetic(on) { UI().magnetic = !!on; T.save(); refreshBar(); say(on ? '마그네틱 켬: 지우기·다듬기·붙이기가 빈틈 없이 이어져요.' : '마그네틱 끔'); }
tools.addEventListener('click', (e) => { const b = e.target.closest('.tltool'); if (b) setTool(b.dataset.tool); });
$('tbMag').onclick = () => setMagnetic(!UI().magnetic);
$('tbFollow').onclick = () => { UI().follow = UI().follow === false; T.save(); refreshBar(); };
$('tbMode').onchange = (e) => { UI().placeMode = e.target.value; T.save(); };
$('tbPlace').onclick = () => placeAssets(binIds());
$('tbMark').onclick = () => addMarker();
$('tbIn').onclick = () => setIO('in'); $('tbOut').onclick = () => setIO('out');
$('tbMarks').onclick = (e) => {
  const b = e.currentTarget.getBoundingClientRect(), ms = P().markers;
  T.showMenu(b.left, b.bottom + 2, ms.length ? [...ms.map((m) => ({ label: `${ed.fmt(m.t)}  ${m.label || '(이름 없음)'}`, fn: () => { ed.seek(m.t); const x = m.t * T.pps(); if (x < tlBox.scrollLeft || x > tlBox.scrollLeft + tlBox.clientWidth - 40) tlBox.scrollLeft = Math.max(0, x - 260); } })),
    sep, { label: '마커 모두 지우기', danger: true, fn: () => T.edit(() => (P().markers = [])) }] : [{ label: '마커가 없어요 (M 으로 추가)', disabled: true }]);
};
window.addEventListener('editor:change', refreshBar);

// ================================================================ 이전 버전 (서버가 자동으로 쌓은 타임라인 스냅샷)
const qs = () => (T.WSI.space ? 'space=' + encodeURIComponent(T.WSI.space) + '&' : '') + 'project=' + encodeURIComponent(T.WSI.id);
const dlg = document.createElement('dialog'); dlg.id = 'histDlg'; document.body.appendChild(dlg);
async function openHistory() {
  dlg.innerHTML = '<div style="display:flex;align-items:center"><b style="flex:1">타임라인 이전 버전</b><button id="hClose">닫기</button></div><p class="muted" style="margin:6px 0 8px">편집 중에 서버가 자동으로 쌓은 최근 20개예요. 되돌려도 지금 상태가 먼저 한 칸 남고, ⌘Z로도 돌아올 수 있어요.</p><div id="hList">불러오는 중…</div>';
  dlg.showModal(); $('hClose').onclick = () => dlg.close();
  try {
    const j = await (await fetch('/api/workspace/timeline/history?' + qs())).json(), box = $('hList');
    if (!j.versions.length) { box.innerHTML = '<p class="muted">아직 쌓인 버전이 없어요. 편집을 하다 보면 자동으로 생겨요.</p>'; return; }
    box.innerHTML = '';
    for (const v of j.versions) {
      const row = document.createElement('div'); row.className = 'hrow';
      row.innerHTML = `<span class="m"><b>${new Date(v.ts * 1000).toLocaleString('ko-KR', { hour12: false })}</b><small>클립 ${v.clips}개 · 길이 ${ed.fmt(v.length)}${v.clips === 0 ? ' · 비어 있음' : ''}</small></span>`;
      const b = document.createElement('button'); b.textContent = '이 버전으로 되돌리기';       b.onclick = () => restoreVersion(v);
      row.appendChild(b); box.appendChild(row);
    }
  } catch (err) { $('hList').textContent = '목록을 불러오지 못했어요: ' + err.message; }
}
async function restoreVersion(v) {
  if (!confirm(`${new Date(v.ts * 1000).toLocaleString('ko-KR', { hour12: false })} 버전(클립 ${v.clips}개)으로 되돌릴까요?\n지금 상태는 먼저 이전 버전으로 남겨 둬요.`)) return;
  try {
    await T.pushServer();   // 아직 못 보낸 편집분을 먼저 서버에 보내고
    const r = await fetch('/api/workspace/timeline/restore?' + qs(), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: v.name }) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
    const snap = await r.json();
    if (!snap.project) throw new Error('버전 내용이 올바르지 않아요.');
    ed.replaceProject(() => snap.project); T.setAllowEmpty(snap.project.clips && snap.project.clips.length === 0);
    await ed.loadMedia(); dlg.close(); say(`이전 버전으로 되돌렸어요 (클립 ${v.clips}개).`);
  } catch (err) { say('되돌리지 못했어요: ' + err.message, true); }
}
$('tbHist').onclick = openHistory;
const saveSt = $('saveSt'); if (saveSt) { saveSt.addEventListener('click', openHistory); saveSt.title += ' · 눌러서 타임라인 이전 버전 보기'; }
window.addEventListener('editor:save-blocked', () => openHistory());

// 단축키 안내 (? 키)
T.KEYS.push(
  ['타임라인 선택', [['빈 곳 끌기', '사각형으로 여러 클립 선택 (⌘/Shift: 더하기)'], ['⌘클릭', '클립 하나씩 더하기·빼기'], ['Shift+클릭', '범위 선택'], ['⌘A', '전체 선택'], ['A / ⇧A', '재생 위치 뒤 전부 / 같은 트랙 전부'], ['I O', '시작점 · 끝점 (⌥I ⌥O ⌥X: 지우기)'], ['; \'', '범위 비우기 / 범위 지우고 당기기']]],
  ['타임라인 편집', [['V B H', '선택 · 칼 · 손 도구'], ['Y U', '슬립 · 슬라이드'], ['Q W', '리플 · 롤링 (⇧끌기·⌘끌기로도)'], ['⌘K / ⇧⌘K', '재생 위치에서 자르기 (선택한 것 / 모든 트랙)'], ['← →', '선택한 클립 한 프레임 이동 (Shift: 1초)'], ['G / ⇧G', '선택을 재생 위치로 당기기 (시작 / 끝)'], ['E', '클립 켜기·끄기'], ['⌘G / ⇧⌘G', '그룹 묶기 · 풀기'], ['⌥⌘L / ⇧⌥⌘L', '연결 · 연결 해제'], ['N / ⇧N', '자석(스냅) / 마그네틱 켜고 끄기']]],
  ['마커 · 보기', [['M / ⇧M', '마커 넣기 / 이름 붙이기'], ['\\ / ⇧\\', '전체 보기 / 선택 구간 보기'], ['Enter', '보관함에서 고른 파일을 재생 위치에 (⌥: 삽입, ⇧: 덮어쓰기)']]]);

refreshBar(); setTool('select');
if (window.__bootNote) say(window.__bootNote, true);
window.__tlPro = { setTool, selectAll, selectAfter, addMarker, setIO, placeAssets, cutRange, liftRange, extractRange, closeAllGaps, groupSel, linkSel, toggleDisabled, pullToPlayhead, openHistory, get tool() { return tool; } };
})();
