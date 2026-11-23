# Authored Spectre testbenches

`agent-testbench` is a registered experiment recipe for a complete testbench
written by the user or an Agent. `mode="agent-tb"` selects its source inventory.
It accepts hierarchical or flat circuits without requiring two MOS devices,
inverter port roles, inferred polarity, or generated stimuli. Existing
`inverter-tran` and `prepared-transient` behavior remains available.

SpiceOwl performs static checks on supplied data. It does not open source paths,
invoke SSH, execute postprocessing code, run a solver, or approve execution.
CircuitGit owns committed inputs, dependency auditing, human review and Runner
execution. A `static_ready` validation or `ready` compilation is review material;
it does not establish simulator success, model qualification or scientific
acceptance.

## Static APIs

```python
inspect_agent_source(source, source_id="dut.scs", support_files=None)
inspect_netlist(source, mode="agent-tb", support_files=None)
validate_agent_tb(
    source=dut_bytes,
    testbench=tb_bytes,
    support_files={"support/leaf.scs": leaf_bytes},
    criteria=complete_authored_draft,
    pdk_binding=selected_binding,
)
```

Text inputs are encoded as UTF-8 once; byte inputs retain their exact content.
Inspection does not normalize line endings or rewrite declarations. Relative
file keys are names within the supplied bundle, never requests to read disk.
The ordinary `inspect_netlist` default is unchanged.

The validator returns `spiceowl.agent-tb-validation.v1`, with `status`, `missing`,
`manifest`, `requirements`, `analyses`, and `saves`. A supported complete TB must
include `dut.scs`, use at least one `tran`, `ac`, or `dc` analysis, and contain an
explicit `save` statement. External masters require the managed `pdk.scs` node
and a matching selected model-name binding. A declared subcircuit/instance/port
map is checked against the text. Flat DUTs use null subcircuit and instance fields.

The `spiceowl.agent-tb-manifest.v1` manifest binds the exact DUT, TB and support
file hashes, the canonical hash of the entire authored criteria object, selected
PDK binding, include graph, and a manifest hash. `pdk_sha256` means the generated
managed `pdk.scs` wrapper bytes, not the whole PDK. `audit_sha256`, when supplied,
identifies a separate dependency audit receipt.

The PDK binding requires `discovery_id`, `candidate_id`, `sections`,
`model_names`, and `pdk_sha256`. Optional fields are `entry_sha256`,
`tmi_candidate_id`, `audit_sha256`, `veriloga_standard_includes` and
`veriloga_header_closure`. These are binding data; the pure validator
does not claim that discovery or auditing actually occurred.

## Registered compiler mapping

`prepare_experiment(sources, selection, spec, model_profile)` dispatches to this
compiler when `spec.recipe == "agent-testbench"`.

```python
selection = {
    "mode": "agent-tb",
    "source_id": "source/dut.scs",
    "testbench_id": "source/tb.scs",
    "pdk_id": "source/pdk.scs",
    "support_files": {"support/leaf.scs": "source/leaf.scs"},
    "manifest_id": "source/draft.json",  # optional authored JSON file
}
spec = {
    "schema": "spiceowl.experiment-spec.v1",
    "recipe": "agent-testbench",
    "corner": "chosen",
    "criteria": complete_authored_draft,
    "pdk_binding": selected_binding,
}
```

`sources` maps the committed source keys to bytes. Every source must have one
explicit role. The electrical model profile keeps its existing schema and
corner-to-alias mapping; its `models` mapping may be empty for this generic
compiler. MOS polarity contracts are not required to preserve arbitrary authored
instances. The execution owner must verify actual external master declarations
and port counts in the dependency audit.

The complete authored draft uses `ic.agent_testbench.v1` with `text`,
`interface: {subckt, instance, port_bindings}`, `analyses`, `stimuli`,
`objectives`, `criteria`, and `postprocess`. Optional `helper_files` maps each
relative support filename to its literal UTF-8 text. When present, its keys and
bytes must exactly match a subset of supplied support files, including Verilog-A
helpers. The remaining support files must belong to the include/ahdl closure
rooted at `dut.scs`; a TB-only file cannot masquerade as original DUT support.
A missing or edited helper, undeclared TB-only support or any unreferenced
supplied file blocks readiness. The flat compiler support mapping is the union
of these distinct origins. Both retain byte hashes and their `support_role`
in the validation manifest. The entire authored object is hashed.
`text` must equal the committed TB bytes. If `manifest_id` is supplied, its JSON
must represent that same object and its original bytes are retained.

Ready compilation returns the existing `status/missing/recipe/files/source`
shape plus `entrypoint="netlist/tb.scs"`. DUT, TB, PDK wrapper and relative support
files are copied byte for byte into `netlist/`. The managed wrapper is restricted
to registered external-alias include lines matching the selected sections; its
actual hash must equal the selected binding. The compiler does not generate
stimuli, select test conditions or append analysis commands.

## Finite supported syntax

The current static grammar supports Spectre language declarations, explicit
subcircuits and hierarchical calls, local model declarations, global nodes,
named parameters, parenthesized or unambiguous bare-node instances, numeric PWL
vectors, bounded arithmetic/common math expressions, `tran/ac/dc`, options and
explicit signal saves. It reports unresolved external masters separately from
local subcircuits and built-in primitives. Duplicate declarations, wrong local
subcircuit arity and recursive hierarchy are rejected.

