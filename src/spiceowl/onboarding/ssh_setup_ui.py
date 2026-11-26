"""Reusable local graphical SSH Setup Wizard HTML + action dispatcher.

Designed to be mounted on loopback HTTP connectors by BOTH:
- SpiceOwl's own local Connector (`src/spiceowl/web/server.py`)
- CircuitGit's local Connector (`tools/workbench/remote_node.py` via submodule)

Security guarantees:
- Strict nonce-based Content-Security-Policy (`default-src 'none'`, no inline handlers).
- All state mutations happen over same-origin loopback `POST` carrying caller-verified
  `request_id` + constant-time `csrf`.
- Private keys and one-time passwords are never serialized into HTML or JSON responses.
"""

from __future__ import annotations

from html import escape as html_escape
import json
from pathlib import Path
import re

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.ssh_setup import (
    MANAGED_KEY_NAME,
    delete_managed_host,
    deploy_public_key,
    diagnose_connection,
    ensure_managed_key,
    list_all_ssh_aliases,
    list_local_keys,
    scan_host_key,
    trust_host_keys,
    upsert_managed_host,
)

SSH_SETUP_ACTIONS = frozenset({
    "state",
    "ensure_key",
    "upsert_host",
    "delete_host",
    "scan_host_key",
    "trust_host_keys",
    "deploy_key",
    "diagnose",
})

_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{12,128}$", re.ASCII)
_ACTION_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]{1,120}$", re.ASCII)


