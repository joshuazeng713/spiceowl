# Cross-platform support and engineering contract

SpiceOwl runs its **local control plane** (CLI, local web console, SQLite state,
SSH client wrapper, direct execution manager, bounded recovery, Codex bridge,
and developer setup) natively on **macOS, Linux, and Windows** (Python 3.11+).
Remote EDA execution targets (Spectre, ngspice, LSF, Slurm, remote `/bin/sh`
probes) remain POSIX/Linux servers accessed over OpenSSH.

Users do not need to run shell scripts manually in the terminal for normal
operations: clicking actions in the local web console invokes bounded backend
helpers automatically, and the backend detects the local OS (`os.name == "nt"`
vs POSIX) to choose the native mechanism.

## Architecture: centralized `_platform.py`

All OS-dependent local operations are centralized in
[`src/spiceowl/_platform.py`](../src/spiceowl/_platform.py):

| Concern | macOS / Linux (POSIX) | Windows (native) | Helper |
|---|---|---|---|
| Advisory file locks | `fcntl.flock` | `msvcrt.locking` at offset 0 (mapped to `BlockingIOError` when busy) | `lock_file`, `unlock_file`, `file_lock` |
| Subprocess isolation | `start_new_session=True` | `creationflags=CREATE_NEW_PROCESS_GROUP` | `popen_isolation_kwargs` |
| Process-tree stop | `os.killpg(..., SIGTERM/SIGKILL)` | `taskkill /F /T /PID` with `process.kill()` fallback | `terminate_process_tree` |
| Bounded pipe I/O | `selectors.DefaultSelector` | Bounded daemon reader/writer threads + `queue.Queue` | `communicate_bounded` |
| OpenSSH client path | `/usr/bin/ssh` (or `SPICEOWL_SSH_EXECUTABLE`) | `shutil.which("ssh")` / `%SystemRoot%\System32\OpenSSH\ssh.exe` | `resolve_ssh_executable`, `ssh_client_options` |
| Symlink-safe open | `O_NOFOLLOW \| O_NONBLOCK` | Pre-open `Path.is_symlink()` check + `O_BINARY` | `open_readonly_nofollow`, `open_lock_nofollow` |
| Owner-private check | `st_uid` + `0o077`/`0o022` mode bits | Regular file/dir check (avoids synthetic Windows mode bits) | `is_owner_private_file`, `is_owner_private_dir`, `chmod_private` |
| Directory fsync | `os.open(dir, O_RDONLY)` + `os.fsync` | No-op (Windows forbids `os.open` on directories) | `fsync_dir` |
| Default state dir | `$XDG_STATE_HOME/spiceowl` or `~/.local/state/spiceowl` | `$XDG_STATE_HOME/spiceowl` or `%LOCALAPPDATA%\spiceowl` | `default_state_dir` |
| Virtualenv binaries | `.venv/bin/<tool>` | `.venv\Scripts\<tool>.exe` | `venv_executable` |

## Rules for ongoing development (enforced by tests)

To ensure every future feature stays natively compatible across macOS, Linux,
and Windows, [`tests/test_cross_platform.py`](../tests/test_cross_platform.py)
runs a static AST/source check on every module under `src/spiceowl/` (excluding
`_platform.py` itself and remote Linux script literals):

1. **Never `import fcntl` directly** in application modules — use
   `spiceowl._platform.file_lock` / `lock_file` / `unlock_file`.
2. **Never hardcode `/usr/bin/ssh`** — use `resolve_ssh_executable()` and
   `ssh_client_options()`.
3. **Never call `os.killpg` or `start_new_session=True` directly** for local
   subprocesses — use `popen_isolation_kwargs()` and `terminate_process_tree()`.
4. **Never pass subprocess pipes to `selectors` / `select.select` or call
   `os.set_blocking` without a Windows path** — use `communicate_bounded()` (or
   handle `IS_WINDOWS` explicitly for streaming transports).
5. **Never reference bare `os.O_NOFOLLOW`, `os.O_NONBLOCK`, `os.getuid()`, or
   `st_mode & 0o077` directly** in local code — use the `_platform.py` helpers.
6. **Never `os.open()` a directory for `os.fsync()`** — use `fsync_dir()`.
7. **Always use `pathlib.Path`** for local filesystem paths and `PurePosixPath`
   only for remote Linux paths.

## Native Windows quick start

Requirements: Python 3.11+ (3.12/3.13 for the full developer profile) and
Windows built-in OpenSSH client (`ssh.exe`, enabled by default on Windows 10/11).

```powershell
git clone https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
py -3 scripts\setup_dev.py
.venv\Scripts\spiceowl.exe serve --demo
```

Or minimal core install without optional extras:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\spiceowl.exe serve --demo
```

Open the printed `http://127.0.0.1:<port>` URL in Chrome or Edge. WSL2 remains
an optional alternative if you prefer running inside a Linux shell.

## Automated test workflow (local & GitHub Actions)

Both new developers and end users can verify their local installation with a
single command that works identically on **macOS, Linux, and Windows**:

