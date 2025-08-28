## SIG Overview

Special Interest Groups (SIGs) are the primary organizational units for coordinating work across the llm-d project. Each SIG focuses on a specific area of the project's technology stack and is responsible for driving design, implementation, and maintenance of their respective components.

SIGs provide a mechanism for:
- **Focused expertise**: Bringing together contributors with specialized knowledge in specific areas
- **Coordinated development**: Ensuring consistent architectural decisions across related components
- **Community building**: Creating smaller, more manageable groups for collaboration and mentorship
- **Accountability**: Clear ownership and responsibility for specific project areas

## SIG Structure and Governance

### SIG Leadership
Each SIG has:
- **SIG Leads** (2-3 people): Responsible for overall SIG direction, coordination, and decision-making

### SIG Responsibilities
- Drive technical design and implementation in their area
- Maintain documentation and architectural decisions
- Coordinate with other SIGs on cross-cutting concerns
- Mentor new contributors and grow the community
- Participate in project-wide planning and releases

### SIG Meetings
- Regular meetings (typically weekly) for technical discussions
  
## Relationship to Project Governance

SIGs operate within the broader llm-d project governance framework defined in [PROJECT.md](PROJECT.md):
- SIGs follow the project's [lazy consensus](https://community.apache.org/committers/decisionMaking.html#lazy-consensus) decision-making process
- Major cross-SIG decisions require project maintainer approval
- All SIG work follows the project's [contribution guidelines](CONTRIBUTING.md)

## Active Special Interest Groups

