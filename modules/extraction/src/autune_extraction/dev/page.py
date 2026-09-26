# ruff: noqa: E501 - the body of this file is an HTML document, not Python.
"""The dev integrations page, as a string. See ``routes.py``'s own docstring
for why this exists and when it goes away."""

PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Autune - 연동 설정 (dev)</title>
<style>
  :root {
    --paper:#F4F3EF; --panel:#FFFFFF; --sunken:#EBEAE5;
    --ink:#16191F; --body:#3A3F47; --muted:#7A8089;
    --hairline:rgba(22,25,31,.12);
    --accent:#3B4A9E; --accent-hover:#2C3878; --selection:#E9EBF6;
    --critical:#C2443C; --ok:#2E7D46; --radius:4px;
    --mono:ui-monospace,Menlo,monospace;
  }
  * { box-sizing:border-box }
  body { margin:0; background:var(--paper); color:var(--body); font:14px/1.65 Pretendard,system-ui,-apple-system,sans-serif; }
  main { max-width:640px; margin:0 auto; padding:28px }
  h1 { font-size:20px; font-weight:600; letter-spacing:-.015em; color:var(--ink); margin:0 }
  .sub { font-size:12.5px; color:var(--muted); margin:6px 0 24px }
  .warn {
    background:var(--ink); color:#fff; border-radius:var(--radius);
    padding:12px 16px; font-size:12.5px; font-weight:500; margin-bottom:24px;
  }
  section {
    background:var(--panel); border:1px solid var(--hairline); border-radius:var(--radius);
    padding:20px; margin-bottom:16px;
  }
  h2 { font-size:14px; font-weight:600; color:var(--ink); margin:0 0 4px }
  p.hint { font-size:12px; color:var(--muted); margin:0 0 14px }
  label { display:block; font-size:12px; color:var(--muted); margin:10px 0 4px }
  input {
    width:100%; padding:9px 10px; border:1px solid var(--hairline); border-radius:var(--radius);
    background:var(--paper); color:var(--ink); font-size:13px; font-family:var(--mono);
  }
  button {
    margin-top:14px; padding:9px 16px; border:none; border-radius:var(--radius);
    background:var(--accent); color:#fff; font-size:13px; font-weight:600; cursor:pointer;
  }
  button:hover { background:var(--accent-hover) }
  .result { margin-top:10px; font-size:12.5px; font-family:var(--mono); white-space:pre-wrap; }
  .result.ok { color:var(--ok) }
  .result.err { color:var(--critical) }
</style>
</head>
<body>
<main>
  <h1>연동 설정 (dev)</h1>
  <p class="sub">S28이 아직 없어서 대신 씁니다. 로컬 개발용, 인증 없음. AUTUNE_ENV=local에서만 마운트됩니다.</p>
  <div class="warn">team_id는 /api/audio/dev/token으로 발급받은 값을 그대로 씁니다.</div>

  <section>
    <h2>Notion</h2>
    <p class="hint">액션아이템·결정 확인 시 실제로 페이지가 생성됩니다.</p>
    <label>team_id</label>
    <input id="notion-team" placeholder="team_...">
    <label>Integration token</label>
    <input id="notion-token" placeholder="ntn_...">
    <label>action_db_id</label>
    <input id="notion-action-db">
    <label>decision_db_id</label>
    <input id="notion-decision-db">
    <button onclick="connectNotion()">Notion 연결</button>
    <div id="notion-result" class="result"></div>
  </section>

  <section>
    <h2>Slack</h2>
    <p class="hint">저장만 됩니다 — 애매한 동의 확인 DM(#12)은 #70·#30이 안 풀려서 아직 아무것도 안 보냅니다.</p>
    <label>team_id</label>
    <input id="slack-team" placeholder="team_...">
    <label>Bot token</label>
    <input id="slack-token" placeholder="xoxb-...">
    <button onclick="connectSlack()">Slack 연결</button>
    <div id="slack-result" class="result"></div>
  </section>

<script>
async function post(path, body, resultId) {
  const el = document.getElementById(resultId);
  el.className = "result";
  el.textContent = "...";
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || JSON.stringify(data));
    el.className = "result ok";
    el.textContent = JSON.stringify(data);
  } catch (e) {
    el.className = "result err";
    el.textContent = String(e);
  }
}

function connectNotion() {
  post("/api/extraction/dev/connect-notion", {
    team_id: document.getElementById("notion-team").value,
    token: document.getElementById("notion-token").value,
    action_db_id: document.getElementById("notion-action-db").value,
    decision_db_id: document.getElementById("notion-decision-db").value,
  }, "notion-result");
}

function connectSlack() {
  post("/api/extraction/dev/connect-slack", {
    team_id: document.getElementById("slack-team").value,
    bot_token: document.getElementById("slack-token").value,
  }, "slack-result");
}
</script>
</main>
</body>
</html>
"""
