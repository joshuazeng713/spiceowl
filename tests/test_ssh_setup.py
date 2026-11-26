"""Cross-platform offline unit tests for SSH Setup Wizard core library, UI, and HTTP endpoints."""

from __future__ import annotations

import base64
import http.client
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from spiceowl._platform import IS_WINDOWS
from spiceowl.domain.common import DomainError
from spiceowl.onboarding.ssh_discovery import discover_ssh_hosts
from spiceowl.onboarding import ssh_setup
from spiceowl.onboarding.ssh_setup import (
    INCLUDE_LINE,
    MANAGED_FILENAME,
    MANAGED_KEY_NAME,
    delete_managed_host,
    deploy_public_key,
    diagnose_connection,
    ensure_managed_key,
    list_all_ssh_aliases,
    list_local_keys,
    list_managed_hosts,
    public_key_fingerprint,
    scan_host_key,
    trust_host_keys,
    upsert_managed_host,
    validate_hostname,
    validate_identity_file,
    validate_known_host_entry,
    validate_port,
    validate_proxy_jump,
    validate_public_key,
    validate_username,
)
from spiceowl.onboarding.ssh_setup_ui import (
    SSH_SETUP_ACTIONS,
    build_ssh_setup_state,
    dispatch_ssh_setup_action,
    ssh_setup_csp,
    ssh_setup_html,
)
from spiceowl.onboarding_wizard import parse_ssh_config_aliases
from spiceowl.service import Controller
from spiceowl.web.server import make_server

# Deterministic synthetic Ed25519 public key material for offline tests (not a real credential)
_FAKE_ED25519_BLOB = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + b"A" * 32).decode("ascii")
_FAKE_PUBKEY = f"ssh-ed25519 {_FAKE_ED25519_BLOB} spiceowl-test"
_FAKE_ED25519_BLOB_2 = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + b"B" * 32).decode("ascii")
_FAKE_PUBKEY_2 = f"ssh-ed25519 {_FAKE_ED25519_BLOB_2} host-key-2"


class TestSshSetupValidation(unittest.TestCase):
    def test_strict_validators_reject_injection_and_wildcards(self) -> None:
        self.assertEqual(validate_hostname("sim-node-01.lab.internal"), "sim-node-01.lab.internal")
        self.assertEqual(validate_hostname("10.20.30.40"), "10.20.30.40")
        for bad in ("", "-oProxyCommand=id", "host name", "host;id", "*.lab", "a..b", "999.1.1.1", "fe80::1"):
            with self.assertRaises(DomainError, msg=f"hostname should reject {bad!r}"):
                validate_hostname(bad)

        self.assertEqual(validate_port(22), 22)
        self.assertEqual(validate_port("2222"), 2222)
        self.assertEqual(validate_port(None), 22)
        for bad in (0, -1, 65536, True, "abc", "22;id"):
            with self.assertRaises(DomainError, msg=f"port should reject {bad!r}"):
                validate_port(bad)

        self.assertEqual(validate_username("alice_01"), "alice_01")
        for bad in ("", "-root", "a b", "root;id", "../etc", "1abc"):
            with self.assertRaises(DomainError, msg=f"user should reject {bad!r}"):
                validate_username(bad)

        self.assertEqual(validate_proxy_jump(""), "")
        self.assertEqual(validate_proxy_jump("bastion"), "bastion")
        self.assertEqual(validate_proxy_jump("jump1, ops@10.0.0.1:2222, jump2"), "jump1,ops@10.0.0.1:2222,jump2")
        for bad in ("-oProxyCommand=x", "a,,b", "a b", "jump*", "a,b,c,d,e,f,g,h,i"):
            with self.assertRaises(DomainError, msg=f"proxy_jump should reject {bad!r}"):
                validate_proxy_jump(bad)
        with self.assertRaises(DomainError):
            validate_proxy_jump("eda-01", self_alias="eda-01")

        self.assertEqual(validate_identity_file("~/.ssh/id_ed25519_spiceowl"), "~/.ssh/id_ed25519_spiceowl")
        self.assertEqual(validate_identity_file("id_ed25519_spiceowl"), "~/.ssh/id_ed25519_spiceowl")
        for bad in ("~/.ssh/../etc/passwd", "~/.ssh/id_ed25519.pub", "-i", "~/.ssh/a b", "relative/path"):
            with self.assertRaises(DomainError, msg=f"identity_file should reject {bad!r}"):
                validate_identity_file(bad)

        self.assertEqual(validate_public_key(_FAKE_PUBKEY), _FAKE_PUBKEY)
        for bad in ("", "ssh-ed25519 !!!notbase64!!!", f"{_FAKE_PUBKEY}\nsecond-line", "bad-type " + _FAKE_ED25519_BLOB):
            with self.assertRaises(DomainError, msg=f"public_key should reject {bad!r}"):
                validate_public_key(bad)

        pat, ktype, blob, norm = validate_known_host_entry(f"[10.0.0.5]:2222 ssh-ed25519 {_FAKE_ED25519_BLOB}")
        self.assertEqual((pat, ktype, blob), ("[10.0.0.5]:2222", "ssh-ed25519", _FAKE_ED25519_BLOB))
        self.assertEqual(norm, f"[10.0.0.5]:2222 ssh-ed25519 {_FAKE_ED25519_BLOB}")
        for bad in ("# comment", "bad host line", f"host;id ssh-ed25519 {_FAKE_ED25519_BLOB}"):
            with self.assertRaises(DomainError):
                validate_known_host_entry(bad)


