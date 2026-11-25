"""Cross-platform onboarding wizard, vivid demo bootstrap, and first-simulation guide.

Helps brand-new standalone users (macOS, Linux, Windows) either:
1. Launch a 1-click vivid end-to-end demo workspace (overview runs + 500-cycle
   PSF Binary waveform with hidden glitch + 27-corner PVT matrix + 40-sample
   Monte Carlo + 10-case Agent E2E scorecard) in under 30 seconds without an EDA
   server; OR
2. Connect their own remote Linux EDA server (auto-discovering read-only
   OpenSSH aliases from ``~/.ssh/config`` without touching keys/secrets),
   verify connectivity & Spectre/ngspice versions, freeze a zero-PDK RC smoke
   testbench (or custom netlist), and run their first real simulation end-to-end.
"""

from __future__ import annotations

import importlib.util
import platform
import re
import shutil
import sys
from pathlib import Path

from spiceowl._platform import IS_WINDOWS, resolve_ssh_executable
from spiceowl.agent_eval import compare_baselines
from spiceowl.batch import DirectBatchManager
from spiceowl.direct import DirectExecutionManager
from spiceowl.domain.common import DomainError, utcnow
from spiceowl.hosts import HostRegistry


WIZARD_SCHEMA = "spiceowl.onboarding-wizard.v1"
MAX_SSH_CONFIG_BYTES = 256 * 1024

_SAFE_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z", re.ASCII)

