# llm-d Threat Model

This document provides a comprehensive threat model for llm-d, a Kubernetes-native distributed inference stack for Large Language Models. It follows [OSSF Security Insights](https://github.com/ossf/security-insights-spec) guidelines and incorporates the [OWASP LLM Top 10 2025](https://genai.owasp.org/llm-top-10/) framework.

## Table of Contents

1. [Overview and Scope](#overview-and-scope)
2. [System Architecture](#system-architecture)
3. [Trust Boundaries](#trust-boundaries)
4. [Threat Actors](#threat-actors)
5. [Attack Surface Analysis](#attack-surface-analysis)
6. [STRIDE Threat Analysis](#stride-threat-analysis)
7. [LLM-Specific Threats (OWASP LLM Top 10)](#llm-specific-threats-owasp-llm-top-10)
8. [Component-Specific Threats](#component-specific-threats)
9. [Mitigations and Recommendations](#mitigations-and-recommendations)
10. [Security Assumptions](#security-assumptions)
11. [Out of Scope](#out-of-scope)
12. [References](#references)

---

## Overview and Scope

### Purpose

This threat model identifies potential security threats to llm-d deployments and provides guidance for operators to secure their inference infrastructure. It covers the core llm-d components and their interactions within a Kubernetes environment.

### System Boundaries

**In Scope:**
- Inference Gateway (IGW) / Endpoint Picker (EPP)
- vLLM model servers (prefill and decode pods)
- KV-cache management (local and distributed)
- Prefix cache hierarchy (HBM, host memory, remote)
- Variant autoscaler
- NIXL GPU-to-GPU transfers
- Kubernetes resources (Pods, Services, ConfigMaps, Secrets)
- Inter-component network communication
- Model artifact storage and loading

**Out of Scope:**
- Underlying Kubernetes cluster security
- Cloud provider infrastructure security
- Model training pipelines
- Client application security
- Network infrastructure outside the cluster

### Deployment Contexts

llm-d supports multiple deployment scenarios with varying security requirements:

| Deployment Type | Trust Level | Primary Concerns |
|----------------|-------------|------------------|
| Single-tenant dedicated | High | External attackers, insider threats |
| Multi-tenant shared | Medium | Tenant isolation, resource exhaustion |
| Public API endpoint | Low | All threat categories, rate limiting |
| Air-gapped internal | High | Insider threats, supply chain |

---

## System Architecture

### Component Overview

```
                              KUBERNETES CLUSTER
 +---------------------------------------------------------------------------+
 |                                                                           |
 |   +-------------------------------------------------------------------+   |
 |   |                     Gateway Service                               |   |
 |   |  +-------------------------------------------------------------+  |   |
 |   |  |           Inference Gateway (IGW) / EPP                     |  |   |
 |   |  |  - Request routing & load balancing                         |  |   |
 |   |  |  - KV-cache-aware scheduling                                |  |   |
 |   |  |  - Prefix cache matching                                    |  |   |
 |   |  |  - Model selection                                          |  |   |
 |   |  +-------------------------------------------------------------+  |   |
 |   +-------------------------------------------------------------------+   |
 |                                    |                                      |
 |                  +-----------------+-----------------+                    |
 |                  |                 |                 |                    |
 |                  v                 v                 v                    |
 |   +------------------+ +------------------+ +------------------+          |
 |   |   Prefill Pod    | |   Decode Pod     | |   Unified Pod    |          |
 |   |  +------------+  | |  +------------+  | |  +------------+  |          |
 |   |  |   vLLM     |  | |  |   vLLM     |  | |  |   vLLM     |  |          |
 |   |  | - Model    |  | |  | - Model    |  | |  | - Model    |  |          |
 |   |  | - KV Cache |  | |  | - KV Cache |  | |  | - KV Cache |  |          |
 |   |  | - GPU Mem  |  | |  | - GPU Mem  |  | |  | - GPU Mem  |  |          |
 |   |  +------------+  | |  +------------+  | |  +------------+  |          |
 |   +------------------+ +------------------+ +------------------+          |
 |            |                   |                   |                      |
 |            +-------------------+-------------------+                      |
 |                                |                                          |
 |   +-------------------------------------------------------------------+   |
 |   |                NIXL (GPU-to-GPU Transfer Layer)                   |   |
 |   |            - KV-cache transfers between P and D pods              |   |
 |   |            - High-bandwidth GPU direct memory access              |   |
 |   +-------------------------------------------------------------------+   |
 |                                |                                          |
 |   +-------------------------------------------------------------------+   |
 |   |                   Prefix Cache Hierarchy                          |   |
 |   |  +-------------+  +---------------+  +--------------------------+ |   |
 |   |  | Local HBM   |->|  Host Memory  |->|  Remote Cache (LMCache)  | |   |
 |   |  | (GPU VRAM)  |  |  (System RAM) |  |  (Distributed/Redis)     | |   |
 |   |  +-------------+  +---------------+  +--------------------------+ |   |
 |   +-------------------------------------------------------------------+   |
 |                                                                           |
 |   +-------------------------------------------------------------------+   |
 |   |                      Variant Autoscaler                           |   |
 |   |            - Dynamic prefill/decode role assignment               |   |
 |   |            - Pod scaling based on queue depth                     |   |
 |   |            - Resource optimization                                |   |
 |   +-------------------------------------------------------------------+   |
 +---------------------------------------------------------------------------+
```

### Data Flow

```
+----------+     +---------+     +------------+     +------------+
|  Client  |---->|   IGW   |---->|  Prefill   |---->|   Decode   |
| Request  |     |  (EPP)  |     |    Pod     |     |    Pod     |
+----------+     +---------+     +------------+     +------------+
                      |               |                   |
                      |               |    KV Transfer    |
                      |               |<------------------|
                      |               |      (NIXL)       |
                      |               |                   |
                      v               v                   v
              +---------------------------------------------+
              |              Shared State                   |
              |  - Prefix cache metadata                    |
              |  - Model weights (read-only)                |
              |  - Queue state and metrics                  |
              +---------------------------------------------+
```

---

## Trust Boundaries

### Boundary Definitions

| Boundary ID | Name | Description |
|-------------|------|-------------|
| TB-1 | Cluster Ingress | External network to Kubernetes cluster |
| TB-2 | Gateway Service | Public service to IGW pod |
| TB-3 | IGW to vLLM | Scheduler to model server communication |
| TB-4 | vLLM to vLLM | Inter-pod KV-cache transfers via NIXL |
| TB-5 | Pod to Cache | vLLM pods to distributed cache (LMCache) |
| TB-6 | Pod to Storage | Model artifact loading from storage |
| TB-7 | Control Plane | Kubernetes API interactions |

### Trust Boundary Diagram

```
                    EXTERNAL NETWORK
                          |
                    ======|======  TB-1: Cluster Ingress
                          |
                    +-----v-----+
                    |  Ingress  |
                    |Controller |
                    +-----+-----+
                          |
                    ======|======  TB-2: Gateway Service
                          |
                    +-----v-----+
                    |    IGW    |
                    |    EPP    |
                    +-----+-----+
                          |
          +---------------+---------------+
          |               |               |
    ======|=======  ======|=======  ======|=======  TB-3: IGW to vLLM
          |               |               |
    +-----v-----+   +-----v-----+   +-----v-----+
    |  Prefill  |   |  Decode   |   |  Unified  |
    |    Pod    |<->|    Pod    |<->|    Pod    |
    +-----+-----+   +-----+-----+   +-----+-----+
          |               |               |
          +---------------+---------------+
                          |
                    ======|======  TB-4: vLLM to vLLM (NIXL)
                          |
                    ======|======  TB-5: Pod to Cache
                          |
                    +-----v-----+
                    |  LMCache  |
                    |  (Redis)  |
                    +-----------+
```

---

## Threat Actors

### Actor Profiles

| Actor | Motivation | Capabilities | Target Assets |
|-------|-----------|--------------|---------------|
| **External Attacker** | Data theft, service disruption, resource hijacking | Network access, exploit development | API endpoints, model outputs, compute resources |
| **Malicious User** | Prompt injection, data exfiltration, abuse | Authenticated API access, crafted inputs | Model behavior, cached data, other users' data |
| **Compromised Pod** | Lateral movement, persistence | Container-level access, network access | Other pods, secrets, KV cache data |
| **Insider Threat** | Data theft, sabotage | Cluster access, configuration access | Model weights, infrastructure, secrets |
| **Supply Chain Attacker** | Backdoors, model poisoning | Upstream repository access | Container images, model artifacts, dependencies |

### Actor Capabilities Matrix

```
                    | Network | Cluster | Pod   | Model  | Physical
Actor               | Access  | Access  |Access | Access | Access
--------------------|---------|---------|-------|--------|----------
External Attacker   |    *    |    -    |   -   |   -    |    -
Malicious User      |    *    |    -    |   -   |   *    |    -
Compromised Pod     |    *    |    ~    |   *   |   *    |    -
Insider Threat      |    *    |    *    |   *   |   *    |    ~
Supply Chain        |    -    |    ~    |   ~   |   *    |    -

* = Full capability   ~ = Partial capability   - = No capability
```

---

## Attack Surface Analysis

### API Endpoints

| Endpoint | Component | Protocol | Authentication | Risk Level |
|----------|-----------|----------|----------------|------------|
| `/v1/completions` | IGW | HTTP/gRPC | API Key/mTLS | High |
| `/v1/chat/completions` | IGW | HTTP/gRPC | API Key/mTLS | High |
| `/health` | IGW/vLLM | HTTP | None | Low |
| `/metrics` | All | HTTP | Network Policy | Medium |
| vLLM internal | vLLM pods | gRPC | mTLS (optional) | High |
| NIXL transfer | vLLM pods | RDMA/TCP | Network isolation | Critical |
| LMCache | Cache pods | Redis protocol | Password/mTLS | High |

### Kubernetes Resources

| Resource | Sensitivity | Threat Vector |
|----------|-------------|---------------|
| Secrets (API keys) | Critical | Unauthorized access, exfiltration |
| ConfigMaps (model config) | Medium | Configuration tampering |
| PersistentVolumes (models) | High | Model theft, poisoning |
| ServiceAccounts | High | Privilege escalation |
| NetworkPolicies | Critical | Policy bypass, lateral movement |

### Network Interfaces

| Interface | Direction | Data Sensitivity | Encryption |
|-----------|-----------|------------------|------------|
| Client to IGW | Ingress | User prompts, responses | TLS required |
| IGW to vLLM | Internal | Inference requests | Recommended |
| vLLM to vLLM | Internal | KV-cache data | Network isolation |
| vLLM to LMCache | Internal | Cached prefixes | Recommended |
| Pods to Model Storage | Internal | Model weights | Recommended |

### GPU/Accelerator Attack Surface

| Vector | Description | Risk |
|--------|-------------|------|
| GPU memory isolation | Side-channel attacks between workloads | Medium |
| RDMA/NIXL transfers | Unencrypted GPU-direct transfers | High |
| Model weights in VRAM | Memory dump attacks | High |
| KV-cache in HBM | Cached prompt/response data | High |

---

## STRIDE Threat Analysis

### Spoofing (S)

| ID | Threat | Component | Impact | Likelihood |
|----|--------|-----------|--------|------------|
| S-1 | Client identity spoofing | IGW | Unauthorized access | Medium |
| S-2 | Pod identity spoofing | vLLM | Request manipulation | Low |
| S-3 | Service impersonation | IGW/vLLM | Man-in-the-middle | Low |
| S-4 | API key theft and reuse | IGW | Account takeover | Medium |

**S-1: Client Identity Spoofing**
- *Attack*: Attacker obtains or guesses API keys to impersonate legitimate users
- *Impact*: Unauthorized inference requests, billing fraud, data access
- *Mitigation*: Strong API key generation, rotation policies, rate limiting per key

**S-2: Pod Identity Spoofing**
- *Attack*: Compromised pod impersonates another pod to redirect traffic
- *Impact*: Traffic interception, cache poisoning
- *Mitigation*: mTLS between pods, Kubernetes service account validation

### Tampering (T)

| ID | Threat | Component | Impact | Likelihood |
|----|--------|-----------|--------|------------|
| T-1 | Request modification | IGW | Altered inference | Medium |
| T-2 | Response modification | IGW/vLLM | Incorrect outputs | Medium |
| T-3 | KV-cache poisoning | LMCache/NIXL | Corrupted inference | Medium |
| T-4 | Model weight tampering | Storage | Backdoored model | Low |
| T-5 | Configuration tampering | ConfigMaps | Altered behavior | Medium |

**T-3: KV-Cache Poisoning**
- *Attack*: Attacker injects malicious KV-cache entries via compromised pod or cache
- *Impact*: All subsequent requests using poisoned cache produce incorrect/malicious outputs
- *Mitigation*: Cache integrity verification, pod isolation, cache access controls

**T-4: Model Weight Tampering**
- *Attack*: Modification of model files in storage to introduce backdoors
- *Impact*: Model produces attacker-controlled outputs for specific triggers
- *Mitigation*: Model checksums, read-only mounts, signed model artifacts

### Repudiation (R)

| ID | Threat | Component | Impact | Likelihood |
|----|--------|-----------|--------|------------|
| R-1 | Inference request denial | IGW | Audit gaps | Medium |
| R-2 | Administrative action denial | Control Plane | Accountability loss | Low |
| R-3 | Data access denial | All | Compliance failure | Medium |

**R-1: Inference Request Denial**
- *Attack*: User denies making specific inference requests
- *Impact*: Unable to attribute problematic outputs, billing disputes
- *Mitigation*: Comprehensive request logging with tamper-proof storage

### Information Disclosure (I)

| ID | Threat | Component | Impact | Likelihood |
|----|--------|-----------|--------|------------|
| I-1 | Prompt leakage via cache | LMCache | Privacy breach | High |
| I-2 | Response leakage | IGW/vLLM | Data exposure | Medium |
| I-3 | Model weight extraction | vLLM | IP theft | Medium |
| I-4 | System prompt disclosure | vLLM | Security bypass | High |
| I-5 | GPU memory side-channel | vLLM | Cross-tenant leak | Medium |
| I-6 | Metrics exposure | All | Infrastructure info | Low |

**I-1: Prompt Leakage via Cache**
- *Attack*: Attacker queries cache to retrieve other users' prompts
- *Impact*: Exposure of sensitive user data, PII, proprietary information
- *Mitigation*: Cache isolation per tenant, encryption at rest, TTL policies

**I-4: System Prompt Disclosure**
- *Attack*: Crafted prompts trick model into revealing system prompts
- *Impact*: Attacker learns security controls, can craft bypass attempts
- *Mitigation*: Input validation, output filtering, system prompt protection

### Denial of Service (D)

| ID | Threat | Component | Impact | Likelihood |
|----|--------|-----------|--------|------------|
| D-1 | Request flooding | IGW | Service unavailability | High |
| D-2 | Resource exhaustion | vLLM | GPU/memory exhaustion | High |
| D-3 | Cache flooding | LMCache | Cache eviction, slowdown | Medium |
| D-4 | Queue saturation | IGW | Request timeout | Medium |
| D-5 | Malformed request attacks | IGW/vLLM | Processing hang | Medium |

**D-2: Resource Exhaustion**
- *Attack*: Sending requests designed to consume maximum GPU memory/compute
- *Impact*: Service degradation or unavailability for all users
- *Mitigation*: Request size limits, timeout enforcement, resource quotas

### Elevation of Privilege (E)

| ID | Threat | Component | Impact | Likelihood |
|----|--------|-----------|--------|------------|
| E-1 | Container escape | vLLM pods | Node compromise | Low |
| E-2 | ServiceAccount abuse | All pods | Cluster access | Medium |
| E-3 | RBAC misconfiguration | Control Plane | Unauthorized actions | Medium |
| E-4 | GPU driver exploitation | vLLM | Kernel access | Low |

**E-2: ServiceAccount Abuse**
- *Attack*: Compromised pod uses overly permissive ServiceAccount
- *Impact*: Access to secrets, other namespaces, control plane
- *Mitigation*: Least-privilege ServiceAccounts, pod security policies

---

## LLM-Specific Threats (OWASP LLM Top 10)

### LLM01: Prompt Injection

**Direct Prompt Injection**
- *Attack*: Malicious instructions embedded in user input override system behavior
- *llm-d Impact*: Model ignores safety guidelines, reveals system prompts
- *Attack Vector*: `/v1/completions` and `/v1/chat/completions` endpoints
- *Mitigation*:
  - Input sanitization at IGW layer
  - System prompt isolation
  - Output filtering for sensitive patterns
  - Separate user and system instruction processing

**Indirect Prompt Injection**
- *Attack*: Malicious content in external data sources influences model behavior
- *llm-d Impact*: RAG pipelines or cached contexts contain attack payloads
- *Attack Vector*: Prefix cache (LMCache), retrieved context
- *Mitigation*:
  - Content validation for cached prefixes
  - Source tracking for cache entries
  - Sandboxed processing of external content

### LLM02: Sensitive Information Disclosure

**Training Data Leakage**
- *Attack*: Prompts designed to extract memorized training data
- *llm-d Impact*: PII, proprietary data, credentials exposed
- *Mitigation*: Output filtering, differential privacy awareness

**Cross-Tenant Data Leakage**
- *Attack*: Multi-tenant cache allows access to other tenants' data
- *llm-d Impact*: Prefix cache contains sensitive prompts from other users
- *Mitigation*:
  - Tenant-isolated cache namespaces
  - Strict cache key segregation
  - Encryption of cached content

### LLM03: Supply Chain Vulnerabilities

**Model Supply Chain**
- *Attack*: Compromised model weights from untrusted sources
- *llm-d Impact*: Backdoored models produce malicious outputs
- *Mitigation*:
  - Model provenance verification
  - Cryptographic signatures for model artifacts
  - Trusted model registries only

**Container Supply Chain**
- *Attack*: Malicious container images for vLLM, IGW, or dependencies
- *llm-d Impact*: Compromised inference infrastructure
- *Mitigation*:
  - Signed container images
  - Vulnerability scanning in CI/CD
  - Minimal base images

### LLM04: Data and Model Poisoning

**Cache Poisoning**
- *Attack*: Injection of malicious entries into prefix cache
- *llm-d Impact*: All requests using poisoned cache affected
- *Mitigation*:
  - Cache entry validation
  - Write access controls
  - Anomaly detection for cache modifications

**KV-Cache Manipulation**
- *Attack*: Tampering with KV-cache during NIXL transfers
- *llm-d Impact*: Corrupted inference state
- *Mitigation*:
  - Integrity verification for transfers
  - Network isolation for NIXL traffic

### LLM05: Improper Output Handling

**Output Injection**
- *Attack*: Model outputs containing executable code or injection payloads
- *llm-d Impact*: Client applications vulnerable to injection attacks
- *Mitigation*:
  - Output encoding at IGW
  - Content-type enforcement
  - Client-side output validation guidance

### LLM06: Excessive Agency

**Tool Abuse**
- *Attack*: Model with tool access performs unauthorized actions
- *llm-d Impact*: If integrated with external tools, unauthorized operations
- *Mitigation*:
  - Strict tool permission boundaries
  - Human-in-the-loop for sensitive operations
  - Action audit logging

### LLM07: System Prompt Leakage

**Prompt Extraction**
- *Attack*: Adversarial prompts extract system prompt content
- *llm-d Impact*: Security controls, business logic exposed
- *Mitigation*:
  - System prompt protection mechanisms
  - Output filtering for prompt patterns
  - Monitoring for extraction attempts

### LLM08: Vector and Embedding Weaknesses

**Embedding Manipulation**
- *Attack*: Crafted inputs produce misleading embeddings
- *llm-d Impact*: If using embedding-based routing, incorrect model selection
- *Mitigation*:
  - Robust embedding models
  - Input validation
  - Anomaly detection for embedding space

### LLM09: Misinformation

**Hallucination Exploitation**
- *Attack*: Prompts designed to generate convincing misinformation
- *llm-d Impact*: Platform used for misinformation generation at scale
- *Mitigation*:
  - Output quality monitoring
  - Factual grounding mechanisms
  - Usage policy enforcement

### LLM10: Unbounded Consumption

**Resource Exhaustion**
- *Attack*: Requests designed to maximize resource consumption
- *llm-d Impact*: GPU memory exhaustion, queue saturation, billing abuse
- *Attack Vectors*:
  - Very long input sequences
  - Maximum output length requests
  - Concurrent request flooding
- *Mitigation*:
  - Token limits per request
  - Rate limiting per user/key
  - Queue depth limits
  - Cost attribution and budgets
  - Automatic scaling with limits

---

## Component-Specific Threats

### Inference Gateway (IGW/EPP)

| Threat | Description | Severity | Mitigation |
|--------|-------------|----------|------------|
| Authentication bypass | Weak or missing auth allows unauthorized access | Critical | Strong API key validation, mTLS |
| Request smuggling | Malformed requests bypass security controls | High | Strict request parsing, input validation |
| Information leakage | Error messages reveal internal details | Medium | Sanitized error responses |
| Cache timing attacks | Timing differences reveal cache state | Medium | Constant-time operations |
| Model enumeration | Probing reveals available models | Low | Model listing restrictions |

### vLLM Model Servers

| Threat | Description | Severity | Mitigation |
|--------|-------------|----------|------------|
| Memory exhaustion | Large requests exhaust GPU memory | High | Request size limits, memory monitoring |
| Model extraction | Side-channels leak model weights | High | Memory isolation, access controls |
| Prompt injection | Malicious prompts alter behavior | High | Input sanitization, output filtering |
| Container escape | Vulnerabilities allow node access | Critical | Security contexts, runtime protection |
| Sidecar injection | Malicious containers in pod | High | Pod security policies |

### KV-Cache and Prefix Cache

| Threat | Description | Severity | Mitigation |
|--------|-------------|----------|------------|
| Cache poisoning | Malicious entries affect inference | High | Entry validation, access controls |
| Cross-tenant leakage | Tenant data accessible to others | Critical | Cache isolation, encryption |
| Cache flooding | Eviction of legitimate entries | Medium | Cache quotas, eviction policies |
| Timing attacks | Cache hits reveal user patterns | Medium | Noise injection, constant-time access |

### NIXL Transfer Layer

| Threat | Description | Severity | Mitigation |
|--------|-------------|----------|------------|
| Data interception | KV-cache data exposed in transit | High | Network isolation, encryption |
| Transfer manipulation | Modified KV-cache data | High | Integrity verification |
| Unauthorized transfers | Rogue pods initiate transfers | Medium | Pod identity verification |

### Variant Autoscaler

| Threat | Description | Severity | Mitigation |
|--------|-------------|----------|------------|
| Scaling manipulation | Attacker influences scaling decisions | Medium | Decision validation, limits |
| Resource exhaustion | Trigger excessive scaling for cost | Medium | Scaling caps, anomaly detection |
| Configuration tampering | Alter autoscaler parameters | High | RBAC, configuration integrity |

---

## Mitigations and Recommendations

### Network Security

**Required:**
- [ ] TLS 1.3 for all external communications
- [ ] Network Policies restricting pod-to-pod communication
- [ ] Ingress controller with rate limiting
- [ ] Egress restrictions for model server pods

**Recommended:**
- [ ] mTLS between all internal services
- [ ] Network segmentation for cache layer
- [ ] RDMA traffic isolation for NIXL
- [ ] Web Application Firewall (WAF) at ingress

### Authentication and Authorization

**Required:**
- [ ] API key authentication for inference endpoints
- [ ] RBAC for Kubernetes resources
- [ ] ServiceAccount per component with minimal permissions
- [ ] Secret encryption at rest (KMS)

**Recommended:**
- [ ] mTLS for service-to-service authentication
- [ ] API key rotation policies
- [ ] JWT tokens with short expiry for users
- [ ] Audit logging for all authentication events

### Input Validation

**Required:**
- [ ] Maximum token/character limits on inputs
- [ ] Request size limits at IGW
- [ ] Timeout enforcement for inference requests
- [ ] Content-type validation

**Recommended:**
- [ ] Input sanitization for injection patterns
- [ ] Schema validation for API requests
- [ ] Rate limiting per API key and IP
- [ ] Blocklist for known attack patterns

### Output Filtering

**Required:**
- [ ] Response size limits
- [ ] Error message sanitization
- [ ] Content-type enforcement

**Recommended:**
- [ ] PII detection and redaction
- [ ] System prompt leakage detection
- [ ] Output encoding for injection prevention
- [ ] Anomaly detection for unusual outputs

### Resource Protection

**Required:**
- [ ] GPU memory limits per request
- [ ] CPU and memory limits on pods
- [ ] Request queue depth limits
- [ ] Concurrent request limits per user

**Recommended:**
- [ ] Request priority queuing
- [ ] Cost attribution per user/key
- [ ] Automatic circuit breakers
- [ ] Graceful degradation policies

### Monitoring and Logging

**Required:**
- [ ] Request/response logging (with PII considerations)
- [ ] Authentication event logging
- [ ] Error and exception logging
- [ ] Resource utilization metrics

**Recommended:**
- [ ] Security event correlation (SIEM)
- [ ] Anomaly detection for request patterns
- [ ] Real-time alerting for security events
- [ ] Audit trail for administrative actions

### Supply Chain Security

**Required:**
- [ ] Container image scanning
- [ ] Dependency vulnerability scanning
- [ ] Signed container images

**Recommended:**
- [ ] Model artifact signatures
- [ ] SBOM (Software Bill of Materials)
- [ ] Reproducible builds
- [ ] Private container registry

### Cache Security

**Required:**
- [ ] Cache authentication (LMCache/Redis)
- [ ] Network isolation for cache
- [ ] TTL policies for cached entries

**Recommended:**
- [ ] Cache encryption at rest
- [ ] Tenant isolation in cache keys
- [ ] Cache integrity verification
- [ ] Audit logging for cache operations

---

## Security Assumptions

### Trusted Components

1. **Kubernetes Control Plane**: Assumed to be securely configured and operated
2. **Container Runtime**: Assumed to provide proper isolation between containers
3. **GPU Drivers**: Assumed to be up-to-date and from trusted sources
4. **Base Container Images**: Assumed to be from trusted registries and scanned

### Operational Assumptions

1. **Network Isolation**: Cluster network provides basic isolation between namespaces
2. **Secret Management**: Kubernetes secrets are encrypted at rest
3. **RBAC Enforcement**: Kubernetes RBAC is properly configured and enforced
4. **Log Integrity**: Audit logs are stored securely and tamper-evident

### Model Assumptions

1. **Model Provenance**: Model weights are from verified, trusted sources
2. **Model Behavior**: Model has been evaluated for safety and alignment
3. **Fine-tuning Security**: Any fine-tuning performed in secure environments

---

## Out of Scope

The following are explicitly out of scope for this threat model:

1. **Kubernetes Cluster Security**: Security of the underlying Kubernetes cluster
2. **Cloud Provider Security**: Security of the cloud infrastructure (AWS, GCP, Azure)
3. **Physical Security**: Physical access to data centers or hardware
4. **Model Training**: Security of model training pipelines and data
5. **Client Applications**: Security of applications consuming llm-d APIs
6. **User Authentication Systems**: External identity providers (OAuth, OIDC)
7. **Data Sovereignty**: Legal and compliance requirements for data location
8. **Cryptographic Primitives**: Security of underlying cryptographic implementations

---

## References

### Standards and Frameworks

- [OWASP LLM Top 10 2025](https://genai.owasp.org/llm-top-10/)
- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [OSSF Security Insights Specification](https://github.com/ossf/security-insights-spec)
- [CIS Kubernetes Benchmarks](https://www.cisecurity.org/benchmark/kubernetes)

### Related Documentation

- [llm-d Security Policy](SECURITY.md)
- [llm-d Security Contacts](SECURITY_CONTACTS.md)
- [vLLM Security Documentation](https://docs.vllm.ai/)
- [Kubernetes Security Best Practices](https://kubernetes.io/docs/concepts/security/)

### Research and Publications

- NVIDIA GPU Security Best Practices
- Container Security Guidance (NIST SP 800-190)
- AI/ML Security Guidelines (MITRE ATLAS)

---

## Document Information

| Field | Value |
|-------|-------|
| Version | 1.0 |
| Created | 2025-01 |
| Authors | Community Contributors |
| Review Status | Initial Release |
| Next Review | 2025-07 |

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-01 | Initial comprehensive threat model |
