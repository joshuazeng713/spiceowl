"""Synthetic discovery evidence; no private setup, SSH, or simulator access."""

import hashlib
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from spiceowl.domain.common import DomainError
from spiceowl.onboarding.pdk_discovery import (
    DIRECTORY_SNAPSHOT_PREFIX, MAX_DIRECTORY_SNAPSHOT_BYTES,
    audit_config_for_candidate, build_pdk_discovery_script, inspect_netlist,
    profile_suggestions,
)


class NetlistRequirementsTests(unittest.TestCase):
    def test_supplied_source_only_unknown_masters_and_dimensions(self):
        source = ('M9 (OUT IN LO LO) example_a w=250n l=30n\n'
                  'M10 (OUT IN HI HI) example_b w=250n l=30n\n')
        with patch("builtins.open", side_effect=AssertionError("unexpected IO")):
            result = inspect_netlist(source)
        self.assertEqual(result["required_masters"], ["example_a", "example_b"])
        self.assertEqual(result["source_sha256"], hashlib.sha256(source.encode()).hexdigest())
        self.assertAlmostEqual(result["instances"][0]["dimensions"]["w"]["value_m"], 2.5e-7)
        self.assertIsNone(result["instances"][0]["polarity"])
        self.assertIsNone(result["instances"][0]["terminal_semantics"])

    def test_dimension_suffix_respects_language_and_retains_ambiguity(self):
        a = inspect_netlist('M1 (d g s b) example_a w=1M l=foo\n')
        b = inspect_netlist('simulator lang=spectre\nM1 (d g s b) example_a w=1M\n')
        c = inspect_netlist('M1 d g s b example_a W=1M\n')
        self.assertIsNone(a["instances"][0]["dimensions"]["w"]["value_m"])
        self.assertIsNone(a["instances"][0]["dimensions"]["l"]["value_m"])
        self.assertEqual(b["instances"][0]["dimensions"]["w"]["value_m"], 1e6)
        self.assertEqual(c["instances"][0]["dimensions"]["w"]["value_m"], 1e-3)

    def test_includes_remain_unopened_and_local_declarations_are_excluded(self):
        source = ('include "/outside/do-not-read.scs"\nsubckt local (d g s b)\n'
                  'M1 (d g s b) example_a w=250n \\\n l=30n\nends local\n'
                  'X1 (d g s b) local\nR1 (d s) resistor r=1k\n')
        with patch("builtins.open", side_effect=AssertionError("unexpected IO")):
            result = inspect_netlist(source)
        self.assertEqual(result["required_masters"], ["example_a"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["issues"][0]["code"], "source_include_not_opened")

    def test_input_boundaries(self):
        for value in (b"\xff", "", "\x00x", "x" * (1024 * 1024 + 1)):
            with self.assertRaises(DomainError):
                inspect_netlist(value)

    def test_published_fixture_matches_source_receipt_and_suggestion_api(self):
        path = Path(__file__).resolve().parents[1] / "docs/contracts/onboarding-discovery.example.json"
        fixture = json.loads(path.read_text())
        self.assertEqual(fixture["evidence_kind"], "synthetic_local_fixture")
        self.assertFalse(fixture["real_ssh_performed"])
        self.assertFalse(fixture["real_simulator_performed"])
        self.assertEqual(inspect_netlist(fixture["source_text"], source_id="synthetic-inverter.scs"),
                         fixture["requirements"])
        self.assertEqual(fixture["requirements"]["source_sha256"], fixture["discovery"]["source_sha256"])
        self.assertEqual(profile_suggestions(fixture["discovery"]), fixture["profile_suggestions"])
        public = json.dumps(fixture["public_environment"])
        self.assertNotIn(fixture["discovery"]["roots"][0], public)
        self.assertIsNone(fixture["profile_suggestions"]["selected_candidate_id"])


@unittest.skipIf(sys.platform == "win32", "Executes remote Linux POSIX-path PDK scanner against local temp dirs")
class PdkDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.roots = [str(self.root)]

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return path

    def script(self, **kwargs):
        return build_pdk_discovery_script(required_masters=["example_a", "example_b"],
                                          roots=self.roots, source_sha256="a" * 64, **kwargs)

    def discover(self, **kwargs):
        output = subprocess.run([sys.executable, "-B", "-"], input=self.script(**kwargs),
                                text=True, capture_output=True, timeout=15, check=True)
        self.assertEqual(output.stderr, "")
        lines = output.stdout.splitlines()
        snapshots = [line for line in lines if line.startswith(DIRECTORY_SNAPSHOT_PREFIX)]
        self.assertEqual(len(snapshots), 1)
        self.assertLessEqual(len((snapshots[0] + "\n").encode()), MAX_DIRECTORY_SNAPSHOT_BYTES)
        final = [line for line in lines if not line.startswith(DIRECTORY_SNAPSHOT_PREFIX)]
        self.assertEqual(len(final), 1)
        return json.loads(final[0])

    def model_source(self):
        return ('inline subckt example_a (port1 port2 port3 port4)\n'
                'parameters sensitive_example_parameter=1234\n'
                'inner (port1 port2 port3 port4) primitive_a w=1u l=1u\n'
                'model primitive_a bsim4 type=1\nends example_a\n'
                'inline subckt example_b (gate drain bulk source)\n'
                'inner (drain gate source bulk) primitive_b w=1u l=1u\n'
                'model primitive_b bsim4 type=-1\nends example_b\n')

    def test_finite_wrapper_evidence_and_machine_readable_suggestions(self):
        self.write("models.scs", self.model_source())
        data = self.discover()
        self.assertTrue(data["search_complete"])
        candidate = data["entry_candidates"][0]
        self.assertEqual(candidate["entry_role"], "definition_only")
        models = candidate["models"]
        self.assertEqual([m["confidence"] for m in models], ["verified", "verified"])
        self.assertEqual([m["polarity"] for m in models], ["n", "p"])
        self.assertEqual(models[1]["ordered_terminal_roles"], ["g", "d", "b", "s"])
        self.assertEqual(models[1]["suggested_contract"], {"polarity": "p", "terminals": ["g", "d", "b", "s"]})
        self.assertTrue(all(m["auto_prefill_allowed"] for m in models))
        self.assertEqual(models[1]["suggestion_basis"], "single_mos_bijective_wrapper")
        self.assertNotIn("sensitive_example_parameter", json.dumps(data))
        self.assertFalse(data["selection_made"])
        self.assertFalse(data["profile_registered"])
        with patch("builtins.open", side_effect=AssertionError("unexpected IO")):
            draft = profile_suggestions(data)["candidates"][0]
        self.assertTrue(draft["model_contract_source_verified"])
        self.assertFalse(draft["ready_to_run"])
        self.assertNotIn("model_polarity", draft["requires"])

    def test_polarity_is_not_guessed_from_master_names_or_port_labels(self):
        self.write("models.scs", 'subckt example_a (d g s b)\nends\n'
                   'subckt example_b (d g s b)\nends\n')
        data = self.discover()
        self.assertTrue(all(m["polarity"] is None and m["confidence"] == "unknown"
                            for m in data["entry_candidates"][0]["models"]))
        self.assertIn("model_terminal_semantics", profile_suggestions(data)["candidates"][0]["requires"])

    def test_direct_spice_primitive_models(self):
        self.write("models.lib", '.model example_a nmos (level=1)\n.model example_b pmos (level=1)\n')
        models = self.discover()["entry_candidates"][0]["models"]
        self.assertEqual([m["polarity"] for m in models], ["n", "p"])
        self.assertTrue(all(m["ordered_terminal_roles"] == ["d", "g", "s", "b"] for m in models))

    def test_wrapper_ambiguity_and_non_bijective_terminal_mapping(self):
        source = self.model_source().replace('inner (port1 port2 port3 port4)', 'inner (port1 port2 port3 port3)')
        source = source.replace('ends example_b', 'helper (drain gate source bulk) primitive_b\nends example_b')
        self.write("models.scs", source)
        self.assertTrue(all(m["confidence"] == "unknown" for m in self.discover()["entry_candidates"][0]["models"]))

    def test_unsupported_and_expression_model_types_are_unknown(self):
        self.write("models.scs", self.model_source().replace('type=1', 'type=choice').replace('bsim4 type=-1', 'unrecognized type=-1'))
        self.assertTrue(all(m["polarity"] is None for m in self.discover()["entry_candidates"][0]["models"]))

    def test_section_scoped_contracts_preserve_case_and_candidates(self):
        self.write("models.scs", 'section Nominal\n' + self.model_source() + 'endsection Nominal\n'
                   'section Other\n' + self.model_source().replace('type=1', 'type=-1') + 'endsection Other\n')
        data = self.discover()
        candidates = data["entry_candidates"]
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["section_candidates"], ["Nominal"])
        self.assertEqual([c["models"][0]["polarity"] for c in candidates], ["n", "p"])
        self.assertIn("multiple_entry_or_section_candidates", {a["code"] for a in data["ambiguities"]})
        config = audit_config_for_candidate(data, candidates[0]["candidate_id"], sections=["Nominal"])
        self.assertEqual(config["sections"], ["Nominal"])
        with self.assertRaises(DomainError):
            audit_config_for_candidate(data, candidates[0]["candidate_id"], sections=["tt"])

    def test_include_graph_finds_extensionless_models(self):
        entry = self.write("entry.scs", 'include "device-data"\n')
        self.write("device-data", self.model_source())
        data = self.discover()
        candidate = next(c for c in data["entry_candidates"] if c["entry_path"] == str(entry))
        self.assertTrue(candidate["coverage_complete"])
        self.assertEqual(candidate["entry_role"], "include_entry_candidate")
        self.assertTrue(all(m["confidence"] == "verified" for m in candidate["models"]))

    def test_missing_include_and_missing_section_leave_incomplete_scope(self):
        self.write("entry.scs", self.model_source() + 'include "missing.inc"\n')
        data = self.discover()
        candidate = data["entry_candidates"][0]
        self.assertFalse(data["search_complete"])
        self.assertFalse(candidate["coverage_complete"])
        self.assertTrue(all(m["confidence"] == "suggested" for m in candidate["models"]))
        self.assertTrue(all(m["suggested_contract"] and not m["auto_prefill_allowed"] for m in candidate["models"]))
        self.write("missing.inc", 'section Found\nendsection Found\n')
        self.write("entry.scs", self.model_source() + 'include "missing.inc" section=Absent\n')
        data = self.discover()
        self.assertFalse(data["search_complete"])
        self.assertIn("include_target_not_scanned", data["entry_candidates"][0]["unknown_codes"])

    def test_duplicate_active_primitive_is_unknown(self):
        self.write("models.scs", self.model_source() + 'model primitive_a bsim4 type=-1\n')
        model = self.discover()["entry_candidates"][0]["models"][0]
        self.assertEqual(model["reason"], "multiple_active_primitive_definitions")
        self.assertEqual(model["confidence"], "unknown")

    def test_large_model_catalog_keeps_requested_contracts_without_name_only_deduplication(self):
        unrelated = "".join('model unrelated_%04d bsim4 type=1\n' % n for n in range(4000))
        scopes = "".join('section Corner%02d\nendsection Corner%02d\n' % (n, n) for n in range(32))
        self.write("models.scs", unrelated + self.model_source() + scopes)
        data = self.discover()
        self.assertEqual(len(data["entry_candidates"]), 33)
        self.assertTrue(all(m["confidence"] == "verified" for c in data["entry_candidates"] for m in c["models"]))
        self.assertNotIn("unrelated_3999", json.dumps(data))
        # Separate source lines remain separate declarations even if the name,
        # family and polarity text are identical; they must not be merged.
        self.write("models.scs", unrelated + self.model_source() +
                   'model primitive_a bsim4 type=1\nmodel primitive_a bsim4 type=1\n')
        model = self.discover()["entry_candidates"][0]["models"][0]
        self.assertEqual(model["reason"], "multiple_active_primitive_definitions")
        self.assertEqual(model["confidence"], "unknown")

    def test_same_named_local_primitives_keep_distinct_wrapper_scope_and_polarity(self):
        self.write("models.scs", self.model_source().replace("primitive_a", "shared_local").replace("primitive_b", "shared_local"))
        models = self.discover()["entry_candidates"][0]["models"]
        self.assertEqual([m["polarity"] for m in models], ["n", "p"])
        self.assertTrue(all(m["confidence"] == "verified" for m in models))

    def binned_source(self):
        return ("simulator lang=spectre\n"
                "inline subckt example_a (bulk gate drain source)\n"
                "core_n drain gate source bulk local_device w=1u l=1u\n"
                "simulator lang=spice\n"
                ".model local_device.global nmos level=54\n"
                ".model local_device.1 nmos lmin=0 lmax=1u\n"
                ".model local_device.2 nmos lmin=1u lmax=2u\n"
                "simulator lang=spectre\nends example_a\n"
                "inline subckt example_b (a b c d)\n"
                "core_p a b c d local_device w=1u l=1u\n"
                "simulator lang=spice\n"
                ".model local_device.global pmos level=54\n"
                ".model local_device.1 pmos lmin=0 lmax=1u\n"
                ".model local_device.2 pmos lmin=1u lmax=2u\n"
                "simulator lang=spectre\nends example_b\n")

    def test_spectre_unparenthesized_inline_calls_and_spice_bins_have_source_contracts(self):
        self.write("models.scs", self.binned_source())
        models = self.discover()["entry_candidates"][0]["models"]
        self.assertEqual([m["polarity"] for m in models], ["n", "p"])
        self.assertEqual(models[0]["ordered_terminal_roles"], ["b", "g", "d", "s"])
        self.assertTrue(all(m["confidence"] == "verified" and m["auto_prefill_allowed"] for m in models))
        self.assertTrue(all(m["reason"] == "single_mos_bijective_binned_wrapper" for m in models))
        for model in models:
            primitives = [e for e in model["evidence"] if e.get("kind") == "primitive_model"]
            self.assertEqual([e["name"] for e in primitives], ["local_device.global", "local_device.1", "local_device.2"])
            self.assertEqual(len({e["owner_line"] for e in primitives}), 1)

    def test_binned_conflicts_unknown_family_and_duplicate_suffixes_are_not_verified(self):
        cases = [
            (self.binned_source().replace('.model local_device.2 nmos', '.model local_device.2 pmos'),
             "conflicting_or_unknown_model_bins"),
            (self.binned_source().replace('.model local_device.2 nmos', '.model LOCAL_DEVICE.2 PMOS'),
             "conflicting_or_unknown_model_bins"),
            (self.binned_source().replace('.model local_device.2 nmos', '.model local_device.2 unknown_mos'),
             "conflicting_or_unknown_model_bins"),
            (self.binned_source().replace('.model local_device.2 nmos', '.model local_device.1 nmos'),
             "ambiguous_binned_primitive_ownership_or_suffix"),
            (self.binned_source().replace('.model local_device.2 nmos', '.model local_device.01 nmos'),
             "ambiguous_binned_primitive_ownership_or_suffix"),
            (self.binned_source().replace('.model local_device.1 nmos lmin=0 lmax=1u\n', '')
             .replace('.model local_device.2 nmos lmin=1u lmax=2u\n', ''), "missing_numeric_model_bin"),
        ]
        for source, reason in cases:
            with self.subTest(reason=reason):
                self.write("models.scs", source)
                model = self.discover()["entry_candidates"][0]["models"][0]
                self.assertEqual(model["reason"], reason)
                self.assertIsNone(model["suggested_contract"])
                self.assertFalse(model["auto_prefill_allowed"])
                self.assertEqual(model["confidence"], "unknown")

    def test_binned_models_never_borrow_another_wrapper_or_merge_with_global_owner(self):
        source = self.binned_source()
        local_start = source.index('.model local_device.global nmos')
        local_end = source.index('simulator lang=spectre\nends example_a')
        self.write("models.scs", source[:local_start] + source[local_end:])
        first, second = self.discover()["entry_candidates"][0]["models"]
        self.assertEqual(first["reason"], "primitive_model_not_resolved")
        self.assertFalse(first["auto_prefill_allowed"])
        self.assertTrue(second["auto_prefill_allowed"])
        self.write("models.scs", source + "simulator lang=spice\n.model local_device.3 nmos level=54\n")
        models = self.discover()["entry_candidates"][0]["models"]
        self.assertTrue(all(m["reason"] == "ambiguous_binned_primitive_ownership_or_suffix" for m in models))
        self.assertTrue(all(m["suggested_contract"] is None for m in models))

    def test_binned_dynamic_topology_and_repeated_nodes_stay_unverified(self):
        cases = [self.binned_source().replace('drain gate source bulk local_device', 'drain gate source source local_device'),
                 self.binned_source().replace('core_n drain', 'if (flag) {\ncore_n drain').replace(
                     'simulator lang=spice\n.model local_device.global nmos', '}\nsimulator lang=spice\n.model local_device.global nmos')]
        for source in cases:
            self.write("models.scs", source)
            first = self.discover()["entry_candidates"][0]["models"][0]
            self.assertEqual(first["confidence"], "unknown")
            self.assertFalse(first["auto_prefill_allowed"])

    def test_spice_switch_allows_mos_calls_but_not_arbitrary_instance_names(self):
        source = ('.subckt example_a d g s b\nM1 d g s b primitive_a\n'
                  '.model primitive_a nmos level=1\n.ends example_a\n'
                  '.subckt example_b d g s b\nX1 d g s b primitive_b\n'
                  '.model primitive_b pmos level=1\n.ends example_b\n')
        self.write("models.lib", source)
        models = self.discover()["entry_candidates"][0]["models"]
        self.assertTrue(models[0]["auto_prefill_allowed"])
        self.assertEqual(models[1]["confidence"], "unknown")
        self.write("models.lib", source.replace('.subckt example_b d g s b\n',
                   '.subckt example_b d g s b\nsimulator lang=spectre\n').replace('.ends example_b', 'ends example_b'))
        self.assertTrue(self.discover()["entry_candidates"][0]["models"][1]["auto_prefill_allowed"])

    def test_unknown_language_and_case_aliased_spice_ports_stay_unknown(self):
        source = ('.subckt example_a D d s b\nM1 D d s b primitive_a\n'
                  '.model primitive_a nmos level=1\n.ends example_a\n'
                  '.subckt example_b d g s b\nM1 d g s b primitive_b\n'
                  '.model primitive_b pmos level=1\n.ends example_b\n')
        self.write("models.lib", source)
        model = self.discover()["entry_candidates"][0]["models"][0]
        self.assertFalse(model["auto_prefill_allowed"])
        self.assertEqual(model["reason"], "unsupported_wrapper_contract")
        self.write("models.lib", 'simulator lang=unknown\n' + self.model_source())
        self.assertTrue(all(m["confidence"] == "unknown" for m in self.discover()["entry_candidates"][0]["models"]))

    def test_candidate_source_roles_preludes_and_literal_values_are_observed_and_scoped(self):
        entry = self.write("entry.scs", 'include "models.scs" section=CornerA\n')
        source = self.write("models.scs", 'parameters tnom=27 vdd_nom=750m\n'
                            'section pre_simu\nparameters vdd_nom=9\nendsection pre_simu\n'
                            'section CornerA\n' + self.model_source().replace(
                                'model primitive_a bsim4 type=1', 'model primitive_a bsim4 type=1 vgs_max=1.2') +
                            'parameters vnom=choice vdd=1234 nominal_temperature={expression}\nendsection CornerA\n')
        candidates = self.discover()["entry_candidates"]
        candidate = next(c for c in candidates if c["entry_path"] == str(source) and c["section_candidates"] == ["CornerA"])
        self.assertEqual(candidate["observed_sections"], [None, "CornerA", "pre_simu"])
        role = candidate["source_role_evidence"]
        self.assertEqual(role["observed_section_count"], 2)
        self.assertEqual(role["incoming_includes"], [{"from": str(entry), "from_section": None,
                         "line": 1, "to": str(source), "section": "CornerA", "role": "model"}])
        self.assertEqual(candidate["prelude_candidates"][0]["section"], "pre_simu")
        self.assertFalse(candidate["prelude_candidates"][0]["auto_prefill_allowed"])
        self.assertEqual([r["value"] for r in candidate["nominal_temperature_evidence"]], [27])
        self.assertEqual([r["value"] for r in candidate["nominal_voltage_evidence"]], [0.75])
        rating = candidate["voltage_limit_evidence"][0]
        self.assertEqual((rating["parameter"], rating["value"], rating["unit"]), ("vgs_max", 1.2, "V"))
        self.assertEqual(rating["model_name"], "primitive_a")
        self.assertIsInstance(rating["owner_line"], int)
        self.assertEqual(rating["path"], str(source))
        self.assertEqual(rating["section"], "CornerA")
        self.assertEqual(rating["scope"], "device_scoped")
        self.assertEqual(rating["required_models"], ["example_a"])
        self.assertEqual(rating["declaration_count"], 1)
        self.assertEqual(rating["declaration_lines"], [rating["line"]])
        self.assertEqual(candidate["nominal_voltage_evidence"][0]["scope"], "global")
        self.assertFalse(candidate["literal_evidence_truncated"])
        entry_candidate = next(c for c in candidates if c["entry_path"] == str(entry))
        self.assertEqual(entry_candidate["source_role_evidence"]["global_include_lines"], [1])
        self.assertTrue(entry_candidate["source_role_evidence"]["global_scope_present"])
        self.assertEqual(entry_candidate["prelude_candidates"], [])

    def test_candidate_with_no_explicit_operating_literals_keeps_them_unknown(self):
        self.write("models.scs", self.model_source())
        candidate = self.discover()["entry_candidates"][0]
        for field in ("nominal_temperature_evidence", "nominal_voltage_evidence", "voltage_limit_evidence", "prelude_candidates"):
            self.assertEqual(candidate[field], [])

    def test_literal_evidence_is_bounded_and_expressions_are_never_partially_numeric(self):
        self.write("models.scs", self.model_source() +
                   "parameters tnom=27+offset vnom=0.8*scale maxvgs=1e999\n"
                   "parameters tnom=27 + offset vnom=0.8 * scale\nparameters tnom=300K\n" +
                   "\n".join("parameters vdd_nom=%d" % n for n in range(300)))
        candidate = self.discover()["entry_candidates"][0]
        self.assertEqual(candidate["nominal_temperature_evidence"], [])
        self.assertEqual(candidate["voltage_limit_evidence"], [])
        self.assertGreater(len(candidate["nominal_voltage_evidence"]), 0)
        self.assertLessEqual(len(candidate["nominal_voltage_evidence"]), 32)
        self.assertEqual(candidate["literal_evidence_available_count"], 300)
        self.assertTrue(candidate["literal_evidence_truncated"])

    def test_unused_io_literals_do_not_limit_required_core_or_consume_its_evidence_budget(self):
        unused = 'subckt unused_io (d g s b)\n' + ''.join(
            'model io_%d bsim4 type=1 vgs_max=0.1 tnom=125\n' % n for n in range(300)) + 'ends unused_io\n'
        source = (unused + 'model unused_global bsim4 type=1 vgs_max=0.2\n' +
                  'parameters maxvgs=0.15\n' + self.model_source().replace(
                      'model primitive_a bsim4 type=1', 'model primitive_a bsim4 type=1 vgs_max=1.2 tnom=27') +
                  'section pre_simu\nparameters maxvgs=0.25\nendsection pre_simu\n')
        self.write("models.scs", source)
        candidate = next(c for c in self.discover()["entry_candidates"] if c["section_candidates"] == ["pre_simu"])
        device = [r for r in candidate["voltage_limit_evidence"] if r["scope"] == "device_scoped"]
        self.assertEqual([(r["required_models"], r["value"]) for r in device], [(["example_a"], 1.2)])
        self.assertEqual({r["scope"] for r in candidate["voltage_limit_evidence"]}, {"device_scoped", "global", "preamble"})
        self.assertEqual([r["value"] for r in candidate["nominal_temperature_evidence"]], [27])
        self.assertNotIn("unused_global", json.dumps(candidate["voltage_limit_evidence"]))
        self.assertFalse(candidate["literal_evidence_truncated"])
        self.assertTrue(all(m["confidence"] == "verified" for m in candidate["models"]))

    def test_repeated_bin_values_keep_complete_values_with_separate_location_sampling(self):
        body = 'simulator lang=spectre\n'
        for name, family in (("example_a", "nmos"), ("example_b", "pmos")):
            body += 'inline subckt ' + name + ' (d g s b)\ninner d g s b local\nsimulator lang=spice\n'
            body += '.model local.global ' + family + ' tnom=27 vgs_max=1.2\n'
            body += ''.join('.model local.%d %s tnom=27 vgs_max=1.2\n' % (n + 1, family) for n in range(70))
            body += 'simulator lang=spectre\nends ' + name + '\n'
        self.write("models.scs", body)
        candidate = self.discover()["entry_candidates"][0]
        self.assertEqual(candidate["literal_evidence_available_count"], 4)
        self.assertEqual(candidate["literal_declaration_count"], 284)
        self.assertFalse(candidate["literal_evidence_truncated"])
        for field in ("nominal_temperature_evidence", "voltage_limit_evidence"):
            self.assertEqual(len(candidate[field]), 2)
            for row in candidate[field]:
                self.assertEqual(row["declaration_count"], 71)
                self.assertEqual(len(row["declaration_lines"]), 16)
                self.assertTrue(row["declaration_locations_truncated"])
                self.assertEqual(row["scope"], "device_scoped")
        self.assertTrue(all(m["confidence"] == "verified" for m in candidate["models"]))
        self.assertTrue(all(len([e for e in m["evidence"] if e.get("kind") == "primitive_model"]) == 71 for m in candidate["models"]))

    def test_incoming_metadata_retains_exact_counts_and_bounded_samples(self):
        leaf = self.write("models.scs", self.model_source())
        for n in range(12):
            self.write('entry-%02d-' % n + 'a' * 130 + '.scs', 'include "models.scs"\n')
        candidate = next(c for c in self.discover()["entry_candidates"] if c["entry_path"] == str(leaf))
        role = candidate["source_role_evidence"]
        self.assertEqual(role["incoming_include_count"], 12)
        self.assertEqual(role["global_incoming_include_count"], 12)
        self.assertLessEqual(len(role["incoming_includes"]), 8)
        self.assertTrue(role["incoming_includes_truncated"])
        self.assertLessEqual(sum(len(json.dumps(row, sort_keys=True, ensure_ascii=True).encode()) for row in role["incoming_includes"]), 1024)
        self.assertTrue(all(m["confidence"] == "verified" for m in candidate["models"]))

    def test_optional_recommendation_metadata_never_displaces_fitting_model_contracts(self):
        sections = ''.join('section C%03d\nendsection C%03d\n' % (n, n) for n in range(210))
        values = ''.join('parameters vdd_nom=%s\n' % (0.5 + n / 100) for n in range(32))
        self.write("models.scs", self.model_source() + values + sections)
        data = self.discover()
        self.assertFalse(data.get("results_omitted", False))
        self.assertEqual(len(data["entry_candidates"]), 211)
        self.assertIn("entry_candidates.*.operating_literal_evidence", {r["field"] for r in data["detail_omissions"]})
        self.assertTrue(data["search_complete"])
        for candidate in data["entry_candidates"]:
            self.assertTrue(all(m["confidence"] == "verified" and m["evidence"] for m in candidate["models"]))
            self.assertTrue(candidate["literal_evidence_truncated"])
            self.assertEqual(candidate["literal_evidence_available_count"], 32)
            self.assertEqual(candidate["nominal_voltage_evidence"], [])
        self.assertLess(len(json.dumps(data, sort_keys=True).encode()), 2 * 1024 * 1024)

    def test_catalog_suggests_roots_without_expanding_scope(self):
        scanned = self.root / "scanned"
        scanned.mkdir()
        outside = self.write("unselected/models.scs", self.model_source())
        catalog = self.write("cds.lib", 'DEFINE unknown_library "' + str(outside.parent) + '"\n')
        self.roots = [str(scanned)]
        data = self.discover(cds_libs=[str(catalog)])
        self.assertEqual(data["entry_candidates"], [])
        self.assertTrue(data["root_suggestions"][0]["requires_root_confirmation"])
        self.assertEqual(data["files"], [])

    def test_preferred_model_subtree_reached_before_wide_oa_branch_exhausts_budget(self):
        for n in range(12):
            (self.root / "aa_oa" / ("cell_%02d" % n) / "layout").mkdir(parents=True)
        target = self.write("zz_models/spectre/device.scs", self.model_source())
        data = self.discover(limits={"max_directories": 3})
        self.assertFalse(data["search_complete"])
        self.assertTrue(any(c["entry_path"] == str(target) for c in data["entry_candidates"]))
        self.assertEqual(data["traversal"]["directories_scanned"], 3)
        self.assertEqual(data["search_policy"]["directory_order"], "root_round_robin_preferred_subtree_bfs")
        self.assertTrue(any(s["path"] == str(target.parent) and s["qualified"] is False
                            for s in data["directory_suggestions"]))

    def test_root_round_robin_reaches_both_model_roots_with_same_small_budget(self):
        roots = [self.root / n for n in ("alpha", "beta", "gamma")]
        for root in roots:
            root.mkdir()
        for n in range(20):
            (roots[0] / "oa" / ("cell_%02d" % n) / "layout").mkdir(parents=True)
        targets = [self.write(name + "/models/spectre/device.scs", self.model_source())
                   for name in ("beta", "gamma")]
        self.roots = [str(r) for r in roots]
        data = self.discover(limits={"max_directories": 9})
        self.assertTrue({str(p) for p in targets} <= {c["entry_path"] for c in data["entry_candidates"]})
        self.assertEqual(data["traversal"]["directories_scanned"], 9)
        self.assertFalse(data["search_complete"])

    def test_normal_lane_is_breadth_first_without_model_name_prior(self):
        path = self.root / "alpha"
        for n in range(8):
            path /= "deep"
        path.mkdir(parents=True)
        target = self.write("zulu/short/device.scs", self.model_source())
        data = self.discover(limits={"max_directories": 5})
        self.assertTrue(any(c["entry_path"] == str(target) for c in data["entry_candidates"]))

    def test_overlapping_roots_are_deduplicated_without_broadening_scope(self):
        target = self.write("models/spectre/device.scs", self.model_source())
        self.roots = [str(self.root), str(target.parent), str(target.parent.parent)]
        data = self.discover()
        self.assertEqual(data["traversal"]["effective_roots"], [str(self.root)])
        self.assertEqual(data["traversal"]["overlapping_roots_removed"], 2)
        self.assertEqual(data["traversal"]["directories_scanned"], 3)
        self.assertEqual(len(data["files"]), 1)
        self.assertTrue(data["search_complete"])

    def test_exhausted_budget_returns_observed_unscanned_directory_suggestions(self):
        self.write("models/device.scs", self.model_source())
        data = self.discover(limits={"max_directories": 1})
        self.assertFalse(data["search_complete"])
        self.assertEqual(data["entry_candidates"], [])
        suggestion = data["directory_suggestions"][0]
        self.assertEqual(suggestion["path"], str(self.root / "models"))
        self.assertFalse(suggestion["scanned"])
        self.assertFalse(suggestion["qualified"])
        self.assertIn("unscanned_directory_frontier", suggestion["basis"])

    def test_observed_root_children_remain_available_under_existing_suggestion_cap(self):
        self.write("process_alpha/models/device.scs", self.model_source())
        for n in range(8):
            (self.root / "process_alpha" / ("models_%02d" % n) / "leaf").mkdir(parents=True)
        (self.root / "process_beta" / "ordinary").mkdir(parents=True)
        data = self.discover(limits={"max_candidates": 2})
        suggestions = data["directory_suggestions"]
        self.assertEqual(len(suggestions), 2)
        self.assertEqual({s["path"] for s in suggestions},
                         {str(self.root / "process_alpha"), str(self.root / "process_beta")})
        for row in suggestions:
            self.assertIn("observed_root_child", row["basis"])
            self.assertEqual(row["depth"], 1)
            self.assertTrue(Path(row["path"]).is_dir())
            self.assertFalse(row["qualified"])
        self.assertTrue(data["directory_suggestions_truncated"])

    def test_directory_snapshot_is_flushed_before_model_file_reads_and_stays_incomplete(self):
        self.write("models/device.scs", self.model_source())
        script = self.script()
        marker = "\nfor path in sorted(file_candidates[:limits['max_files']],key=priority):"
        self.assertEqual(script.count(marker), 1)
        directory_phase = script.split(marker, 1)[0] + "\n"
        class Output(io.StringIO):
            flushed = False
            def flush(self):
                self.flushed = True
                super().flush()
        output = Output()
        with contextlib.redirect_stdout(output), patch("os.open", side_effect=AssertionError("model files must not be opened")):
            exec(compile(directory_phase, "<directory-phase-only>", "exec"), {"__name__": "__main__"})
        self.assertTrue(output.flushed)
        line = output.getvalue()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertTrue(line.startswith(DIRECTORY_SNAPSHOT_PREFIX))
        self.assertLessEqual(len(line.encode()), MAX_DIRECTORY_SNAPSHOT_BYTES)
        snapshot = json.loads(line[len(DIRECTORY_SNAPSHOT_PREFIX):])
        self.assertEqual(snapshot["phase"], "directory_inventory")
        self.assertEqual(snapshot["status"], "incomplete")
        self.assertTrue(snapshot["snapshot"])
        self.assertTrue(snapshot["results_omitted"])
        self.assertFalse(snapshot["search_complete"])
        self.assertEqual(snapshot["summary"]["model_files_read"], 0)
        self.assertEqual(snapshot["summary"]["observed_candidate_file_count"], 1)
        for key in ("files", "declarations", "entry_candidates"):
            self.assertEqual(snapshot[key], [])
        self.assertEqual(snapshot["source_sha256"], "a" * 64)
        self.assertEqual(snapshot["required_masters"], ["example_a", "example_b"])
        self.assertEqual(snapshot["roots"], self.roots)
        self.assertFalse(snapshot["selection_made"])
        self.assertFalse(snapshot["profile_registered"])
        self.assertTrue(snapshot["directory_suggestions"])
        self.assertNotIn("sensitive_example_parameter", line)
        self.assertEqual(profile_suggestions(snapshot)["candidates"], [])

    def test_real_response_overflow_preserves_auditable_observed_directory_choices(self):
        names = ["node-%03d-" % n + "x" * 150 + ".scs" for n in range(120)]
        for n, name in enumerate(names):
            body = 'include "' + names[n + 1] + '"\n' if n + 1 < len(names) else self.model_source()
            self.write("models/" + name, body)
        (self.root / "unvisited").mkdir()
        data = self.discover(limits={"max_directories": 2})
        self.assertEqual(data["status"], "incomplete")
        self.assertTrue(data["results_omitted"])
        self.assertFalse(data["search_complete"])
        self.assertEqual(data["files"], [])
        self.assertEqual(data["entry_candidates"], [])
        self.assertEqual(data["summary"]["observed_file_count"], 120)
        self.assertEqual(data["summary"]["observed_entry_candidate_count"], 120)
        metrics = data["summary"]
        self.assertEqual(metrics["candidate_bytes"]["count"], 120)
        self.assertGreaterEqual(metrics["candidate_bytes"]["max"], metrics["candidate_bytes"]["min"])
        self.assertGreater(metrics["candidate_bytes"]["total"], 2000000)
        self.assertGreater(metrics["field_bytes"]["entry_candidates"], 2000000)
        self.assertGreater(metrics["candidate_field_bytes"]["dependency_scopes"], 0)
        self.assertGreater(metrics["full_payload_bytes"], metrics["compact_payload_bytes"])
        self.assertGreater(metrics["compact_payload_bytes"], 2000000)
        self.assertTrue(all(type(size) is int and size >= 0 for size in metrics["field_bytes"].values()))
        self.assertNotIn(str(self.root), json.dumps(metrics["field_bytes"]))
        self.assertGreater(data["unknown_count"], data["summary"]["observed_unknown_count"])
        self.assertIn("response_limit", {r["code"] for r in data["unknown"]})
        self.assertEqual(data["traversal"]["directories_scanned"], 2)
        self.assertTrue(data["directory_suggestions"])
        self.assertLessEqual(len(data["directory_suggestions"]), data["discovery_limits"]["max_candidates"])
        for row in data["directory_suggestions"]:
            self.assertTrue(Path(row["path"]).is_dir())
            self.assertTrue(Path(row["path"]).is_relative_to(self.root))
            self.assertFalse(row["qualified"])
        frontier = data["directory_suggestions"][0]
        self.assertFalse(frontier["scanned"])
        self.assertEqual(frontier["path"], str(self.root / "unvisited"))
        self.assertIn("unscanned_directory_frontier", frontier["basis"])
        self.assertLess(len(json.dumps(data, sort_keys=True).encode()), 2 * 1024 * 1024)
        self.assertEqual(profile_suggestions(data)["candidates"], [])

    def test_oversized_diagnostic_graph_compacts_without_losing_candidate_evidence(self):
        leaf = "leaf-" + "l" * 150 + ".inc"
        self.write("models/" + leaf, "// original empty diagnostic leaf\n")
        noise = "diagnostics-" + "n" * 140 + ".scs"
        sections = []
        for n in range(1000):
            name = "diagnostic_%04d_" % n + "q" * 95
            sections.append("section " + name + "\n" + ('include "' + leaf + '"\n') * 3 +
                            "endsection " + name + "\n")
        self.write("models/" + noise, "".join(sections))
        self.write("models/entry.scs", self.model_source() + 'include "absent.inc"\n')
        script = self.script()
        marker = "\nencoded=json.dumps(result,sort_keys=True,ensure_ascii=True)\n"
        self.assertEqual(script.count(marker), 1)
        full_script = script.split(marker, 1)[0] + "\nresult['discovery_id']=stable(result)\nprint(json.dumps(result,sort_keys=True))\n"
        output = subprocess.run([sys.executable, "-B", "-"], input=full_script, text=True,
                                capture_output=True, timeout=15, check=True)
        final_lines = [line for line in output.stdout.splitlines() if not line.startswith(DIRECTORY_SNAPSHOT_PREFIX)]
        full = json.loads(final_lines[0])
        self.assertGreater(len(json.dumps(full, sort_keys=True).encode()), 2000000)
        compact = self.discover()
        self.assertLess(len(json.dumps(compact, sort_keys=True).encode()), 2 * 1024 * 1024)
        self.assertEqual(compact["section_graph"], [])
        self.assertEqual(compact["includes"], [])
        counts = {row["field"]: row["observed_count"] for row in compact["detail_omissions"]}
        self.assertEqual(counts["section_graph"], len(full["section_graph"]))
        self.assertEqual(counts["includes"], len(full["includes"]))
        self.assertFalse(compact.get("results_omitted", False))
        self.assertTrue(compact["entry_candidates"])
        self.assertFalse(compact["search_complete"])
        for key in ("status", "search_complete", "files", "declarations", "entry_candidates",
                    "roots", "audit_limits", "discovery_limits", "unknown", "unknown_count", "ambiguities"):
            self.assertEqual(compact[key], full[key], key)
        for candidate in compact["entry_candidates"]:
            self.assertTrue(candidate["models"])
            self.assertTrue(candidate["dependency_scopes"])
            self.assertFalse(candidate["ready_to_run"])
        self.assertTrue(profile_suggestions(compact)["candidates"])

    def test_symlink_escape_does_not_read_outside_root(self):
        selected = self.root / "selected"
        selected.mkdir()
        outside = self.write("outside.scs", self.model_source())
        (selected / "link.scs").symlink_to(outside)
        self.roots = [str(selected)]
        data = self.discover()
        self.assertEqual(data["files"], [])
        self.assertFalse(data["search_complete"])

    def test_tmi_platforms_observed_never_chosen(self):
        self.write("models.scs", self.model_source() + '.option tmipath="tmi"\n')
        self.write("tmi/platformA/libTMImodel.so", b"\x00synthetic-a")
        self.write("tmi/platformB/libTMImodel.so", b"\x00synthetic-b")
        data = self.discover()
        candidate = data["entry_candidates"][0]
        self.assertEqual(len(candidate["tmi_candidates"][0]["platform_candidates"]), 2)
        with self.assertRaises(DomainError):
            audit_config_for_candidate(data, candidate["candidate_id"], sections=[None])
        with self.assertRaises(DomainError):
            audit_config_for_candidate(data, candidate["candidate_id"], sections=[None],
                                       tmi_relative_library="unobserved/libTMImodel.so")
        cfg = audit_config_for_candidate(data, candidate["candidate_id"], sections=[None],
                                         tmi_relative_library="platformA/libTMImodel.so")
        self.assertEqual(cfg["tmi_relative_library"], "platformA/libTMImodel.so")

    def test_repeated_tmi_declaration_retains_every_applied_scope_without_repeating_platform_arrays(self):
        entry = self.write("entry.scs", 'include "shared.scs" section=CornerA\ninclude "shared.scs" section=CornerB\n')
        shared = self.write("shared.scs", '.option tmipath="tmi"\n.option etmiUsrInput="tmi"\n' +
                            self.model_source() + 'section CornerA\n.option tmipath="tmi"\nendsection CornerA\n'
                            'section CornerB\nendsection CornerB\n')
        self.write("tmi/platformA/libTMImodel.so", b"\x00synthetic-library")
        data = self.discover()
        candidate = next(c for c in data["entry_candidates"] if c["entry_path"] == str(entry))
        groups = candidate["tmi_candidates"]
        self.assertEqual(len(groups), 1)
        refs = [dict(row, source_path=group["source_path"], directory=group["directory"],
                     platform_candidates=group["platform_candidates"])
                for group in groups for row in group["declarations"]]
        self.assertEqual(len(refs), 3)
        globals_ = [r for r in refs if r["section"] is None]
        self.assertEqual(len(globals_), 2)
        for row in globals_:
            self.assertEqual(set(row["applied_scopes"]), {"CornerA", "CornerB"})
            self.assertEqual(row["source_path"], str(shared))
            self.assertEqual(len(row["platform_candidates"]), 1)
        scoped = next(r for r in refs if r["section"] == "CornerA")
        self.assertEqual(scoped["applied_scopes"], ["CornerA"])
        shared_global = next(c for c in data["entry_candidates"] if c["entry_path"] == str(shared) and c["section_candidates"] == [None])
        self.assertTrue(all(r["applied_scopes"] == [None]
                            for group in shared_global["tmi_candidates"] for r in group["declarations"]))
        config = audit_config_for_candidate(data, candidate["candidate_id"], sections=[None],
                                            tmi_relative_library="platformA/libTMImodel.so")
        self.assertEqual(config["tmi_relative_library"], "platformA/libTMImodel.so")

    def test_tmi_common_evidence_grouping_is_lossless_and_materially_smaller(self):
        name = "model-source-" + "m" * 125 + ".scs"
        sections = ["Corner%02d" % n for n in range(32)]
        entry = self.write("entry.scs", "".join('include "' + name + '" section=' + section + '\n' for section in sections))
        body = self.model_source()
        expected = set()
        for section in sections:
            body += "section " + section + "\n"
            for attribute in ("tmipath", "etmiUsrInput"):
                line = len(body.splitlines()) + 1
                body += '.option ' + attribute + '="tmi"\n'
                expected.add((line, section, attribute, section))
            body += "endsection " + section + "\n"
        source = self.write(name, body)
        for platform in ("platformA", "platformB"):
            self.write("tmi/" + platform + "/libTMImodel.so", b"\x00synthetic-library-" + platform.encode())
        data = self.discover()
        candidate = next(c for c in data["entry_candidates"] if c["entry_path"] == str(entry))
        groups = candidate["tmi_candidates"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["source_path"], str(source))
        self.assertEqual(len(groups[0]["declarations"]), 64)
        expanded = [dict(declaration, source_path=group["source_path"], directory=group["directory"],
                         platform_candidates=group["platform_candidates"])
                    for group in groups for declaration in group["declarations"]]
        actual = {(row["line"], row["section"], row["attribute"], section)
                  for row in expanded for section in row["applied_scopes"]}
        self.assertEqual(actual, expected)
        self.assertEqual(len(expanded), len({(r["source_path"], r["line"], r["section"], r["attribute"]) for r in expanded}))
        self.assertLess(len(json.dumps(groups).encode()), len(json.dumps(expanded).encode()) // 4)
        self.assertEqual(len(data["entry_candidates"]), 34)
        self.assertTrue(all(m["confidence"] == "verified" for m in candidate["models"]))
        self.assertFalse(candidate["ready_to_run"])
        self.assertTrue(profile_suggestions(data)["candidates"])
        config = audit_config_for_candidate(data, candidate["candidate_id"], sections=[None],
                                            tmi_relative_library="platformA/libTMImodel.so")
        self.assertEqual(config["main"], str(entry))
        self.assertEqual(config["tmi_relative_library"], "platformA/libTMImodel.so")

    def test_budget_caps_and_read_limit_surface_incompleteness(self):
        for limits in ({"max_files": 257}, {"max_logical_line_bytes": 5000000},
                       {"max_candidates": 257}, {"max_depth": True}):
            with self.assertRaises(DomainError):
                self.script(limits=limits)
        self.write("models.scs", self.model_source())
        data = self.discover(limits={"max_file_bytes": 10})
        self.assertFalse(data["search_complete"])
        self.assertEqual(data["entry_candidates"], [])

    def test_receipt_tampering_and_inert_search_hint(self):
        self.write("models.scs", self.model_source())
        data = self.discover(search_hints=["$(touch forbidden)"])
        self.assertFalse((self.root / "forbidden").exists())
        before = data["discovery_id"]
        other = self.discover(limits={"max_depth": 2})
        self.assertNotEqual(before, other["discovery_id"])
        data["entry_candidates"][0]["section_candidates"] = ["invented"]
        with self.assertRaises(DomainError):
            profile_suggestions(data)

    def test_script_build_is_pure_and_python27_grammar_compatible(self):
        with patch("builtins.open", side_effect=AssertionError("unexpected IO")):
            script = self.script()
            compile(script, "generated-discovery.py", "exec")
        try:
            from lib2to3.refactor import RefactoringTool
        except ImportError:
            self.skipTest("Python 2 grammar checker unavailable")
        RefactoringTool([]).refactor_string(script, "generated-discovery.py")


if __name__ == "__main__":
    unittest.main()
