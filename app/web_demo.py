"""`/demo` 页面：打开即用，不出现任何账号密码。

页面做的事：
1. 打开时 POST /api/v1/demo/token 拿一枚 2 小时有效的 access_token；
2. 输入问题后 POST /api/v1/chat（SSE），边收边渲染，所以有打字机效果；
3. 收到 sources 事件时先把来源渲染出来，再渲染正文。
"""

DEMO_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 问答演示 · 白松豪</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6;
       min-height:100vh;display:flex;justify-content:center;padding:28px 16px}
  .wrap{width:100%;max-width:760px}
  h1{font-size:20px;font-weight:700;margin-bottom:6px}
  .sub{font-size:13px;color:#8b93a1;margin-bottom:18px;line-height:1.6}
  .sub b{color:#7fb383}
  .panel{background:#171a21;border:1px solid #262b36;border-radius:12px;padding:16px}
  .row{display:flex;gap:8px}
  input{flex:1;background:#0f1115;border:1px solid #2c3340;color:#e6e6e6;border-radius:8px;
        padding:11px 13px;font-size:14px;outline:none}
  input:focus{border-color:#3d6fb5}
  button{background:#2f6fd0;color:#fff;border:0;border-radius:8px;padding:0 18px;font-size:14px;
         cursor:pointer;font-weight:600}
  button:disabled{background:#2a3340;color:#7d8798;cursor:not-allowed}
  .log{margin-top:16px;display:flex;flex-direction:column;gap:12px}
  .msg{font-size:14px;line-height:1.72;white-space:pre-wrap;word-break:break-word}
  .msg.me{color:#9fc3f0}
  .msg.me::before{content:"你：";color:#5f6b7d}
  .src{font-size:12px;color:#8b93a1;border-left:2px solid #2f6fd0;padding-left:9px;margin-bottom:8px}
  .src b{color:#a8b6c8;font-weight:600}
  .err{color:#e06c60;font-size:13px}
  .hint{font-size:12px;color:#6c7484;margin-top:14px}
</style>
</head>
<body>
<div class="wrap">
  <h1>AI 问答演示</h1>
  <div class="sub">
    FastAPI 全异步 + RAG 检索 + ReAct 智能体 + SSE 流式，线上部署在 Railway。<br>
    <b>无需账号，打开即用</b>；体验 token 有效期 2 小时，回答下方会附检索来源与相似度。
  </div>
  <div class="panel">
    <div class="row">
      <input id="q" placeholder="问点什么，比如：JWT 双 Token 是什么？" autocomplete="off">
      <button id="go">发送</button>
    </div>
    <div class="log" id="log"></div>
    <div class="hint" id="hint">正在准备体验 token…</div>
  </div>
</div>

<script>
const log = document.getElementById('log');
const hint = document.getElementById('hint');
const input = document.getElementById('q');
const btn = document.getElementById('go');
let token = null;

async function getToken() {
  try {
    const r = await fetch('/api/v1/demo/token', {method: 'POST'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    token = d.access_token;
    hint.textContent = '体验 token 已就绪（2 小时有效）。';
  } catch (e) {
    hint.innerHTML = '<span class="err">体验入口暂不可用：' + e.message + '</span>';
  }
}

function addMsg(cls, text) {
  const div = document.createElement('div');
  div.className = 'msg ' + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

async function ask() {
  const q = input.value.trim();
  if (!q || !token) return;
  input.value = '';
  btn.disabled = true;
  addMsg('me', q);

  const box = document.createElement('div');
  log.appendChild(box);
  let body = '';

  try {
    const resp = await fetch('/api/v1/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token},
      body: JSON.stringify({prompt: q})
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const parts = buf.split('\\n\\n');
      buf = parts.pop();
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith('data:')) continue;
        const payload = line.slice(5).trim();
        if (payload === '[DONE]') continue;
        let obj;
        try { obj = JSON.parse(payload); } catch (e) { continue; }
        if (obj.sources) {
          const s = document.createElement('div');
          s.className = 'src';
          s.innerHTML = '<b>检索来源：</b>' + obj.sources.map(function (x) {
            const raw = String(x.source || '');
            const name = raw.length > 34 ? raw.slice(0, 34) + '…' : raw;
            const score = (x.score !== undefined) ? Number(x.score).toFixed(3) : '-';
            return name + '（' + score + '）';
          }).join(' · ');
          box.appendChild(s);
        }
        if (obj.content) {
          body += obj.content;
          let m = box.querySelector('.msg');
          if (!m) { m = document.createElement('div'); m.className = 'msg'; box.appendChild(m); }
          m.textContent = body;
          log.scrollTop = log.scrollHeight;
        }
        if (obj.error) {
          const e = document.createElement('div');
          e.className = 'err';
          e.textContent = obj.error;
          box.appendChild(e);
        }
      }
    }
  } catch (e) {
    const err = document.createElement('div');
    err.className = 'err';
    err.textContent = '请求失败：' + e.message;
    box.appendChild(err);
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

btn.addEventListener('click', ask);
input.addEventListener('keydown', function (e) { if (e.key === 'Enter') ask(); });
getToken();
</script>
</body>
</html>
"""
