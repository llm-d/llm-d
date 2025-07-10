# Special Interest Groups (SIGs)

## Overview

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

| SIG | Leadership | Focus Area | Meeting Schedule | Documentation |
|-----|------------|------------|------------------|---------------|
| **[SIG Inference Scheduler](#sig-inference-scheduler)** | Nili Guy<br>Abdullah Gharaibeh<br>Vita Bortnikov | Intelligent request routing, load balancing, and traffic management | Weekly Tuesdays 12:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12pm)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1aKTJru43krjHP2ORayEEp4JP-N7dJL8S)<br>[Project Repository](https://github.com/llm-d/llm-d-inference-scheduler/) |
| **[SIG Benchmarking](#sig-benchmarking)** | Marcio A L Silva<br>Ashok Chandrasekar | Performance testing, benchmarking frameworks, and optimization | Weekly Thursdays 1:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=1pm)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1Hd-rCRLDbucl-LD0RlQwOCLqERWF-obT?usp=drive_link)<br>[Project Repository](https://github.com/llm-d/llm-d-benchmark) |
| **[SIG PD-Disaggregation](#sig-pd-disaggregation)** | Robert Shaw | Prefill/decode separation, distributed serving, and workload disaggregation | Weekly Tuesdays 1:30 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=130pm)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1jk7wtojsWNbYQVf7BY8BEvIg8FMRZV0q?usp=drive_link) |
| **[SIG KV-Disaggregation](#sig-kv-disaggregation)** | Maroon Aoyub<br>Danny Harnik | KV caching, prefix caching, and distributed storage systems | Weekly Tuesdays 12:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12pm)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1mFbzwEWL2-LvD21owgxlKRcQD0eSmcz6?usp=drive_link)<br>[Project Repository](https://github.com/llm-d/llm-d-kv-cache-manager) |
| **[SIG Installation](#sig-installation)** | Brent Salisbury<br>Greg Pereira | Kubernetes integration, deployment tooling, and platform operations | Weekly Thursdays 11:00 AM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=11am)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1H-0Y8fXepzrYpcaUOBfuphn1Cl-gU0xr?usp=drive_link) |
| **[SIG Autoscaling](#sig-autoscaling)** | Tamar Eilam | Traffic-aware autoscaling, resource management, and capacity planning | Weekly Wednesdays 2:00 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=2pm)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1iDlTgpFPOrSQn7dWR3uCQLtqhz86HTAi?usp=drive_link) |
| **[SIG Observability](#sig-observability)** | Sally O'Malley | Monitoring, logging, metrics, and operational visibility | Weekly Thursdays 12:30 PM ET<br>([Convert to your TZ](https://dateful.com/convert/eastern-time-et?t=12:30pm)) | [Meeting Recordings and Docs](https://drive.google.com/drive/folders/1H-TVTCKYVxUn4fER7xuTPmscNttZCutN?usp=drive_link) |

## SIG Detailed Descriptions

### SIG Inference Scheduler
**Charter**: Develop and maintain intelligent request routing and load balancing systems that optimize for latency, throughput, and resource utilization across distributed inference workloads.

**Key Areas**:
- vLLM-optimized inference scheduling algorithms
- KV-cache aware routing and load balancing
- Integration with Kubernetes Gateway API and Inference Gateway Extension
- Flow control and traffic shaping
- SLA-aware request prioritization

### SIG Benchmarking
**Charter**: Establish comprehensive performance testing and benchmarking frameworks to ensure llm-d delivers optimal performance across diverse workloads and hardware configurations.

**Key Areas**:
- Benchmarking frameworks and methodologies
- Performance regression testing
- Workload simulation and synthetic data generation
- Hardware-specific optimization
- Performance analysis and profiling tools

### SIG PD-Disaggregation
**Charter**: Design and implement prefill/decode disaggregation patterns that enable efficient separation of inference workloads across heterogeneous hardware and scaling requirements.

**Key Areas**:
- Prefill/decode workload separation
- Disaggregated serving architecture
- Cross-instance communication protocols
- Heterogeneous hardware optimization
- Dynamic workload balancing between P and D instances

### SIG KV-Disaggregation
**Charter**: Design and implement distributed KV caching solutions that improve inference performance through intelligent cache management, prefix sharing, and disaggregated storage.

**Key Areas**:
- Distributed KV cache architecture
- Prefix cache hierarchies (local, remote, shared)
- Cache-aware scheduling and routing
- Storage optimization for inference workloads
- Integration with vLLM's KVConnector

### SIG Installation
**Charter**: Ensure llm-d integrates seamlessly with Kubernetes and provides robust deployment, scaling, and operational capabilities for production environments.

**Key Areas**:
- Kubernetes-native deployment patterns
- Helm charts and operators
- Installation and configuration management
- Multi-node orchestration with LeaderWorkerSet
- Platform integration and operational best practices

### SIG Autoscaling
**Charter**: Develop intelligent autoscaling solutions that automatically adjust llm-d deployments based on traffic patterns, workload characteristics, and hardware utilization.

**Key Areas**:
- Traffic-aware autoscaling algorithms
- Hardware-specific scaling policies
- Workload-based capacity planning
- Integration with Kubernetes HPA/VPA
- Cost-optimized scaling strategies

### SIG Observability
**Charter**: Provide comprehensive monitoring, logging, and observability capabilities that enable operators to understand system behavior, diagnose issues, and optimize performance.

**Key Areas**:
- Metrics collection and visualization
- Distributed tracing and logging
- Performance monitoring and alerting
- Operational dashboards and reporting
- Integration with monitoring ecosystems (Prometheus, Grafana, etc.)

## Getting Involved

### Joining a SIG
1. **Attend a meeting**: Check the [project calendar](https://red.ht/llm-d-public-calendar) for SIG meeting times
2. **Join the conversation**: Participate in SIG-specific channels on [Slack](https://inviter.co/llm-d-slack)
3. **Review documentation**: Read the SIG's charter and current initiatives
4. **Start contributing**: Look for "good first issues" labeled with the SIG's area

### SIG Communication Channels
- **Slack**: Each SIG has dedicated channels in the [llm-d Slack workspace](https://llm-d.slack.com)
- **Google Groups**: Join [llm-d-contributors](https://groups.google.com/g/llm-d-contributors) for commenter access to SIG documents
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
- **Project Overview**: [PROJECT.md](PROJECT.md)
- **Contributing Guidelines**: [CONTRIBUTING.md](CONTRIBUTING.md)

## Maintenance

This document is maintained by the project maintainers and updated as SIGs evolve. For questions or suggestions about SIG structure, please reach out via:
- Weekly project standup (Wednesdays 12:30 PM ET)
- [llm-d Slack channel](https://llm-d.slack.com/)
- GitHub issues in the [llm-d/llm-d](https://github.com/llm-d/llm-d) repository
