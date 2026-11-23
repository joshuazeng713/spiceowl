# Discovery onboarding: trust and acceptance boundaries

This document defines the acceptance contract for clean-slate onboarding on the
`feat/discovery-onboarding` workstream. It is a design and review specification;
it does not establish that every capability below is deployed or has passed a
real-server trial. Existing [host registration](host-onboarding.md), the
[connection checks](real-connections.md), and the registered
[experiment workflow](workflow.md) retain their separate scopes.

The intended sequence is: supply a netlist → choose SSH targets →
inspect environment evidence → choose a model candidate and section → supply
experiment intent → prepare → run → inspect. Discovery gathers evidence and
configuration suggestions. The existing CircuitGit Runner continues to own
execution, launch keys and native collections.

## Start from this onboarding session

The initial scientific inputs are the exact supplied netlist bytes, explicitly
selected SSH aliases or manually entered targets, optional explicit search hints,
and the experiment intent supplied for this session. The source hash must describe the bytes actually
inspected. Additional source files retain their own hashes and include edges.

A fresh session must work with an empty application state directory. It must not
silently read a previous `.spiceowl` state, a historical site profile, a previous
project's PDK installation, or another session's selected corner, section or
hash. Tests must use newly authored fixtures rather than captured private CAD
files or server logs. Returning to an existing session may explicitly restore
its own recorded selection; that is a resumed session, not fresh discovery.

Optional hints narrow an explicitly bounded search. They are neither a trusted
model definition nor execution instructions. An absent hint remains absent;
the backend must not fill it from historical private knowledge. A missing
include cannot be resolved by opening an arbitrary path found in netlist text.
Additional local files must be supplied or selected through the source-input
boundary; remote reads must remain within the authorized search roots.

## Separate operations and their evidence

| Operation | Allowed effect | Required boundary |
| --- | --- | --- |
| List SSH aliases | Read the explicitly selected OpenSSH configuration and bounded include files | Literal aliases only; no network call or evaluation of `Match exec` or `ProxyCommand` while listing |
| Inspect a selected host | Execute the installed, bounded environment probe through that route | Only explicitly selected targets; preserve OpenSSH host-key and account checks; record observed identity separately from configured identity |
| Follow setup clues | Statically parse supported source/include statements and configuration metadata | Never source or evaluate a discovered script; unresolved expansion, cycle or budget exhaustion stays explicit |
| Find model candidates | Read bounded metadata and supported declarations within the search scope | Match source-required masters and actual include/section evidence; no unrestricted filesystem scan |
| Save a selection | Persist explicit candidate, section and intent as session configuration | Bind the selected source, probe and candidate revisions; caller origin does not grant execution authority |
| Prepare | Reuse the registered deterministic compiler and dependency audit | No unresolved selection ambiguity; exact current source and model evidence; no simulator launch |
| Run / inspect | Invoke the existing finite runtime operations | Current project/host/caller grant, original submission key, native run binding and existing no-replay rules |

Normal OpenSSH transport still uses the operator's existing route configuration.
The prohibition on evaluating configuration during alias listing does not claim
that an actual SSH connection bypasses normal OpenSSH routing or the remote
account's startup behavior. Discovery does not install keys, replace host trust,
edit SSH configuration, or change the account's license environment.

An explicitly entered target does not need to appear in the alias inventory.
The current target parser supports a literal alias or a restricted
`user@hostname`/`user@IPv4` target. Record whether it came from a discovered alias
or explicit target input. Neither route accepts embedded SSH options, shell
syntax, arbitrary port syntax or an implicit scan of neighboring machines.

Every active read needs explicit finite budgets before dispatch: selected-host
count, concurrency, deadline, output bytes, source-chain depth, files visited,
bytes per file, total bytes and candidate count. The installed implementation
must validate requests against hard ceilings. A hint cannot raise those ceilings,
authorize `/` as a search root, or expand an unselected host set. A timeout or
limit hit returns incomplete evidence; it must not masquerade as an exhaustive
empty result. Repeated UI reads of a saved snapshot do not start another probe.
On older Python runtimes, an entry limit may be checked only after one operating
system directory-listing call. Report that coverage limitation instead of
claiming a strict bound on every filesystem operation or exhaustive discovery.

## Discovery results retain provenance and uncertainty

