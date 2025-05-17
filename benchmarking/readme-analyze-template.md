# Benchmark Analysis Plots

This directory contains visualization files generated from the benchmark results.

## Latency Analysis

![Latency Analysis](latency_analysis.png)

This plot shows four key latency metrics across different QPS (Queries Per Second) levels:

1. **Time to First Token (TTFT) vs QPS**
   - Shows how quickly the model starts generating tokens
   - Lower values indicate faster initial response

2. **Generation Time vs QPS**
   - Shows the time taken to generate the complete response
   - Helps identify performance bottlenecks at different load levels

3. **Total Time (TTFT + Generation) vs QPS**
   - Shows the complete end-to-end latency
   - Combines initial response time and generation time

4. **Token Generation Rate vs QPS**
   - Shows how many tokens are generated per second
   - Higher values indicate better throughput

## Throughput Analysis

![Throughput Analysis](throughput_analysis.png)

This plot shows two throughput-related metrics:

1. **Throughput (Tokens/Second) vs QPS**
   - Shows the overall token processing rate
   - Combines both prompt and generation tokens
   - Higher values indicate better performance

2. **Token Counts vs QPS**
   - Shows the average number of prompt and generation tokens
   - Helps understand the input/output token ratio
   - Useful for capacity planning 
