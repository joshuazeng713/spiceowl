"""Reusable local graphical SSH Setup Wizard HTML + action dispatcher.

Designed to be mounted on loopback HTTP connectors by BOTH:
- SpiceOwl's own local Connector (`src/spiceowl/web/server.py`)
- CircuitGit's local Connector (`tools/workbench/remote_node.py` via submodule)

UX philosophy:
- Ask ONLY for the minimal essentials up front (target host + username, plus
  optional ProxyJump toggle). Alias, port (22), and managed Ed25519 key are
  handled automatically and tucked under an expandable "Advanced" section.
- Progressive disclosure: click ONE button ("保存并测试连接"); if host key
  trust or public-key deployment is needed, the wizard automatically surfaces
  only that exact step in-place and re-tests once confirmed.

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
    initial_alias: str = "",
) -> bytes:
    """Render the streamlined, progressive-disclosure SSH Setup Wizard page."""
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
        "initialAlias": str(initial_alias or ""),
        "state": state,
    })

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_brand} · 连接 SSH 服务器</title>
<style nonce="{safe_nonce}">
:root{{--bg:#f4f5f0;--card:#ffffff;--ink:#172e35;--muted:#53666c;--line:#d8dfd7;--accent:#1f6f54;--accent-hover:#185842;--accent-soft:#edf5f0;--danger:#b42318;--danger-soft:#fef3f2;--warn:#9a5b13;--warn-soft:#fef7ec;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif}}
*{{box-sizing:border-box}}
body{{margin:0;padding:18px;background:var(--bg);color:var(--ink);font:14px/1.55 var(--sans)}}
.wrap{{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
.hdr{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
.hdr h1{{margin:0;font-size:18px;letter-spacing:-.01em}}
.hdr p{{margin:3px 0 0;font-size:12.5px;color:var(--muted)}}
.pill{{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:999px;background:var(--accent-soft);color:var(--accent-hover);font-size:11.5px;font-weight:600;white-space:nowrap}}
.layout{{display:grid;grid-template-columns:240px minmax(0,1fr);gap:14px;align-items:start}}
.layout.single{{grid-template-columns:minmax(0,1fr)}}
@media(max-width:740px){{.layout{{grid-template-columns:1fr}}}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;display:flex;flex-direction:column;gap:14px}}
.panel h2{{margin:0;font-size:15.5px;display:flex;align-items:center;gap:8px}}
.sub{{margin:0;font-size:12.5px;color:var(--muted)}}
.host-list{{display:flex;flex-direction:column;gap:8px;max-height:520px;overflow:auto}}
.host-item{{border:1px solid var(--line);border-radius:9px;padding:10px 11px;background:#fbfcfa;display:flex;flex-direction:column;gap:6px}}
.host-item.active{{border-color:var(--accent);background:var(--accent-soft)}}
.host-item-top{{display:flex;justify-content:space-between;align-items:center;gap:6px}}
.host-item-top strong{{font-size:13px;overflow-wrap:anywhere;word-break:normal;line-height:1.35}}
.host-meta{{font:11.5px/1.35 var(--mono);color:var(--muted);overflow-wrap:anywhere}}
.tag{{display:inline-block;padding:2px 6px;border-radius:999px;font-size:10.5px;font-weight:600;background:#eef2ee;color:var(--muted);white-space:nowrap;flex:none}}
.tag.managed{{background:var(--accent-soft);color:var(--accent-hover)}}
.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
@media(max-width:540px){{.grid2{{grid-template-columns:1fr}}}}
label.field{{display:flex;flex-direction:column;gap:5px;font-size:13px;font-weight:600;color:var(--ink)}}
label.field small{{font-weight:400;font-size:11.5px;color:var(--muted)}}
input,select,textarea{{width:100%;padding:9px 11px;border:1px solid #c7d2ca;border-radius:8px;font:14px/1.4 var(--sans);background:#fff;color:var(--ink)}}
input:focus,select:focus,textarea:focus{{outline:2px solid rgba(31,111,84,.25);border-color:var(--accent)}}
textarea.mono{{font:12px/1.45 var(--mono);background:#f6f8f6;border:1px solid var(--line);border-radius:8px;padding:9px 10px;resize:none;word-break:break-all}}
.check-toggle{{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--ink);cursor:pointer;user-select:none}}
.check-toggle input{{width:auto;margin:0;accent-color:var(--accent)}}
.jump-box{{background:#f8fbf9;border:1px solid #d4e3da;border-radius:9px;padding:12px;display:flex;flex-direction:column;gap:8px}}
.row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
button{{border:1px solid transparent;border-radius:8px;padding:9px 15px;font:600 13.5px/1.2 var(--sans);cursor:pointer;transition:background .15s,border-color .15s}}
button:disabled{{opacity:.55;cursor:not-allowed}}
.btn-primary{{background:var(--accent);color:#fff}}
.btn-primary:hover:not(:disabled){{background:var(--accent-hover)}}
.btn-sec{{background:#eef2ee;color:var(--ink);border-color:#d4ddd5}}
.btn-sec:hover:not(:disabled){{background:#e3eae4}}
.btn-danger{{background:var(--danger-soft);color:var(--danger);border-color:#f3c4c0}}
.btn-sm{{padding:5px 9px;font-size:12px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;align-items:center}}
.chip-btn{{background:#fff;border:1px solid #cbd7cf;color:var(--ink);font:500 12px/1.2 var(--mono);padding:4px 8px;border-radius:6px}}
.chip-btn:hover{{border-color:var(--accent);background:var(--accent-soft)}}
.notice{{border-radius:9px;padding:11px 13px;font-size:13px;border:1px solid var(--line);background:#f8faf8}}
.notice.ok{{background:var(--accent-soft);border-color:#b6d7c7;color:#144c38}}
.notice.err{{background:var(--danger-soft);border-color:#f4c5c1;color:var(--danger)}}
.notice.warn{{background:var(--warn-soft);border-color:#f0d4a8;color:var(--warn)}}
.step-list{{display:flex;flex-direction:column;gap:7px;margin:0;padding:0;list-style:none}}
.step-row{{display:flex;gap:10px;align-items:flex-start;padding:9px 11px;border-radius:8px;border:1px solid var(--line);background:#fbfcfa}}
.step-row.ok{{border-color:#b8d9c8;background:#f4faf6}}
.step-row.fail{{border-color:#f3c4c0;background:#fff6f5}}
.step-icon{{font-weight:700;flex:none;width:18px;text-align:center}}
.assist-card{{border:1px solid #c5dbd0;background:#f7fbf9;border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:10px}}
.assist-card h3{{margin:0;font-size:14px;color:var(--ink)}}
.fp-item{{font:12px/1.4 var(--mono);padding:8px 10px;border-radius:7px;background:#fff;border:1px solid var(--line);word-break:break-all}}
details.adv{{border-top:1px dashed var(--line);padding-top:10px}}
details.adv summary{{cursor:pointer;font-size:12.5px;color:var(--muted);user-select:none}}
details.adv[open] summary{{margin-bottom:10px;color:var(--ink);font-weight:600}}
.footer-bar{{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;padding-top:4px;border-top:1px solid #ebefea}}
.hidden{{display:none !important}}
</style>
</head>
<body>
<div class="wrap">
  <header class="hdr">
    <div>
      <h1>🦉 连接你的 SSH 服务器</h1>
      <p>只需填写服务器地址和用户名；密钥、跳板机、指纹信任和连通检测全自动引导，绝不覆盖你原有的 SSH 配置。</p>
    </div>
    <span class="pill">🔒 仅本机 127.0.0.1 · 密码绝不落盘</span>
  </header>

  <div id="layout" class="layout">
    <aside id="sidebar-panel" class="panel">
      <div class="row" style="justify-content:space-between">
        <h2>已有服务器</h2>
        <button id="btn-new-host" class="btn-sec btn-sm" type="button">+ 新增</button>
      </div>
      <div id="host-list" class="host-list"></div>
    </aside>

    <main class="panel">
      <h2 id="form-title">填写服务器信息</h2>
      <form id="host-form" autocomplete="off">
        <div class="grid2">
          <label class="field">服务器地址（IP 或域名）
            <input id="f-hostname" required maxlength="253" placeholder="例如：10.0.1.25 或 alice@sim.lab.edu">
            <small>支持直接粘贴 <code>用户名@地址</code>，会自动拆开填好。</small>
          </label>
          <label class="field">登录用户名
            <input id="f-user" required maxlength="64" placeholder="例如：alice">
            <small>你在该服务器上的 Linux 账号名。</small>
          </label>
        </div>

        <div style="margin-top:12px">
          <label class="check-toggle">
            <input id="f-use-jump" type="checkbox">
            <span>需要经过跳板机中转（ProxyJump）</span>
          </label>
        </div>

        <div id="jump-box" class="jump-box hidden" style="margin-top:10px">
          <label class="field">跳板机地址或已有别名
            <input id="f-jump" maxlength="320" placeholder="例如：bastion 或 user@jump.lab.edu（多跳用逗号分隔）">
            <small>若还没有配跳板机，可先填跳板机的地址保存一次，再新建目标机并选择它。</small>
          </label>
          <div id="jump-chips" class="chips"></div>
        </div>

        <details id="adv-details" class="adv" style="margin-top:12px">
          <summary>更多选项（自定义别名 / 端口 22 / 切换密钥）</summary>
          <div class="grid2">
            <label class="field">服务器别名（默认自动生成）
              <input id="f-alias" maxlength="96" placeholder="留空则按地址自动命名">
            </label>
            <label class="field">SSH 端口
              <input id="f-port" type="number" min="1" max="65535" value="22">
            </label>
            <label class="field" style="grid-column:1 / -1">免密私钥（默认自动管理 Ed25519 密钥）
              <select id="f-key"></select>
            </label>
          </div>
        </details>

        <div class="row" style="margin-top:14px">
          <button id="btn-save-test" class="btn-primary" type="submit">保存并测试连接</button>
          <button id="btn-save-only" class="btn-sec" type="button">仅保存</button>
          <span id="busy-text" class="sub"></span>
        </div>
      </form>

      <div id="global-msg" class="notice hidden" role="status"></div>

      <!-- Progressive diagnostic timeline (shown after clicking Save & Test) -->
      <section id="diag-section" class="hidden" style="display:flex;flex-direction:column;gap:10px">
        <ul id="diag-steps" class="step-list"></ul>

        <!-- Step-on-demand A: Host key verification (shown ONLY when code === need_host_key) -->
        <div id="assist-hostkey" class="assist-card hidden">
          <h3>🔑 首次连接：确认服务器指纹</h3>
          <p class="sub">请核对下方服务器公钥指纹，确认无误后点击信任即可自动写入 <code>~/.ssh/known_hosts</code> 并继续测试。</p>
          <div id="fp-results" style="display:flex;flex-direction:column;gap:6px"></div>
          <div class="row">
            <button id="btn-trust-retry" class="btn-primary" type="button" disabled>信任此指纹并继续测试</button>
            <button id="btn-rescan-fp" class="btn-sec btn-sm" type="button">重新扫描指纹</button>
          </div>
        </div>

        <!-- Step-on-demand B: Public key deployment (shown ONLY when code === need_key_deploy) -->
        <div id="assist-deploy" class="assist-card hidden">
          <h3>🔐 首次免密：将本机公钥写入目标服务器</h3>
          <p class="sub">只需配置这一次免密，之后永久免密码。输入一次登录密码可自动写入（密码用完即销毁，绝不保存）；若服务器禁止密码登录，直接复制公钥即可。</p>
          <div class="row">
            <input id="f-password" type="password" autocomplete="new-password" placeholder="输入一次远端登录密码（仅本次生效）" style="flex:1 1 220px">
            <button id="btn-deploy-retry" class="btn-primary" type="button">自动配置免密并重测</button>
          </div>
          <div id="deploy-msg" class="notice hidden"></div>
          <details class="adv">
            <summary>服务器禁止密码登录或需动态验证码？展开手动复制公钥</summary>
            <div style="display:flex;flex-direction:column;gap:8px">
              <div id="key-fp" class="sub"></div>
              <textarea id="pub-key-box" class="mono" rows="2" readonly></textarea>
              <div class="row">
                <button id="btn-copy-pub" class="btn-sec btn-sm" type="button">复制公钥</button>
                <button id="btn-copy-cmd" class="btn-sec btn-sm" type="button">复制终端安装命令</button>
                <button id="btn-retest-after-copy" class="btn-primary btn-sm" type="button">我已粘贴，重新测试</button>
              </div>
            </div>
          </details>
        </div>
      </section>

      <div class="footer-bar">
        <span class="sub">配置独立保存在 <code>spiceowl_hosts.conf</code></span>
        <div class="row">
          <button id="btn-close-only" class="btn-sec btn-sm" type="button">关闭</button>
          <button id="btn-done-return" class="btn-primary" type="button">完成并返回网页工作台</button>
        </div>
      </div>
    </main>
  </div>
</div>

<script nonce="{safe_nonce}">
(function() {{
  const BOOT = {boot};
  let appState = BOOT.state || {{}};
  let scannedEntries = [];
  let selectedAlias = '';
  let lastDiagnosis = null;
  let aliasTouched = false;

  const $ = (id) => document.getElementById(id);

  function setMsg(el, text, kind) {{
    if (!el) return;
    if (!text) {{ el.className = 'notice hidden'; el.textContent = ''; return; }}
    el.className = 'notice ' + (kind || '');
    el.textContent = text;
  }}

  function deriveAlias(host) {{
    const clean = (host || '').trim().toLowerCase().replace(/^https?:\\/\\//, '');
    if (!clean) return '';
    const isIp = /^[0-9.]+$/.test(clean);
    const base = isIp ? ('eda-' + clean.replace(/\\./g, '-')) : clean.split('.')[0];
    const safe = base.replace(/[^a-z0-9_-]/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);
    return safe || 'eda-server';
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
    optAuto.value = '__auto__';
    optAuto.textContent = '自动使用 {safe_brand} 托管 Ed25519 密钥（推荐）';
    sel.appendChild(optAuto);
    const optDefault = document.createElement('option');
    optDefault.value = '';
    optDefault.textContent = '使用 OpenSSH 默认密钥（不指定 IdentityFile）';
    sel.appendChild(optDefault);
    for (const k of (appState.keys || [])) {{
      if (!k.has_private) continue;
      const opt = document.createElement('option');
      opt.value = k.config_path;
      opt.textContent = k.name + (k.managed ? '（托管）' : '') + ' · ' + k.key_type;
      sel.appendChild(opt);
    }}
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
    else {{
      const mk = (appState.keys || []).find(k => k.managed && k.has_private);
      sel.value = mk ? mk.config_path : '__auto__';
    }}
    const mk = managedKeyObj();
    $('pub-key-box').value = mk ? mk.public_key : '';
    $('key-fp').textContent = mk ? ('本机公钥：' + mk.name + ' · ' + mk.fingerprint) : '';
  }}

  function renderJumpChips() {{
    const box = $('jump-chips');
    box.replaceChildren();
    const curAlias = ($('f-alias').value.trim() || deriveAlias($('f-hostname').value));
    const candidates = (appState.existing_aliases || []).filter(a => a !== curAlias);
    if (!candidates.length) return;
    const hint = document.createElement('span');
    hint.className = 'sub';
    hint.textContent = '点选已有跳板机：';
    box.appendChild(hint);
    for (const alias of candidates.slice(0, 10)) {{
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

  function hideAssists() {{
    $('assist-hostkey').classList.add('hidden');
    $('assist-deploy').classList.add('hidden');
    setMsg($('deploy-msg'), '', '');
  }}

  function fillFormFromManaged(h) {{
    selectedAlias = h.alias;
    aliasTouched = true;
    $('form-title').textContent = '编辑服务器 · ' + h.alias;
    $('f-alias').value = h.alias || '';
    $('f-hostname').value = h.hostname || '';
    $('f-user').value = h.user || '';
    $('f-port').value = String(h.port || 22);
    const hasJump = Boolean(h.proxy_jump);
    $('f-use-jump').checked = hasJump;
    $('jump-box').classList.toggle('hidden', !hasJump);
    $('f-jump').value = h.proxy_jump || '';
    if (h.identity_file && [...$('f-key').options].some(o => o.value === h.identity_file)) {{
      $('f-key').value = h.identity_file;
    }} else if (!h.identity_file) {{
      $('f-key').value = '';
    }}
    hideAssists();
    $('diag-section').classList.add('hidden');
    setMsg($('global-msg'), '', '');
    renderHosts();
    renderJumpChips();
  }}

  function resetNewForm() {{
    selectedAlias = '';
    aliasTouched = false;
    lastDiagnosis = null;
    $('form-title').textContent = '新增 SSH 服务器';
    $('f-alias').value = '';
    $('f-hostname').value = '';
    $('f-user').value = '';
    $('f-port').value = '22';
    $('f-use-jump').checked = false;
    $('jump-box').classList.add('hidden');
    $('f-jump').value = '';
    hideAssists();
    $('diag-section').classList.add('hidden');
    setMsg($('global-msg'), '', '');
    renderAll();
  }}

  function renderHosts() {{
    const managed = appState.managed || [];
    const unmanaged = appState.unmanaged_aliases || [];
    const hasAny = managed.length > 0 || unmanaged.length > 0;
    $('sidebar-panel').classList.toggle('hidden', !hasAny);
    $('layout').classList.toggle('single', !hasAny);
    if (!hasAny) return;

    const box = $('host-list');
    box.replaceChildren();
    for (const h of managed) {{
      const item = document.createElement('div');
      item.className = 'host-item' + (selectedAlias === h.alias ? ' active' : '');
      const top = document.createElement('div');
      top.className = 'host-item-top';
      const strong = document.createElement('strong');
      strong.textContent = h.alias;
      const badge = document.createElement('span');
      badge.className = 'tag managed';
      badge.textContent = '已配置';
      top.append(strong, badge);
      const meta = document.createElement('div');
      meta.className = 'host-meta';
      meta.textContent = (h.user ? h.user + '@' : '') + h.hostname + (h.port && h.port !== 22 ? ':' + h.port : '') + (h.proxy_jump ? ' via ' + h.proxy_jump : '');
      const actions = document.createElement('div');
      actions.className = 'row';
      const bEdit = document.createElement('button');
      bEdit.type = 'button'; bEdit.className = 'btn-sec btn-sm'; bEdit.textContent = '编辑';
      bEdit.addEventListener('click', () => fillFormFromManaged(h));
      const bDiag = document.createElement('button');
      bDiag.type = 'button'; bDiag.className = 'btn-sec btn-sm'; bDiag.textContent = '测试';
      bDiag.addEventListener('click', () => {{ fillFormFromManaged(h); runDiagnosis(h.alias); }});
      const bDel = document.createElement('button');
      bDel.type = 'button'; bDel.className = 'btn-danger btn-sm'; bDel.textContent = '删';
      bDel.addEventListener('click', async () => {{
        try {{
          await callAction('delete_host', {{alias: h.alias}});
          if (selectedAlias === h.alias) resetNewForm();
          else renderAll();
          setMsg($('global-msg'), '已移除 ' + h.alias + '。', 'ok');
        }} catch (e) {{ setMsg($('global-msg'), e.message, 'err'); }}
      }});
      actions.append(bEdit, bDiag, bDel);
      item.append(top, meta, actions);
      box.appendChild(item);
    }}
    for (const alias of unmanaged) {{
      const item = document.createElement('div');
      item.className = 'host-item' + (selectedAlias === alias ? ' active' : '');
      const top = document.createElement('div');
      top.className = 'host-item-top';
      const strong = document.createElement('strong');
      strong.textContent = alias;
      const badge = document.createElement('span');
      badge.className = 'tag';
      badge.textContent = '原有';
      top.append(strong, badge);
      const actions = document.createElement('div');
      actions.className = 'row';
      const bJump = document.createElement('button');
      bJump.type = 'button'; bJump.className = 'btn-sec btn-sm'; bJump.textContent = '作跳板机';
      bJump.addEventListener('click', () => {{
        $('f-use-jump').checked = true;
        $('jump-box').classList.remove('hidden');
        const cur = $('f-jump').value.trim();
        const parts = cur ? cur.split(',').map(s => s.trim()).filter(Boolean) : [];
        if (!parts.includes(alias)) parts.push(alias);
        $('f-jump').value = parts.join(',');
      }});
      const bDiag = document.createElement('button');
      bDiag.type = 'button'; bDiag.className = 'btn-sec btn-sm'; bDiag.textContent = '测试';
      bDiag.addEventListener('click', () => {{ selectedAlias = alias; renderHosts(); runDiagnosis(alias); }});
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

  async function ensureKeySilently() {{
    const mk = (appState.keys || []).find(k => k.managed && k.has_private);
    if (mk) return mk;
    const res = await callAction('ensure_key', {{}});
    renderKeys();
    return res.key;
  }}

  async function saveCurrentHost() {{
    let rawHost = $('f-hostname').value.trim();
    let user = $('f-user').value.trim();
    let port = Number($('f-port').value || 22);
    // Convenience: if user pasted user@host[:port] into hostname box, split cleanly
    if (rawHost.includes('@')) {{
      const parts = rawHost.split('@');
      if (parts.length === 2 && !user) {{
        user = parts[0].trim();
        $('f-user').value = user;
      }}
      rawHost = parts[parts.length - 1].trim();
      $('f-hostname').value = rawHost;
    }}
    if (rawHost.includes(':')) {{
      const [h, p] = rawHost.split(':', 2);
      if (p && /^[0-9]+$/.test(p)) {{
        rawHost = h.trim();
        port = Number(p);
        $('f-hostname').value = rawHost;
        $('f-port').value = String(port);
      }}
    }}
    const alias = $('f-alias').value.trim() || deriveAlias(rawHost);
    if (!alias) throw new Error('请填写有效的服务器地址。');
    $('f-alias').value = alias;

    let keyChoice = $('f-key').value;
    if (keyChoice === '__auto__') {{
      const mk = await ensureKeySilently();
      keyChoice = mk.config_path;
      $('f-key').value = keyChoice;
    }}
    const proxyJump = $('f-use-jump').checked ? $('f-jump').value.trim() : '';
    const res = await callAction('upsert_host', {{
      alias: alias,
      hostname: rawHost,
      user: user,
      port: port,
      proxy_jump: proxyJump,
      identity_file: keyChoice,
    }});
    selectedAlias = res.host.alias;
    renderAll();
    return res.host;
  }}

  async function scanFingerprintsForCurrent() {{
    scannedEntries = [];
    const box = $('fp-results');
    box.replaceChildren();
    $('btn-trust-retry').disabled = true;
    const loading = document.createElement('div');
    loading.className = 'sub';
    loading.textContent = '正在获取服务器指纹…';
    box.appendChild(loading);
    try {{
      const res = await callAction('scan_host_key', {{
        hostname: $('f-hostname').value.trim(),
        port: Number($('f-port').value || 22),
        jump_alias: $('f-use-jump').checked ? $('f-jump').value.trim() : '',
      }});
      const scan = res.scan || {{}};
      scannedEntries = scan.entries || [];
      box.replaceChildren();
      for (const fp of (scan.fingerprints || [])) {{
        const div = document.createElement('div');
        div.className = 'fp-item';
        div.textContent = fp.key_type + '  ' + fp.fingerprint + '  (' + fp.host_pattern + ')';
        box.appendChild(div);
      }}
      $('btn-trust-retry').disabled = scannedEntries.length === 0;
    }} catch (e) {{
      box.replaceChildren();
      const err = document.createElement('div');
      err.className = 'notice err';
      err.textContent = e.message;
      box.appendChild(err);
    }}
  }}

  async function runDiagnosis(alias) {{
    const target = (alias || selectedAlias || $('f-alias').value || '').trim();
    if (!target) return;
    $('busy-text').textContent = '正在测试连接 ' + target + '…';
    $('btn-save-test').disabled = true;
    hideAssists();
    $('diag-section').classList.remove('hidden');
    const ul = $('diag-steps');
    ul.replaceChildren();
    try {{
      const res = await callAction('diagnose', {{alias: target}});
      const d = res.diagnosis || {{}};
      lastDiagnosis = d;
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
        setMsg($('global-msg'), '✅ 连接成功！' + target + ' 已全部就绪，点击底部「完成并返回网页工作台」即可直接使用。', 'ok');
      }} else if (d.code === 'need_host_key') {{
        setMsg($('global-msg'), '首次连接该服务器，确认下方指纹后即可继续。', 'warn');
        $('assist-hostkey').classList.remove('hidden');
        if ($('f-hostname').value.trim()) await scanFingerprintsForCurrent();
      }} else if (d.code === 'need_key_deploy') {{
        setMsg($('global-msg'), '网络已通，只需把免密公钥写入远端服务器即可（下面输入一次密码自动完成，或手动复制）。', 'warn');
        await ensureKeySilently();
        $('assist-deploy').classList.remove('hidden');
      }} else {{
        setMsg($('global-msg'), '连接未通过，请查看上方红色步骤提示（如需内网中转，请勾选「需要经过跳板机中转」）。', 'err');
      }}
    }} catch (e) {{
      setMsg($('global-msg'), e.message, 'err');
    }} finally {{
      $('busy-text').textContent = '';
      $('btn-save-test').disabled = false;
    }}
  }}

  async function copyText(text, okLabel) {{
    if (!text) return;
    try {{ await navigator.clipboard.writeText(text); setMsg($('deploy-msg'), okLabel, 'ok'); }}
    catch {{
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      setMsg($('deploy-msg'), okLabel, 'ok');
    }}
  }}

  $('f-hostname').addEventListener('input', () => {{
    const val = $('f-hostname').value.trim();
    if (val.includes('@') && !$('f-user').value.trim()) {{
      const [u, rest] = val.split('@', 2);
      if (u && rest) {{ $('f-user').value = u.trim(); $('f-hostname').value = rest.trim(); }}
    }}
    if (!aliasTouched) $('f-alias').value = deriveAlias($('f-hostname').value);
    renderJumpChips();
  }});
  $('f-alias').addEventListener('input', () => {{ aliasTouched = Boolean($('f-alias').value.trim()); renderJumpChips(); }});
  $('f-use-jump').addEventListener('change', () => {{
    $('jump-box').classList.toggle('hidden', !$('f-use-jump').checked);
    renderJumpChips();
  }});
  $('btn-new-host').addEventListener('click', resetNewForm);

  $('host-form').addEventListener('submit', async (ev) => {{
    ev.preventDefault();
    $('btn-save-test').disabled = true;
    $('busy-text').textContent = '正在准备密钥并保存配置…';
    setMsg($('global-msg'), '', '');
    try {{
      const host = await saveCurrentHost();
      await runDiagnosis(host.alias);
    }} catch (e) {{
      setMsg($('global-msg'), e.message, 'err');
      $('busy-text').textContent = '';
      $('btn-save-test').disabled = false;
    }}
  }});

  $('btn-save-only').addEventListener('click', async () => {{
    try {{
      const host = await saveCurrentHost();
      setMsg($('global-msg'), '已保存 ' + host.alias + '（如需接着配置目标机，点左上角「+ 新增」并勾选跳板机即可）。', 'ok');
    }} catch (e) {{ setMsg($('global-msg'), e.message, 'err'); }}
  }});

  $('btn-rescan-fp').addEventListener('click', scanFingerprintsForCurrent);
  $('btn-trust-retry').addEventListener('click', async () => {{
    if (!scannedEntries.length) return;
    $('btn-trust-retry').disabled = true;
    try {{
      await callAction('trust_host_keys', {{entries: scannedEntries}});
      await runDiagnosis(selectedAlias || $('f-alias').value.trim());
    }} catch (e) {{
      setMsg($('global-msg'), e.message, 'err');
      $('btn-trust-retry').disabled = false;
    }}
  }});

  $('btn-deploy-retry').addEventListener('click', async () => {{
    const pwEl = $('f-password');
    const pw = pwEl.value;
    if (!pw) {{ setMsg($('deploy-msg'), '请输入一次性登录密码。', 'warn'); return; }}
    $('btn-deploy-retry').disabled = true;
    setMsg($('deploy-msg'), '正在通过临时免落盘通道写入公钥…', '');
    try {{
      const mk = await ensureKeySilently();
      const res = await callAction('deploy_key', {{
        hostname: $('f-hostname').value.trim(),
        port: Number($('f-port').value || 22),
        username: $('f-user').value.trim(),
        proxy_jump: $('f-use-jump').checked ? $('f-jump').value.trim() : '',
        public_key: mk.public_key,
        password: pw,
        accept_new_host_key: true,
      }});
      pwEl.value = '';
      const info = res.deploy || {{}};
      if (res.ok) {{
        setMsg($('deploy-msg'), info.message || '公钥写入成功，正在自动重测…', 'ok');
        await runDiagnosis(selectedAlias || $('f-alias').value.trim());
      }} else {{
        setMsg($('deploy-msg'), info.message || '自动写入未成功，可展开下方手动复制公钥。', 'err');
      }}
    }} catch (e) {{
      pwEl.value = '';
      setMsg($('deploy-msg'), e.message, 'err');
    }} finally {{
      $('btn-deploy-retry').disabled = false;
    }}
  }});

  $('btn-copy-pub').addEventListener('click', () => copyText($('pub-key-box').value.trim(), '已复制公钥。'));
  $('btn-copy-cmd').addEventListener('click', () => {{
    const pub = $('pub-key-box').value.trim();
    if (!pub) return;
    const cmd = "mkdir -p ~/.ssh && chmod 700 ~/.ssh && printf '%s\\n' '" + pub.replace(/'/g, "'\\''") + "' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys";
    copyText(cmd, '已复制终端安装命令，在远端粘贴执行后点右侧「重新测试」。');
  }});
  $('btn-retest-after-copy').addEventListener('click', () => runDiagnosis(selectedAlias || $('f-alias').value.trim()));

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
          diagnosis_ok: Boolean(lastDiagnosis && lastDiagnosis.ok),
          remote: (lastDiagnosis && lastDiagnosis.remote) || null,
        }}, BOOT.origin);
      }} catch {{}}
    }}
    window.close();
  }});

  renderAll();
  if (BOOT.initialAlias) {{
    const found = (appState.managed || []).find(h => h.alias === BOOT.initialAlias);
    if (found) {{ fillFormFromManaged(found); runDiagnosis(found.alias); }}
    else if ((appState.existing_aliases || []).includes(BOOT.initialAlias)) {{
      selectedAlias = BOOT.initialAlias;
      renderHosts();
      runDiagnosis(BOOT.initialAlias);
    }}
  }}
}})();
</script>
</body>
</html>"""
    return html.encode("utf-8")