FIRST_SIM_TEMPLATES: dict[str, dict] = {
    "spectre_rc_smoke": {
        "id": "spectre_rc_smoke",
        "simulator": "spectre",
        "output_format": "psfbin",
        "title": "Spectre 零 PDK RC 瞬态烟雾验证（推荐首次联调）",
        "description": "不依赖任何外部 PDK 模型库，仅使用理想电压源、电阻与电容跑 100ns 阶跃响应，1–3 秒即可验证远端 Spectre、日志诊断、五维报告与 PSF Binary 波形链路。",
        "netlist": (
            "// SpiceOwl First-Simulation Smoke Testbench (Zero-PDK Spectre RC Step)\n"
            "simulator lang=spectre\n"
            "global 0\n"
            "parameters vdd=1.2 r_val=1k c_val=10p\n"
            "V_in (in 0) vsource type=pulse val0=0 val1=vdd delay=5n rise=1n fall=1n width=40n period=100n\n"
            "R1 (in out) resistor r=r_val\n"
            "C1 (out 0) capacitor c=c_val\n"
            "save in out\n"
            "tran tran stop=100n errpreset=moderate\n"
        ),
    },
    "spectre_rlc_ringing": {
        "id": "spectre_rlc_ringing",
        "simulator": "spectre",
        "output_format": "psfbin",
        "title": "Spectre 二阶 RLC 欠阻尼阶跃响应（验证过冲 / 建立时间 / 双游标）",
        "description": "零 PDK 理想二阶 RLC 网络，产生清晰可测的过冲与阻尼震荡，适合测试 SpiceOwl 的无损建立时间、过冲百分比与双游标卡尺。",
        "netlist": (
            "// SpiceOwl Zero-PDK 2nd-Order RLC Underdamped Step Testbench\n"
            "simulator lang=spectre\n"
            "global 0\n"
            "parameters vdd=1.2 r_val=120 l_val=200n c_val=5p\n"
            "V_in (in 0) vsource type=pulse val0=0 val1=vdd delay=2n rise=0.2n fall=0.2n width=80n period=120n\n"
            "R1 (in n1) resistor r=r_val\n"
            "L1 (n1 out) inductor l=l_val\n"
            "C1 (out 0) capacitor c=c_val\n"
            "save in out\n"
            "tran tran stop=60n maxstep=50p errpreset=conservative\n"
        ),
    },
    "spectre_opamp_ac": {
        "id": "spectre_opamp_ac",
        "simulator": "spectre",
        "output_format": "psfascii",
        "title": "Spectre 理想两极点运放 AC 频响（验证 DC 增益 / UGBW / 相位裕度）",
        "description": "零 PDK 宏观行为级两极点运放，无需工艺库即可直接验证 AC 小信号解析与直流增益、-3dB 带宽、单位增益带宽（UGBW）及相位裕度提取。",
        "netlist": (
            "// SpiceOwl Zero-PDK Two-Pole Macro Op-Amp AC Loop Testbench\n"
            "simulator lang=spectre\n"
            "global 0\n"
            "parameters gm1=1m ro1=100k c1=10p gm2=1m ro2=10k c2=0.5p\n"
            "V_ac (in 0) vsource type=dc dc=0 mag=1 phase=0\n"
            "G1 (n1 0 in 0) vccs gm=gm1\n"
            "R1 (n1 0) resistor r=ro1\n"
            "C1 (n1 0) capacitor c=c1\n"
            "G2 (out 0 n1 0) vccs gm=gm2\n"
            "R2 (out 0) resistor r=ro2\n"
            "C2 (out 0) capacitor c=c2\n"
            "save out\n"
            "ac ac start=10 stop=1G dec=20\n"
        ),
    },
    "spectre_mos1_inverter": {
        "id": "spectre_mos1_inverter",
        "simulator": "spectre",
        "output_format": "psfbin",
        "title": "Spectre 零 PDK Level-1 CMOS 反相器瞬态（验证上升/下降时间与 PVT 扫角）",
        "description": "内置理想 Level-1 NMOS/PMOS 模型，适合快速验证反相器翻转延迟、10%–90% 上升下降时间、压摆率以及 PVT 电压/温度批量矩阵。",
        "netlist": (
            "// SpiceOwl Zero-PDK Level-1 CMOS Inverter Transient Testbench\n"
            "simulator lang=spectre\n"
            "global 0\n"
            "parameters vdd=1.2 cload=20f\n"
            "model nch mos1 type=n vto=0.35 kp=180u\n"
            "model pch mos1 type=p vto=-0.35 kp=90u\n"
            "V_dd (vdd_n 0) vsource dc=vdd\n"
            "V_in (in 0) vsource type=pulse val0=0 val1=vdd delay=2n rise=0.2n fall=0.2n width=8n period=20n\n"
            "MN (out in 0 0) nch w=1u l=0.18u\n"
            "MP (out in vdd_n vdd_n) pch w=2u l=0.18u\n"
            "CL (out 0) capacitor c=cload\n"
            "save in out\n"
            "tran tran stop=40n errpreset=moderate\n"
        ),
    },
    "ngspice_rc_smoke": {
        "id": "ngspice_rc_smoke",
        "simulator": "ngspice",
        "output_format": "psfascii",
        "title": "ngspice 零 PDK RC 瞬态烟雾验证（开源仿真器）",
        "description": "不依赖商业许可证与 PDK，适用于已安装开源 ngspice 的 Linux 服务器快速跑通首次直连仿真。",
        "netlist": (
            "* SpiceOwl First-Simulation Smoke Testbench (Zero-PDK ngspice RC Step)\n"
            "V1 in 0 PULSE(0 1.2 5n 1n 1n 40n 100n)\n"
            "R1 in out 1k\n"
            "C1 out 0 10p\n"
            ".tran 1n 100n\n"
            ".control\n"
            "run\n"
            "quit\n"
            ".endc\n"
            ".end\n"
        ),
    },
}

LEGACY_COMPATIBILITY_NOTICES: tuple[dict, ...] = (
    {
        "surface": "spiceowl mcp",
        "status": "retired_cli_deprecated_adapter",
        "replacement": "Use the standalone Codex Agent channel (`#agents`), embedded CircuitGit tool bridge, or `spiceowl read`.",
        "doc": "docs/legacy-compatibility.md",
    },
    {
        "surface": "legacy_qa_history",
        "status": "read_only_archive",
        "replacement": "Use `#agents` standalone Codex conversations or `#direct` structured diagnostics & reports.",
        "doc": "docs/legacy-compatibility.md",
    },
)


