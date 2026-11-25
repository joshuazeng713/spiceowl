"""Tests for remote EDA environment profiles (module load / setup scripts) and expanded zero-PDK templates."""

from __future__ import annotations

import unittest

from spiceowl.adapters.executors import direct as transport
from spiceowl.domain.common import DomainError
from spiceowl.onboarding_wizard import FIRST_SIM_TEMPLATES


def _sample_profile() -> dict:
    return {
        "schema": "spiceowl.host.v1",
        "host_id": "eda-sim-01",
        "label": "Lab Spectre Server",
        "ssh_target": "eda-sim",
        "expected_host": "sim-node-01",
        "expected_user": "researcher",
        "tools": [{"kind": "spectre", "executable": "/opt/cadence/tools/bin/spectre"}],
    }


class TestRemoteEnvAndTemplates(unittest.TestCase):
    def test_remote_env_validation_and_manifest_freezing(self) -> None:
        # Backward compatible: empty remote_env omits key from manifest
        plan_plain = transport.prepare(_sample_profile(), "env-plain-01", "simulator lang=spectre\n")
        self.assertNotIn("remote_env", plan_plain["manifest"])

        # Valid remote_env freezes modules, setup_scripts, and extra_path into manifest SHA-256
        env_cfg = {
            "modules": ["cadence/spectre-23.1", "eda/pdk-base"],
            "setup_scripts": ["/opt/cadence/setup.sh"],
            "extra_path": ["/opt/cadence/tools/bin"],
            "shell": "bash",
        }
        plan_env = transport.prepare(_sample_profile(), "env-plain-01", "simulator lang=spectre\n", remote_env=env_cfg)
        self.assertEqual(plan_env["manifest"]["remote_env"], env_cfg)
        self.assertNotEqual(plan_env["manifest_sha256"], plan_plain["manifest_sha256"])
        # Re-validating frozen plan succeeds
        self.assertEqual(transport._validate(plan_env)["remote_env"], env_cfg)

    def test_remote_env_rejects_shell_injection(self) -> None:
        for bad in (
            {"modules": ["spectre; rm -rf /"]},
            {"modules": ["$(whoami)"]},
            {"modules": ["../escape"]},
            {"setup_scripts": ["relative/script.sh"]},
            {"setup_scripts": ["/opt/a;id"]},
            {"extra_path": ["/opt/bin && echo pwn"]},
            {"shell": "zsh"},
            {"unknown_key": []},
        ):
            with self.assertRaises(DomainError) as ctx:
                transport.prepare(_sample_profile(), "env-bad-01", "simulator lang=spectre\n", remote_env=bad)
            self.assertEqual(ctx.exception.code, "invalid_direct_plan")

    def test_zero_pdk_starter_template_library(self) -> None:
        expected_ids = {
            "spectre_rc_smoke",
            "spectre_rlc_ringing",
            "spectre_opamp_ac",
            "spectre_mos1_inverter",
            "ngspice_rc_smoke",
        }
        self.assertTrue(expected_ids <= set(FIRST_SIM_TEMPLATES))
        for tid in expected_ids:
            tpl = FIRST_SIM_TEMPLATES[tid]
            self.assertIn(tpl["simulator"], ("spectre", "ngspice"))
            self.assertTrue(len(tpl["netlist"]) > 60)
            self.assertNotIn("include \"/", tpl["netlist"].lower())


if __name__ == "__main__":
    unittest.main()
