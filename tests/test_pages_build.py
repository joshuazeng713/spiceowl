"""The public demo is compiled from isolated synthetic state, never user data."""

import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.agent.diagnosis import build_evidence, validate_proposal
from spiceowl.service import Controller


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("spiceowl_pages_builder", ROOT / "scripts/build_pages.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PagesBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "site"

    def data(self):
        raw = (self.output / "demo-data.js").read_text()
        return json.loads(raw.removeprefix("window.__SPICEOWL_DEMO_DATA__=").removesuffix(";\n"))

    def test_build_uses_fresh_controller_without_network_or_operator_files(self):
        directories = []
        original_open = Path.open
        def isolated(directory):
            directories.append(Path(directory))
            self.assertNotEqual(Path(directory), ROOT / ".spiceowl")
            self.assertEqual(list(Path(directory).iterdir()), [])
            return Controller(directory)
        def bounded_open(path, *args, **kwargs):
            absolute = path.absolute()
            if absolute.is_relative_to(ROOT / ".spiceowl") or absolute.is_relative_to(Path.home() / ".ssh"):
                raise AssertionError("Builder read private operator state")
            return original_open(path, *args, **kwargs)
        with patch("spiceowl.service.Controller", side_effect=isolated), \
                patch("socket.create_connection", side_effect=AssertionError("Network")), \
                patch("subprocess.Popen", side_effect=AssertionError("External process")), \
                patch.object(Path, "open", bounded_open):
            result = builder.build(self.output, commit="a" * 40, source_branch="feat/public-demo")
        self.assertEqual(len(directories), 1)
        self.assertFalse(directories[0].exists())
        self.assertEqual((result["run_count"], result["frame_count"]), (4, 13))
        self.assertEqual({p.name for p in self.output.iterdir()}, builder.OUTPUT_FILES)
        self.assertEqual(len(builder.OUTPUT_FILES), 17 + len(builder.SHOWCASE_ASSETS))
        self.assertEqual((self.output / "LICENSE.txt").read_bytes(), (ROOT / "LICENSE").read_bytes())
        text = (self.output / "demo-data.js").read_text()
        for forbidden in (str(Path.home()), str(ROOT), str(directories[0]), "ssh_target", "api_key", "remote_directory"):
            self.assertNotIn(forbidden, text)

    def test_original_frames_and_policy_preserve_disconnect_and_valid_target_failure(self):
        builder.build(self.output)
        data = self.data()
        self.assertTrue(data["initial_state"]["public_demo"])
        self.assertTrue(data["initial_state"]["scripted"])
        self.assertEqual(len(data["initial_state"]["connections"]), 2)
        self.assertFalse(data["initial_state"]["capabilities"]["real_inference"])
        self.assertEqual(data["initial_state"]["hosts"], [])
        for run_id, frames in data["frames"].items():
            for frame in frames:
                self.assertTrue(frame["synthetic"])
                self.assertEqual(frame["run_id"], run_id)
                evidence = build_evidence(frame)
                for compiled in data["diagnostics"][run_id][frame["cursor"]].values():
                    self.assertEqual(compiled["evidence"], evidence)
                    if compiled["proposal"]:
                        validate_proposal(compiled["proposal"], evidence)
                        self.assertFalse(compiled["decision"]["dispatch_enabled"])
        disconnect = data["frames"]["demo-disconnect"][1]
        self.assertEqual((disconnect["execution"], disconnect["observation"]), ("running", "unknown"))
        miss = data["frames"]["demo-target_miss"][-1]
        self.assertEqual((miss["measurement_validity"], miss["specification_result"]), ("valid", "fail"))
        self.assertEqual(data["diagnostics"]["demo-target_miss"][-1]["success"]["proposal"]["kind"], "report")

    def test_demo_injection_only_affects_build_and_all_assets_are_subpath_relative(self):
        local_index = ROOT / "src/spiceowl/web/static/index.html"
        original = local_index.read_bytes()
        builder.build(self.output, commit="b" * 40, source_branch="feat/demo")
        self.assertEqual(local_index.read_bytes(), original)
        self.assertNotIn(b"data-public-demo=", original)
        index = (self.output / "index.html").read_text()
        self.assertIn('data-public-demo="true"', index)
        self.assertIn("connect-src http://127.0.0.1:8765", index)
        self.assertIn("frame-src spiceowl:", index)
        metadata = json.loads((self.output / "build.json").read_text())
        ver = metadata["build_id"][:10]
        self.assertIn(f'src="./demo-data.js?v={ver}"', index)
        self.assertIn(f'href="./app.css?v={ver}"', index)
        self.assertLess(index.index('./demo-data.js?v='), index.index('./demo.js?v='))
        self.assertLess(index.index('./demo.js?v='), index.index('./app.js?v='))
        for name in ("app.js", "app.css", "owl.svg"):
            self.assertNotIn('"/' + name + '"', index)
        self.assertEqual(metadata["commit"], "b" * 40)
        self.assertEqual(metadata["source_branch"], "feat/demo")
        self.assertEqual(metadata, self.data()["metadata"])

    def test_existing_output_never_copies_or_deletes_unregistered_files(self):
        self.output.mkdir()
        private = self.output / "operator-config.json"
        private.write_text("do not publish")
        with self.assertRaises(ValueError):
            builder.build(self.output)
        self.assertEqual(list(self.output.iterdir()), [private])
        self.assertEqual(private.read_text(), "do not publish")

    def test_workflow_page_is_allowlisted_local_and_honest_about_pending_results(self):
        builder.build(self.output)
        source = ROOT / 'src/spiceowl/web/static'
        for name in ('workflow.html', 'workflow.js', 'workflow.css'):
            self.assertEqual((self.output / name).read_bytes(), (source / name).read_bytes())
        html = (self.output / 'workflow.html').read_text()
        self.assertIn('lang="zh-CN"', html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn('src="./workflow.js"', html)
        self.assertIn('href="./workflow.css"', html)
        self.assertIn('href="./index.html"', html)
        script = (self.output / 'workflow.js').read_text()
        self.assertIn('awaiting_human_review', script)
        self.assertIn('contract_illustration_not_service_receipt', script)
        self.assertIn("measurement_status: 'not_run'", script)
        self.assertNotIn('fetch(', script)
        self.assertNotIn('XMLHttpRequest', script)
        self.assertNotIn('WebSocket', script)
        for private in (str(Path.home()), str(ROOT), 'thu-shu', 'thu-wei', 'api_key', 'ssh_target='):
            self.assertNotIn(private, html + script)

    def test_rebuild_updates_only_the_known_public_outputs(self):
        first = builder.build(self.output, commit="c" * 40)
        second = builder.build(self.output, commit="d" * 40)
        self.assertNotEqual(first["metadata"]["build_id"], second["metadata"]["build_id"])
        self.assertEqual({p.name for p in self.output.iterdir()}, builder.OUTPUT_FILES)

    def test_publication_assets_keep_exact_approved_hashes_and_reject_mutated_text(self):
        builder.build(self.output)
        assets = {name: (self.output / name).read_bytes() for name in builder.ASSETS}
        builder.validate_publications(assets)
        for name, expected in builder.PUBLICATION_HASHES.items():
            self.assertEqual(hashlib.sha256(assets[name]).hexdigest(), expected)
        evidence = json.loads(assets['bootstrap-evidence.publication.json'])
        self.assertEqual(evidence['status']['state'], 'awaiting_human_review')
        for field in ('solverStarted', 'jobPresent', 'waveformDataPresent', 'fftResultsPresent'):
            self.assertFalse(evidence['status'][field])
        for name in ('bootstrap-tb.publication.scs', 'workflow.js'):
            altered = dict(assets)
            altered[name] = altered[name].replace(b'CHOLD', b'CFAKE')
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.validate_publications(altered)

    def test_visual_evidence_assets_fail_closed_on_plot_or_sample_changes(self):
        builder.build(self.output)
        assets = {name: (self.output / name).read_bytes() for name in builder.ASSETS}
        for name in ("showcase-spectrum.svg", "showcase-samples.json"):
            altered = dict(assets)
            altered[name] += b" "
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.validate_publications(altered)

    def test_build_metadata_cannot_contain_credentials_or_shell_tokens(self):
        for changes in ({"commit": "not-a-commit"}, {"repository_url": "https://token@github.com/owner/repo"},
                        {"repository_url": "https://github.com/owner/repo?key=secret"},
                        {"source_branch": "branch; command"}, {"source_branch": "../main"},
                        {"build_date": "2026-11-22T00:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                builder.build(self.output, **changes)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
