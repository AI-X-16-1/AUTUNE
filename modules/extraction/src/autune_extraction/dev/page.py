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
  <p class="sub">S28이 아직 없어서 대신 씁니다. 로컬 개발용, 인증 없음. AUTUNE_ENV=local과 AUTUNE_EXTRACTION_DEV_ROUTES=true가 둘 다 있어야 마운트됩니다.</p>
  <div class="warn">team_id는 /api/audio/dev/token으로 발급받은 값을 그대로 씁니다.</div>

  <section>
    <h2>Notion</h2>
    <p class="hint">페이지 하나를 integration에 먼저 공유하세요. 그 아래에 "액션 아이템"·"결정" DB를 만들고, 이후 확정한 항목이 그 DB에 페이지로 생깁니다.</p>
    <label>team_id</label>
    <input id="notion-team" placeholder="team_...">
    <label>Integration token</label>
    <input id="notion-token" placeholder="ntn_...">
    <label>Notion 페이지 URL 또는 id</label>
    <input id="notion-page" placeholder="https://www.notion.so/...">
    <button onclick="connectNotion()">Notion 연결</button>
    <div id="notion-result" class="result"></div>
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
    if (!res.ok) throw new Error(describe(data));
    el.className = "result ok";
    el.textContent = JSON.stringify(data);
  } catch (e) {
    el.className = "result err";
    el.textContent = String(e);
  }
}

// A 422's detail is a list; show where and what, never a value (the token is one).
function describe(data) {
  if (Array.isArray(data.detail)) {
    return data.detail.map((e) => (e.loc || []).join(".") + ": " + e.msg).join("; ");
  }
  return data.detail || "request failed";
}

function connectNotion() {
  post("/api/extraction/dev/connect-notion", {
    team_id: document.getElementById("notion-team").value,
    token: document.getElementById("notion-token").value,
    page_id: document.getElementById("notion-page").value,
  }, "notion-result");
}
</script>
</main>
</body>
</html>
"""
