header:
  schema-version: 1.0.0
  last-updated: '2025-05-16'
  last-reviewed: '2025-05-16'
  expiration-date: '2026-12-01T01:00:00.000Z'
  project-url: https://github.com/llm-d/llm-d
  project-release: '0.0.1'
project-lifecycle:
  status: active
  bug-fixes-only: false
  core-team:
  - contact: github:chcost
  - contact: github:robertgshaw2-redhat
  - contact: github:smarterclayton
  - contact: github:clubanderson
contribution-policy:
  accepts-pull-requests: true
  accepts-automated-pull-requests: true
  code-of-conduct: https://github.com/llm-d/llm-d/blob/main/CODE_OF_CONDUCT.md
documentation:
- https://llm-d.ai
distribution-points:
- https://github.com/llm-d/llm-d/releases
security-artifacts:
  threat-model:
    threat-model-created: true
    evidence-url:
    - https://github.com/llm-d/llm-d/blob/main/THREAT-MODEL.md
security-testing:
- tool-type: sca
  tool-name: Dependabot
  tool-version: latest
  integration:
    ad-hoc: false
    ci: true
    before-release: true
  comment: |
    Dependabot is enabled for this repo.
security-contacts:
- type: email
  value: llm-d-security-announce@googlegroups.com
vulnerability-reporting:
  accepts-vulnerability-reports: true
  security-policy: https://github.com/llm-d/llm-d/blob/main/SECURITY.md
  email-contact: llm-d-security-announce@googlegroups.com
  comment: |
    The first and best way to report a vulnerability is by using private security issues in GitHub.
dependencies:
  third-party-packages: true
  dependencies-lists:
  - https://github.com/llm-d/llm-d/blob/main/SECURITY.md