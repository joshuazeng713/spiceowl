# Host onboarding and connection diagnosis

This slice adds browser registration to the existing single-user, loopback console. It reuses the strict private SSH profiles and bounded checks in the [connection guide](real-connections.md) and [pool guide](server-pool.md). It does not provide automatic network discovery, edit SSH credentials, accept unknown host keys, or submit simulations.

## Register a route once

Open **Simulation hosts** and add a host. Supply a stable host ID, a display label, an existing OpenSSH alias, the expected remote hostname and username, and optional supported simulator executable paths. The optional target hostname override uses the alias's existing route while selecting a different destination. No password or private-key field is accepted.

For example, an operator might already have a reviewed SSH alias named `lab-route-example`. A profile can bind it to expected hostname `node-a.example.invalid` and username `researcher-example`. All names in this guide are fictional; choose actual values from the operator's environment. Expected hostname is the remote hostname reported by the server, which can differ from the alias or connection address.

Review and save the profile, then explicitly check its connection. Saving only validates and persists local configuration. A listed host is a registered candidate, not proof of successful SSH access or an idle machine. The application does not inspect arbitrary SSH files to infer all available machines.

Use the pool import form to choose a registered route template, enter up to 32 distinct target hostnames, review the names, and import. Each imported node retains the template's SSH alias, expected user and tool paths, with its own target and expected hostname. This assumes those nodes actually share a route, account and installation paths; each must pass its own check. No subnet scan or server contact occurs during import.

## Read connection and resource evidence separately

The connection check verifies the configured SSH route and expected remote identity before querying fixed simulator version commands. Pool checks additionally sample aggregate Linux resources. A successful version command does not establish licenses, model availability, testbench correctness or simulation readiness.

| Displayed condition | Meaning and next check |
| --- | --- |
| Registered / not checked | Configuration is saved. Run an explicit connection or pool check. |
| Host trust not verified | OpenSSH has no matching trusted host record. Verify the target or jump-host fingerprint through an independently trusted source, then establish trust using the site's SSH procedure. |
| Host key changed | A previously trusted identity no longer matches. Resolve the change with the administrator before replacing any trust record. |
| Authentication denied | The route refused access. Check the account, key selection, permissions and any required existing SSH-agent session. |
| Name resolution failed | Check target and jump-host names, VPN and site DNS. |
| Network unreachable / connection refused | Check the network path, jump host, SSH service and port. An error may concern any hop; the application does not invent the failing hop. |
| Connection lost / timed out | The check did not establish the current remote state. This does not mean a simulator job failed. |
| Identity mismatch | The returned hostname or username differs from the profile. Check routing and the expected identity. |
| Resource observation stale | A previous sample exists but its age exceeds the placement freshness budget. Request a new pool check. |

Error messages are selected from fixed safe categories; remote startup text is not treated as an instruction or exposed as guidance. If identity verification stops a pool check, its specific connection error remains visible in the skipped resource result. No missing metric is changed to zero or interpreted as idle.

Connection and resource timestamps describe saved observations. Listing the page does not refresh remote evidence. Select 1–32 registered hosts for each pool check; the registry may contain more than 32 hosts. The limit applies to one selected batch, not the total number registered. Pool scanning remains explicit, checks at most two selected hosts at a time, and has bounded operations. Unselected hosts retain their previous observations. This slice does not run a periodic background watcher.

Placement and execution planning use the same selected pool. They evaluate saved observations for those hosts without contacting them. A standalone plan can propose only a selected candidate. A Virtuoso-managed origin retains its original host/native-run reference even when that host is outside the selected candidate pool; changing a pool selection never moves an existing native run.

## Durable registration and HTTP behavior

The CLI remains available for importing private JSON files. Browser writes use the same validation and local SQLite database, with loopback origin and CSRF checks and a 16 KiB JSON limit. API responses contain host summaries rather than SSH aliases, expected usernames or executable paths. Keep the state directory private and on local storage.

| Endpoint | Request / behavior |
| --- | --- |
| `GET /api/hosts` | Returns `{hosts: [...]}` with saved, sanitized summaries. No network operation. |
| `POST /api/hosts` | Exact `spiceowl.host.v1` or `spiceowl.host.v2` profile. Returns the sanitized host summary; HTTP 201 on creation, HTTP 200 for an identical retry. |
| `POST /api/hosts/import-pool` | Exactly `{template_id, names}`. Returns selected host IDs and `created` flags, with `network_operations: 0`. |
| `POST /api/hosts/HOST_ID/probe` | Empty JSON object; explicitly checks the registered host. |
| `POST /api/hosts/scan` | Exactly `{host_ids: [...]}` selects 1–32 distinct registered IDs. An empty object retains the original all-hosts behavior when the total is 1–32. |
| `POST /api/placement/recommend` | `{cpu_count, memory_bytes, simulator}` with optional `host_ids` to evaluate only 1–32 selected registered hosts. |
| `POST /api/execution/plan` | `{origin, requirements}` with optional `host_ids` for the candidate pool. Native origins retain their original host mapping. |

The host ID identifies a registration. An identical retry preserves existing observations and creation metadata. Different settings under an existing ID return HTTP 409, so a lost response cannot silently overwrite a working route. Pool import is atomic: an invalid name or conflicting ID leaves no partial set of newly registered nodes. Repeating the same pool import reuses equivalent registrations.

For checks, recommendations and plans, an explicitly supplied selection must contain 1–32 unique registered IDs. Invalid or unknown selections are rejected before any host is checked or evaluated. Omitting selection preserves the earlier whole-registry behavior; choose an explicit batch when more than 32 hosts are registered. Resource requirements keep their original strict CPU, memory and simulator validation.

In-place configuration editing, deletion, and credential management are not part of this slice. Use a new ID for a different profile. The existing CLI `hosts add` keeps its explicit duplicate-ID conflict behavior. These alpha HTTP interfaces are local implementation interfaces, not a stable remote management API.

## Next steps toward unattended operation

A reliable onboarding window should eventually support selected alias or scheduler-inventory import, a reviewed configuration editor with revision history, explicit fingerprint provenance, and separate reachability, account, simulator, license, model and output-storage validation. Imported hostnames still need operator selection and identity verification; a successful route on one node is not proof of every other node's configuration.

Long-lived access also needs an operating plan for VPN expiry, SSH-agent/key availability after reboot, credential rotation and controller restarts. A future remote monitor or site scheduler should own jobs independently of the browser, retain durable run identities, and reconnect to an existing job before considering any new submission. Those lifecycle capabilities remain on the [roadmap](roadmap.md); the present checks establish connectivity and timestamped resources only.
