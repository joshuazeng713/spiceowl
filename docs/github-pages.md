# Interactive GitHub Pages demo

[Open the public demo](https://joshuazeng713.github.io/spiceowl/).

The hosted site reuses the local console's HTML, CSS and application code. A browser-only adapter replaces the local HTTP API in the published build. Its four runs, evidence and diagnostic responses are authored synthetic fixtures. The build computes run transitions and proposals using the existing Python reducers and validators in a fresh temporary workspace.

The public demo supports inspecting runs, advancing observations, managing fake connection cards and requesting scripted diagnoses. Browser state survives reloads within the same tab and build when session storage is available. If storage is unavailable, the page explains that state is temporary. **Reset demo** clears only this demo's stored state. A new build starts a fresh demo.

SSH host configuration, native agent questions, real simulation execution and private IC records stay in the local installation. The hosted page does not connect to a visitor's localhost or request host credentials or API keys. Its local-feature pages explain how to install and use the source branch instead.

## Automatic publication

The repository's **Publish interactive demo** workflow builds and publishes using GitHub Actions. `PAGES_SOURCE_BRANCH` is a repository Actions variable selecting the source branch. It is currently `feat/server-pool-agent-qa`, where the runnable alpha lives. Each push to that branch rebuilds the site. A push to an unrelated branch does not publish it.

The workflow is also present on `main` so **Actions → Publish interactive demo → Run workflow** can explicitly republish the selected source branch. Both paths build the exact checked-out commit, expose that commit and build time in the demo, and upload only `dist/pages`. No personal token is stored in the workflow; deployment uses GitHub's scoped Pages and OIDC permissions.

When the implementation is merged into `main`, set `PAGES_SOURCE_BRANCH` to `main` in **Settings → Secrets and variables → Actions → Variables**, then run the workflow once. Subsequent `main` pushes publish automatically. Keep the README's source-branch note aligned with that change. The `github-pages` environment permits the current source branch and `main`; changing to another branch also requires adding that branch to its deployment rules.

The README entry appears on both the implementation branch and the repository's default branch. The repository homepage points to the same public URL. Publishing does not merge the existing stacked implementation PRs.

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
