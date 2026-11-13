# Security and sensitive research data

## Current status

SpiceOwl has no runtime service or released package yet. Deployment security, authentication, and isolation are implementation requirements, not capabilities of this scaffold.

## Reporting a vulnerability

Do not put exploit details, credentials, proprietary circuits, or sensitive logs in a public issue. If GitHub shows **Security → Report a vulnerability** for this repository, use that private channel. Private reporting availability has not been verified or enabled by this scaffold. If the channel is absent, open a minimal issue asking the maintainer to establish a private reporting channel, without disclosing sensitive details.

## Data boundaries

- Keep credentials and host-specific configuration out of version control.
- Keep proprietary PDKs, licensed models, private netlists, and raw run artifacts in access-controlled local/site storage.
- Sanitize evidence before sharing it with a model or publishing it. Prefer synthetic fixtures for public development.
- Logs, imported manifests, simulator output, and model responses are untrusted data. They must not change tool permissions, execution policies, or authorization.
- Only an explicit, verified run mapping may authorize an action on an external job. A PID, arbitrary path, or a model's guessed task identity is not enough.
- Public CI must never execute proprietary simulations or accept credentials from untrusted contributions.

The ignore rules reduce accidental additions; they do not remove tracked files or Git history. Review a change before publishing it.
