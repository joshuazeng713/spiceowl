# References and candidate components

These are design references, not an installed dependency list or compatibility claim. Recheck the installed Cadence release, site configuration, and current upstream documentation when implementing an adapter.

## Execution and scientific workflows

- [Prefect](https://github.com/PrefectHQ/prefect): Python workflow orchestration, retries, events, and a self-hosted dashboard. Candidate integration after executor behavior is tested.
- [Custodian](https://github.com/materialsproject/custodian) and its [API](https://materialsproject.github.io/custodian/custodian.html): job, error-handler, and validator boundaries for long-running scientific calculations.
- [Temporal design patterns](https://docs.temporal.io/design-patterns): durable coordination, external polling, and human decisions. An alternative orchestration direction.
- [AiiDA calculation jobs](https://aiida.readthedocs.io/projects/aiida-core/en/stable/topics/calculations/usage.html): remote calculation lifecycle, daemon execution, input preparation, and output parsing. An alternative when scientific provenance is the primary platform requirement.
- [Slurm job states](https://slurm.schedmd.com/job_state_codes.html) and [accounting](https://slurm.schedmd.com/sacct.html): authoritative execution evidence for an optional scheduler adapter.
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence): a reference for the optional diagnostic agent, distinct from solver state.

## Analog design and Cadence

- [BAG overview](https://bag-framework.readthedocs.io/en/latest/overview/overview.html): Python-driven analog design/testbench flows and Virtuoso/OCEAN integration patterns. The linked documentation describes BAG 2.0 and is not a current-version compatibility guarantee.
- [Cadence discussion: background execution and ADE Simulation Manager](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/66028/modify-assembler-max-jobs-on-frozen-virtuoso/1408528): describes version-dependent background management and reconnection capabilities.
- [Cadence: optimizing Spectre APS performance](https://community.cadence.com/cadence_blogs_8/b/cic/posts/spectre-optimizing-spectre-aps-performance): resource/diagnostic observations and save/recover background. Published in 2019; verify options in the installed tool documentation.
- [Cadence discussion: PSS/tstab and saved state](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/47432/configure-outputstart-in-tstab): illustrates why checkpoint assumptions must be analysis-specific.

The open-source project will publish tested capabilities rather than reproduce vendor documentation or bundle commercial tools and PDKs.
