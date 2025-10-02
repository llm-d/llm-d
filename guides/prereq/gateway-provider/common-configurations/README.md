# Gateway Provider Common Configurations

Each guide pulls in these gateway configurations. They are meant to abstract all the basic values that get set if you are using a gateway of a certain type. Performance related configurations should live in [benchmarking.yaml](./benchmarking.yaml), and can then be combined with environment specific settings to create a benchmarking setup for that given environment. For an example of that see the [inference-scheduling helmfile](../../../inference-scheduling/helmfile.yaml.gotmpl).
