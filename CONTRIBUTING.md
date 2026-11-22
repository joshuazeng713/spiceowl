# Contributing to SpiceOwl

SpiceOwl has an offline alpha CLI/web console and synthetic backend tests. Read the [local console guide](docs/local-console.md), [roadmap](docs/roadmap.md) and [architecture](docs/architecture.md) before starting implementation.

## Useful first contributions

- A reproducible, sanitized simulation failure with a clear expected outcome.
- Review of the experiment/run contract from an analog-design perspective.
- A small connection-management slice with fake agent/API backends, following the [parallel design workstreams](docs/design/README.md).
- A small slice of the mock executor and read-only monitor.
- An input or measurement validator with synthetic/public fixtures.
- Documentation corrections and improvements to the English/Chinese introductions.

Discuss large integrations in an issue before introducing a new orchestration framework, simulator family, or frontend application. Small fixes can go directly to a pull request.

## Sharing a failure case

Describe:

1. The intended experiment and the failure being investigated.
2. Tool/version and execution-profile information that can be disclosed.
3. Whether the execution outcome is confirmed or only the connection was lost.
4. A minimal sanitized or synthetic log/observation sequence.
5. The expected classification, acceptable actions, and actions that must not occur.

Label synthetic and redacted evidence explicitly. Preserve an unambiguous explanation of missing information. Never describe synthetic results as measurements from a real simulator.

Do not submit proprietary PDK/model files, private circuit sources, license files, credentials, private host details, or raw production waveforms. Use original public examples or material you are authorized to redistribute. See [SECURITY.md](SECURITY.md).

## Implementation expectations

- Keep changes scoped to one milestone and its acceptance criteria.
- Add a dependency only when a concrete implementation uses it; record its version and relevant license information.
- Preserve the separation between experiment intent, execution, validation, and specification outcome.
- Make submission and recovery uncertainty explicit. Never blindly repeat an external action after a lost response.
- Give new adapters capability declarations and deterministic fake-backed tests before adding optional real-tool tests.
- When runtime code arrives, add meaningful tests for restart/reconnection, duplicate events, failure injection, and scientific acceptance.
- Keep standard public CI independent of Cadence, PDKs, private hosts, and model API keys.
- Use English for technical specifications and code; keep user-facing README content aligned in English and Chinese.

## Checks available today

Use Python 3.11+ on macOS or Linux. Public tests use temporary SQLite data and loopback HTTP:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m json.tool examples/run-manifest.example.json
python3 -m json.tool tests/fixtures/synthetic/disconnected-running.json
git diff --check
```

Also check relative links, the rendered icon, and that `.gitignore` permits intentional public fixtures while excluding runtime directories. For frontend changes, check actual browser behavior, keyboard access, narrow viewports and packaged static assets. Do not claim real-provider or simulator verification from the offline tests.

## Pull requests

Explain the concrete problem, resulting behavior, validation performed, and remaining limitations. Distinguish implemented functionality from interface sketches and future work. Keep existing copyright and third-party notices intact. Contributions are intended to be distributed under the repository's [MIT license](LICENSE); include only material you can license on those terms or identify compatible third-party material explicitly.