```bash
# macOS / Linux (full verification across all modules, UI, extras, links & pages)
.venv/bin/python scripts/run_full_tests.py

# Fast smoke + core + cross-platform guard (~15s)
.venv/bin/python scripts/run_full_tests.py --quick
```

```powershell
# Windows PowerShell (full verification)
.venv\Scripts\python.exe scripts\run_full_tests.py

# Fast smoke + core + cross-platform guard
.venv\Scripts\python.exe scripts\run_full_tests.py --quick
```

What `scripts/run_full_tests.py` covers automatically (no real SSH hosts,
simulators, or credentials required):

1. **Cross-platform primitives & AST static POSIX guardrail** (`tests/test_cross_platform.py`)
2. **Full Python unit test suite** (`unittest discover -s tests`: CLI, HTTP console,
   SQLite store, SSH onboarding & pool placement, Direct SSH/LSF/Slurm execution,
   M4 Bounded Recovery & searchable reports, `RuntimeMonitor`, Codex bridge,
   Standalone/Embedded Agent, CircuitGit/IC Workbench/Live client, PDK
   discovery/LUT/scope, Verilog-A, Experiment recipes/measurement, Spectre
   diagnostics/reports/datasets)
3. **CLI & live loopback web console smoke** (`/` + `/api/state` + `/api/direct/runs` + `/api/direct/recovery-reports`)
4. **Optional extras** (`[adctools]` numerical/ADCToolbox 0.9.3 tests and `[mcp]` stdio protocol tests; use `--require-extras` to enforce)
5. **Offline frontend JS & static demo pages** (`ui-host-onboarding.mjs`, `ui-agent-channel.mjs`, `workflow-page.mjs`, `build_pages.py`, `pages-demo.mjs`; use `--require-node` to enforce)
6. **Documentation relative link check** (`scripts/check_links.py`)
7. **Structured JSON report** written to `.spiceowl/test-report.json`

### GitHub Actions matrix

[`.github/workflows/tests.yml`](../.github/workflows/tests.yml) runs on every
`push`, `pull_request`, and manual `workflow_dispatch` across:

- **`test`**: `ubuntu-latest` (Python 3.11/3.12/3.13), `macos-latest` (3.12), `windows-latest` (3.12)
- **`ui`**: `ubuntu-latest`, `macos-latest`, `windows-latest` (Node 24 + Pages build/verify)
- **`mcp`**: `ubuntu-latest`, `macos-latest`, `windows-latest`
- **`adctools`**: `ubuntu-latest`, `macos-latest`, `windows-latest`
- **`developer-setup`**: `ubuntu-latest`, `macos-latest`, `windows-latest` (bootstraps `.venv` via `scripts/setup_dev.py`, runs `scripts/run_full_tests.py --quick --require-extras --require-node`, uploads `test-report-<os>` artifact)

### Ongoing compatibility maintenance checklist

When adding any new feature or PR:

- Route all local OS primitives (locks, subprocesses, SSH client path, private
  files, directory fsync, state/venv paths) through [`src/spiceowl/_platform.py`](../src/spiceowl/_platform.py).
- Keep `tests/test_cross_platform.py` green — never bypass the AST guardrail.
- Guard POSIX-only test assertions (such as `st_mode & 0o777 == 0o600`,
  `os.killpg`, `/bin/sh -n`, or remote Linux worker `exec()` tests) with
  `if os.name != "nt":` or `@unittest.skipUnless(Path("/bin/sh").is_file(), ...)`,
  while keeping all local functional tests running on Windows.
- Run `scripts/run_full_tests.py` locally and confirm all macOS, Windows, and
  Linux checks pass in GitHub Actions before merging.

---

## 中文说明

SpiceOwl 的**本地控制端**（CLI、网页控制台、SQLite 状态库、OpenSSH 客户端封装、
直连仿真管理、有界自动恢复、Codex 桥接与开发者安装脚本）现已原生支持
**macOS、Linux 与 Windows**，并在启动时自动检测当前操作系统选择对应实现。
远端仿真机仍为标准 Linux EDA 服务器。

### 本地一键全量测试工作流（新开发者 / 用户自检）

```bash
# macOS / Linux
.venv/bin/python scripts/run_full_tests.py
```

```powershell
# Windows (PowerShell)
.venv\Scripts\python.exe scripts\run_full_tests.py
```

脚本会自动覆盖跨平台 AST 守卫、全量 Python 单元测试（1000+ 用例）、本地回环
Web 控制台烟雾检查、`[adctools]` / `[mcp]` 可选扩展、前端离线 JS / 静态页构建检查与
文档断链检查，并在 `.spiceowl/test-report.json` 输出结构化报告。

同时，[`.github/workflows/tests.yml`](../.github/workflows/tests.yml) 在每次提交、PR
以及手动触发（`workflow_dispatch`）时自动在 **Ubuntu、macOS、Windows** 三端运行完整
测试矩阵并归档每端测试报告，持续守卫后续功能的跨平台兼容性。
