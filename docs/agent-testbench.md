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
`tmi_candidate_id`, and `audit_sha256`. These are binding data; the pure validator
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
`objectives`, `criteria`, and `postprocess`. The entire object is hashed.
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
includes, AHDL, language switches to SPICE, shell commands, executable functions,
file-writing options and unsupported dynamic constructs are explicit gaps.
Unsupported syntax is not silently declared compatible. Limits are 64 supplied
files, 1 MiB per file, 8 MiB total, 16,384 statements per file, 512 terminals per
instance/interface and 64 levels of hierarchy.

Authored postprocessing remains inert metadata at this layer. The old inverter
PSF measurement function returns `unknown` for this recipe, with an explicit
postprocessor requirement; it cannot turn an authored AC/DC or other experiment
into an inverter measurement success.
