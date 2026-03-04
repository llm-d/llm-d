# Proposal: Prism - Performance analysis for distributed inference systems

## Status: Accepted

## Motivation

Currently, AI Platform Engineers and ML Engineers face significant challenges assembling the full end-to-end inference serving stack for their applications, leading to lengthy, manual evaluation cycles, suboptimal performance, and unnecessarily high friction & costs. While many benchmarks and tools exist, the data follows different methodologies, formats, and is often scattered across disconnected docs, spreadsheets, or vendor-specific sites.

**Prism** aims to solve this by providing a streamlined, intuitive interface for discovering, comparing, and reproducing benchmarks for state-of-the-art distributed inference systems that scale from single-node to complex multi-node disaggregated environments and agentic & RL systems.

We are proposing to donate the Prism codebase, previously at https://github.com/seanhorgan/prism, and in the process of [being moved to the `llm-d` organization](https://github.com/llm-d/llm-d-prism) to:

1.  Establish a community-driven reference for visualizing performance of distributed inference systems.
2.  Allow the community to contribute high-quality benchmarks from various sources, e.g. results that conform to the standard `llm-d` benchmark format.
3.  Assist `llm-d` users in visualizing results of their benchmark sweeps
4.  Make it easier to validate benchmarks and deploy optimized `llm-d` stacks.

## Goals

*   **Discoverability:** Provide a public site for viewing validated performance profiles for popular open-weight models (e.g., Qwen, Kimi, Gemma, DeepSeek) on various machine types/accelerators, inference serving components, and optimizations.
*   **Comparison:** Enable side-by-side comparison of profiles based on a variety of performance, quality, and cost data.
*   **Validation:** Enable users to reproduce benchmarks to validate performance on their own infrastructure.

## Non-Goals

*   **Benchmarking Hardness:** Prism does not replace the actual benchmarking engines and frameworks (like `inference-perf` and `llm-d-benchmark`) but rather consumes their output for analysis.
*   **Model Serving:** Prism is not a model serving platform; it generates visualizations of the performance for existing serving platforms (vLLM, TGI, etc.).

## Proposal

We propose the following:

1.  Create a new repository `llm-d/prism` to host the source code for the Prism application.
2.  Deploy Prism as a publicly accessible application that is linked to from the `llm-d.ai` website.
3.  Create a process for the full lifecycle of benchmarks (e.g. ingesting, validating, publishing, archiving) new benchmarks from `llm-d` feature developers and community members, and making them accessible through Prism.

## User Stories (CUJs)

The following Critical User Journeys highlight Prism's core capabilities, aligned with the `llm-d` user roles:

### Story 1: Multi-Source Data Unification
*As a **Feature Developer**, I need to compare my internal experimental results against official public benchmarks to validate my tuning efforts.*

*   **Workflow:** I open the **Data Connection** slide-over menu. I enable the "LLM-D Results Store" (Google Drive) connection to load official baselines, and then use the "Paste Results" feature to ingest a raw JSON file from my local `inference-perf` run.
*   **Outcome:** Both datasets are immediately indexed and available in the **Unified Benchmark Filter**, allowing me to overlay my private data against the official "well-lit paths" without setting up a database.

### Story 2: Architecture Validation (P/D Disaggregation)
*As a **Config Tuner**, I need to determine if the operational complexity of disaggregated serving (splitting Prefill and Decode nodes) yields sufficient performance gains over standard replicas.*

*   **Workflow:** I use the **P/D Disaggregation** controls in the filter panel to select specific P:D node ratios (e.g., `1P:1D`, `2P:4D`) and compare them against standard "Aggregated" benchmarks.
*   **Outcome:** I can visually verify if the disaggregated setup offers better tail latency (TTFT/TPOT) compared to a simpler replicated setup for my specific sequence length bucket.

### Story 3: Hardware Normalization & Scaling
*As a **Stack Operator**, I want to compare the raw efficiency of different accelerators regardless of the cluster size to inform future purchasing decisions.*

*   **Workflow:** I select benchmarks for an 8-chip H100 machine and a 4-chip B200 machine. I enable the **"Per Chip" normalization** toggle.
*   **Outcome:** All throughput and QPS metrics are scaled by the accelerator count, allowing me to make a fair, apples-to-apples comparison of per-chip efficiency across different hardware scales.

### Story 4: Efficiency & Cost Analysis
*As a **Solutions Architect** or **Analyst**, I want to identify the most cost-effective serving infrastructure for a high-volume model, accounting for different purchasing commitments.*