All includes must be top-level and resolve to supplied relative files or managed `dut.scs` and
`pdk.scs`. Missing files, unused supplied files, traversal, absolute/dynamic
includes, language switches to SPICE, shell commands, executable functions,
file-writing options and unsupported dynamic constructs are explicit gaps.
Unsupported syntax is not silently declared compatible. Limits are 64 supplied
files, 1 MiB per file, 8 MiB total, 16,384 statements per file, 512 terminals per
instance/interface and 64 levels of hierarchy.

## Reviewed relative Verilog-A sources

An authored Spectre file can contain `ahdl_include "behavior/library.va"`.
Supply those exact bytes through `support_files`; the registered compiler uses
the existing relative-to-committed-source mapping. A plain Spectre `include`
cannot switch a file to Verilog-A. Absolute paths, environment substitutions,
parent traversal and unprovided files are gaps.

A supplied `.va` or `.vams` file can use a literal top-level directive such as
`` `include "cells/sample.va" ``. References resolve relative to that file's
directory and must name supplied `.va` or `.vams` files. Nested includes are
walked, cycles are rejected and every supplied support file must be reachable.
The supported local include units contain complete module declarations or
further top-level includes; arbitrary lexical fragments inside a module,
conditional preprocessing, macro-defined includes and user-defined macros are
not implemented.

The scalar behavioral subset includes ordered module ports, scalar
`input/output/inout` and `electrical` declarations, real/integer parameters and
variables, explicit parameter bounds, `analog` contributions with `V`/`I`,
assignments, `begin/end`, `if/else`, `cross`, `timer`, `initial_step`,
`final_step`, `transition`, selected arithmetic/math functions, and read-only
`$abstime`/`$temperature`/`$vt` forms. A finite allowlist also recognizes common
standard constant macros when the constants dependency is declared. Module
interfaces enter the normal master-resolution and port-count checks; known
local modules are excluded from external `required_masters`. The declared
authored port map is checked against the module's ordered ports when selected
directly as the DUT interface.

Unknown functions, system/file tasks, output tasks such as `$display`, process
control such as `$finish`, `$bound_step`, arrays, loops, custom functions,
custom disciplines and structural Verilog module instantiation are outside
this slice. They return explicit gaps rather than being silently passed to the
solver. Statements hidden in comments do not become instructions.

This checker is **not a Verilog-A compiler or an execution sandbox**. Its finite
grammar does not prove semantic correctness, convergence, absence of every
possible simulator-side effect, or compatibility with a selected Spectre
release. Real compilation/execution still requires review and the execution
owner's isolation, dependency checks and authorization. Compiler-generated
AHDL build files belong to the controlled run directory and are separate from
the reviewed source inventory.

## Controlled standard-header dependencies

The exact names `constants.vams` and `disciplines.vams` refer to controlled
external dependencies. Standard headers cannot be supplied or shadowed in
`support_files`. No PDK/tool header contents are copied into this repository,
and the checker does not open them from the local computer or guess an install
directory.

The selected binding includes observed identities in this shape; placeholders
below must be replaced with actual inventory values before validation:

```python
selected_binding["veriloga_standard_includes"] = [
    {"name": "disciplines.vams", "alias": "VA_DISCIPLINES", "sha256": observed_header_sha256},
]
selected_binding["veriloga_header_closure"] = {
    "inventory_sha256": observed_complete_header_inventory_sha256,
    "files": [
        {"name": "disciplines.vams", "alias": "VA_DISCIPLINES", "sha256": observed_header_sha256},
        {"name": "units/physical.vams", "alias": "VA_UNITS", "sha256": observed_dependency_sha256},
    ],
    "includes": [
        {"from": "disciplines.vams", "reference": "units/physical.vams", "target": "units/physical.vams"},
    ],
}
```

The transitive include shown here illustrates the schema; it is not a claim
about a vendor header. A truly observed leaf header still requires a closure
with its file row and `includes=[]`. Never infer a leaf solely from its name.
An include edge may also retain a positive `line` number. Standard entry rows
must exactly match their closure file rows. Names are relative staging names;
aliases are bounded uppercase identifiers and cannot collide with PDK aliases.
Raw host paths and shell commands are not accepted in these bindings.

The static check validates the bounded graph: all files must be reachable from
the selected standard entries, every edge must resolve literally within that
graph, and missing files, cycles, duplicate names/aliases, unused bindings and
unreferenced closure files are gaps. It cannot authenticate an inventory receipt
or establish that a declared graph matches actual header bytes.

The Runner must therefore verify `inventory_sha256` against the complete
observation from the explicitly selected simulator environment, resolve the
registered aliases to its observed paths, audit **every** closure file and
include edge, and stage the exact audited bytes. Compilation must resolve the
unchanged source directives to these staged headers and their staged transitive
closure. Falling back to a host search path or an unobserved tool installation
does not satisfy this contract. A failed or incomplete observation blocks the
run; an agent cannot supply an empty closure to bypass it.

`veriloga_modules` and `veriloga_external_dependencies` are additive inventory
fields. The validation manifest and compiled recipe add the standard binding
to each external usage and retain `veriloga_header_closure`. External usages
remain labeled `requires_runner_dependency_audit`; `static_ready` is not an
audit receipt. All supplied VA bytes, the complete authored draft, selected
standard identities and closure metadata feed the existing manifest/recipe
hashes. Editing a helper or a header identity changes the reviewed identity.

Synthetic regressions can be run without a simulator, SSH, PDK or LLM:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_agent_tb tests.test_veriloga
```

Authored postprocessing remains inert metadata at this layer. The old inverter
PSF measurement function returns `unknown` for this recipe, with an explicit
postprocessor requirement; it cannot turn an authored AC/DC or other experiment
into an inverter measurement success.