An environment or PDK candidate needs enough provenance to reconstruct what was
observed: session/source identity, selected route or host observation, discovery
revision or digest, evidence type and locator, relevant file hashes, the searched
scope, and an observation timestamp when a real read occurred. A timestamp from
a cached read is not a new remote observation. A candidate ID identifies its
recorded evidence; it is not a confidence score or a selected configuration.

A recomputable digest detects changed metadata but does not authenticate who
produced it. The trusted backend must validate the bounded result schema and
associate the observation with the selected session and transport call. It must
not accept a client-supplied digest as proof of a completed probe or authority to
register a profile.

Represent at least these distinctions explicitly:

- **Observed:** supported source text or an active probe directly establishes a
  bounded fact, such as a declaration, include edge, section label or executable.
- **Suggested:** a deterministic rule or agent proposes a configuration using
  cited observations. The proposal has no implicit authority to save or run it.
- **Ambiguous:** more than one candidate, section, terminal contract, platform
  library or revision remains compatible with the available evidence.
- **Missing or unsupported:** a required fact was absent, unreadable, outside the
  allowed scope, unsupported by the parser, or omitted after a budget limit.

Missing data must not become a successful default. If two candidate entry files
contain the required masters, their presence does not authorize selecting the
first one. A model definition file and a valid include entry are different
candidate roles. A model name alone does not establish polarity, ordered
terminals, electrical compatibility or a supported process corner. Section
labels must come from actual declarations or explicit user-supplied intent;
names such as `TT`, directory names, and a previous successful run are not
sufficient evidence to invent a section mapping.

Static discovery does not certify the entire simulator loader closure. A found
TMI directory is a directory reference; its platform-specific files must be
enumerated and validated separately. Finding one architecture does not select it
as the current host's compatible platform. Candidate selection is followed by the
existing pre-execution audit, pinning and same-host checks before a launch.
Source-derived model contracts are scoped to the candidate's exact dependency
sections. Selecting different or additional sections must not reuse a contract
verified for the earlier combination. Resolve the final combination's contract
evidence, or retain explicit missing semantics, before the final dependency audit.

## Keep license and credential material private

Transport and license configuration stay in the operator's existing SSH and
remote environment. Passwords, tokens, private keys, license-server values,
license-file contents, raw license paths, full environment dumps, and unfiltered
startup output must not appear in browser/API responses, agent summaries,
diagnostic messages, public examples or exports.

The environment adapter may retain explicitly allowlisted facts in a private
runtime record for the trusted backend. Its public projection must be constructed
from an allowlist, not by deleting a few known sensitive keys from the private
object. License presence may be represented as a boolean or bounded status; it
does not establish checkout availability or legal entitlement. Error messages
use controlled reason codes and safe wording, including on malformed remote
output, decoding errors and transport failure.

Candidate metadata needed for local configuration is distinct from a raw model
body or an environment dump. Any privileged operator view of locations must be
deliberate and scoped to that session; generic runtime, live-client and passive
MCP summaries retain their existing sanitized projections. Identifiers, names
and paths remain untrusted display text and must not be inserted as HTML.

## Configuration selection is not a second approval system

Saving a candidate and experiment intent is an ordinary configuration operation.
It does not introduce a new human-approval ledger or require repeated approval
when the user has already authorized the task and the existing grant covers the
operation. The frontend should show the concrete selection and missing fields
before submission so the user can understand the resulting experiment.

Record the submitting channel honestly:

| Origin | Meaning |
| --- | --- |
| `interactive_client` | The configured interactive client submitted the selection; this is not independent proof of a human identity or a physical click |
| `native_agent` | The agent submitted it through the existing authorized finite interface |
| `test_harness` | A test supplied simulated new-user input; it must not be described as the user actually clicking the page |

The trusted adapter determines origin. A request body containing
`confirmed: true`, `selected_by: user`, or `origin: interactive_client` cannot
upgrade permissions, overwrite trusted provenance, or turn a suggestion into a
human attestation. Do not label these ordinary configuration records
`human_verified`. An authorized agent can submit an explicit selection without
pretending it came from the interactive client.

Preparation requires the exact selected candidate ID and section, together with
the supplied experiment intent. An explicit global/no-section choice is different
from an omitted section when the candidate supports global scope. Missing
fields and unresolved multiple matches remain blocking configuration issues.