def parse_ssh_config_aliases(ssh_config_path: str | Path | None = None) -> list[dict]:
    """Safely parse explicit non-wildcard OpenSSH ``Host`` aliases (read-only, zero secrets).

    Reads at most 256 KiB and extracts only ``alias``, ``hostname``, ``user``,
    ``port``, and ``proxy_jump`` metadata so new users can select an existing
    EDA server alias in one click. Never reads private keys or credentials.
    """
    path = Path(ssh_config_path) if ssh_config_path is not None else (Path.home() / ".ssh" / "config")
    try:
        if not path.is_file() or path.is_symlink():
            return []
        if path.stat().st_size > MAX_SSH_CONFIG_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    aliases: list[dict] = []
    seen: set[str] = set()
    current: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip inline comments conservatively only when preceded by whitespace
        if " #" in line:
            line = line.split(" #", 1)[0].strip()
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        key, val = parts[0].lower(), parts[1].strip()
        if key == "host":
            current = []
            for token in val.split():
                if any(ch in token for ch in "*?!"):
                    continue
                if _SAFE_ALIAS.fullmatch(token) and token not in seen:
                    entry = {
                        "alias": token,
                        "hostname": None,
                        "user": None,
                        "port": None,
                        "proxy_jump": None,
                    }
                    seen.add(token)
                    aliases.append(entry)
                    current.append(entry)
                    if len(aliases) >= 64:
                        return aliases
        elif current:
            if key == "hostname" and not any(ch in val for ch in " \t"):
                for entry in current:
                    entry["hostname"] = entry["hostname"] or val[:253]
            elif key == "user" and not any(ch in val for ch in " \t"):
                for entry in current:
                    entry["user"] = entry["user"] or val[:64]
            elif key == "port" and val.isdigit():
                for entry in current:
                    entry["port"] = entry["port"] or int(val)
            elif key == "proxyjump":
                for entry in current:
                    entry["proxy_jump"] = entry["proxy_jump"] or val[:253]
    return aliases


