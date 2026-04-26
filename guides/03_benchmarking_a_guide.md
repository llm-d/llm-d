# Benchmarking a Guide - EPHEMERAL DOC

This document will contain the shared benchmarking instructions for all guides. It is kept minimal during the refactor and will be fleshed out as more guides are migrated.

## Running Benchmarks

To run benchmarks against an installed llm-d guide, you need [run_only.sh](https://github.com/llm-d/llm-d-benchmark/blob/main/existing_stack/run_only.sh), a template file from the guide's `benchmark-templates/` directory, and optionally a PVC to store results.

```bash
curl -L -O https://raw.githubusercontent.com/llm-d/llm-d-benchmark/main/existing_stack/run_only.sh
chmod u+x run_only.sh
```

Select a benchmark template for your guide:

```bash
select f in $(
    curl -s https://api.github.com/repos/llm-d/llm-d/contents/guides/<guide>/benchmark-templates?ref=main |
    sed -n '/[[:space:]]*"name":[[:space:]][[:space:]]*"\([[:alnum:]].*\.yaml\)".*/ s//\1/p'
  ); do
  curl -LJO "https://raw.githubusercontent.com/llm-d/llm-d/main/guides/<guide>/benchmark-templates/$f"
  break
done
```

Configure and run:

```bash
export NAMESPACE=<your-namespace>
export BENCHMARK_PVC=<your-pvc>        # optional
export GATEWAY_SVC=<your-service-name>
envsubst < <template>.yaml > config.yaml
./run_only.sh -c config.yaml
```

For detailed benchmark documentation, see the [benchmark README](../helpers/benchmark.md) and the [benchmark report format](https://github.com/llm-d/llm-d-benchmark/blob/main/docs/benchmark_report.md).

Individual guide benchmark results are found in each guide's README.
