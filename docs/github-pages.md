# Interactive GitHub Pages demo

[Open the public demo](https://spiceowl-dev.github.io/spiceowl/).

The hosted site reuses the local console's HTML, CSS and application code. A browser-only adapter replaces the local HTTP API in the published build. Its four runs, evidence and diagnostic responses are authored synthetic fixtures. The build computes run transitions and proposals using the existing Python reducers and validators in a fresh temporary workspace.

The public demo supports inspecting runs, advancing observations, managing fake connection cards and requesting scripted diagnoses. Browser state survives reloads within the same tab and build when session storage is available. If storage is unavailable, the page explains that state is temporary. **Reset demo** clears only this demo's stored state. A new build starts a fresh demo.

SSH host configuration, real simulation execution and private IC records stay in the local installation. Local AI conversations use standalone SpiceOwl or embedded CircuitGit mode. The demo's Agent channel page explains both modes; it does not start or verify a live AI connection. The hosted page does not connect to a visitor's localhost or request host credentials or API keys. Its local-feature pages explain how to install and use the source branch instead.

## Full workflow subpage

[Open the ten-stage Bootstrap/Inverter walkthrough](https://spiceowl-dev.github.io/spiceowl/workflow.html). The static page switches inputs, output contracts, UI states, tool calls and responsibility matrices without API requests. It includes authorized publication copies of the DUT/testbench and a fixed source manifest. Source hashes and publication labels remain visible; no PDK implementation, private host identity or credential is included. The Bootstrap checkpoint is revision 10 awaiting human review with no submitted solver job. Execution and measurement screens show planned contracts, not measured Bootstrap FFT/AC/DC results. The build allowlist and tests verify every shipped source asset.

## Automatic publication

The repository's **Publish interactive demo** workflow builds and publishes using GitHub Actions. `PAGES_SOURCE_BRANCH` is a repository Actions variable selecting the source branch. It is set to `main`, which contains the runnable alpha. Each push to `main` rebuilds the site. A push to an unrelated branch does not publish it.

Use **Actions → Publish interactive demo → Run workflow** to explicitly republish `main`. Both paths build the exact checked-out commit, expose that commit and build time in the demo, and upload only `dist/pages`. No personal token is stored in the workflow; deployment uses GitHub's scoped Pages and OIDC permissions.

`PAGES_SOURCE_BRANCH` is configured as `main` in **Settings → Secrets and variables → Actions → Variables**. The `github-pages` environment must permit `main`. To publish another source branch later, update the variable, allow that branch in the environment's deployment rules, and keep the README's source-branch note aligned.

The README entry and repository homepage point to the same public URL. Implementation changes are merged into `main` through pull requests; the resulting `main` push triggers publication.

## Build and verify locally

From the implementation checkout with Python 3.11+ and Node.js available for development checks:

```bash
python3 scripts/build_pages.py --output dist/pages --commit "$(git rev-parse HEAD)" --source-branch "$(git branch --show-current)"
node tests/pages-demo.mjs dist/pages
python3 -m http.server 8080 --directory dist
```

Open `http://127.0.0.1:8080/pages/`. The app uses relative asset paths and hash navigation so a project subpath works without server routing.

`tests/test_pages_build.py` checks public output generation and input boundaries. `tests/pages-demo.mjs` exercises the browser adapter without a network or browser dependency. `tests/pages-browser.mjs` provides opt-in real-browser acceptance using an existing Playwright installation and Chrome, including the `/spiceowl/` project path, mobile layout, state persistence, scenario outcomes and absence of API requests. Set `PLAYWRIGHT_MODULE` to an existing Playwright module path when it is not installed in the default Node search path. Optional `CHROME_PATH`, `PAGES_DIR` and `PAGES_QA_DIR` select the browser, build directory and private report directory.

The builder only emits its explicit asset allowlist; it never copies `.spiceowl`, private profiles, simulator output, PDK files or repository history. It refuses an output directory containing unexpected files. Keep real operator data outside public source, regardless of hosting mode.

## Visual experiment guide (2026-11-24)

`workflow.html` opens with Agent-assisted simulation management: resource selection,
PDK setup, testbench preparation, run supervision and diagnosis. Real Bootstrap
sample-and-hold and CMOS inverter results support that story. Seven Matplotlib plots
derive from three native Spectre result archives. The two 4,096-point spectra were
independently recomputed with NumPy; the downloadable samples can be checked with:

```bash
python scripts/verify_showcase.py src/spiceowl/web/static/showcase-samples.json
```

This optional check requires NumPy. `showcase-evidence.json` records input/archive digests,
measurement conditions and provenance. The raw archives, host identities and PDK models
are not distributed. These were separate Runner reruns; they do not complete the historical
onboarding review. The prior ten-stage reference remains under an expandable section with
its original snapshot identity. Equivalent bit count is derived from deterministic
sample-and-hold SNDR, not a measured complete-ADC ENOB. No transient noise, PVT or extracted
layout qualification is claimed. The AC plot does not assert feedback-loop phase margin.

The explicit `spiceowl.web.showcase_assets.SHOWCASE_ASSETS` list is shared by the local
HTTP server and Pages builder; no filesystem scan imports private runtime data.

## Product-specific management story

The revised guide presents SpiceOwl as an independent simulation management tool,
with its own console, Agent conversation, CLI and Python SDK. CircuitGit is an
optional design integration; the current Service/Runner execution dependency is
documented in setup details rather than used as the product definition. The guide explains
user-provided information and observed evidence; standalone vs embedded Agent
configuration; resource scans, freshness and CPU/RAM reserves; bounded PDK discovery,
section/closure selection and SQLite LUT reuse; Agent-authored TBs; exact run/key
identity; monitoring, collection, and five distinct scientific report dimensions.

`pool-demo.json` is explicitly synthetic teaching data. The four CPU/RAM combinations
were evaluated offline using the production `recommend_hosts` function with unit-test
style fixtures. They are not current observations of any real server. The five-dimensional
diagnostic cards and example conversation are also clearly labeled teaching material.
The plots remain real native-result derivatives. A fresh inspection of the saved 1 V
Bootstrap logs found CMI-2752/SOA-related warnings, recorded separately in the evidence
manifest and discussed alongside improved SNDR. No model qualification is inferred.

CircuitGit's corresponding page instead leads with local AI design, Git, the RNL
renderer, Agent/developer configuration and the contrast with online Virtuoso editing.
The two guides no longer share one generic simulation-results narrative.