def _safe_json_for_script(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    return (
        raw.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_ssh_setup_state(ssh_dir: str | Path | None = None) -> dict:
    """Return presentation-safe SSH setup wizard state (zero private keys or secrets)."""
    base = Path(ssh_dir).expanduser().resolve(strict=False) if ssh_dir is not None else (Path.home() / ".ssh").resolve(strict=False)
    aliases_info = list_all_ssh_aliases(base)
    keys = list_local_keys(base)
    return {
        "managed": aliases_info["managed"],
        "existing_aliases": aliases_info["existing_aliases"],
        "unmanaged_aliases": aliases_info["unmanaged_aliases"],
        "managed_file": aliases_info["managed_file"],
        "keys": keys,
        "default_key_name": MANAGED_KEY_NAME,
        "has_managed_key": any(k.get("name") == MANAGED_KEY_NAME and k.get("has_private") for k in keys),
        "ssh_dir": str(base),
    }


def dispatch_ssh_setup_action(ssh_dir: str | Path | None, action: str, payload: dict | None = None) -> dict:
    """Validate and execute one SSH setup wizard action against ``ssh_dir``."""
    if not isinstance(action, str) or action not in SSH_SETUP_ACTIONS:
        raise DomainError("Unsupported SSH setup action.", "ssh_setup_action_invalid")
    data = {} if payload is None else payload
    if not isinstance(data, dict):
        raise DomainError("SSH setup payload must be a JSON object.", "ssh_setup_payload_invalid")
    base = Path(ssh_dir).expanduser().resolve(strict=False) if ssh_dir is not None else (Path.home() / ".ssh").resolve(strict=False)

    if action == "state":
        return {"ok": True, "state": build_ssh_setup_state(base)}

    if action == "ensure_key":
        key_name = data.get("key_name") or MANAGED_KEY_NAME
        key_info = ensure_managed_key(base, key_name=key_name)
        return {"ok": True, "key": key_info, "state": build_ssh_setup_state(base)}

    if action == "upsert_host":
        res = upsert_managed_host(base, data)
        return {"ok": True, **res, "state": build_ssh_setup_state(base)}

    if action == "delete_host":
        res = delete_managed_host(base, data.get("alias", ""))
        return {"ok": True, **res, "state": build_ssh_setup_state(base)}

    if action == "scan_host_key":
        cfg = base / "config"
        scan = scan_host_key(
            data.get("hostname", ""),
            data.get("port", 22),
            jump_alias=data.get("jump_alias", "") or data.get("proxy_jump", ""),
            ssh_config=cfg if cfg.is_file() else None,
        )
        return {"ok": True, "scan": scan}

    if action == "trust_host_keys":
        trust = trust_host_keys(base, data.get("entries", []))
        return {"ok": True, "trust": trust, "state": build_ssh_setup_state(base)}

    if action == "deploy_key":
        cfg = base / "config"
        pub = data.get("public_key", "")
        if not pub:
            pub = ensure_managed_key(base)["public_key"]
        accept_new = data.get("accept_new_host_key", True)
        if type(accept_new) is not bool:
            raise DomainError("accept_new_host_key must be boolean.", "ssh_setup_payload_invalid")
        deploy = deploy_public_key(
            ssh_dir=base,
            hostname=data.get("hostname", ""),
            port=data.get("port", 22),
            username=data.get("username", "") or data.get("user", ""),
            public_key=pub,
            password=data.get("password", ""),
            proxy_jump=data.get("proxy_jump", "") or data.get("jump_alias", ""),
            ssh_config=cfg if cfg.is_file() else None,
            accept_new_host_key=accept_new,
        )
        return {"ok": bool(deploy["ok"]), "deploy": deploy}

    # action == "diagnose"
    diag = diagnose_connection(base, data.get("alias", ""))
    return {"ok": bool(diag["ok"]), "diagnosis": diag}


def ssh_setup_csp(nonce: str) -> str:
    """Build the strict loopback Content-Security-Policy header for ``ssh_setup_html``."""
    if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
        raise DomainError("Invalid CSP nonce.", "ssh_setup_nonce_invalid")
    return (
        "default-src 'none'; "
        f"script-src 'nonce-{nonce}'; "
        f"style-src 'nonce-{nonce}'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'self'"
    )


def ssh_setup_html(
    origin: str,
    request_id: str,
    csrf: str,
    state: dict,
    nonce: str,
    *,
    brand: str = "SpiceOwl",
    action_path: str = "/ssh/setup/action",
    post_message_type: str = "spiceowl.ssh.updated",
) -> bytes:
    """Render the self-contained, strict-CSP graphical SSH Setup Wizard page."""
    if not isinstance(nonce, str) or not _NONCE_RE.fullmatch(nonce):
        raise DomainError("Invalid CSP nonce.", "ssh_setup_nonce_invalid")
    if not isinstance(action_path, str) or not _ACTION_PATH_RE.fullmatch(action_path):
        raise DomainError("Invalid SSH setup action path.", "ssh_setup_path_invalid")
    if not isinstance(origin, str) or not origin:
        raise DomainError("Origin is required for SSH setup wizard.", "ssh_setup_origin_invalid")

    safe_brand = html_escape(str(brand or "SpiceOwl"), quote=True)
    safe_nonce = html_escape(nonce, quote=True)
    boot = _safe_json_for_script({
        "origin": origin,
        "requestId": request_id,
        "csrf": csrf,
        "actionPath": action_path,
        "postMessageType": post_message_type,
        "brand": str(brand or "SpiceOwl"),
        "state": state,
    })

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_brand} · SSH 图形化配置向导</title>
<style nonce="{safe_nonce}">
:root{{--bg:#f4f5f0;--card:#ffffff;--ink:#172e35;--muted:#53666c;--line:#d8dfd7;--accent:#1f6f54;--accent-hover:#185842;--accent-soft:#edf5f0;--danger:#b42318;--danger-soft:#fef3f2;--warn:#9a5b13;--warn-soft:#fef7ec;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}}
*{{box-sizing:border-box}}
body{{margin:0;padding:18px;background:var(--bg);color:var(--ink);font:14px/1.55 var(--sans)}}
.wrap{{max-width:980px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
.hdr{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}
.hdr h1{{margin:0;font-size:18px;letter-spacing:-.01em}}
.hdr p{{margin:4px 0 0;font-size:12.5px;color:var(--muted)}}
.pill{{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:var(--accent-soft);color:var(--accent-hover);font-size:11.5px;font-weight:600}}
.layout{{display:grid;grid-template-columns:280px minmax(0,1fr);gap:14px;align-items:start}}
@media(max-width:780px){{.layout{{grid-template-columns:1fr}}}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;display:flex;flex-direction:column;gap:12px}}
.panel h2{{margin:0;font-size:15px;display:flex;align-items:center;gap:8px}}
.step-badge{{width:22px;height:22px;border-radius:50%;background:var(--accent);color:#fff;font:700 12px/1 var(--mono);display:inline-flex;align-items:center;justify-content:center;flex:none}}
.sub{{margin:0;font-size:12.5px;color:var(--muted)}}
.host-list{{display:flex;flex-direction:column;gap:8px}}
.host-item{{border:1px solid var(--line);border-radius:9px;padding:10px 12px;background:#fbfcfa;display:flex;flex-direction:column;gap:6px}}
.host-item.active{{border-color:var(--accent);background:var(--accent-soft)}}
.host-item-top{{display:flex;justify-content:space-between;align-items:center;gap:6px}}
.host-item-top strong{{font-size:13.5px;word-break:break-all}}
.host-meta{{font:11.5px/1.4 var(--mono);color:var(--muted);word-break:break-all}}
.tag{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;font-weight:600;background:#eef2ee;color:var(--muted)}}
.tag.managed{{background:var(--accent-soft);color:var(--accent-hover)}}
.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}
@media(max-width:560px){{.grid2{{grid-template-columns:1fr}}}}
label.field{{display:flex;flex-direction:column;gap:4px;font-size:12.5px;font-weight:600;color:var(--ink)}}
label.field small{{font-weight:400;font-size:11.5px;color:var(--muted)}}
input,select,textarea{{width:100%;padding:8px 10px;border:1px solid #c7d2ca;border-radius:7px;font:13.5px/1.4 var(--sans);background:#fff;color:var(--ink)}}
input:focus,select:focus,textarea:focus{{outline:2px solid rgba(31,111,84,.25);border-color:var(--accent)}}
textarea.mono,pre.mono{{font:12px/1.45 var(--mono);background:#f6f8f6;border:1px solid var(--line);border-radius:8px;padding:10px;margin:0;white-space:pre-wrap;word-break:break-all}}
.row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
button{{border:1px solid transparent;border-radius:7px;padding:8px 13px;font:600 13px/1.2 var(--sans);cursor:pointer;transition:background .15s,border-color .15s}}
button:disabled{{opacity:.55;cursor:not-allowed}}
.btn-primary{{background:var(--accent);color:#fff}}
.btn-primary:hover:not(:disabled){{background:var(--accent-hover)}}
.btn-sec{{background:#eef2ee;color:var(--ink);border-color:#d4ddd5}}
.btn-sec:hover:not(:disabled){{background:#e3eae4}}
.btn-danger{{background:var(--danger-soft);color:var(--danger);border-color:#f3c4c0}}
.btn-sm{{padding:5px 9px;font-size:12px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px}}
.chip-btn{{background:#f4f7f5;border:1px solid #cbd7cf;color:var(--ink);font:500 12px/1.2 var(--mono);padding:4px 8px;border-radius:6px}}
.chip-btn:hover{{border-color:var(--accent);background:var(--accent-soft)}}
.notice{{border-radius:8px;padding:10px 12px;font-size:12.5px;border:1px solid var(--line);background:#f8faf8}}
.notice.ok{{background:var(--accent-soft);border-color:#b6d7c7;color:#144c38}}
.notice.err{{background:var(--danger-soft);border-color:#f4c5c1;color:var(--danger)}}
.notice.warn{{background:var(--warn-soft);border-color:#f0d4a8;color:var(--warn)}}
.step-list{{display:flex;flex-direction:column;gap:8px;margin:0;padding:0;list-style:none}}
.step-row{{display:flex;gap:10px;align-items:flex-start;padding:9px 11px;border-radius:8px;border:1px solid var(--line);background:#fbfcfa}}
.step-row.ok{{border-color:#b8d9c8;background:#f4faf6}}
.step-row.fail{{border-color:#f3c4c0;background:#fff6f5}}
.step-icon{{font-weight:700;flex:none;width:18px;text-align:center}}
.fp-box{{display:flex;flex-direction:column;gap:6px}}
.fp-item{{font:12px/1.4 var(--mono);padding:8px 10px;border-radius:7px;background:#f6f8f6;border:1px solid var(--line);word-break:break-all}}
.stack{{display:flex;flex-direction:column;gap:14px;min-width:0}}
.footer-bar{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
details{{border:1px dashed var(--line);border-radius:8px;padding:10px 12px;background:#fcfdfb}}
details summary{{cursor:pointer;font-weight:600;font-size:13px;color:var(--ink)}}
.hidden{{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <header class="hdr">
    <div>
      <h1>🦉 {safe_brand} · 本机 SSH 图形化配置向导</h1>
      <p>零命令行配置目标 EDA 服务器、跳板机（支持单跳/多跳）、免密密钥与指纹信任。配置独立写入 <code>spiceowl_hosts.conf</code>，绝不覆盖你原有的 <code>~/.ssh/config</code>。</p>
    </div>
    <span class="pill">🔒 仅限本机 127.0.0.1 · 密码绝不落盘</span>
  </header>

  <div id="global-msg" class="notice hidden" role="status"></div>

  <div class="layout">
    <aside class="panel">
      <div class="row" style="justify-content:space-between">
        <h2>已发现的 SSH 主机</h2>
        <button id="btn-new-host" class="btn-sec btn-sm" type="button">+ 新建</button>
      </div>
      <p class="sub">点击托管主机即可编辑或诊断；原有 <code>~/.ssh/config</code> 别名可直接选作跳板机。</p>
      <div id="host-list" class="host-list"></div>
    </aside>

    <main class="stack">
      <section class="panel">
        <h2><span class="step-badge">1</span> 配置目标服务器与跳板机（ProxyJump）</h2>
        <form id="host-form" autocomplete="off">
          <div class="grid2">
            <label class="field">服务器别名（Alias）
              <input id="f-alias" required maxlength="96" placeholder="例如：eda-sim-01">
              <small>字母/数字/短横线，用于在网页端一键选择。</small>
            </label>
            <label class="field">目标主机地址（IP 或域名）
              <input id="f-hostname" required maxlength="253" placeholder="例如：10.0.1.25 或 sim.lab.edu">
              <small>内网 IP 或域名均可。</small>
            </label>
            <label class="field">登录用户名（User）
              <input id="f-user" required maxlength="64" placeholder="例如：alice">
            </label>
            <label class="field">SSH 端口（Port）
              <input id="f-port" type="number" min="1" max="65535" value="22">
            </label>
            <label class="field">跳板机（ProxyJump，可选）
              <input id="f-jump" maxlength="320" placeholder="直连留空；多跳用逗号分隔，如 jump1,jump2">
              <small>点下方已有别名可一键追加跳板机。</small>
            </label>
            <label class="field">免密私钥（IdentityFile）
              <select id="f-key"></select>
              <small>默认使用 {safe_brand} 自动管理的 Ed25519 密钥。</small>
            </label>
          </div>
          <div id="jump-chips" class="chips" style="margin-top:8px"></div>
          <div class="row" style="margin-top:12px">
            <button id="btn-save-host" class="btn-primary" type="submit">保存服务器配置</button>
            <button id="btn-diag-current" class="btn-sec" type="button">直接测试当前别名连通性</button>
          </div>
        </form>
      </section>

      <section class="panel">
        <h2><span class="step-badge">2</span> 免密密钥准备与公钥部署</h2>
        <p class="sub">使用标准 Ed25519 密钥（<code>~/.ssh/id_ed25519_spiceowl</code>）。私钥只留在你本机，向导仅读取公钥与 SHA256 指纹。</p>
        <div class="row">
          <button id="btn-ensure-key" class="btn-primary btn-sm" type="button">一键生成 / 检查托管密钥</button>
          <button id="btn-copy-pub" class="btn-sec btn-sm" type="button">复制公钥</button>
          <button id="btn-copy-cmd" class="btn-sec btn-sm" type="button">复制手动安装命令</button>
        </div>
        <div id="key-fp" class="sub"></div>
        <textarea id="pub-key-box" class="mono" rows="2" readonly placeholder="点击上方「一键生成 / 检查托管密钥」生成 Ed25519 公钥…"></textarea>

        <details id="deploy-details">
          <summary>选填：输入一次性登录密码，自动把公钥写入远端服务器（密码用完即销毁）</summary>
          <p class="sub" style="margin:8px 0">若远端允许密码登录，输入一次密码即可自动追加到远端 <code>~/.ssh/authorized_keys</code>；之后全程免密。若公司/学校要求动态验证码或禁止密码登录，请直接用上方「复制公钥」手动添加。</p>
          <div class="grid2">
            <label class="field">远端登录密码（仅本次生效，绝不保存）
              <input id="f-password" type="password" autocomplete="new-password" placeholder="输入后点右侧自动部署">
            </label>
            <label class="field" style="justify-content:flex-end">
              <button id="btn-deploy-key" class="btn-primary" type="button">一次性部署公钥到目标机</button>
            </label>
          </div>
          <div id="deploy-msg" class="notice hidden" style="margin-top:8px"></div>
        </details>
      </section>

      <section class="panel">
        <h2><span class="step-badge">3</span> 扫描并信任服务器指纹（known_hosts）</h2>
        <p class="sub">首次连接陌生服务器前，先获取其主机指纹并确认写入本机 <code>~/.ssh/known_hosts</code>（有跳板机时自动经由跳板机扫描）。</p>
        <div class="row">
          <button id="btn-scan-fp" class="btn-sec" type="button">扫描当前主机指纹</button>
          <button id="btn-trust-fp" class="btn-primary" type="button" disabled>确认指纹无误，写入 known_hosts</button>
        </div>
        <div id="fp-results" class="fp-box"></div>
      </section>

      <section class="panel">
        <h2><span class="step-badge">4</span> 分步连通性诊断</h2>
        <p class="sub">依次核验网络/VPN 端口、跳板机免密、目标机免密，以及远端 Python 与 Spectre / ngspice 可用性。</p>
        <div class="row">
          <button id="btn-run-diag" class="btn-primary" type="button">运行完整分步诊断</button>
          <span id="diag-badge" class="sub"></span>
        </div>
        <ul id="diag-steps" class="step-list"></ul>
      </section>
    </main>
  </div>

  <footer class="footer-bar">
    <span class="sub">配置完成后点击右侧按钮，自动把最新 SSH 别名同步回 {safe_brand} 网页工作台。</span>
    <div class="row">
      <button id="btn-close-only" class="btn-sec" type="button">关闭窗口</button>
      <button id="btn-done-return" class="btn-primary" type="button">完成并返回网页工作台</button>
    </div>
  </footer>
</div>

<script nonce="{safe_nonce}">
(function() {{
  const BOOT = {boot};
  let appState = BOOT.state || {{}};
  let scannedEntries = [];
  let selectedAlias = '';

  const $ = (id) => document.getElementById(id);

  function setMsg(el, text, kind) {{
    if (!el) return;
    if (!text) {{ el.className = 'notice hidden'; el.textContent = ''; return; }}
    el.className = 'notice ' + (kind || '');
    el.textContent = text;
  }}

  async function callAction(action, payload) {{
    const res = await fetch(BOOT.actionPath, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        request_id: BOOT.requestId,
        csrf: BOOT.csrf,
        action: action,
        payload: payload || {{}},
      }}),
    }});
    const data = await res.json().catch(() => ({{}}));
    if (!res.ok) {{
      throw new Error(data.error && data.error.message ? data.error.message : '本机操作失败');
    }}
    if (data.state) appState = data.state;
    return data;
  }}

  function managedKeyObj() {{
    const keys = appState.keys || [];
    return keys.find(k => k.managed && k.has_private) || keys.find(k => k.has_private) || keys[0] || null;
  }}

  function renderKeys() {{
    const sel = $('f-key');
    const prev = sel.value;
    sel.replaceChildren();
    const optAuto = document.createElement('option');
    optAuto.value = '';
    optAuto.textContent = '使用 OpenSSH 默认密钥（不固定 IdentityFile）';
    sel.appendChild(optAuto);
    const keys = appState.keys || [];
    for (const k of keys) {{
      if (!k.has_private) continue;
      const opt = document.createElement('option');
      opt.value = k.config_path;
      opt.textContent = k.name + (k.managed ? '（推荐 · 托管密钥）' : '') + ' · ' + k.key_type;
      sel.appendChild(opt);
    }}
    const mk = managedKeyObj();
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
    else if (mk && mk.has_private) sel.value = mk.config_path;

    $('pub-key-box').value = mk ? mk.public_key : '';
    $('key-fp').textContent = mk ? ('当前公钥：' + mk.name + ' · 指纹 ' + mk.fingerprint) : '尚未生成托管 Ed25519 密钥，点击上方按钮 1 秒生成。';
  }}

  function renderJumpChips() {{
    const box = $('jump-chips');
    box.replaceChildren();
    const curAlias = $('f-alias').value.trim();
    const candidates = (appState.existing_aliases || []).filter(a => a !== curAlias);
    if (!candidates.length) return;
    const hint = document.createElement('span');
    hint.className = 'sub';
    hint.textContent = '快捷选跳板机：';
    box.appendChild(hint);
    for (const alias of candidates.slice(0, 12)) {{
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chip-btn';
      btn.textContent = '+ ' + alias;
      btn.addEventListener('click', () => {{
        const input = $('f-jump');
        const cur = input.value.trim();
        const parts = cur ? cur.split(',').map(s => s.trim()).filter(Boolean) : [];
        if (!parts.includes(alias)) parts.push(alias);
        input.value = parts.join(',');
      }});
      box.appendChild(btn);
    }}
  }}

  function fillFormFromManaged(h) {{
    selectedAlias = h.alias;
    $('f-alias').value = h.alias || '';
    $('f-hostname').value = h.hostname || '';
    $('f-user').value = h.user || '';
    $('f-port').value = String(h.port || 22);
    $('f-jump').value = h.proxy_jump || '';
    if (h.identity_file && [...$('f-key').options].some(o => o.value === h.identity_file)) {{
      $('f-key').value = h.identity_file;
    }} else if (!h.identity_file) {{
      $('f-key').value = '';
    }}
    scannedEntries = [];
    $('fp-results').replaceChildren();
    $('btn-trust-fp').disabled = true;
    renderHosts();
    renderJumpChips();
  }}

  function renderHosts() {{
    const box = $('host-list');
    box.replaceChildren();
    const managed = appState.managed || [];
    const unmanaged = appState.unmanaged_aliases || [];
    if (!managed.length && !unmanaged.length) {{
      const p = document.createElement('p');
      p.className = 'sub';
      p.textContent = '还没有配置任何 SSH 主机。直接在右侧填写第 1 步即可开始。';
      box.appendChild(p);
      return;
    }}
    for (const h of managed) {{
      const item = document.createElement('div');
      item.className = 'host-item' + (selectedAlias === h.alias ? ' active' : '');
      const top = document.createElement('div');
      top.className = 'host-item-top';
      const strong = document.createElement('strong');
      strong.textContent = h.alias;
      const badge = document.createElement('span');
      badge.className = 'tag managed';
      badge.textContent = '向导托管';
      top.append(strong, badge);
      const meta = document.createElement('div');
      meta.className = 'host-meta';
      meta.textContent = (h.user ? h.user + '@' : '') + h.hostname + (h.port && h.port !== 22 ? ':' + h.port : '') + (h.proxy_jump ? '  via ' + h.proxy_jump : '');
      const actions = document.createElement('div');
      actions.className = 'row';
      const bEdit = document.createElement('button');
      bEdit.type = 'button'; bEdit.className = 'btn-sec btn-sm'; bEdit.textContent = '编辑';
      bEdit.addEventListener('click', () => fillFormFromManaged(h));
      const bDiag = document.createElement('button');
      bDiag.type = 'button'; bDiag.className = 'btn-sec btn-sm'; bDiag.textContent = '诊断';
      bDiag.addEventListener('click', () => {{ fillFormFromManaged(h); runDiagnosis(h.alias); }});
      const bDel = document.createElement('button');
      bDel.type = 'button'; bDel.className = 'btn-danger btn-sm'; bDel.textContent = '删除';
      bDel.addEventListener('click', async () => {{
        try {{
          await callAction('delete_host', {{alias: h.alias}});
          if (selectedAlias === h.alias) selectedAlias = '';
          renderAll();
          setMsg($('global-msg'), '已从托管配置中移除 ' + h.alias + '（原有 ~/.ssh/config 不受影响）。', 'ok');
        }} catch (e) {{ setMsg($('global-msg'), e.message, 'err'); }}
      }});
      actions.append(bEdit, bDiag, bDel);
      item.append(top, meta, actions);
      box.appendChild(item);
    }}
    for (const alias of unmanaged) {{
      const item = document.createElement('div');
      item.className = 'host-item';
      const top = document.createElement('div');
      top.className = 'host-item-top';
      const strong = document.createElement('strong');
      strong.textContent = alias;
      const badge = document.createElement('span');
      badge.className = 'tag';
      badge.textContent = '原有 config';
      top.append(strong, badge);
      const actions = document.createElement('div');
      actions.className = 'row';
      const bJump = document.createElement('button');
      bJump.type = 'button'; bJump.className = 'btn-sec btn-sm'; bJump.textContent = '设为跳板机';
      bJump.addEventListener('click', () => {{
        const cur = $('f-jump').value.trim();
        const parts = cur ? cur.split(',').map(s => s.trim()).filter(Boolean) : [];
        if (!parts.includes(alias)) parts.push(alias);
        $('f-jump').value = parts.join(',');
      }});
      const bDiag = document.createElement('button');
      bDiag.type = 'button'; bDiag.className = 'btn-sec btn-sm'; bDiag.textContent = '诊断';
      bDiag.addEventListener('click', () => {{ selectedAlias = alias; runDiagnosis(alias); }});
      actions.append(bJump, bDiag);
      item.append(top, actions);
      box.appendChild(item);
    }}
  }}

  function renderAll() {{
    renderKeys();
    renderHosts();
    renderJumpChips();
  }}

  async function copyText(text, okLabel) {{
    if (!text) {{ setMsg($('global-msg'), '当前没有可复制的内容，请先生成密钥。', 'warn'); return; }}
    try {{
      await navigator.clipboard.writeText(text);
      setMsg($('global-msg'), okLabel, 'ok');
    }} catch {{
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      setMsg($('global-msg'), okLabel, 'ok');
    }}
  }}

  async function runDiagnosis(alias) {{
    const target = (alias || $('f-alias').value || '').trim();
    if (!target) {{ setMsg($('global-msg'), '请先填写并保存一个服务器别名，或点击左侧已有别名。', 'warn'); return; }}
    $('btn-run-diag').disabled = true;
    $('diag-badge').textContent = '正在分步诊断 ' + target + '…';
    const ul = $('diag-steps');
    ul.replaceChildren();
    try {{
      const res = await callAction('diagnose', {{alias: target}});
      const d = res.diagnosis || {{}};
      $('diag-badge').textContent = d.ok ? '✅ 全部就绪（code: ready）' : ('⚠️ 需处理（code: ' + (d.code || 'unknown') + '）');
      for (const s of (d.steps || [])) {{
        const li = document.createElement('li');
        li.className = 'step-row ' + (s.ok ? 'ok' : 'fail');
        const ic = document.createElement('span');
        ic.className = 'step-icon';
        ic.textContent = s.ok ? '✓' : '✕';
        const body = document.createElement('div');
        const st = document.createElement('strong');
        st.textContent = s.label;
        const dt = document.createElement('div');
        dt.className = 'sub';
        dt.textContent = s.detail;
        body.append(st, dt);
        li.append(ic, body);
        ul.appendChild(li);
      }}
      if (d.ok) {{
        selectedAlias = target;
        setMsg($('global-msg'), '诊断通过！' + target + ' 已可免密直连，点击底部「完成并返回网页工作台」即可使用。', 'ok');
      }} else {{
        setMsg($('global-msg'), '诊断发现阻塞项，请按第 4 步红色提示处理后重新诊断。', 'warn');
      }}
    }} catch (e) {{
      $('diag-badge').textContent = '';
      setMsg($('global-msg'), e.message, 'err');
    }} finally {{
      $('btn-run-diag').disabled = false;
    }}
  }}

  $('btn-new-host').addEventListener('click', () => {{
    selectedAlias = '';
    $('f-alias').value = '';
    $('f-hostname').value = '';
    $('f-user').value = '';
    $('f-port').value = '22';
    $('f-jump').value = '';
    renderAll();
  }});

  $('f-alias').addEventListener('input', renderJumpChips);

  $('host-form').addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    $('btn-save-host').disabled = true;
    try {{
      const payload = {{
        alias: $('f-alias').value.trim(),
        hostname: $('f-hostname').value.trim(),
        user: $('f-user').value.trim(),
        port: Number($('f-port').value || 22),
        proxy_jump: $('f-jump').value.trim(),
        identity_file: $('f-key').value,
      }};
      const res = await callAction('upsert_host', payload);
      selectedAlias = res.host.alias;
      renderAll();
      setMsg($('global-msg'), '已保存服务器 ' + res.host.alias + ' 到独立托管配置，并已确保 ~/.ssh/config 顶部包含 Include。', 'ok');
    }} catch (e) {{
      setMsg($('global-msg'), e.message, 'err');
    }} finally {{
      $('btn-save-host').disabled = false;
    }}
  }});

  $('btn-diag-current').addEventListener('click', () => runDiagnosis($('f-alias').value.trim()));
  $('btn-run-diag').addEventListener('click', () => runDiagnosis($('f-alias').value.trim()));

  $('btn-ensure-key').addEventListener('click', async () => {{
    $('btn-ensure-key').disabled = true;
    try {{
      const res = await callAction('ensure_key', {{}});
      renderAll();
      setMsg($('global-msg'), res.key.created ? ('已生成新的 Ed25519 密钥（指纹 ' + res.key.fingerprint + '）。') : ('本机托管密钥已就绪（指纹 ' + res.key.fingerprint + '）。'), 'ok');
    }} catch (e) {{ setMsg($('global-msg'), e.message, 'err'); }}
    finally {{ $('btn-ensure-key').disabled = false; }}
  }});

  $('btn-copy-pub').addEventListener('click', () => copyText($('pub-key-box').value.trim(), '已复制公钥到剪贴板。'));
  $('btn-copy-cmd').addEventListener('click', () => {{
    const pub = $('pub-key-box').value.trim();
    if (!pub) {{ setMsg($('global-msg'), '请先点击「一键生成 / 检查托管密钥」。', 'warn'); return; }}
    const cmd = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && printf '%s\\n' '" + pub.replace(/'/g, "'\\''") + "' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys";
    copyText(cmd, '已复制远端公钥安装命令（在远端终端粘贴执行即可）。');
  }});

  $('btn-deploy-key').addEventListener('click', async () => {{
    const pwEl = $('f-password');
    const pw = pwEl.value;
    if (!pw) {{ setMsg($('deploy-msg'), '请输入一次性登录密码。', 'warn'); return; }}
    $('btn-deploy-key').disabled = true;
    setMsg($('deploy-msg'), '正在通过临时 SSH_ASKPASS 安全部署公钥…', '');
    try {{
      const res = await callAction('deploy_key', {{
        hostname: $('f-hostname').value.trim(),
        port: Number($('f-port').value || 22),
        username: $('f-user').value.trim(),
        proxy_jump: $('f-jump').value.trim(),
        public_key: $('pub-key-box').value.trim(),
        password: pw,
        accept_new_host_key: true,
      }});
      pwEl.value = '';
      const info = res.deploy || {{}};
      setMsg($('deploy-msg'), info.message || (res.ok ? '公钥已部署。' : '部署未成功。'), res.ok ? 'ok' : 'err');
    }} catch (e) {{
      pwEl.value = '';
      setMsg($('deploy-msg'), e.message, 'err');
    }} finally {{
      $('btn-deploy-key').disabled = false;
    }}
  }});

  $('btn-scan-fp').addEventListener('click', async () => {{
    $('btn-scan-fp').disabled = true;
    $('btn-trust-fp').disabled = true;
    scannedEntries = [];
    const box = $('fp-results');
    box.replaceChildren();
    try {{
      const res = await callAction('scan_host_key', {{
        hostname: $('f-hostname').value.trim(),
        port: Number($('f-port').value || 22),
        jump_alias: $('f-jump').value.trim(),
      }});
      const scan = res.scan || {{}};
      scannedEntries = scan.entries || [];
      for (const fp of (scan.fingerprints || [])) {{
        const div = document.createElement('div');
        div.className = 'fp-item';
        div.textContent = fp.key_type + '  ' + fp.fingerprint + '  (' + fp.host_pattern + ')';
        box.appendChild(div);
      }}
      $('btn-trust-fp').disabled = scannedEntries.length === 0;
      setMsg($('global-msg'), '已扫描到 ' + scannedEntries.length + ' 条主机指纹，请核对后点击「确认指纹无误，写入 known_hosts」。', 'ok');
    }} catch (e) {{
      setMsg($('global-msg'), e.message, 'err');
    }} finally {{
      $('btn-scan-fp').disabled = false;
    }}
  }});

  $('btn-trust-fp').addEventListener('click', async () => {{
    if (!scannedEntries.length) return;
    $('btn-trust-fp').disabled = true;
    try {{
      const res = await callAction('trust_host_keys', {{entries: scannedEntries}});
      const t = res.trust || {{}};
      setMsg($('global-msg'), '已写入 ~/.ssh/known_hosts（新增 ' + (t.added || 0) + ' 条，共 ' + (t.total || 0) + ' 条）。', 'ok');
    }} catch (e) {{
      setMsg($('global-msg'), e.message, 'err');
      $('btn-trust-fp').disabled = false;
    }}
  }});

  $('btn-close-only').addEventListener('click', () => window.close());
  $('btn-done-return').addEventListener('click', () => {{
    if (window.opener) {{
      try {{
        window.opener.postMessage({{
          type: BOOT.postMessageType,
          request_id: BOOT.requestId,
          ok: true,
          aliases: appState.existing_aliases || [],
          managed: appState.managed || [],
          selected_alias: selectedAlias || $('f-alias').value.trim() || '',
        }}, BOOT.origin);
      }} catch {{}}
    }}
    window.close();
  }});

  renderAll();
  if ((appState.managed || []).length) fillFormFromManaged(appState.managed[0]);
}})();
</script>
</body>
</html>"""
    return html.encode("utf-8")