Bind the configuration to the session revision, source digest, relevant host
probe/discovery identity and candidate evidence. Changing the source, include
closure, host observation used for selection, candidate revision, model section
or scientific intent invalidates a prepared view of that selection. A stale
form submission must be rejected or require an explicit refresh; it must not
silently select the current first candidate. Harmless display refreshes may keep
the same evidence identity when no relevant fact changed.

The experiment intent must explicitly settle the supported recipe's required
conditions: selected circuit/ports, model terminal contract, supplies, stimulus,
load, temperatures, corner/section, analysis settings, saved signals and units,
measurement coverage and acceptance windows. An agent may suggest values with
their rationale. Suggested values remain identified as suggestions until
submitted as configuration through the applicable authorized channel. A source
statement that already defines a condition should be preserved as source evidence,
not replaced with a familiar default.

## Logs and discovered files have no execution authority

Only installed finite probe and compiler operations generate commands. Netlist
comments, log text, shell-startup text, model metadata and agent replies cannot
request arbitrary shell execution, policy changes, another source file, a wider
search, a license modification or a replacement run. Shell quoting alone is not
authorization: a syntactically valid path outside the chosen search scope is
still disallowed.

Source-chain parsers must report unsupported substitutions, loops and dynamic
branches instead of evaluating them. Preserve enough bounded provenance to
explain the gap. The UI can ask for an explicit hint or another selected source;
it must not hide the gap by importing an old known-working profile.

Run submission retains the existing stable key and durable Runner mapping. An
unknown HTTP or SSH outcome, an oversized response, a stale observation or an
empty lookup does not authorize a new key or automatic relaunch. Inspect targets
the original run. Discovery and configuration records must not create another
execution ledger beside the Runner.

## Acceptance and independent review checklist

| Case | Required result |
| --- | --- |
| Fresh temporary state, supplied netlist, selected aliases, no hints | No historical profile/state reads; discovered facts cite only current inputs and bounded probes |
| Alias listing with `Match exec`, `ProxyCommand`, wildcards and recursive includes | No process/network execution while listing; unsupported/over-budget coverage is reported |
| Only some aliases selected | No probe or scan for unselected routes |
| User explicitly supplies a supported target absent from the inventory | Accept it as explicit target input under the existing scope; do not fabricate alias-discovery provenance |
| Unreachable host, identity mismatch, stale snapshot | Preserve specific uncertainty; do not report a failed simulation or idle host |
| Startup source includes a command, substitution, cycle, oversized file or escaping path | No evaluation; stop or report unsupported/limited coverage within the declared scope |
| Required master absent or declaration unsupported | Structured `missing`; no invented model mapping |
| Multiple valid entry files, sections or platform libraries | Preserve candidate evidence and ambiguity; no first-match default |
| Hint names a path outside allowed roots or raises limits | Reject or report scope mismatch before additional reads |
| License text appears in stdout, stderr, metadata or an exception | Sanitized API response; no raw value or path in serialized public output |
| Candidate name contains HTML or instruction-like text | Display as text; no command, HTML or policy interpretation |
| Client submits a confirmation/origin flag | No permission or provenance upgrade; trusted channel origin remains authoritative |
| Source/candidate/intent changes after selection | Reject stale preparation binding; retain the previous evidence record |
| Agent operates under an existing grant | Use `native_agent` origin; do not require a new artificial approval or claim a human click |
| Test drives onboarding | Mark simulated input as `test_harness`; separate fixture success from actual server evidence |
| Lost prepare/run/inspect response | Preserve original session/plan/run identity and uncertain action receipt; no launch replay |
| Collected run produces a waveform | Apply the existing five independent report dimensions; a valid waveform does not establish SOA or complete dependency provenance |

Unit tests and API tests use synthetic bytes, fake transports and fresh temporary
directories. They should fail if a discovered command is executed or a forbidden
path is opened. Browser tests verify the visible selection/ambiguity/missing
states and honest submission origin. A separately authorized real-server trial
must identify the newly selected aliases and evidence, preserve license secrets,
and report which steps were automated, supplied by a test harness, or actually
performed by the user. No parser, fixture, import or HTTP success is itself a
claim of simulator qualification.