def diagnose_local_environment(
    data_dir: str | Path,
    *,
    hosts: HostRegistry | None = None,
    direct_manager: DirectExecutionManager | None = None,
    batch_manager: DirectBatchManager | None = None,
    ssh_config_path: str | Path | None = None,
) -> dict:
    """Inspect local OS, OpenSSH, SSH config aliases, optional tools, and workspace readiness."""
    root = Path(data_dir).expanduser().resolve()
    hosts_reg = hosts or HostRegistry(root)
    dm = direct_manager or DirectExecutionManager(root, hosts=hosts_reg)
    bm = batch_manager or DirectBatchManager(root, dm)

    raw_ssh = resolve_ssh_executable()
    ssh_exe = raw_ssh if (Path(raw_ssh).is_file() or shutil.which(raw_ssh)) else None
    codex_exe = shutil.which("codex")
    has_numpy = importlib.util.find_spec("numpy") is not None
    ssh_aliases = parse_ssh_config_aliases(ssh_config_path)

    host_rows = hosts_reg.list()
    verified_hosts = [h for h in host_rows if (h.get("last_probe") or {}).get("identity", {}).get("verified")]
    run_rows = dm.list()
    batch_rows = bm.list()

    checks = [
        {
            "id": "os_python",
            "label": "操作系统与 Python 运行时",
            "status": "ok",
            "detail": f"{platform.system()} ({platform.machine()}) · Python {sys.version.split()[0]}（原生跨平台模式）",
        },
        {
            "id": "openssh",
            "label": "OpenSSH 客户端 (ssh)",
            "status": "ok" if ssh_exe else "missing",
            "detail": f"已检测到 {ssh_exe}" if ssh_exe else (
                "未找到 ssh.exe；请在 Windows「设置 → 系统 → 可选功能」中启用 OpenSSH 客户端。"
                if IS_WINDOWS else "未在 PATH 中找到 ssh；请安装 OpenSSH 客户端。"
            ),
        },
        {
            "id": "ssh_config",
            "label": "本机 ~/.ssh/config 主机别名",
            "status": "ok" if ssh_aliases else "warning",
            "detail": (
                f"已自动发现 {len(ssh_aliases)} 个可用 SSH 别名（可直接下拉选用，不读取私钥）"
                if ssh_aliases else
                "未发现 ~/.ssh/config 别名；可手动填写别名或按下方模板配置免密 SSH 登录。"
            ),
        },
        {
            "id": "adctools",
            "label": "数值分析扩展 (adctools / numpy)",
            "status": "ok" if has_numpy else "warning",
            "detail": "已就绪，可执行 FFT / SNDR / 线性度与无损波形测量。" if has_numpy else "可选未安装（运行 pip install '.[adctools]' 即可启用）。",
        },
        {
            "id": "codex",
            "label": "本地 Codex Agent CLI（可选）",
            "status": "ok" if codex_exe else "warning",
            "detail": f"已检测到 {codex_exe}" if codex_exe else "未检测到 codex（不影响直连仿真、波形查看、PVT/MC 批量实验与演示模式）。",
        },
    ]

    ssh_config_hint = (
        "# 在本机 ~/.ssh/config（Windows 为 C:\\Users\\<你的用户名>\\.ssh\\config）添加：\n"
        "Host eda-sim\n"
        "    HostName 10.0.0.10\n"
        "    User your_eda_user\n"
        "    IdentityFile ~/.ssh/id_ed25519\n"
        "    ServerAliveInterval 30\n"
    )

    return {
        "schema": WIZARD_SCHEMA,
        "checked_at": utcnow(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split()[0],
            "is_windows": IS_WINDOWS,
            "data_dir": str(root),
            "ssh_executable": ssh_exe,
            "codex_executable": codex_exe,
            "has_adctools": has_numpy,
        },
        "checks": checks,
        "ssh_aliases": ssh_aliases,
        "ssh_config_example": ssh_config_hint,
        "workspace": {
            "host_count": len(host_rows),
            "verified_host_count": len(verified_hosts),
            "direct_run_count": len(run_rows),
            "batch_count": len(batch_rows),
            "has_demo_waveform": any(r.get("run_id") == "demo-psfbin-500c" for r in run_rows),
            "has_demo_pvt": any(b.get("batch_id") == "demo-pvt-27" for b in batch_rows),
            "has_demo_mc": any(b.get("batch_id") == "demo-mc-40" for b in batch_rows),
            "hosts": host_rows,
            "first_sim_runs": [r for r in run_rows if str(r.get("run_id", "")).startswith("first-sim-")][:10],
        },
        "templates": list(FIRST_SIM_TEMPLATES.values()),
        "legacy_notices": list(LEGACY_COMPATIBILITY_NOTICES),
    }


