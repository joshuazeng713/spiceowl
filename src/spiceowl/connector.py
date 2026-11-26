"""Headless local SpiceOwl Connector & custom URI scheme (spiceowl://) registration.

Allows https://spiceowl-dev.github.io/spiceowl/ to act as the single formal
product interface while the local Python process stays silently in the
background:
- One-time user-level installation (zero admin privileges required):
  - Windows: HKCU\\Software\\Classes\\spiceowl + optional HKCU Run autostart.
  - macOS: ~/Applications/SpiceOwl Connector.app + optional ~/Library/LaunchAgents.
  - Linux: ~/.local/share/applications/spiceowl-connector.desktop.
- Clicking "Connect local connector" on github.io probes http://127.0.0.1:8765/health;
  if not already running, triggers spiceowl://start which launches the headless
  connector idempotently without opening a second local browser interface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.error
from urllib.parse import urlsplit
import urllib.request

from spiceowl._platform import IS_MACOS, IS_WINDOWS, default_state_dir, popen_isolation_kwargs, resolve_codex_executable, resolve_ssh_executable
from spiceowl.domain.common import DomainError
import signal


DEFAULT_CONNECTOR_PORT = 8765
SCHEME_NAME = "spiceowl"
OFFICIAL_WEB_URL = "https://spiceowl-dev.github.io/spiceowl/"


def connector_health(port: int = DEFAULT_CONNECTOR_PORT, *, timeout: float = 1.2) -> dict:
    """Probe http://127.0.0.1:<port>/health locally without modifying anything."""
    url = f"http://127.0.0.1:{int(port)}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if int(getattr(resp, "status", 0)) == 200:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("ok") is True and data.get("product") == "spiceowl":
                    return {"running": True, "port": int(port), "health": data}
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return {"running": False, "port": int(port), "health": None}


def _needs_source_pythonpath() -> Path | None:
    """Return src root ONLY when running from a source checkout outside site-packages."""
    src_root = Path(__file__).resolve().parents[1]
    prefix = Path(sys.prefix).resolve()
    try:
        src_root.relative_to(prefix)
        return None  # Installed inside the Python environment's own site-packages (permanent!)
    except ValueError:
        pass
    if (src_root / "spiceowl" / "__init__.py").is_file():
        return src_root
    return None


def connector_readiness_summary() -> dict:
    """Inspect local OpenSSH aliases and optional Codex CLI presence without network I/O."""
    from spiceowl.onboarding.ssh_discovery import discover_ssh_hosts

    raw_ssh = resolve_ssh_executable()
    ssh_bin = raw_ssh if (Path(raw_ssh).is_file() or shutil.which(raw_ssh)) else None
    codex_bin = resolve_codex_executable()
    aliases = []
    try:
        cfg = Path.home() / ".ssh" / "config"
        if cfg.is_file():
            res = discover_ssh_hosts(cfg)
            if isinstance(res, dict) and isinstance(res.get("hosts"), list):
                aliases = res["hosts"]
    except Exception:
        aliases = []
    return {
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "python_executable": str(Path(sys.executable)),
        "installed_in_venv": _needs_source_pythonpath() is None,
        "ssh_available": bool(ssh_bin),
        "ssh_alias_count": len(aliases),
        "codex_available": bool(codex_bin),
        "codex_executable": codex_bin,
    }


def _connector_pid_file(state_root: Path, port: int) -> Path:
    return state_root / (f"connector-{int(port)}.pid" if int(port) != DEFAULT_CONNECTOR_PORT else "connector.pid")


