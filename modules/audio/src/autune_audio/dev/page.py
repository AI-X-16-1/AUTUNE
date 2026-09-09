"""The dev page, as a string.

Kept in Python rather than a static file so the router has nothing to mount and
no path to resolve. It is served same-origin from the API, which is what lets it
call the endpoint without any CORS setup.

Colours and sizes come from docs/design/design-tokens.json. This is a developer
tool, not a product screen, so it does not use the app shell — but there is no
reason for it to invent its own palette.
"""

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autune - 전사 테스트</title>
<style>
  :root {
    --paper:#F4F3EF; --panel:#FFFFFF; --sunken:#EBEAE5;
    --ink:#16191F; --body:#3A3F47; --muted:#7A8089;
    --hairline:rgba(22,25,31,.12);
    --accent:#3B4A9E; --accent-hover:#2C3878; --selection:#E9EBF6;
    --critical:#C2443C; --attention:#B8862B; --radius:4px;
    --mono:ui-monospace,Menlo,monospace;
  }
  * { box-sizing:border-box }
  body {
    margin:0; background:var(--paper); color:var(--body);
    font:14px/1.65 Pretendard,system-ui,-apple-system,sans-serif;
  }
  main { max-width:860px; margin:0 auto; padding:28px }
  h1 { font-size:20px; font-weight:600; letter-spacing:-.015em; color:var(--ink); margin:0 }
  .sub { font-size:12.5px; color:var(--muted); margin:6px 0 24px }
  .warn {
    background:var(--ink); color:#fff; border-radius:var(--radius);
    padding:12px 16px; font-size:12.5px; font-weight:500;
    display:flex; gap:10px; align-items:center; margin-bottom:20px;
  }
  .warn::before {
    content:""; width:6px; height:6px; border-radius:50%;
    background:var(--critical); flex:none;
  }
  #drop {
    border:1.5px dashed var(--hairline); border-radius:var(--radius);
    background:var(--panel); padding:40px 24px; text-align:center; cursor:pointer;
    transition:border-color .15s, background .15s;
  }
  #drop.over { border-color:var(--accent); background:var(--selection) }
  #drop strong { color:var(--ink); font-weight:600; display:block; margin-bottom:6px }
  #drop span { font-size:12.5px; color:var(--muted) }
  input[type=file] { display:none }
  .row {
    display:flex; align-items:center; gap:12px;
    padding:14px 0; border-bottom:1px solid var(--hairline);
  }
  .dot { width:6px; height:6px; border-radius:50%; flex:none }
  .tc { font-family:var(--mono); font-size:12.5px; color:var(--muted); flex:none; min-width:112px }
  .txt { flex:1; color:var(--ink); font-size:13.5px }
  .stats { display:flex; gap:1px; background:var(--hairline); border:1px solid var(--hairline);
           border-radius:var(--radius); overflow:hidden; margin:20px 0 }
  .stat { background:var(--panel); padding:14px 16px; flex:1 }
  .stat b { display:block; font-family:var(--mono); font-size:18px; color:var(--ink); font-weight:700 }
  .stat span { font-size:11.5px; color:var(--muted) }
  .over-target b { color:var(--critical) }
  details { margin-top:6px }
  summary { font-size:11.5px; color:var(--accent); cursor:pointer; list-style:none }
  summary::-webkit-details-marker { display:none }
  .words { display:flex; flex-wrap:wrap; gap:4px; margin-top:8px }
  .w {
    font-family:var(--mono); font-size:11px; padding:2px 6px;
    background:var(--sunken); border-radius:var(--radius); color:var(--body);
  }
  .w.low { background:#F6ECD8; color:var(--attention) }
  .err { color:var(--critical); font-size:13.5px; padding:14px 0 }
  #out:empty { display:none }
  .hint { font-size:12.5px; color:var(--muted); margin-top:24px; line-height:1.7 }
</style>
</head>
<body>
<main>
  <h1>전사 테스트</h1>
  <p class="sub">녹음 파일을 올리면 Whisper 가 전사합니다. mp3 · wav · m4a</p>

  <div class="warn">
    개발 전용입니다. 동기 처리이고 PII 마스킹도 원본 삭제 이후 단계도 거치지 않습니다.
    실제 파이프라인이 아닙니다.
  </div>

  <label id="drop">
    <input type="file" id="file" accept="audio/*">
    <strong>파일을 끌어다 놓거나 클릭해서 선택</strong>
    <span>최대 500MB · 처리 시간은 녹음 길이에 비례합니다</span>
  </label>

  <div id="out"></div>

  <p class="hint">
    처리 시간 목표는 <b>녹음 길이의 1.5배 이내</b>입니다. 첫 실행은 모델을 내려받고
    메모리에 올리므로 훨씬 느립니다 — 두 번째 실행부터가 실제 속도입니다.<br>
    단어별 신뢰도가 0.6 미만이면 노란색으로 표시됩니다. 화자 분리는 아직 붙지 않았습니다.
  </p>
</main>

<script>
const drop = document.getElementById('drop');
const input = document.getElementById('file');
const out = document.getElementById('out');

['dragenter','dragover'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.add('over');
}));
['dragleave','drop'].forEach(e => drop.addEventListener(e, ev => {
  ev.preventDefault(); drop.classList.remove('over');
}));
drop.addEventListener('drop', ev => { if (ev.dataTransfer.files[0]) send(ev.dataTransfer.files[0]); });
input.addEventListener('change', () => { if (input.files[0]) send(input.files[0]); });

const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tc = s => {
  const m = Math.floor(s / 60), r = (s % 60).toFixed(2).padStart(5, '0');
  return `${m}:${r}`;
};

async function send(file) {
  out.innerHTML = '<div class="row"><span class="dot" style="background:var(--accent)"></span>'
    + `<span class="txt">${esc(file.name)} 전사 중…</span></div>`;
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('transcribe', { method: 'POST', body: form });
    render(await res.json());
  } catch (e) {
    out.innerHTML = `<p class="err">요청 실패: ${esc(String(e))}</p>`;
  }
}

function render(d) {
  if (d.error) { out.innerHTML = `<p class="err">${esc(d.error)}</p>`; return; }
  const over = d.realtime_factor > 1.5;
  let html = `<div class="stats">
    <div class="stat"><b>${d.duration}s</b><span>녹음 길이</span></div>
    <div class="stat"><b>${d.transcribe_seconds}s</b><span>전사 시간</span></div>
    <div class="stat ${over ? 'over-target' : ''}"><b>${d.realtime_factor}x</b><span>목표 1.5x 이내</span></div>
    <div class="stat"><b>${d.language}</b><span>언어 ${(d.language_probability*100).toFixed(0)}%</span></div>
  </div>`;

  if (!d.segments.length) {
    html += '<p class="err">전사된 내용이 없습니다. 무음이거나 인식되지 않았습니다.</p>';
  }
  for (const s of d.segments) {
    const words = s.words.map(w =>
      `<span class="w ${w.probability < 0.6 ? 'low' : ''}" title="${w.start}s-${w.end}s p=${w.probability}">${esc(w.text)}</span>`
    ).join('');
    html += `<div class="row">
      <span class="dot" style="background:var(--ink)"></span>
      <span class="tc">${tc(s.start)} - ${tc(s.end)}</span>
      <span class="txt">${esc(s.text)}
        ${s.words.length ? `<details><summary>단어 ${s.words.length}개</summary><div class="words">${words}</div></details>` : ''}
      </span>
    </div>`;
  }
  out.innerHTML = html;
}
</script>
</body>
</html>
"""