def bootstrap_vivid_demo(
    controller,
    direct_manager: DirectExecutionManager,
    batch_manager: DirectBatchManager,
) -> dict:
    """Seed the complete vivid offline demo workspace across every signature SpiceOwl capability."""
    overview = controller.seed_demo()
    wf_run = direct_manager.create_demo_waveform_run(run_id="demo-psfbin-500c", cycles=500, glitch_cycle=317)
    wf_index = direct_manager.waveform_index(wf_run["run_id"], allow_remote=False)
    pvt = batch_manager.create_demo_batch(kind="pvt", batch_id="demo-pvt-27")
    mc = batch_manager.create_demo_batch(kind="monte_carlo", batch_id="demo-mc-40")
    eval_comp = compare_baselines(repeats=1)

    stations = [
        {
            "step": 1,
            "id": "overview_runs",
            "title": "五维状态总览与断连诊断",
            "badge": "4 个合成场景",
            "summary": "区分执行完成、测量有效、性能未达标与连接未知，点击任意仿真查看日志证据与诊断建议。",
            "route": "#overview",
            "action_label": "前往总览 →",
        },
        {
            "step": 2,
            "id": "binary_waveform",
            "title": "500 周期 PSF Binary 大波形与毛刺钻取",
            "badge": "20,000 点 · #317 隐藏毛刺",
            "summary": "体验 Min-Max 包络预览零丢毛刺、鼠标拖拽放大自动切 100% 原始无损点，以及 Cycle Slicer 一键抓出 #317 异常周期与眼图叠画。",
            "route": "#direct",
            "target_run_id": wf_run["run_id"],
            "action_label": "打开 500 周期波形 →",
        },
        {
            "step": 3,
            "id": "pvt_batch",
            "title": "27-Corner PVT 批量矩阵（含 OOM 与慢角超标）",
            "badge": f"保守良率 {pvt['summary']['yield']['conservative_yield_pct']}%",
            "summary": "查看 3×3×3 彩色工艺角矩阵，区分有效测量的慢角未达标（ss/1.08V/125°C）与确认 OOM 执行失败（ff/1.32V/-40°C），点击任意卡片直达波形。",
            "route": "#direct",
            "target_batch_id": pvt["batch"]["batch_id"],
            "action_label": "查看 PVT 矩阵 →",
        },
        {
            "step": 4,
            "id": "mc_batch",
            "title": "40 样本 Monte Carlo 失配分布与种子保留",
            "badge": f"保守良率 {mc['summary']['yield']['conservative_yield_pct']}% · 种子零丢弃",
            "summary": "查看带 LSL/USL 规格线的 SVG 失配直方图、Cpk 与显式保留的执行失败种子 #2029，支持导出 Markdown 与 CSV。",
            "route": "#direct",
            "target_batch_id": mc["batch"]["batch_id"],
            "action_label": "查看 MC 直方图 →",
        },
        {
            "step": 5,
            "id": "agent_eval",
            "title": "Agent 10 项安全闭环评测（确定性 vs 弱模型）",
            "badge": f"{eval_comp['deterministic']['overall_pass_rate_pct']}% vs {eval_comp['weak_naive']['overall_pass_rate_pct']}%",
            "summary": "直观对比确定性 Next-Action 编排器（0 越权 / 0 重复 / 0 假通过）与弱模型基线的差距，并体验中断回合安全恢复机制。",
            "route": "#agents",
            "action_label": "前往 Agent 评测 →",
        },
    ]

    return {
        "schema": "spiceowl.vivid-demo.v1",
        "bootstrapped_at": utcnow(),
        "overview_runs": len(overview.get("runs") or []),
        "waveform_demo_run_id": wf_run["run_id"],
        "waveform_datasets": len(wf_index.get("datasets") or []),
        "pvt_batch_id": pvt["batch"]["batch_id"],
        "mc_batch_id": mc["batch"]["batch_id"],
        "eval_deterministic_pass_pct": eval_comp["deterministic"]["overall_pass_rate_pct"],
        "eval_weak_pass_pct": eval_comp["weak_naive"]["overall_pass_rate_pct"],
        "stations": stations,
    }


def quick_register_eda_host(
    hosts: HostRegistry,
    *,
    host_id: str,
    label: str,
    ssh_target: str,
    expected_host: str,
    expected_user: str,
    simulator: str = "spectre",
    simulator_executable: str | None = None,
    ssh_hostname: str | None = None,
) -> tuple[dict, bool]:
    """Register one EDA server profile locally (zero implicit network call)."""
    if simulator not in ("spectre", "ngspice"):
        raise DomainError("simulator must be 'spectre' or 'ngspice'.", "invalid_wizard_input")
    default_exe = "/opt/cadence/tools/bin/spectre" if simulator == "spectre" else "/usr/bin/ngspice"
    exe = (simulator_executable or "").strip() or default_exe
    tools = [{"kind": simulator, "executable": exe}]
    profile = {
        "schema": "spiceowl.host.v2" if ssh_hostname else "spiceowl.host.v1",
        "host_id": host_id.strip(),
        "label": (label or host_id).strip(),
        "ssh_target": ssh_target.strip(),
        "expected_host": expected_host.strip(),
        "expected_user": expected_user.strip(),
        "tools": tools,
    }
    if ssh_hostname and ssh_hostname.strip():
        profile["ssh_hostname"] = ssh_hostname.strip()
    return hosts.register(profile)