def _terminate_pid(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if IS_WINDOWS:
        res = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        return res.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def stop_connector_background(
    *,
    port: int = DEFAULT_CONNECTOR_PORT,
    data_dir: str | Path | None = None,
) -> dict:
    """Cleanly stop any previously running SpiceOwl headless Connector on this port."""
    state_root = Path(data_dir).expanduser().resolve() if data_dir is not None else default_state_dir("spiceowl")
    pid_file = _connector_pid_file(state_root, port)
    stopped_pids: list[int] = []
    candidate_pids: list[int] = []
    if pid_file.is_file():
        try:
            recorded = int(pid_file.read_text(encoding="utf-8").strip())
            if recorded > 0:
                candidate_pids.append(recorded)
        except (OSError, ValueError):
            pass
    if not candidate_pids and not IS_WINDOWS and connector_health(port, timeout=0.6)["running"]:
        # Fallback for older SpiceOwl Connectors started before connector.pid existed:
        # strictly match our own headless serve command on this exact port.
        try:
            ps_out = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            port_token = f"--port {int(port)}"
            for line in ps_out.stdout.splitlines():
                parts = line.strip().split(None, 1)
                if len(parts) != 2 or not parts[0].isdigit():
                    continue
                cmdline = parts[1]
                if "spiceowl" in cmdline and " serve " in f" {cmdline} " and "--headless" in cmdline and port_token in cmdline:
                    candidate_pids.append(int(parts[0]))
        except (OSError, subprocess.SubprocessError):
            pass
    for pid in dict.fromkeys(candidate_pids):
        if _terminate_pid(pid):
            stopped_pids.append(pid)
    if stopped_pids:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not connector_health(port, timeout=0.35)["running"]:
                break
            time.sleep(0.15)
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
    return {"stopped": bool(stopped_pids), "pids": stopped_pids, "port": int(port)}


def _python_for_background() -> str:
    exe = Path(sys.executable)
    if IS_WINDOWS:
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return str(exe)


def start_connector_background(
    *,
    port: int = DEFAULT_CONNECTOR_PORT,
    data_dir: str | Path | None = None,
    enable_direct_exec: bool = True,
    restart: bool = False,
) -> dict:
    """Ensure the headless SpiceOwl Connector is running on 127.0.0.1:<port>."""
    restarted = False
    st = connector_health(port)
    if st["running"]:
        if not restart:
            return {"started": False, "already_running": True, "ready": True, "port": int(port), "url": f"http://127.0.0.1:{int(port)}"}
        stop_res = stop_connector_background(port=port, data_dir=data_dir)
        restarted = stop_res["stopped"]
        if connector_health(port, timeout=0.5)["running"]:
            return {"started": False, "already_running": True, "restarted": False, "ready": True, "port": int(port), "url": f"http://127.0.0.1:{int(port)}"}

    state_root = Path(data_dir).expanduser().resolve() if data_dir is not None else default_state_dir("spiceowl")
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = state_root / "connector.log"
    pid_file = _connector_pid_file(state_root, port)

    cmd = [
        _python_for_background(),
        "-m",
        "spiceowl",
        "--data-dir",
        str(state_root),
        "serve",
        "--port",
        str(int(port)),
        "--headless",
    ]
    if enable_direct_exec:
        cmd.append("--enable-direct-exec")

    env = dict(os.environ)
    src_root = _needs_source_pythonpath()
    if src_root is not None:
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(src_root) + (os.pathsep + existing_pp if existing_pp else "")

    kwargs = popen_isolation_kwargs()
    if IS_WINDOWS:
        # Prevent flashing console window on Windows when launched via spiceowl://
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if no_window:
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | int(no_window)

    with log_path.open("ab") as log_file:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            env=env,
            **kwargs,
        )
    try:
        pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    except OSError:
        pass
    ready = False
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        if connector_health(port, timeout=0.5)["running"]:
            ready = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    return {
        "started": True,
        "already_running": False,
        "restarted": restarted,
        "ready": ready,
        "pid": proc.pid,
        "port": int(port),
        "url": f"http://127.0.0.1:{int(port)}",
        "log_path": str(log_path),
    }


def parse_connector_uri(uri: str) -> dict:
    """Validate and parse a spiceowl:// action URI."""
    if not isinstance(uri, str) or not uri.strip():
        raise DomainError("URI must be a non-empty string.", "invalid_connector_uri")
    parsed = urlsplit(uri.strip())
    if parsed.scheme.lower() != SCHEME_NAME:
        raise DomainError(f"Unsupported URI scheme '{parsed.scheme}'; expected 'spiceowl://'.", "invalid_connector_uri")
    action = (parsed.netloc or parsed.path.lstrip("/")).lower() or "start"
    if action not in ("start", "connect", "status"):
        raise DomainError(f"Unsupported spiceowl:// action '{action}'.", "invalid_connector_uri")
    return {"scheme": SCHEME_NAME, "action": action}


def handle_connector_uri(
    uri: str,
    *,
    port: int = DEFAULT_CONNECTOR_PORT,
    data_dir: str | Path | None = None,
) -> dict:
    """Handle a spiceowl:// URI dispatched by the OS protocol handler."""
    parsed = parse_connector_uri(uri)
    if parsed["action"] == "status":
        return connector_health(port)
    return start_connector_background(port=port, data_dir=data_dir, enable_direct_exec=True)


