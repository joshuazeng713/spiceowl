"""Setup and link boundaries use local fixtures, never package downloads."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


setup, check_env, links = map(load, ('setup_dev', 'check_dev_env', 'check_links'))


class DeveloperSetupTests(unittest.TestCase):
    def test_existing_content_and_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'existing'; path.mkdir()
            evidence = path / 'notes.txt'; evidence.write_text('preserve')
            with self.assertRaises(ValueError): setup.inspect_target(path)
            alias = Path(temp) / 'alias'; alias.symlink_to(path, target_is_directory=True)
            with self.assertRaises(ValueError): setup.inspect_target(alias)
            self.assertEqual(evidence.read_text(), 'preserve')

    def test_old_python_environment_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp); (path / 'pyvenv.cfg').write_text('version = 3.11')
            with patch.object(setup, 'supported', return_value=False):
                with self.assertRaisesRegex(ValueError, 'preserved'): setup.inspect_target(path)
            self.assertEqual((path / 'pyvenv.cfg').read_text(), 'version = 3.11')

    def test_check_only_never_downloads_or_installs(self):
        with patch.object(setup, 'inspect_target', return_value=Path('/synthetic/venv/bin/python')), \
                patch.object(setup, 'download_python', side_effect=AssertionError('no download')), \
                patch.object(setup, 'run') as run:
            self.assertEqual(setup.main(['--check-only']), 0)
        calls = [list(map(str, call.args[0])) for call in run.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-2:], ['pip', 'check'])
        self.assertTrue(calls[1][-1].endswith('check_dev_env.py'))

    def test_required_optional_and_build_dependencies_are_exact(self):
        pins = check_env.pins()
        self.assertEqual(pins['adctoolbox'], '0.9.3')
        self.assertEqual(pins['mcp'], '1.30.0')
        for name in ('numpy', 'scipy', 'matplotlib', 'cryptography', 'pip', 'setuptools', 'wheel'):
            self.assertIn(name, pins)


class LinkChecksTests(unittest.TestCase):
    def test_local_targets_and_fragments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / 'docs').mkdir()
            (root / 'docs/read.md').write_text('# A simple heading\n')
            self.assertEqual(links.local_result(root, 'README.md', 'docs/read.md#a-simple-heading')['status'], 'valid')
            self.assertEqual(links.local_result(root, 'README.md', 'docs/read.md#missing')['reason'], 'missing_local_anchor')
            self.assertEqual(links.local_result(root, 'README.md', 'missing.md')['status'], 'broken')

    def test_non_public_links_are_not_requested(self):
        for url in ('http://127.0.0.1:8766/run', 'https://example.com/demo',
                    'https://host.invalid/run', 'https://github.com/owner/repo',
                    'https://token@github.com/example/repo', 'https://api.openai.com/v1'):
            with self.subTest(url=url), patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
                self.assertEqual(links.fetch(url, 1)['status'], 'skipped')


if __name__ == '__main__': unittest.main()