def prepare_first_simulation(
    direct_manager: DirectExecutionManager,
    *,
    host_id: str,
    simulator: str = "spectre",
    backend: str = "ssh_direct",
    template_id: str | None = None,
    netlist_text: str | None = None,
    run_key: str | None = None,
    queue: str | None = None,
    partition: str | None = None,
    remote_python: str = "/usr/bin/python3",
    remote_root: str = ".spiceowl-runs",
    output_format: str | None = None,
) -> dict:
    """Freeze a beginner-friendly first simulation targeting the user's EDA server."""
    if simulator not in ("spectre", "ngspice"):
        raise DomainError("simulator must be 'spectre' or 'ngspice'.", "invalid_wizard_input")
    tpl_key = template_id or ("spectre_rc_smoke" if simulator == "spectre" else "ngspice_rc_smoke")
    tpl = FIRST_SIM_TEMPLATES.get(tpl_key)
    effective_netlist = (netlist_text or "").strip() or (tpl["netlist"] if tpl else "")
    if not effective_netlist:
        raise DomainError("Select a built-in template or supply non-empty netlist text.", "invalid_wizard_input")
    effective_format = output_format or (tpl["output_format"] if tpl else ("psfbin" if simulator == "spectre" else "psfascii"))

    safe_host = re.sub(r"[^A-Za-z0-9_-]", "-", host_id.strip())[:36].strip("-") or "eda"
    if not run_key:
        existing = {r.get("run_id") for r in direct_manager.list()}
        idx = 1
        while f"first-sim-{safe_host}-{idx:02d}" in existing and idx < 999:
            idx += 1
        run_key = f"first-sim-{safe_host}-{idx:02d}"

    scheduler = None
    if backend == "lsf":
        if not queue or not queue.strip():
            raise DomainError("LSF backend requires a queue name.", "invalid_wizard_input")
        scheduler = {"queue": queue.strip(), "exclusive": False}
    elif backend == "slurm":
        if not partition or not partition.strip():
            raise DomainError("Slurm backend requires a partition name.", "invalid_wizard_input")
        scheduler = {"partition": partition.strip(), "exclusive": False}

    run = direct_manager.prepare(
        run_key=run_key,
        netlist_text=effective_netlist,
        host_id=host_id.strip(),
        backend=backend,
        simulator=simulator,
        pin_version=False,
        limits={"cpu_count": 1, "memory_bytes": 2 * 2**30, "wall_seconds": 600, "license_queue_seconds": 120},
        scheduler=scheduler,
        output_format=effective_format,
        remote_python=remote_python.strip() or "/usr/bin/python3",
        remote_root=remote_root.strip() or ".spiceowl-runs",
    )
    return {
        "schema": "spiceowl.first-sim-plan.v1",
        "template_id": tpl_key if not (netlist_text or "").strip() else "custom",
        "run": run,
        "next_steps": [
            {"step": 1, "action": "stage", "label": "上传输入到远端隔离目录 (Stage)"},
            {"step": 2, "action": "launch", "label": "启动你的第一个远端仿真 (Launch)"},
            {"step": 3, "action": "status", "label": "查询状态或开启后台盯守 (Status / Watch)"},
            {"step": 4, "action": "collect", "label": "收集结果、五维报告与波形 (Collect)"},
        ],
    }
