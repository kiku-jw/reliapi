# Security Policy

## Supported versions

Security fixes are applied to the latest revision on the default branch. Older
commits, tags, and self-hosted deployments are not guaranteed to receive
backports.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability.

Use GitHub's private vulnerability reporting flow from the repository's
**Security** tab when it is available. Otherwise email
[hi@kikuai.dev](mailto:hi@kikuai.dev) with the subject
`[SECURITY] reliapi`.

Include, when possible:

- the affected version or commit;
- a minimal reproduction;
- the expected security impact;
- relevant logs or screenshots with secrets and personal data removed;
- any remediation idea you have already tested.

Please allow time for validation and a coordinated fix before publishing
details. Reports made in good faith and without accessing data that is not
yours are welcome.

## Scope

The preferred scope is code maintained in this repository. Vulnerabilities in
third-party services, unsupported deployments, social engineering, denial of
service by excessive traffic, and reports without a reproducible security
impact may be closed as out of scope.
