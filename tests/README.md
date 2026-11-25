# Tests and validation plan

Run the one-command cross-platform test workflow on **macOS, Linux, or Windows**:

```bash
# macOS / Linux
python3 scripts/run_full_tests.py

# Windows (PowerShell)
py -3 scripts\run_full_tests.py
```

Or run the Python unit test suite directly with `PYTHONPATH=src python3 -m unittest discover -s tests` (Python 3.11+). Tests cover cross-platform primitives and the AST POSIX guardrail (`test_cross_platform.py`), fake connections, strict evidence/proposals, acceptance, synthetic replay, SQLite service recovery, loopback HTTP, bounded SSH peer failures, private profile import, Direct SSH/LSF/Slurm execution, M4 bounded recovery & searchable reports, `RuntimeMonitor`, Codex bridge, standalone/embedded Agent workflows, PDK discovery/LUT/scope, Verilog-A, experiment recipes/measurement, Spectre diagnostics/reports, and read-only storage. Install `.[mcp]` and `.[adctools]` to include stdio MCP protocol and numerical ADCToolbox tests. Temporary local data is isolated per test. No credentials, provider inference requests, real SSH hosts or real simulations are used. See [cross-platform support and maintenance](../docs/cross-platform.md) for the GitHub Actions macOS/Windows/Linux CI matrix and compatibility rules.

Browser verification covers connection creation/probing, the host page and explicit probe controls, manual run replay, diagnostic events, persisted-session reload, escaped content and narrow layouts. The [local guide](../docs/local-console.md) records the implemented scope.

The optional `node tests/ui-host-onboarding.mjs` checks production host-page logic in a Node VM with local DOM/fetch stubs. It covers selected batch limits, invalid/future observation timestamps, request payloads, and rejecting delayed replies after selection changes. It needs no external packages or network access. These checks do not replace real-browser layout, accessibility and interaction verification. Node is a development test dependency only; the shipped console does not require it.

## Additional public tests for later milestones

- Domain model validation, including explicit units and immutable attempt identity.
- Duplicate/out-of-order observations and log rotation.
- Lost connection while the original job remains running.
- Controller restart after dispatch but before the submission receipt is persisted.
- Confirmed OOM, time limit, license wait, and incomplete artifact retrieval.
- Missing signals, mismatched inputs, and invalid measurement windows.
- A valid measurement that fails a circuit specification.
- Bounded recovery, ambiguous outcomes, and unsupported checkpoint requests.
- Model unavailability and malicious instructions embedded in log text.

## Fixtures

[fixtures/synthetic/disconnected-running.json](fixtures/synthetic/disconnected-running.json) is authored synthetic data. It is not a captured Spectre log or a test result. A future mock executor should consume this scenario deterministically.

Public fixtures must be original, synthetic, or explicitly redistributable. `.log` fixtures under `tests/fixtures/synthetic/` are allowed by the repository ignore rules. Native waveforms, private logs, PDKs, and model credentials do not belong here.

## Real-tool checks

Add opt-in integration tests only after selecting the first backend. Run them in a configured private environment with user-provided tools and inputs. They must be skipped by default and must not be required for a public pull request to run its normal tests.

## IC Workbench read integration

`test_ic_workbench.py`, `test_ic_cli.py`, and `test_ic_integration_verifier.py` cover bounded gateway reads, native identity preservation, failed-read caching, submission-key reconciliation, and CLI behavior. Fixtures are synthetic. MCP/readonly tests also cover the three cache-only IC tools, including independent stdio calls and unchanged database bytes. Live checks are opt-in and must use saved-record endpoints; a single-run status endpoint can perform SSH and is not part of a passive check.
