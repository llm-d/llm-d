# Optimized Baseline

Traditional HTTP requests are fast, uniform, and cheap. Standard round-robin request scheduling strategies balance this load well.

LLM requests break all three assumptions. They are:
* **Multi-turn** - conversations and agentic tool loops send the same growing prefix repeatedly
* **Slow** - a single request can take over a minute generating tokens
* **Non-uniform** - range from 1000s of reasoning tokens to a 100k+ context tokens

EPP injects awareness of the LLM-workload into the load-balancing layer considering **prefix-cache affinity** and **server load metrics**.

> [!NOTE]
> This guide demonstrates one approach to prefix- and load-aware scheduling. EPP supports other options as well, including session affinity and active request based scheduling, which make no assumptions about the scheduler's ability to parse the request or probe the servers. See [EPP configuration](../architecture/core/epp/configuration.md) for more details on the available scorers.

## Deploy

See the [optimized baseline guide](https://github.com/llm-d/llm-d/tree/main/guides/optimized-baseline) for manifests and step-by-step deployment.

## Architecture

### Prefix-Aware Scheduling

EPP maintains a view of each endpoints's prefix-cache state in memory. When a request arrives, it identifies which pod already holds the matching prefix in KV-cache and routes the request there. For multi-turn workloads, this optimization is critical to avoid excessive recomputation in a scale-out setting.

![Prefix-Aware Routing](../../assets/prefix-aware-routing.svg)

### Load-Aware Scheduling

EPP continuously probes each endpoints's metrics by scraping `/metrics` at a regular interval (50ms default). It scores endpoints on queue depth, running requests, and KV-cache utilization to schedule requests to the endpoint with the lowest load, avoiding hotspots caused by heterogeneous request patterns.

![Load-Aware Routing](../../assets/load-aware-routing.svg)

## Further Reading

See [EPP Architecture](../architecture/core/epp/README.md) for more details.
