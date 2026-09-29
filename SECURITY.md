# Security Policy

MAK4I Reference issues bearer credentials, enforces project-level
authorization and stores project context, so we take security reports
seriously and ask that you report them privately.

## Supported versions

MAK4I Reference is a **Developer Preview** published as release
candidates (`v0.1.0-rc.N`). It is not recommended for production use.

| Version | Supported |
|---|---|
| The latest release candidate ([Releases](https://github.com/talvikai/mak4i-reference/releases)) | Yes |
| Earlier release candidates | No. Please upgrade to the latest release candidate. |
| Unreleased commits on `main` | Best effort |

Security fixes go into the next release candidate. We don't backport them
to earlier release candidates.

## Reporting a vulnerability

**Do not report security vulnerabilities through public GitHub issues,
pull requests, discussions or any other public channel.** Public
disclosure before a fix is available puts every MAK4I installation at
risk.

Report privately by one of these routes:

1. **GitHub private vulnerability reporting.** If it's enabled for this
   repository, use **Security → Report a vulnerability** on the repository
   page. This is the preferred route.
2. **Email** Talvik at **hello@talvik.ai** with the subject line
   `SECURITY: MAK4I Reference`. Don't include working credentials in the
   email.

Please include:

- the affected version (release tag or commit) and deployment mode (Local
  or Enterprise Self-Hosted);
- a description of the issue and its impact;
- steps to reproduce, or a proof of concept;
- any known mitigation.

**Never include real credentials, tokens, private keys or customer data**
in a report. Redact them. If a credential was exposed, revoke it
(`mak4i credential revoke`) and tell us that you did.

## What to expect

- We'll acknowledge your report and keep you informed as we investigate.
- We'll agree a disclosure timeline with you, and credit you in the
  release notes unless you'd rather stay anonymous.
- Please give us reasonable time to release a fix before any public
  disclosure.

## Scope

In scope: this repository's code and the deployment configuration it
ships (`Dockerfile`, `deploy/compose/`), including authentication,
authorization, credential handling, data isolation between organizations
and projects, and the MCP transports.

Out of scope:

- vulnerabilities in third-party base images or dependencies that have
  no MAK4I-specific impact (report these upstream);
- insecure configurations that the documentation explicitly warns
  against (for example, exposing plain HTTP on `0.0.0.0` without a
  firewall or TLS reverse proxy);
- the behavior of external AI clients, except where MAK4I can mitigate
  it.

## Known limitations

See the "Known limitations" section of each release in
[`CHANGELOG.md`](CHANGELOG.md). MAK4I can't control how an external AI
client chooses between several MCP connections. For example, a client
might retry a denied write on a different MAK4I connection. The work to
mitigate this is tracked in
[#8](https://github.com/talvikai/mak4i-reference/issues/8) and
[#9](https://github.com/talvikai/mak4i-reference/issues/9).
