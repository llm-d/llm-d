## Security Announcements

Join the [llm-d-security-announce](https://groups.google.com/u/1/g/llm-d-security-announce) group for emails about security and major API announcements.

## Report a Vulnerability

We're extremely grateful for security researchers and users that report vulnerabilities to the llm-d Open Source Community. All reports are thoroughly investigated by a set of community volunteers.

Before submitting a report, check open and closed [pull requests](https://github.com/llm-d/llm-d/pulls) and [published security advisories](https://github.com/llm-d/llm-d/security/advisories) for the same issue.

Please report security issues privately using the [vulnerability submission form](https://github.com/llm-d/llm-d/security/advisories/new).

Include the affected component and version, the configuration needed to reproduce the issue, the steps to reproduce it, and the security impact in your private report.

### Urgent contact

We prefer to keep all vulnerability-related communication in the GitHub security report. For urgent issues, you can contact the Vulnerability Management Team (VMT) directly.

- Juan Pérez de Algaba ([jperezde@redhat.com](mailto:jperezde@redhat.com))

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

Credit for a vulnerability is given only to the first reporter. Subsequent reports of the same issue are not credited, including independent rediscoveries.

We do not credit every submitter. The volume of duplicate and AI-generated reports would otherwise create unsustainable workload for the vulnerability management team.

## Security Vulnerability Response

The Vulnerability Management Team will acknowledge and assess each report as soon as possible.

Reporters are expected to answer the Vulnerability Management Team's follow-up questions before the team accepts a report as a draft advisory. The team will close reports for inactivity when those questions go unanswered.

Any vulnerability information shared with Security Response Committee stays within llm-d project and will not be disseminated to other projects unless it is necessary to get the issue fixed.

As the security issue moves from triage, to identified fix, to release planning we will keep the reporter updated.

### CVSS scoring

The Vulnerability Management Team has final say over the CVSS score and vector reported in a security advisory. The team may adjust the submitted CVSS metric values to align with the project's expectations and security guidelines.

## CVE assignment and advisory publication

After a security advisory has been accepted:

- A CVE is requested when a patch for the advisory is merged.
- The advisory is published when the fix lands in a vLLM release.

## Public Disclosure Timing

A public disclosure date is negotiated by the llm-d Security Response Committee and the bug submitter.
We prefer to fully disclose the bug as soon as possible once a user mitigation is available.
It is reasonable to delay disclosure when the bug or the fix is not yet fully understood, the solution is not well-tested, or for vendor coordination.
The timeframe for disclosure is from immediate (especially if it's already publicly known) to a few weeks.
The llm-d maintainers hold the final say when setting a disclosure date.