| SIG | Focus Area | Meeting Schedule | Documentation |
|-----|------------|------------------|---------------|
| **[SIG Inference Scheduler](#sig-inference-scheduler)** | Intelligent request routing, load balancing, and traffic management | Weekly Tuesdays 12:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12pm)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1aKTJru43krjHP2ORayEEp4JP-N7dJL8S)<br>• [llm-d-inference-scheduler Repository](https://github.com/llm-d/llm-d-inference-scheduler/) |
| **[SIG Benchmarking](#sig-benchmarking)** | Performance testing, benchmarking frameworks, and optimization | Weekly Thursdays 1:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=1pm)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1Hd-rCRLDbucl-LD0RlQwOCLqERWF-obT)<br>• [llm-d-benchmark Repository](https://github.com/llm-d/llm-d-benchmark) |
| **[SIG PD-Disaggregation](#sig-pd-disaggregation)** | Prefill/decode separation, distributed serving, and workload disaggregation | Weekly Tuesdays 1:30 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=130pm)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1jk7wtojsWNbYQVf7BY8BEvIg8FMRZV0q) |
| **[SIG KV-Disaggregation](#sig-kv-disaggregation)** | KV caching, prefix caching, and distributed storage systems | Weekly Tuesdays 12:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12pm)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1mFbzwEWL2-LvD21owgxlKRcQD0eSmcz6)<br>• [llm-d-kv-cache-manager Repository](https://github.com/llm-d/llm-d-kv-cache-manager) |
| **[SIG Installation](#sig-installation)** | Kubernetes integration, deployment tooling, and platform operations | Weekly Thursdays 11:00 AM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=11am)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1H-0Y8fXepzrYpcaUOBfuphn1Cl-gU0xr) |
| **[SIG Autoscaling](#sig-autoscaling)** | Traffic-aware autoscaling, resource management, and capacity planning | Weekly Thursday 12:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12pm)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1iDlTgpFPOrSQn7dWR3uCQLtqhz86HTAi) |
| **[SIG Observability](#sig-observability)** | Monitoring, logging, metrics, and operational visibility | Weekly Thursdays 12:30 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12:30pm)) | • [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1H-TVTCKYVxUn4fER7xuTPmscNttZCutN) |

## SIG Detailed Descriptions

### SIG Inference Scheduler

> **👥 Leadership:** [Nili Guy](https://github.com/nilig), [Abdullah Gharaibeh](https://github.com/ahg-g), [Vita Bortnikov](https://github.com/vitabortnikov)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1kE1LY8OVjiOgKVD9-9Po96HODbTIbgHp4qgvw06BCOc) *(Google Docs)*

**Charter**: Develop and maintain intelligent request routing and load balancing systems that optimize for latency, throughput, and resource utilization across distributed inference workloads.

**Key Areas**:
- vLLM-optimized inference scheduling algorithms
- KV-cache aware routing and load balancing
- Integration with Kubernetes Gateway API and Inference Gateway Extension
- Flow control and traffic shaping
- SLA-aware request prioritization

**💬 Communication**:
- **Slack Channel**: [#sig-inference-scheduler](https://llm-d.slack.com/archives/C08SBNRRSBD)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1aKTJru43krjHP2ORayEEp4JP-N7dJL8S)

### SIG Benchmarking

> **👥 Leadership:** [Marcio A L Silva](https://github.com/maugustosilva), [Ashok Chandrasekar](https://github.com/achandrasekar)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1DtSEMRu3ann5M43TVB3vENPRoRkqBr_UiuwFnzit8mw) *(Google Docs)*

**Charter**: Establish comprehensive performance testing and benchmarking frameworks to ensure llm-d delivers optimal performance across diverse workloads and hardware configurations.

**Key Areas**:
- Benchmarking frameworks and methodologies
- Performance regression testing
- Workload simulation and synthetic data generation
- Hardware-specific optimization
- Performance analysis and profiling tools

**💬 Communication**:
- **Slack Channel**: [#sig-benchmarking](https://llm-d.slack.com/archives/C08TSFYMSCQ)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1Hd-rCRLDbucl-LD0RlQwOCLqERWF-obT)


### SIG PD-Disaggregation

> **👥 Leadership:** [Robert Shaw](https://github.com/robertgshaw2-redhat)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1FNN5snmipaTxEA1FGEeSH7Z_kEqskouKD1XYhVyTHr8) *(Google Docs)*

**Charter**: Design and implement prefill/decode disaggregation patterns that enable efficient separation of inference workloads across heterogeneous hardware and scaling requirements.

**Key Areas**:
- Prefill/decode workload separation
- Disaggregated serving architecture
- Cross-instance communication protocols
- Heterogeneous hardware optimization
- Dynamic workload balancing between Prefill and Decode instances

**💬 Communication**:
- **Slack Channel**: [#sig-pd-disaggregation](https://llm-d.slack.com/archives/C08T1E128PK)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1jk7wtojsWNbYQVf7BY8BEvIg8FMRZV0q)


### SIG KV-Disaggregation

> **👥 Leadership:** [Maroon Ayoub](https://github.com/vMaroon), [Danny Harnik](https://github.com/dannyharnik)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1EM1QtDUaw7pVRkbHQFTSCQhmWqAcRPJugJgqPbvzGTA) *(Google Docs)*

**Charter**: Design and implement distributed KV caching solutions that improve inference performance through intelligent cache management, prefix sharing, and disaggregated storage.

**Key Areas**:
- Distributed KV cache architecture
- Prefix cache hierarchies (local, remote, shared)
- Cache-aware scheduling and routing
- Storage optimization for inference workloads
- Integration with vLLM's KVConnector

**💬 Communication**:
- **Slack Channel**: [#sig-kv-disaggregation](https://llm-d.slack.com/archives/C08TB7ZDV7S)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1mFbzwEWL2-LvD21owgxlKRcQD0eSmcz6)

### SIG Installation

> **👥 Leadership:** [Brent Salisbury](https://github.com/nerdalert), [Greg Pereira](https://github.com/Gregory-Pereira)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1Y0fJGhELfdXj-Xkznhrl48sDOp_dUvuy5sX4lf9g63o) *(Google Docs)*

**Charter**: Ensure llm-d integrates seamlessly with Kubernetes and provides robust deployment, scaling, and operational capabilities for production environments.

**Key Areas**:
- Kubernetes-native deployment patterns
- Helm charts and operators
- Installation and configuration management
- Multi-node orchestration with LeaderWorkerSet
- Platform integration and operational best practices

**💬 Communication**:
- **Slack Channel**: [#sig-installation](https://llm-d.slack.com/archives/C08SLBGKBEZ)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1H-0Y8fXepzrYpcaUOBfuphn1Cl-gU0xr)

### SIG Autoscaling

> **👥 Leadership:** [Tamar Eilam](https://github.com/eilamt), [Abhishek Malvankar](https://github.com/asm582)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1inTneLEZTv3rDEBB9KLOB9K6oMq8c3jkogARJqdt_58) *(Google Docs)*

**Charter**: Develop intelligent autoscaling solutions that automatically adjust llm-d deployments based on traffic patterns, workload characteristics, and hardware utilization.

**Key Areas**:
- Traffic-aware autoscaling algorithms
- Hardware-specific scaling policies
- Workload-based capacity planning
- Integration with Kubernetes HPA/VPA
- Cost-optimized scaling strategies

**💬 Communication**:
- **Slack Channel**: [#sig-autoscaling](https://llm-d.slack.com/archives/C08T899332A)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1iDlTgpFPOrSQn7dWR3uCQLtqhz86HTAi)

### SIG Observability

> **👥 Leadership:** [Sally O'Malley](https://github.com/sallyom), [Roy Nissim](https://www.linkedin.com/in/roy-nissim/), [Benedikt Bongartz](https://github.com/frzifus)

> [**⭐️ North Star Design Document** ↗️](https://docs.google.com/document/d/1UNa75BBzoMFZgImAnqd89KyT-W1MmO0VKLRqgF9ikWA) *(Google Docs)*

**Charter**: Provide comprehensive monitoring, logging, and observability capabilities that enable operators to understand system behavior, diagnose issues, and optimize performance.

**Key Areas**:
- Metrics collection and visualization
- Distributed tracing and logging
- Performance monitoring and alerting
- Operational dashboards and reporting
- Integration with monitoring ecosystems (Prometheus, Grafana, etc.)

**💬 Communication**:
- **Slack Channel**: [#sig-observability](https://llm-d.slack.com/archives/C09305NHZ45)
- **Meeting Recordings and Docs**: [Public Google Drive](https://drive.google.com/drive/folders/1H-TVTCKYVxUn4fER7xuTPmscNttZCutN)

## Getting Involved

### Joining a SIG
1. **Attend a meeting**: Check the [project calendar](https://red.ht/llm-d-public-calendar) for SIG meeting times
2. **Join the conversation**: Participate in SIG-specific channels on [Slack](https://llm-d.ai/slack)
3. **Review documentation**: Read the SIG's charter and current initiatives
4. **Start contributing**: Look for "good first issues" labeled with the SIG's area

### SIG Communication Channels
- **Slack**: Each SIG has dedicated channels in the [llm-d Slack workspace](https://llm-d.slack.com)
- **Google Groups**: Join [llm-d-contributors](https://groups.google.com/g/llm-d-contributors) for comment access to SIG documents
- **GitHub**: Issues and discussions are labeled by SIG area
- **Calendar**: All SIG meetings are on the [shared project calendar](https://red.ht/llm-d-public-calendar)

## SIG Formation and Evolution

### Creating a New SIG
1. **Identify need**: Demonstrate community interest and technical necessity
2. **Draft charter**: Define scope, goals, and initial leadership
3. **Proposal process**: Submit proposal following [project contribution guidelines](CONTRIBUTING.md)
4. **Community review**: Present at weekly project standup and gather feedback
5. **Approval**: Obtain approval from project maintainers

### SIG Lifecycle Management
- **Active**: Regular meetings, active development, engaged community
- **Maintenance**: Limited active development, focus on stability and bug fixes
- **Archived**: No longer active, historical reference only

SIGs may evolve, merge, or be archived based on project needs and community engagement.

## Resources

- **Project Calendar**: [llm-d Public Calendar](https://red.ht/llm-d-public-calendar)
- **Slack Workspace**: [https://llm-d.slack.com](https://llm-d.slack.com)
- **Google Groups**: [https://groups.google.com/g/llm-d-contributors](https://groups.google.com/g/llm-d-contributors)
- **Community Governance**: [PROJECT.md](PROJECT.md)
- **Contributing Guidelines**: [CONTRIBUTING.md](CONTRIBUTING.md)

## Maintenance

This document is maintained by the project maintainers and updated as SIGs evolve. For questions or suggestions about SIG structure, please reach out via:
- Weekly project standup (Wednesdays 12:30 PM ET)
- [llm-d Slack channel](https://llm-d.slack.com/)
- GitHub issues in the [llm-d/llm-d](https://github.com/llm-d/llm-d) repository