def _install_macos(
    *,
    port: int,
    data_dir: Path,
    autostart: bool,
    home_dir: Path,
) -> dict:
    app_dir = home_dir / "Applications" / "SpiceOwl Connector.app"
    contents = app_dir / "Contents"
    macos_dir = contents / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    python_bin = str(Path(sys.executable))
    src_root = _needs_source_pythonpath()
    macos_path = ":".join([
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(home_dir / ".local" / "bin"),
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ])
    launcher = macos_dir / "spiceowl-connector"
    script_lines = [
        "#!/bin/sh",
        f'export PATH="{macos_path}${{PATH:+:$PATH}}"',
    ]
    if src_root is not None:
        script_lines.append(f'export PYTHONPATH="{src_root}${{PYTHONPATH:+:$PYTHONPATH}}"')
    script_lines.extend([
        'case "${1:-}" in',
        '  spiceowl://*) URI="$1" ;;',
        '  *) URI="spiceowl://start" ;;',
        'esac',
        f'exec "{python_bin}" -m spiceowl --data-dir "{data_dir}" connector handle-uri "$URI" --port {int(port)}',
        "",
    ])
    launcher.write_text("\n".join(script_lines), encoding="utf-8")
    launcher.chmod(0o755)

    info_plist = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": "spiceowl-connector",
        "CFBundleIdentifier": "io.github.spiceowl.connector",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "SpiceOwl Connector",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "LSBackgroundOnly": True,
        "LSUIElement": True,
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "SpiceOwl Local Connector",
                "CFBundleURLSchemes": [SCHEME_NAME],
            }
        ],
    }
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump(info_plist, fh)

    # Register app bundle with macOS LaunchServices (best-effort; skipped if tool missing)
    lsreg = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister")
    registered_ls = False
    if lsreg.is_file():
        res = subprocess.run([str(lsreg), "-f", str(app_dir)], capture_output=True, check=False, timeout=15)
        registered_ls = res.returncode == 0

    launch_agent_path = None
    if autostart:
        agents_dir = home_dir / "Library" / "LaunchAgents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        launch_agent_path = agents_dir / "io.github.spiceowl.connector.plist"
        agent_plist = {
            "Label": "io.github.spiceowl.connector",
            "ProgramArguments": [
                python_bin,
                "-m",
                "spiceowl",
                "--data-dir",
                str(data_dir),
                "serve",
                "--port",
                str(int(port)),
                "--headless",
                "--enable-direct-exec",
            ],
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": str(data_dir / "connector.log"),
            "StandardErrorPath": str(data_dir / "connector.log"),
        }
        agent_env = {"PATH": macos_path}
        if src_root is not None:
            agent_env["PYTHONPATH"] = str(src_root)
        agent_plist["EnvironmentVariables"] = agent_env
        with launch_agent_path.open("wb") as fh:
            plistlib.dump(agent_plist, fh)

    return {
        "platform": "macos",
        "scheme": f"{SCHEME_NAME}://",
        "app_bundle": str(app_dir),
        "launch_services_registered": registered_ls,
        "autostart_enabled": bool(autostart),
        "autostart_path": str(launch_agent_path) if launch_agent_path else None,
        "port": int(port),
        "web_url": OFFICIAL_WEB_URL,
    }


def _install_windows(
    *,
    port: int,
    data_dir: Path,
    autostart: bool,
    dry_run: bool = False,
) -> dict:
    py = _python_for_background()
    command = f'"{py}" -m spiceowl --data-dir "{data_dir}" connector handle-uri "%1" --port {int(port)}'
    autostart_cmd = f'"{py}" -m spiceowl --data-dir "{data_dir}" serve --port {int(port)} --headless --enable-direct-exec'
    reg_entries = {
        r"HKCU\Software\Classes\spiceowl": "URL:SpiceOwl Connector Protocol",
        r"HKCU\Software\Classes\spiceowl\URL Protocol": "",
        r"HKCU\Software\Classes\spiceowl\shell\open\command": command,
    }
    if not dry_run:
        import winreg  # type: ignore[import-not-found]
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\spiceowl") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:SpiceOwl Connector Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\spiceowl\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        if autostart:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.SetValueEx(key, "SpiceOwlConnector", 0, winreg.REG_SZ, autostart_cmd)

    return {
        "platform": "windows",
        "scheme": f"{SCHEME_NAME}://",
        "registry_keys": reg_entries,
        "autostart_enabled": bool(autostart),
        "autostart_command": autostart_cmd if autostart else None,
        "port": int(port),
        "web_url": OFFICIAL_WEB_URL,
        "dry_run": bool(dry_run),
    }


