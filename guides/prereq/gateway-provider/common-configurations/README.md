# Gateway Provider Common Configurations

Each guide pulls in these gateway configurations. They are meant to abstract all the basic values that get set if you are using a gateway of a certain type.

For llm-d inference guides, both the deprecated `kgateway` mode and the preferred `agentgateway` mode currently set `gateway.gatewayClassName: agentgateway`.
The difference between those modes is how the gateway provider stack is installed:

* `kgateway` installs the deprecated llm-d `kgateway` path via the `ghcr.io/kgateway-dev/charts/agentgateway*` charts at `v2.2.3`. This mode is deprecated in llm-d and will be removed in the next release.
* `agentgateway` installs the `agentgateway` control plane and data plane. This is the preferred self-installed inference path.
