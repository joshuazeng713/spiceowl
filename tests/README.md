# Tests and validation plan

Run `PYTHONPATH=src python3 -m unittest discover -s tests` with Python 3.11+. Tests cover fake connections, strict evidence/proposals, acceptance, synthetic replay, SQLite service recovery, loopback HTTP, bounded SSH peer failures, private profile import and read-only storage. Install `.[mcp]` to include actual stdio SDK handshake/tool-call tests. Temporary local data is isolated per test. No credentials, provider inference requests, real SSH hosts or real simulations are used.

Browser verification covers connection creation/probing, the host page and explicit probe controls, manual run replay, diagnostic events, persisted-session reload, escaped content and narrow layouts. The [local guide](../docs/local-console.md) records the implemented scope.

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
