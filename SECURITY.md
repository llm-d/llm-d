## Security Announcements

Join the [llm-d-security-announce](https://groups.google.com/u/1/g/llm-d-security-announce) group for emails about security and major API announcements.

## Report a Vulnerability

We're extremely grateful for security researchers and users that report vulnerabilities to the llm-d Open Source Community. All reports are thoroughly investigated by a set of community volunteers.

Before submitting a report, check open and closed pull requests and published security advisories for the same issue. Check both this repository ([pull requests](https://github.com/llm-d/llm-d/pulls), [advisories](https://github.com/llm-d/llm-d/security/advisories)) and the affected component's repository if it is separate (for example, [llm-d-router advisories](https://github.com/llm-d/llm-d-router/security/advisories)).

Please report security issues privately in the repository that owns the affected code or use [this repository's vulnerability submission form](https://github.com/llm-d/llm-d/security/advisories/new). The Vulnerability Management Team will move reports between repositories as needed.

Include the affected component and version, the configuration needed to reproduce the issue, the steps to reproduce it, and the security impact in your private report.

### Email contact

We prefer to keep all vulnerability-related communication in the GitHub security report. Reporters who cannot use GitHub may email [llm-d-security-reporting@googlegroups.com](mailto:llm-d-security-reporting@googlegroups.com) instead. You can also use this address to contact the Vulnerability Management Team about urgent issues.

### When Should I Report a Vulnerability?

- You think you discovered a potential security vulnerability in llm-d
- You are unsure how a vulnerability affects llm-d
- You think you discovered a vulnerability in another project that llm-d depends on
  - For projects with their own vulnerability reporting and disclosure process, please report it directly there

### When Should I NOT Report a Vulnerability?

- You need help tuning llm-d components for security
- You need help applying security related updates
- Your issue is not security related

## Security questions

For general security questions, feel free to ask in `#sig-security` on the [llm-d Slack](https://llm-d.ai/slack).

## Reporter credit

The Vulnerability Management Team credits the first reporter of a vulnerability. The team may also credit a later reporter whose report adds material analysis.

## Security Vulnerability Response

The Vulnerability Management Team will acknowledge and assess each report as soon as possible.

Reporters are expected to answer the Vulnerability Management Team's follow-up questions before the team accepts a report as a draft advisory. The team will close a report for inactivity if its follow-up questions go unanswered for 14 days.

The Vulnerability Management Team keeps vulnerability information within the llm-d project and shares it with other projects only when necessary to fix the issue.

As the security issue moves from triage, to identified fix, to release planning we will keep the reporter updated.

### CVSS scoring

The Vulnerability Management Team has final say over the CVSS score and vector reported in a security advisory. The team may adjust the submitted CVSS metric values to align with the project's expectations and security guidelines.

## CVE assignment and advisory publication

After a security advisory has been accepted:

- A CVE is requested when a patch for the advisory is merged.
- The advisory is published when the fix ships in a release of the affected llm-d component.

## Public Disclosure Timing

The Vulnerability Management Team and the reporter negotiate a public disclosure date.
We prefer to fully disclose the bug as soon as possible once a user mitigation is available.
It is reasonable to delay disclosure when the bug or the fix is not yet fully understood, the solution is not well-tested, or for vendor coordination.
The timeframe for disclosure is from immediate (especially if it's already publicly known) to a few weeks.
The Vulnerability Management Team sets the final disclosure date.