def _install_linux(
    *,
    port: int,
    data_dir: Path,
    autostart: bool,
    home_dir: Path,
) -> dict:
    apps_dir = home_dir / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desktop_path = apps_dir / "spiceowl-connector.desktop"
    py = str(Path(sys.executable))
    desktop_text = "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=SpiceOwl Connector",
        "Comment=Headless local SSH and Agent connector for SpiceOwl Web",
        f'Exec="{py}" -m spiceowl --data-dir "{data_dir}" connector handle-uri %u --port {int(port)}',
        "Terminal=false",
        "NoDisplay=true",
        f"MimeType=x-scheme-handler/{SCHEME_NAME};",
        "",
    ])
    desktop_path.write_text(desktop_text, encoding="utf-8")

    xdg_registered = False
    if shutil.which("xdg-mime"):
        res = subprocess.run(
            ["xdg-mime", "default", "spiceowl-connector.desktop", f"x-scheme-handler/{SCHEME_NAME}"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        xdg_registered = res.returncode == 0

    autostart_path = None
    if autostart:
        auto_dir = home_dir / ".config" / "autostart"
        auto_dir.mkdir(parents=True, exist_ok=True)
        autostart_path = auto_dir / "spiceowl-connector.desktop"
        autostart_path.write_text(
            "\n".join([
                "[Desktop Entry]",
                "Type=Application",
                "Name=SpiceOwl Connector",
                f'Exec="{py}" -m spiceowl --data-dir "{data_dir}" serve --port {int(port)} --headless --enable-direct-exec',
                "Terminal=false",
                "X-GNOME-Autostart-enabled=true",
                "",
            ]),
            encoding="utf-8",
        )

    return {
        "platform": "linux",
        "scheme": f"{SCHEME_NAME}://",
        "desktop_file": str(desktop_path),
        "xdg_mime_registered": xdg_registered,
        "autostart_enabled": bool(autostart),
        "autostart_path": str(autostart_path) if autostart_path else None,
        "port": int(port),
        "web_url": OFFICIAL_WEB_URL,
    }


def install_connector(
    *,
    port: int = DEFAULT_CONNECTOR_PORT,
    data_dir: str | Path | None = None,
    autostart: bool = True,
    home_dir: str | Path | None = None,
    platform_override: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Register spiceowl:// protocol handler and optional user-level background autostart."""
    if not isinstance(port, int) or not (1024 <= port <= 65535):
        raise DomainError("Connector port must be an integer between 1024 and 65535.", "invalid_port")
    state_root = Path(data_dir).expanduser().resolve() if data_dir is not None else default_state_dir("spiceowl")
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    home = Path(home_dir).expanduser().resolve() if home_dir is not None else Path.home()
    plat = platform_override or ("windows" if IS_WINDOWS else "macos" if IS_MACOS else "linux")
    if plat == "windows":
        res = _install_windows(port=port, data_dir=state_root, autostart=autostart, dry_run=dry_run)
    elif plat == "macos":
        res = _install_macos(port=port, data_dir=state_root, autostart=autostart, home_dir=home)
    else:
        res = _install_linux(port=port, data_dir=state_root, autostart=autostart, home_dir=home)
    res["readiness"] = connector_readiness_summary()
    return res


def uninstall_connector(
    *,
    home_dir: str | Path | None = None,
    platform_override: str | None = None,
) -> dict:
    """Remove user-level spiceowl:// registration and autostart files."""
    home = Path(home_dir).expanduser().resolve() if home_dir is not None else Path.home()
    plat = platform_override or ("windows" if IS_WINDOWS else "macos" if IS_MACOS else "linux")
    removed: list[str] = []
    if plat == "macos":
        app_dir = home / "Applications" / "SpiceOwl Connector.app"
        if app_dir.exists():
            shutil.rmtree(app_dir)
            removed.append(str(app_dir))
        plist = home / "Library" / "LaunchAgents" / "io.github.spiceowl.connector.plist"
        if plist.exists():
            plist.unlink()
            removed.append(str(plist))
    elif plat == "linux":
        for path in (
            home / ".local" / "share" / "applications" / "spiceowl-connector.desktop",
            home / ".config" / "autostart" / "spiceowl-connector.desktop",
        ):
            if path.exists():
                path.unlink()
                removed.append(str(path))
    elif plat == "windows" and IS_WINDOWS:
        import winreg  # type: ignore[import-not-found]
        for subkey in (
            r"Software\Classes\spiceowl\shell\open\command",
            r"Software\Classes\spiceowl\shell\open",
            r"Software\Classes\spiceowl\shell",
            r"Software\Classes\spiceowl",
        ):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
                removed.append(f"HKCU\\{subkey}")
            except OSError:
                pass
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "SpiceOwlConnector")
                removed.append(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run\SpiceOwlConnector")
        except OSError:
            pass
    return {"platform": plat, "removed": removed}