class TestSshManagedConfigAndKeys(unittest.TestCase):
    def test_managed_hosts_preserve_existing_config_and_integrate_with_discovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-cfg-") as tmp:
            fake_home = Path(tmp) / "home"
            ssh_dir = fake_home / ".ssh"
            ssh_dir.mkdir(parents=True)
            original_user_cfg = (
                "# Existing user config\n"
                "Host lab-bastion\n"
                "  HostName bastion.example.invalid\n"
                "  User researcher\n"
                "  Port 2200\n"
            )
            (ssh_dir / "config").write_text(original_user_cfg, encoding="utf-8")

            res1 = upsert_managed_host(ssh_dir, {
                "alias": "eda-sim-01",
                "hostname": "10.0.1.25",
                "port": 2222,
                "user": "alice",
                "proxy_jump": "lab-bastion",
                "identity_file": f"~/.ssh/{MANAGED_KEY_NAME}",
            })
            self.assertTrue(res1["created"])
            self.assertEqual(res1["host"]["port"], 2222)
            self.assertEqual(res1["host"]["proxy_jump"], "lab-bastion")

            # Existing user config lines must be preserved verbatim, with Include at the top
            cfg_text = (ssh_dir / "config").read_text(encoding="utf-8")
            self.assertIn(INCLUDE_LINE, cfg_text)
            self.assertLess(cfg_text.index(INCLUDE_LINE), cfg_text.index("Host lab-bastion"))
            self.assertTrue(cfg_text.endswith(original_user_cfg))

            # Managed file permissions on POSIX
            managed_path = ssh_dir / MANAGED_FILENAME
            self.assertTrue(managed_path.is_file())
            if not IS_WINDOWS:
                self.assertEqual(stat.S_IMODE(managed_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((ssh_dir / "config").stat().st_mode), 0o600)

            # Upsert second host & update first host (no duplicate Include lines!)
            upsert_managed_host(ssh_dir, {
                "alias": "eda-sim-02",
                "hostname": "10.0.1.26",
                "user": "bob",
                "proxy_jump": "lab-bastion,eda-sim-01",
            })
            res_upd = upsert_managed_host(ssh_dir, {
                "alias": "eda-sim-01",
                "hostname": "10.0.1.99",
                "port": 22,
                "user": "alice",
                "proxy_jump": "lab-bastion",
                "identity_file": f"~/.ssh/{MANAGED_KEY_NAME}",
            })
            self.assertFalse(res_upd["created"])
            self.assertEqual((ssh_dir / "config").read_text(encoding="utf-8").count(INCLUDE_LINE), 1)

            managed = list_managed_hosts(ssh_dir)
            self.assertEqual([h["alias"] for h in managed], ["eda-sim-01", "eda-sim-02"])
            self.assertEqual(managed[0]["hostname"], "10.0.1.99")

            # Compatible with ssh_discovery.discover_ssh_hosts and onboarding_wizard.parse_ssh_config_aliases
            disc = discover_ssh_hosts(ssh_dir / "config", user_home=fake_home)
            disc_aliases = [h["alias"] for h in disc["hosts"]]
            self.assertEqual(disc_aliases, ["eda-sim-01", "eda-sim-02", "lab-bastion"])

            wiz_aliases = {a["alias"]: a for a in parse_ssh_config_aliases(ssh_dir / "config")}
            self.assertIn("lab-bastion", wiz_aliases)
            self.assertIn("eda-sim-01", wiz_aliases)
            self.assertEqual(wiz_aliases["eda-sim-01"]["proxy_jump"], "lab-bastion")

            all_info = list_all_ssh_aliases(ssh_dir)
            self.assertEqual(all_info["existing_aliases"], ["eda-sim-01", "eda-sim-02", "lab-bastion"])
            self.assertEqual(all_info["unmanaged_aliases"], ["lab-bastion"])

            # Delete managed host leaves user host intact
            del_res = delete_managed_host(ssh_dir, "eda-sim-02")
            self.assertTrue(del_res["deleted"])
            self.assertEqual([h["alias"] for h in list_managed_hosts(ssh_dir)], ["eda-sim-01"])
            self.assertIn("Host lab-bastion", (ssh_dir / "config").read_text(encoding="utf-8"))

    def test_ensure_managed_key_and_list_local_keys_never_read_private_key(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-key-") as tmp:
            ssh_dir = Path(tmp) / ".ssh"
            ssh_dir.mkdir(parents=True)

            def fake_run(argv, **_kwargs):
                # Emulate ssh-keygen writing private + .pub files
                self.assertIn("-t", argv)
                self.assertIn("ed25519", argv)
                f_idx = argv.index("-f")
                priv = Path(argv[f_idx + 1])
                priv.write_text("PRIVATE-KEY-SENTINEL-DO-NOT-READ\n", encoding="utf-8")
                Path(str(priv) + ".pub").write_text(_FAKE_PUBKEY + "\n", encoding="utf-8")
                return 0, b"", b""

            with patch.object(ssh_setup, "_run_bounded", side_effect=fake_run):
                created = ensure_managed_key(ssh_dir)
            self.assertTrue(created["created"])
            self.assertEqual(created["name"], MANAGED_KEY_NAME)
            self.assertEqual(created["public_key"], _FAKE_PUBKEY)
            self.assertTrue(created["fingerprint"].startswith("SHA256:"))
            self.assertEqual(created["fingerprint"], public_key_fingerprint(_FAKE_PUBKEY))

            # Second call is idempotent without invoking ssh-keygen
            with patch.object(ssh_setup, "_run_bounded", side_effect=AssertionError("should not invoke ssh-keygen")):
                existing = ensure_managed_key(ssh_dir)
            self.assertFalse(existing["created"])
            self.assertEqual(existing["fingerprint"], created["fingerprint"])

            # list_local_keys must never read private key contents
            keys = list_local_keys(ssh_dir)
            self.assertEqual(len(keys), 1)
            self.assertTrue(keys[0]["managed"])
            self.assertTrue(keys[0]["has_private"])
            self.assertNotIn("PRIVATE-KEY-SENTINEL", json.dumps(keys))


class TestSshHostKeysDeployAndDiagnostics(unittest.TestCase):
    def test_scan_host_key_direct_and_multihop_and_trust_dedupe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-kh-") as tmp:
            ssh_dir = Path(tmp) / ".ssh"
            ssh_dir.mkdir(parents=True)

            calls: list[list[str]] = []

            def fake_run(argv, **_kwargs):
                calls.append(list(argv))
                return 0, f"10.0.1.25 ssh-ed25519 {_FAKE_ED25519_BLOB}\n".encode("utf-8"), b""

            with patch.object(ssh_setup, "_run_bounded", side_effect=fake_run):
                direct = scan_host_key("10.0.1.25", 2222)
            self.assertEqual(len(direct["entries"]), 1)
            self.assertTrue(direct["entries"][0].startswith("[10.0.1.25]:2222 ssh-ed25519 "))
            self.assertIn("-p", calls[0])
            self.assertIn("2222", calls[0])

            with patch.object(ssh_setup, "_run_bounded", side_effect=fake_run):
                jumped = scan_host_key("10.0.1.25", 22, jump_alias="jump1,jump2")
            self.assertEqual(jumped["jump_alias"], "jump1,jump2")
            self.assertIn("-J", calls[1])
            self.assertIn("jump1", calls[1])
            self.assertIn("jump2", calls[1])

            # Trust deduplicates atomically
            t1 = trust_host_keys(ssh_dir, direct["entries"])
            self.assertEqual((t1["added"], t1["total"]), (1, 1))
            t2 = trust_host_keys(ssh_dir, direct["entries"] + [f"10.0.1.26 ssh-ed25519 {_FAKE_ED25519_BLOB_2}"])
            self.assertEqual((t2["added"], t2["total"]), (1, 2))
            if not IS_WINDOWS:
                self.assertEqual(stat.S_IMODE((ssh_dir / "known_hosts").stat().st_mode), 0o600)

    def test_deploy_public_key_ephemeral_askpass_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-dep-") as tmp:
            ssh_dir = Path(tmp) / ".ssh"
            ssh_dir.mkdir(parents=True)
            captured: dict = {}

            def fake_run(argv, *, stdin_bytes=None, env=None, **_kwargs):
                captured["argv"] = list(argv)
                captured["stdin"] = (stdin_bytes or b"").decode("utf-8")
                captured["askpass"] = Path(env["SSH_ASKPASS"])
                captured["askpass_exists_during_run"] = captured["askpass"].is_file()
                captured["askpass_require"] = env.get("SSH_ASKPASS_REQUIRE")
                return 0, b"SPICEOWL_KEY_DEPLOYED\n", b""

            secret_pw = "OneTimeSecret!9876"
            with patch.object(ssh_setup, "_run_bounded", side_effect=fake_run):
                res = deploy_public_key(
                    ssh_dir=ssh_dir,
                    hostname="10.0.1.25",
                    port=22,
                    username="alice",
                    public_key=_FAKE_PUBKEY,
                    password=secret_pw,
                    proxy_jump="bastion",
                )
            self.assertTrue(res["ok"])
            self.assertEqual(res["code"], "deployed")
            self.assertTrue(captured["askpass_exists_during_run"])
            self.assertEqual(captured["askpass_require"], "force")
            # Ephemeral askpass temp directory MUST be deleted immediately in finally
            self.assertFalse(captured["askpass"].exists())
            self.assertFalse(captured["askpass"].parent.exists())
            # Password never appears in argv, stdin, or return payload
            self.assertNotIn(secret_pw, " ".join(captured["argv"]))
            self.assertNotIn(secret_pw, captured["stdin"])
            self.assertNotIn(secret_pw, json.dumps(res))
            # Base64 key sent via stdin, never raw key in argv
            self.assertIn(base64.b64encode(_FAKE_PUBKEY.encode("utf-8")).decode("ascii"), captured["stdin"])

            # Windows askpass helper generation check (cross-platform without running cmd.exe)
            with tempfile.TemporaryDirectory(prefix="spiceowl-win-ap-") as wtmp, patch.object(ssh_setup, "IS_WINDOWS", True):
                helper = ssh_setup._create_askpass_helper(Path(wtmp), "win-secret")
                self.assertEqual(helper.suffix, ".cmd")
                self.assertIn("askpass.py", helper.read_text(encoding="utf-8"))

    def test_diagnose_connection_staged_outcomes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-diag-") as tmp:
            ssh_dir = Path(tmp) / ".ssh"
            upsert_managed_host(ssh_dir, {
                "alias": "eda-direct",
                "hostname": "10.0.1.25",
                "port": 22,
                "user": "alice",
            })
            upsert_managed_host(ssh_dir, {
                "alias": "eda-jumped",
                "hostname": "10.0.2.30",
                "port": 22,
                "user": "alice",
                "proxy_jump": "10.0.0.1:2222",
            })

            # 1. Network unreachable on TCP
            with patch.object(ssh_setup, "_tcp_probe", return_value=(False, "timeout")):
                r_net = diagnose_connection(ssh_dir, "eda-direct")
            self.assertFalse(r_net["ok"])
            self.assertEqual(r_net["code"], "network_unreachable")
            self.assertEqual(r_net["steps"][0]["id"], "target_tcp")

            # 2. Need host key
            with patch.object(ssh_setup, "_tcp_probe", return_value=(True, "ok")), \
                    patch.object(ssh_setup, "_run_bounded", return_value=(255, b"", b"Host key verification failed.\r\n")):
                r_hk = diagnose_connection(ssh_dir, "eda-direct")
            self.assertEqual(r_hk["code"], "need_host_key")

            # 3. Need key deploy
            with patch.object(ssh_setup, "_tcp_probe", return_value=(True, "ok")), \
                    patch.object(ssh_setup, "_run_bounded", return_value=(255, b"", b"Permission denied (publickey).\r\n")):
                r_kd = diagnose_connection(ssh_dir, "eda-direct")
            self.assertEqual(r_kd["code"], "need_key_deploy")

            # 4. Jump host + full ready (verifies csh/tcsh-safe `/bin/sh -s` + stdin script!)
            ssh_calls: list[tuple[list[str], bytes | None]] = []
            ssh_replies = [
                (0, b"", b""),
                (0, b"USER=alice\nHOST=sim-02\nPY3=/usr/bin/python3\nPY=\nSPECTRE=/opt/cadence/bin/spectre\nNGSPICE=\n", b""),
            ]
            def fake_diag_run(argv, *, stdin_bytes=None, **_kwargs):
                ssh_calls.append((list(argv), stdin_bytes))
                return ssh_replies[len(ssh_calls) - 1]
            with patch.object(ssh_setup, "_tcp_probe", return_value=(True, "ok")), \
                    patch.object(ssh_setup, "_run_bounded", side_effect=fake_diag_run):
                r_ok = diagnose_connection(ssh_dir, "eda-jumped")
            self.assertTrue(r_ok["ok"])
            self.assertEqual(r_ok["code"], "ready")
            self.assertEqual(r_ok["remote"]["hostname"], "sim-02")
            self.assertEqual(r_ok["remote"]["spectre"], "/opt/cadence/bin/spectre")
            self.assertEqual(ssh_calls[1][0][-1], "/bin/sh -s")
            self.assertIn(b"id -un", ssh_calls[1][1] or b"")
            self.assertEqual([s["id"] for s in r_ok["steps"]], ["jump_tcp", "jump_auth", "target_auth", "remote_python", "remote_simulators"])

            # 5. Missing python
            with patch.object(ssh_setup, "_tcp_probe", return_value=(True, "ok")), \
                    patch.object(ssh_setup, "_run_bounded", return_value=(0, b"USER=alice\nHOST=sim-01\nPY3=\nPY=\nSPECTRE=\nNGSPICE=\n", b"")):
                r_py = diagnose_connection(ssh_dir, "eda-direct")
            self.assertFalse(r_py["ok"])
            self.assertEqual(r_py["code"], "missing_python")

            # 6. Legacy OpenSSH 5.3 (ssh-rsa only) on jump host reports failing_hop + needs_legacy_rsa
            legacy_err = b"Unable to negotiate with 10.0.0.1 port 2222: no matching host key type found. Their offer: ssh-rsa,ssh-dss\r\n"
            with patch.object(ssh_setup, "_tcp_probe", return_value=(True, "ok")), \
                    patch.object(ssh_setup, "_run_bounded", return_value=(255, b"", legacy_err)):
                r_leg = diagnose_connection(ssh_dir, "eda-jumped")
            self.assertFalse(r_leg["ok"])
            self.assertEqual(r_leg["code"], "legacy_ssh_negotiation")
            self.assertEqual(r_leg["failing_hop"]["role"], "jump")
            self.assertTrue(r_leg["failing_hop"]["needs_legacy_rsa"])

            # Upsert with legacy_rsa=True writes HostKeyAlgorithms +ssh-rsa and parses back cleanly
            upsert_managed_host(ssh_dir, {
                "alias": "jump-legacy",
                "hostname": "10.0.0.1",
                "user": "alice",
                "legacy_rsa": True,
                "legacy_kex": True,
            })
            managed_map = {h["alias"]: h for h in list_managed_hosts(ssh_dir)}
            self.assertTrue(managed_map["jump-legacy"]["legacy_rsa"])
            self.assertTrue(managed_map["jump-legacy"]["legacy_kex"])
            conf_text = (ssh_dir / MANAGED_FILENAME).read_text(encoding="utf-8")
            self.assertIn("HostKeyAlgorithms +ssh-rsa", conf_text)
            self.assertIn("PubkeyAcceptedAlgorithms +ssh-rsa", conf_text)

            # ensure_managed_key(key_type="rsa") generates 4096-bit RSA key
            def fake_rsa_keygen(argv, **_kwargs):
                self.assertIn("rsa", argv)
                self.assertIn("4096", argv)
                priv = Path(argv[argv.index("-f") + 1])
                priv.write_text("FAKE-RSA-PRIV\n", encoding="utf-8")
                Path(str(priv) + ".pub").write_text(f"ssh-rsa {_FAKE_ED25519_BLOB} spiceowl-local\n", encoding="utf-8")
                return 0, b"", b""

            with patch.object(ssh_setup, "_run_bounded", side_effect=fake_rsa_keygen):
                rsa_k = ensure_managed_key(ssh_dir, key_type="rsa")
            self.assertEqual(rsa_k["name"], ssh_setup.MANAGED_RSA_KEY_NAME)
            self.assertEqual(rsa_k["key_type"], "ssh-rsa")

            # deploy_public_key(legacy_rsa=True) passes +ssh-rsa options
            dep_argv = []
            def fake_dep(argv, **_kwargs):
                dep_argv.extend(argv)
                return 0, b"SPICEOWL_KEY_DEPLOYED\n", b""

            with patch.object(ssh_setup, "_run_bounded", side_effect=fake_dep):
                dep_res = deploy_public_key(
                    ssh_dir=ssh_dir, hostname="10.0.0.1", port=22, username="alice",
                    public_key=rsa_k["public_key"], password="temp-pw-123", legacy_rsa=True,
                )
            self.assertTrue(dep_res["ok"])
            self.assertIn("HostKeyAlgorithms=+ssh-rsa", dep_argv)
            self.assertIn("PubkeyAcceptedAlgorithms=+ssh-rsa", dep_argv)


class TestSshSetupUiAndServerRoutes(unittest.TestCase):
    def test_reusable_html_csp_and_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-ui-") as tmp:
            ssh_dir = Path(tmp) / ".ssh"
            state = build_ssh_setup_state(ssh_dir)
            nonce = "AbCdEf1234567890"
            csp = ssh_setup_csp(nonce)
            self.assertIn(f"script-src 'nonce-{nonce}'", csp)
            self.assertIn(f"style-src 'nonce-{nonce}'", csp)
            self.assertIn("default-src 'none'", csp)

            html = ssh_setup_html(
                "https://circuitgit-dev.github.io",
                "req-1",
                "csrf-1",
                state,
                nonce,
                brand="CircuitGit",
                action_path="/ssh/setup/action",
                post_message_type="circuitgit.ssh.updated",
            ).decode("utf-8")
            self.assertIn("CircuitGit", html)
            self.assertIn(f'nonce="{nonce}"', html)
            self.assertIn("circuitgit.ssh.updated", html)
            self.assertNotIn("onclick=", html)
            # Public open-source UI must never embed private lab hostnames, IPs, or personal usernames
            self.assertIsNone(re.search(r"\bthu-[a-z]+\b", html))
            self.assertNotIn("101.6.68.50", html)
            self.assertNotIn("zengsy", html)
            self.assertEqual(
                SSH_SETUP_ACTIONS,
                frozenset({"state", "ensure_key", "delete_key", "upsert_host", "delete_host", "scan_host_key", "trust_host_keys", "deploy_key", "diagnose"}),
            )

            # Safe key deletion removes keypair, clears dangling host IdentityFile, and protects config/known_hosts
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "config").write_text("Host keep\n  HostName 1.2.3.4\n", encoding="utf-8")
            with self.assertRaises(DomainError):
                dispatch_ssh_setup_action(ssh_dir, "delete_key", {"name": "config"})
            (ssh_dir / "id_test_clean").write_text("PRIVATE-KEY\n", encoding="utf-8")
            (ssh_dir / "id_test_clean.pub").write_text(_FAKE_PUBKEY + "\n", encoding="utf-8")
            dispatch_ssh_setup_action(ssh_dir, "upsert_host", {
                "alias": "host-using-key",
                "hostname": "10.0.9.8",
                "user": "designer",
                "identity_file": str(ssh_dir / "id_test_clean"),
            })
            del_k = dispatch_ssh_setup_action(ssh_dir, "delete_key", {"name": "id_test_clean"})
            self.assertTrue(del_k["deleted"])
            self.assertEqual(del_k["cleared_hosts"], ["host-using-key"])
            self.assertFalse((ssh_dir / "id_test_clean").exists())
            self.assertFalse((ssh_dir / "id_test_clean.pub").exists())
            self.assertEqual(list_managed_hosts(ssh_dir)[0]["identity_file"], "")

            res = dispatch_ssh_setup_action(ssh_dir, "upsert_host", {
                "alias": "cg-eda-01",
                "hostname": "10.0.9.9",
                "user": "designer",
            })
            self.assertTrue(res["ok"])
            self.assertIn("cg-eda-01", res["state"]["existing_aliases"])
            with self.assertRaises(DomainError):
                dispatch_ssh_setup_action(ssh_dir, "evil_action", {})

    def test_server_ssh_setup_endpoints_and_security_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spiceowl-ssh-srv-") as tmp:
            data_dir = Path(tmp) / "data"
            ssh_dir = Path(tmp) / ".ssh"
            ctrl = Controller(data_dir, agent_mode="standalone")
            srv = make_server(ctrl, port=0, enable_direct_exec=True, ssh_dir=ssh_dir)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                port = srv.server_address[1]

                def raw_req(method: str, path: str, headers: dict | None = None, body: dict | None = None):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    hdrs = dict(headers or {})
                    payload = None
                    if body is not None:
                        hdrs["Content-Type"] = "application/json"
                        payload = json.dumps(body).encode("utf-8")
                    conn.request(method, path, body=payload, headers=hdrs)
                    resp = conn.getresponse()
                    raw_body = resp.read()
                    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                    conn.close()
                    return resp.status, resp_headers, raw_body

                # Foreign origin rejected
                self.assertEqual(raw_req("GET", "/ssh/setup?origin=https://evil.example")[0], 403)
                self.assertEqual(
                    raw_req("POST", "/ssh/setup/action", {"Origin": "https://spiceowl-dev.github.io"}, {"action": "state"})[0],
                    403,
                )

                # Allowed official origin opens local loopback wizard with strict nonce CSP
                st, hdrs, html_bytes = raw_req("GET", "/ssh/setup?origin=https://spiceowl-dev.github.io")
                self.assertEqual(st, 200)
                self.assertEqual(hdrs.get("cache-control"), "no-store")
                csp = hdrs.get("content-security-policy", "")
                self.assertIn("default-src 'none'", csp)
                self.assertIn("script-src 'nonce-", csp)
                html = html_bytes.decode("utf-8")
                m = re.search(r"const BOOT = (\{.*?\});", html, re.S)
                self.assertIsNotNone(m)
                boot = json.loads(m.group(1))

                # Bad CSRF rejected
                self.assertEqual(
                    raw_req(
                        "POST",
                        "/ssh/setup/action",
                        {"Origin": f"http://127.0.0.1:{port}"},
                        {"request_id": boot["requestId"], "csrf": "wrong-csrf", "action": "state", "payload": {}},
                    )[0],
                    403,
                )

                # Valid CSRF upserts host and immediately appears in /api/wizard/status
                st, _, out_bytes = raw_req(
                    "POST",
                    "/ssh/setup/action",
                    {"Origin": f"http://127.0.0.1:{port}"},
                    {
                        "request_id": boot["requestId"],
                        "csrf": boot["csrf"],
                        "action": "upsert_host",
                        "payload": {"alias": "eda-lab-1", "hostname": "10.0.8.8", "user": "edauser"},
                    },
                )
                self.assertEqual(st, 200)
                body = json.loads(out_bytes.decode("utf-8"))
                self.assertTrue(body["ok"])
                self.assertIn("eda-lab-1", body["state"]["existing_aliases"])

                st, _, wiz_bytes = raw_req("GET", "/api/wizard/status")
                self.assertEqual(st, 200)
                wiz = json.loads(wiz_bytes.decode("utf-8"))
                self.assertIn("eda-lab-1", [a["alias"] for a in wiz["ssh_aliases"]])

                # Quick-connect fails cleanly when host is unreachable without requiring 6-field form
                with patch.object(ssh_setup, "_tcp_probe", return_value=(False, "timeout")):
                    st_fail, _, qc_fail_bytes = raw_req(
                        "POST",
                        "/api/wizard/quick-connect",
                        {"Origin": f"http://127.0.0.1:{port}", "X-SpiceOwl-Token": json.loads(raw_req("GET", "/api/state")[2].decode("utf-8"))["csrf_token"]},
                        {"alias": "eda-lab-1"},
                    )
                self.assertEqual(st_fail, 200)
                qc_fail = json.loads(qc_fail_bytes.decode("utf-8"))
                self.assertFalse(qc_fail["ok"])
                self.assertEqual(qc_fail["diagnosis"]["code"], "network_unreachable")

                # Quick-connect succeeds and auto-registers host with discovered hostname & username
                fake_diag = {
                    "ok": True,
                    "alias": "eda-lab-1",
                    "code": "ready",
                    "steps": [{"id": "target_auth", "label": "ok", "ok": True, "detail": "ok"}],
                    "remote": {"username": "edauser", "hostname": "sim-node-08", "python3": "/usr/bin/python3", "python": None, "spectre": "/opt/cadence/bin/spectre", "ngspice": None},
                }
                csrf_tok = json.loads(raw_req("GET", "/api/state")[2].decode("utf-8"))["csrf_token"]
                with patch("spiceowl.onboarding.ssh_setup.diagnose_connection", return_value=fake_diag), \
                        patch.object(ctrl.hosts, "probe", return_value={"identity": {"verified": True, "actual_host": "sim-node-08", "actual_user": "edauser"}, "tools": []}):
                    st_ok, _, qc_ok_bytes = raw_req(
                        "POST",
                        "/api/wizard/quick-connect",
                        {"Origin": f"http://127.0.0.1:{port}", "X-SpiceOwl-Token": csrf_tok},
                        {"alias": "eda-lab-1"},
                    )
                self.assertEqual(st_ok, 200)
                qc_ok = json.loads(qc_ok_bytes.decode("utf-8"))
                self.assertTrue(qc_ok["ok"])
                self.assertEqual(qc_ok["host"]["host_id"], "eda-lab-1")
                self.assertEqual(qc_ok["host"]["expected_host"], "sim-node-08")
                self.assertEqual(qc_ok["host"]["expected_user"], "edauser")

                # Mark host verified in store and confirm /api/wizard/status annotates verified=True
                ctrl.hosts.store.update(
                    "host",
                    "eda-lab-1",
                    lambda cur: cur | {"last_probe": {"transport": "reachable", "identity": {"verified": True}, "tools": []}},
                )
                st_w2, _, wiz2_bytes = raw_req("GET", "/api/wizard/status")
                self.assertEqual(st_w2, 200)
                wiz2 = json.loads(wiz2_bytes.decode("utf-8"))
                self.assertEqual(wiz2["workspace"]["verified_host_count"], 1)
                self.assertTrue(next(a for a in wiz2["ssh_aliases"] if a["alias"] == "eda-lab-1")["verified"])

                # Deleting the managed SSH host via wizard also removes its registered host so no phantom "1 台已验证" remains
                st_del, _, del_bytes = raw_req(
                    "POST",
                    "/ssh/setup/action",
                    {"Origin": f"http://127.0.0.1:{port}"},
                    {
                        "request_id": boot["requestId"],
                        "csrf": boot["csrf"],
                        "action": "delete_host",
                        "payload": {"alias": "eda-lab-1"},
                    },
                )
                self.assertEqual(st_del, 200)
                del_res = json.loads(del_bytes.decode("utf-8"))
                self.assertTrue(del_res["deleted"])
                self.assertEqual(del_res["removed_registered_hosts"], ["eda-lab-1"])

                st_w3, _, wiz3_bytes = raw_req("GET", "/api/wizard/status")
                self.assertEqual(st_w3, 200)
                wiz3 = json.loads(wiz3_bytes.decode("utf-8"))
                self.assertEqual(wiz3["ssh_aliases"], [])
                self.assertEqual(wiz3["workspace"]["verified_host_count"], 0)
                self.assertEqual(wiz3["workspace"]["hosts"], [])
            finally:
                srv.shutdown()
                srv.server_close()
                ctrl.close()


if __name__ == "__main__":
    unittest.main()
