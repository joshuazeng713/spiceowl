# Developer setup

Clone the organization repository, then use one setup command:

```bash
git clone https://github.com/spiceowl-dev/spiceowl.git
cd spiceowl
python3 scripts/setup_dev.py
```

Start with `.venv/bin/spiceowl serve --demo` and follow the printed localhost URL.
`python3 scripts/setup_dev.py --serve-demo` combines installation and startup.
It starts no SSH connection, AI session, commercial simulator or real simulation.

The launcher needs Python 3.9+ on macOS/Linux. The complete developer profile
uses Python 3.12/3.13. It reuses an available supported interpreter; otherwise it
bootstraps pinned `uv==0.9.5` in a temporary environment and downloads Python
3.12.12 into the ignored `.dev-tools/python` directory. Global Python packages
and shell configuration are unchanged. On Windows use WSL. Application core
support for Python 3.11+ is unchanged.

## Pinned dependencies and self-check

Setup creates `.venv`, installs exact build-tool pins from
[`dev-tools.txt`](../requirements/dev-tools.txt) and the Python dependency
closure from [`dev-lock.txt`](../requirements/dev-lock.txt), including MCP 1.30.0,
ADCToolbox 0.9.3, NumPy, SciPy, Matplotlib and MCP's cryptography dependencies.
It installs the checkout as editable `.[mcp,adctools]` without resolving a second
dependency set or using an unpinned isolated build environment.

Checks cover `pip check`, every pinned version, editable source identity,
browser assets, both CLI entry points, MCP import and actual local/upstream sine
fits over a small original synthetic signal. The receipt at
`.venv/spiceowl-dev-environment.json` records Python/platform, package versions
and lock digest. It contains no server credentials.

The lock freezes versions, not every platform wheel or BLAS implementation.
Keep the receipt with scientific reports; version equality does not promise
bit-for-bit results across machines. PDKs, Cadence, licenses, host configuration
and agent subscriptions are provisioned separately.

## Repeat or inspect

```bash
python3 scripts/setup_dev.py --check-only
python3 scripts/setup_dev.py --python /absolute/path/to/python3.12 --no-python-download
python3 scripts/setup_dev.py --venv .dev-tools/alternate-venv
```

Re-running setup installs the same pins. Existing non-environment directories,
symlink targets and unsupported Python environments are preserved and reported;
the script never deletes them. Use another `--venv` if the current one belongs
to another workflow. Download or installation failures stop with nonzero status;
rerun after resolving access. No automatic mirror or dependency substitution is
used. `--check-only` performs no installation or interpreter download.

## Tests and link checking

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python scripts/check_links.py
.venv/bin/python scripts/check_links.py --external
.venv/bin/python scripts/build_pages.py
```

The link checker inventories non-ignored tracked/new files and checks relative
documents, assets and local document fragments. HTTP results are separate:
404/410 mean broken; access restrictions, rate limits and network/server errors
remain unverified. Local/example/template URLs and API configuration endpoints
are skipped, not claimed valid. The default report is `.spiceowl/link-check.json`.
HTTP success verifies reachability, not the accuracy of referenced information.

Python tests and static-demo builds need no browser. Optional `.mjs` browser QA
needs Node.js, Playwright and Chrome, with `PLAYWRIGHT_MODULE` / `CHROME_PATH`
overrides. Those tools are outside the Python dependency lock.

## 中文说明

下载新组织的仓库后，执行 `python3 scripts/setup_dev.py` 即可创建环境、安装固定版本的
全部 Python 开发依赖和可选功能，并完成自检。没有合适的 Python 时，会在项目内下载
固定版本；不会修改系统 Python。随后执行 `.venv/bin/spiceowl serve --demo` 打开本地
演示。`--serve-demo` 可以把安装和启动合并成一条命令。

已有环境不会被删除。只检查配置用 `--check-only`；另建环境用 `--venv`。
依赖下载、环境自检和真实仿真是不同步骤，这个入口不会自动连接仿真服务器。