*   **Workflow:** I configure the **Chart** to "Cost" mode. I toggle the pricing model from "On-Demand" to "CUD-3y" (Committed Use Discount) to see long-term efficiency.
*   **Outcome:** The chart updates to show the **Pareto Frontier**, highlighting the specific hardware and serving stack combination that delivers the lowest cost per million tokens at my required throughput level.

## Risks and Mitigations

*   **Data Freshness:** Benchmarks age quickly.
    *   *Mitigation:* The UI is designed to be data-driven via APIs to ensure benchmarks are updated automatically as they are ingested, validated, and published.
*   **Vendor Bias:** Currently, the data includes benchmarks sources from Google Cloud (GKE/GIQ).
    *   *Mitigation:* Moving to `llm-d` allows the community to define schemas for importing benchmarks from other sources (e.g., InferenceMax, self-hosted runs).

## Design Details

### Architecture
Prism is architected as a high-performance Single Page Application (SPA) backed by a lightweight Node.js proxy.

*   **Frontend:** React-based dashboard featuring a high-density filtering system and a WebGL-accelerated charting engine.
*   **BFF Proxy (Backend for Frontend):** A Node.js service that handles authentication and proxies requests to external APIs (like Google Cloud). It supports Application Default Credentials (ADC) for seamless access to organization-wide storage buckets without exposing tokens to the client.
*   **Persistence:** User preferences, active data connections, and filter states are persisted in local storage, allowing for a tailored, stateful work session.

### Data Ingestion: Catalog-First Architecture
Prism utilizes a "Catalog-First" approach to manage disparate benchmark sources via a unified ingestion layer. This allows users to mix-and-match official baselines with private experimental data.

*   **Connectors:**
    *   **Recursive Storage Indexing:** A crawler that indexes `llm-d` benchmark results stored in Google Drive or GCS Buckets, supporting deep directory structures.
    *   **API Integrations:** Native support for the GKE Inference Quickstart (GIQ) API with pagination and batching.
    *   **Local/Ephemeral:** A "Paste Results" feature for immediate ingestion of raw JSON logs from `inference-perf` or `Lohi` pipelines, processed entirely client-side for privacy.
*   **Multi-Schema Support:** The ingestion engine natively parses both v0.1 and v0.2 `llm-d-benchmark` report schemas, automatically mapping them to the internal data model.

### Normalization Engine
To enable "apples-to-apples" comparison across heterogeneous sources, Prism applies a normalization pipeline upon data load:

*   **Entity Resolution:** Maps divergent naming conventions (e.g., `gpt-oss-120b-bf16` vs. `gpt-oss-120b`) to canonical Model IDs.
*   **Hardware Grouping:** Standardizes accelerator names (e.g., grouping `nvidia-h100-80gb` and `H100`) to ensure consistent filtering.
*   **Metric Derivation:**
    *   **NTPOT (Normalized Time Per Output Token):** Where explicit metrics are missing, Prism derives NTPOT from steady-state throughput to provide a comparable latency metric across different serving frameworks.

### Visualization & Analysis
The core of Prism is a multi-dimensional chart designed for technical analysis & decision-making.

*   **Hardware Normalization:** A "Per Chip" toggle that scales throughput and QPS metrics by accelerator count, allowing users to compare the raw efficiency of a single TPU v5e chip against an H100 GPU regardless of cluster size.
*   **P/D Architecture Analysis:** Specialized controls to filter and compare Disaggregated (Prefill/Decode split) architectures against standard Replicated setups, visualizing the latency impact of different P:D node ratios.
*   **Pareto Frontiers:** Dynamic efficiency lines that automatically highlight "best in class" configurations for specific trade-offs (e.g., Throughput vs. Cost).

## Alternatives

*   **Spreadsheets:** Hard to maintain, difficult to visualize multidimensional data (cost vs. latency vs. throughput), and cannot auto-generate deployment manifests.
*   **Vendor Consoles:** A standalone open-source version allows for community contribution, broader access without authentication, and support for multi-cloud/hybrid benchmark data.
*   **CLI Tools:** While useful for running benchmarks, CLIs are poor for high-level discovery and comparison of hundreds of potential configurations.

## Upgrade Strategy

This is a new project donation. The initial code will be migrated from the private/personal repository [https://github.com/seanhorgan/prism](https://github.com/seanhorgan/prism) to `llm-d/prism`. Future upgrades will follow standard PR/Review processes within the `llm-d` organization.
