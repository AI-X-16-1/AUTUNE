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
  button:disabled { opacity:.6; cursor:default }
  .result { margin-top:14px; font-size:13px; }
  .result:empty { display:none }
  .result.busy { color:var(--muted) }
  .card { border:1px solid var(--hairline); border-radius:var(--radius); padding:14px 16px; background:var(--paper) }
  .card.ok { border-color:rgba(46,125,70,.35); background:rgba(46,125,70,.06) }
  .card.err { border-color:rgba(194,68,60,.35); background:rgba(194,68,60,.06) }
  .card h3 { margin:0 0 4px; font-size:13.5px; font-weight:600 }
  .card.ok h3 { color:var(--ok) }
  .card.err h3 { color:var(--critical) }
  .card p { margin:4px 0; color:var(--body) }
  .links { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 6px }
  .links a {
    display:inline-block; padding:7px 12px; border:1px solid var(--hairline); border-radius:var(--radius);
    background:var(--panel); color:var(--accent); text-decoration:none; font-weight:600; font-size:12.5px;
  }
  .links a:hover { background:var(--selection) }
  .next { font-size:12px; color:var(--muted) }
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
    <button id="notion-connect" onclick="connectNotion()">Notion 연결</button>
    <div id="notion-result" class="result"></div>
  </section>

<script>
// Built with DOM calls rather than innerHTML: the text comes from the server
// and from Notion's own error messages.
function node(tag, attrs, children) {
  const el = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
  (children || []).forEach((c) => el.append(c));
  return el;
}

function card(kind, title, lines) {
  return node("div", { class: "card " + kind }, [node("h3", {}, [title]), ...lines]);
}

function connected(data) {
  const made = data.databases === "created";
  return card("ok", "✓ Notion에 연결했습니다", [
    node("p", {}, [made
      ? "페이지 아래에 “액션 아이템”·“결정” DB를 새로 만들었습니다."
      : "이 페이지에 전에 만든 DB가 있어서 그대로 씁니다. 새로 만들지 않았습니다."]),
    node("div", { class: "links" }, [
      node("a", { href: data.action_db_url, target: "_blank", rel: "noopener" }, ["액션 아이템 DB 열기 ↗"]),
      node("a", { href: data.decision_db_url, target: "_blank", rel: "noopener" }, ["결정 DB 열기 ↗"]),
    ]),
    node("p", { class: "next" }, ["이제 액션 보드에서 항목을 확정하면 이 DB에 페이지가 생깁니다."]),
  ]);
}

function failed(status, message) {
  const hint = status === 404
    ? "페이지를 연동에 공유했는지 확인하세요: Notion 페이지 오른쪽 위 ••• → 연결에서 추가."
    : status === 401
      ? "Integration token을 다시 확인하세요."
      : null;
  return card("err", "연결하지 못했습니다", [
    node("p", {}, [message]),
    ...(hint ? [node("p", { class: "next" }, [hint])] : []),
  ]);
}

async function post(path, body, resultId, buttonId, render) {
  const el = document.getElementById(resultId);
  const button = document.getElementById(buttonId);
  button.disabled = true;
  el.className = "result busy";
  el.replaceChildren("연결 중… Notion에 DB를 만드는 데 몇 초 걸립니다.");
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    el.className = "result";
    el.replaceChildren(res.ok ? render(data) : failed(res.status, describe(data)));
  } catch (e) {
    el.className = "result";
    el.replaceChildren(failed(0, "API에 닿지 못했습니다: " + String(e)));
  } finally {
    button.disabled = false;
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
  }, "notion-result", "notion-connect", connected);
}
</script>
</main>
</body>
</html>
"""
