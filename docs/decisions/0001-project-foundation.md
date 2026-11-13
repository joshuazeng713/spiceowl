# ADR 0001: Start with observable, reproducible execution

- Date: 2026-11-13
- Status: accepted for the documentation scaffold; technology choices below are provisional

## Context

SpiceOwl starts from an analog IC researcher's need to supervise overnight and multi-day circuit simulations. The initial repository contains a README, a Python ignore template, and an MIT license. The actual Cadence versions and execution environment are not yet specified.

## Decision

1. Preserve the MIT license and existing copyright attribution.
2. Start with documentation, an illustrative run contract, synthetic fixtures, and a Python source layout. Do not advertise an executable package or simulator support before implementation.
3. Implement read-only monitoring before submission, experiment validation before automatic recovery, and a stable run API before a web console.
4. Keep experiment meaning, execution state, measurement validity, and specification outcome distinct.
5. Put simulator and execution-system differences behind separate adapters. Integrate an existing native run manager or scheduler where available.
6. Keep agents optional and restrict them to structured evidence-backed proposals evaluated by the same policy layer used for other actions.
7. Use the owl-and-waveform icon as the initial editable identity. Keep all project-referenced assets in the repository.

## Provisional technology direction

- Python 3.11+ for the controller, validators, CLI, and adapters; confirm when packaging is introduced.
- Local SQLite for the first single-controller persistence experiment; an interface permits later PostgreSQL support.
- Evaluate Prefect once submission/reconciliation semantics have tests. Temporal and AiiDA remain alternatives if requirements favor them.
- Study Custodian's job/handler/validator separation. Implement Spectre-specific behavior rather than porting material-science corrections verbatim.
- Start with mock fixtures for public development. Real-tool tests are opt-in and run in an explicitly configured private environment.

No dependency is installed, pinned, or integrated by this decision. Select and lock dependencies in the implementation change that actually uses them.

## Deferred decisions

- Supported Virtuoso/Spectre release and analysis matrix.
- Direct Linux execution, site scheduler, or ADE-managed execution for the first real backend.
- Native result reader and measurement export path.
- Available checkpoint capabilities and compatibility validation.
- LLM provider and whether diagnosis can remain fully local.
- Web console framework, remote authentication, and multi-user deployment.

## Consequences

The first release will be narrower than the product vision but easier to verify. Contributors can inspect failure behavior without licensed software. Submission reliability and scientific acceptance remain project responsibilities even when an orchestration framework is used.
