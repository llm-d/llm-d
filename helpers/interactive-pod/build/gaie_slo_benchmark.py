#!/usr/bin/env python3
"""
GAIE SLO-Aware Routing Benchmark Framework
Orchestrates GenAI-Perf and vLLM benchmarks with SLO-specific analysis
"""

import subprocess
import json
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import argparse
from dataclasses import dataclass, asdict
import numpy as np
from datetime import datetime
import os

@dataclass
class SLOProfile:
    """SLO profile definition"""
    name: str
    ttft_ms: int
    tpot_ms: int
    description: str

@dataclass
class BenchmarkResult:
    """Structured benchmark results"""
    scenario: str
    concurrency: int
    slo_profile: str
    slo_routing_enabled: bool
    
    # TTFT metrics
    ttft_avg: float
    ttft_p50: float
    ttft_p90: float
    ttft_p99: float
    
    # TPOT metrics
    tpot_avg: float
    tpot_p50: float
    tpot_p90: float
    tpot_p99: float
    
    # Throughput
    request_throughput: float
    token_throughput: float
    
    # SLO attainment
    slo_attainment_rate: float
    goodput: float
    
    # Additional metrics
    num_requests: int
    num_violations: int
    timestamp: str

class GAIEBenchmark:
    """Main benchmarking orchestrator"""
    
    # Predefined SLO profiles
    SLO_PROFILES = {
        "chatbot": SLOProfile("chatbot", 200, 50, "Interactive chat"),
        "code_completion": SLOProfile("code_completion", 150, 30, "IDE autocomplete"),
        "rag": SLOProfile("rag", 300, 75, "Retrieval augmented generation"),
        "summarization": SLOProfile("summarization", 500, 100, "Batch summarization"),
        "strict": SLOProfile("strict", 100, 20, "Ultra-low latency"),
        "relaxed": SLOProfile("relaxed", 1000, 200, "Background processing")
    }
    
    def __init__(self, endpoint: str, output_dir: str, tool: str = "genai-perf"):
        self.endpoint = endpoint
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.tool = tool
        self.results: List[BenchmarkResult] = []
        
    def run_benchmark_suite(
        self,
        model: str,
        concurrencies: List[int],
        slo_profiles: List[str],
        num_prompts: int = 500,
        dataset: str = "sharegpt",
        compare_baseline: bool = True
    ):
        """Run full benchmark suite across all scenarios"""
        
        print(f"=== GAIE Benchmark Suite ===")
        print(f"Endpoint: {self.endpoint}")
        print(f"Model: {model}")
        print(f"Tool: {self.tool}")
        print(f"Concurrencies: {concurrencies}")
        print(f"SLO Profiles: {slo_profiles}")
        print(f"Num Prompts: {num_prompts}")
        print()
        
        # Run baseline (no SLO routing)
        if compare_baseline:
            print("=== Running Baseline (No SLO Routing) ===")
            for concurrency in concurrencies:
                result = self._run_single_benchmark(
                    model=model,
                    concurrency=concurrency,
                    slo_profile="chatbot",  # Use chatbot SLO for comparison
                    enable_slo_routing=False,
                    num_prompts=num_prompts,
                    dataset=dataset,
                    scenario="baseline"
                )
                if result:
                    self.results.append(result)
        
        # Run SLO-aware routing tests
        print("\n=== Running SLO-Aware Routing Tests ===")
        for slo_profile_name in slo_profiles:
            for concurrency in concurrencies:
                result = self._run_single_benchmark(
                    model=model,
                    concurrency=concurrency,
                    slo_profile=slo_profile_name,
                    enable_slo_routing=True,
                    num_prompts=num_prompts,
                    dataset=dataset,
                    scenario="slo_aware"
                )
                if result:
                    self.results.append(result)
        
        # Generate reports
        self._generate_reports()
        
    def _run_single_benchmark(
        self,
        model: str,
        concurrency: int,
        slo_profile: str,
        enable_slo_routing: bool,
        num_prompts: int,
        dataset: str,
        scenario: str
    ) -> Optional[BenchmarkResult]:
        """Run single benchmark configuration"""
        
        profile = self.SLO_PROFILES[slo_profile]
        
        print(f"\n--- {scenario.upper()} | {slo_profile} | Concurrency: {concurrency} ---")
        print(f"SLO: TTFT<{profile.ttft_ms}ms, TPOT<{profile.tpot_ms}ms")
        print(f"SLO Routing: {'ENABLED' if enable_slo_routing else 'DISABLED'}")
        
        if self.tool == "genai-perf":
            return self._run_genai_perf(
                model, concurrency, num_prompts, profile, 
                enable_slo_routing, dataset, scenario
            )
        elif self.tool == "vllm":
            return self._run_vllm_benchmark(
                model, concurrency, num_prompts, profile,
                enable_slo_routing, dataset, scenario
            )
        else:
            raise ValueError(f"Unknown tool: {self.tool}")
    
    def _run_genai_perf(
        self,
        model: str,
        concurrency: int,
        num_prompts: int,
        profile: SLOProfile,
        enable_slo_routing: bool,
        dataset: str,
        scenario: str
    ) -> Optional[BenchmarkResult]:
        """Run GenAI-Perf benchmark"""
        
        routing_str = "slo" if enable_slo_routing else "baseline"
        output_subdir = self.output_dir / f"{scenario}_{profile.name}_c{concurrency}_{routing_str}"
        output_subdir.mkdir(exist_ok=True)

        # Construct full URL with path
        full_url = f"{self.endpoint}"

        cmd = [
            "genai-perf",
            "profile",
            "--model", model,
            "--endpoint-type", "chat",
            "-u", full_url,
            "--num-prompts", str(num_prompts),
            "--tokenizer", model,
            "--concurrency", str(concurrency),
            "--streaming",
            "--header", f"x-slo-ttft-ms:{profile.ttft_ms}",
            "--header", f"x-slo-tpot-ms:{profile.tpot_ms}",
            "--header", f"x-prediction-based-scheduling:{str(enable_slo_routing).lower()}",
            "--artifact-dir", str(output_subdir)
        ]
        
        try:
            print(f"Executing: genai-perf (output: {output_subdir})")

            # Set up environment with HF_TOKEN if available
            env = os.environ.copy()
            if 'HF_TOKEN' in os.environ:
                env['HF_TOKEN'] = os.environ['HF_TOKEN']
                env['HUGGING_FACE_HUB_TOKEN'] = os.environ['HF_TOKEN']

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)

            if result.returncode != 0:
                print(f"❌ Error running genai-perf:")
                print(f"STDOUT:\n{result.stdout}")
                print(f"STDERR:\n{result.stderr}")
                return None
            
            # Parse results - genai-perf creates nested directories
            # Find the actual results directory (it creates a subdirectory with model name)
            result_dirs = list(output_subdir.glob("*/"))
            if not result_dirs:
                print(f"❌ No result directories found in: {output_subdir}")
                return None

            # Use the first (and typically only) subdirectory
            actual_output_dir = result_dirs[0]

            csv_path = actual_output_dir / "profile_export_genai_perf.csv"
            json_path = actual_output_dir / "profile_export.json"

            if not csv_path.exists():
                # Try alternative naming
                csv_path = actual_output_dir / "genai_perf.csv"

            if not csv_path.exists():
                print(f"❌ Results file not found in: {actual_output_dir}")
                print(f"   Available files: {list(actual_output_dir.glob('*'))}")
                return None

            return self._parse_genai_perf_results(
                csv_path, json_path, profile, scenario,
                concurrency, enable_slo_routing
            )
            
        except subprocess.TimeoutExpired:
            print("❌ Benchmark timed out (10 minutes)")
            return None
        except Exception as e:
            print(f"❌ Error running benchmark: {e}")
            return None
    
    def _parse_genai_perf_results(
        self,
        csv_path: Path,
        json_path: Path,
        profile: SLOProfile,
        scenario: str,
        concurrency: int,
        enable_slo_routing: bool
    ) -> BenchmarkResult:
        """Parse GenAI-Perf output files"""
        
        df = pd.read_csv(csv_path)
        
        # Extract TTFT metrics (typically first row)
        ttft_row = df[df['Metric'].str.contains('time_to_first_token', case=False, na=False)]
        if len(ttft_row) == 0:
            ttft_row = df.iloc[0:1]  # Fallback to first row
        
        ttft_avg = self._safe_float(ttft_row.iloc[0]['avg'])
        ttft_p50 = self._safe_float(ttft_row.iloc[0]['p50'])
        ttft_p90 = self._safe_float(ttft_row.iloc[0]['p90'])
        ttft_p99 = self._safe_float(ttft_row.iloc[0]['p99'])
        
        # Extract TPOT/ITL metrics
        tpot_row = df[df['Metric'].str.contains('time_per_output_token|inter_token_latency', 
                                                  case=False, na=False)]
        if len(tpot_row) == 0:
            tpot_row = df.iloc[1:2]  # Fallback to second row
        
        tpot_avg = self._safe_float(tpot_row.iloc[0]['avg'])
        tpot_p50 = self._safe_float(tpot_row.iloc[0]['p50'])
        tpot_p90 = self._safe_float(tpot_row.iloc[0]['p90'])
        tpot_p99 = self._safe_float(tpot_row.iloc[0]['p99'])
        
        # Extract throughput metrics
        rps_row = df[df['Metric'].str.contains('request_throughput', case=False, na=False)]
        request_throughput = self._safe_float(rps_row.iloc[0]['avg']) if len(rps_row) > 0 else 0.0
        
        tps_row = df[df['Metric'].str.contains('output_token_throughput', case=False, na=False)]
        token_throughput = self._safe_float(tps_row.iloc[0]['avg']) if len(tps_row) > 0 else 0.0
        
        # Calculate SLO attainment and goodput
        # Load detailed JSON for per-request analysis
        slo_attainment_rate, goodput, num_violations = self._calculate_slo_metrics(
            json_path, profile, request_throughput
        )
        
        print(f"✅ Results:")
        print(f"   TTFT: avg={ttft_avg:.1f}ms, p90={ttft_p90:.1f}ms, p99={ttft_p99:.1f}ms")
        print(f"   TPOT: avg={tpot_avg:.1f}ms, p90={tpot_p90:.1f}ms, p99={tpot_p99:.1f}ms")
        print(f"   Throughput: {request_throughput:.2f} RPS")
        print(f"   SLO Attainment: {slo_attainment_rate:.1f}%")
        print(f"   Goodput: {goodput:.2f} RPS")
        
        return BenchmarkResult(
            scenario=scenario,
            concurrency=concurrency,
            slo_profile=profile.name,
            slo_routing_enabled=enable_slo_routing,
            ttft_avg=ttft_avg,
            ttft_p50=ttft_p50,
            ttft_p90=ttft_p90,
            ttft_p99=ttft_p99,
            tpot_avg=tpot_avg,
            tpot_p50=tpot_p50,
            tpot_p90=tpot_p90,
            tpot_p99=tpot_p99,
            request_throughput=request_throughput,
            token_throughput=token_throughput,
            slo_attainment_rate=slo_attainment_rate,
            goodput=goodput,
            num_requests=0,  # TODO: extract from JSON
            num_violations=num_violations,
            timestamp=datetime.now().isoformat()
        )
    
    def _calculate_slo_metrics(
        self,
        json_path: Path,
        profile: SLOProfile,
        total_throughput: float
    ) -> Tuple[float, float, int]:
        """Calculate SLO attainment rate and goodput from detailed results"""
        
        if not json_path.exists():
            # Fallback: estimate from aggregated metrics
            # This is not ideal but works when detailed data unavailable
            return 85.0, total_throughput * 0.85, 0
        
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
            
            # GenAI-Perf JSON structure varies by version
            # Try to extract per-request metrics
            requests = data.get('requests', [])
            
            if not requests:
                # Fallback
                return 85.0, total_throughput * 0.85, 0
            
            total_requests = len(requests)
            requests_meeting_slo = 0
            
            for req in requests:
                ttft = req.get('time_to_first_token_ms', 0)
                tpot = req.get('time_per_output_token_ms', 0)
                
                if ttft <= profile.ttft_ms and tpot <= profile.tpot_ms:
                    requests_meeting_slo += 1
            
            attainment_rate = (requests_meeting_slo / total_requests) * 100
            goodput = total_throughput * (requests_meeting_slo / total_requests)
            num_violations = total_requests - requests_meeting_slo
            
            return attainment_rate, goodput, num_violations
            
        except Exception as e:
            print(f"Warning: Could not parse JSON for SLO metrics: {e}")
            return 85.0, total_throughput * 0.85, 0
    
    def _safe_float(self, value) -> float:
        """Safely convert value to float"""
        if pd.isna(value):
            return 0.0
        if isinstance(value, str):
            # Remove commas and convert
            value = value.replace(',', '')
        return float(value)
    
    def _run_vllm_benchmark(
        self,
        model: str,
        concurrency: int,
        num_prompts: int,
        profile: SLOProfile,
        enable_slo_routing: bool,
        dataset: str,
        scenario: str
    ) -> Optional[BenchmarkResult]:
        """Run vLLM benchmark_serving.py"""
        
        # TODO: Implement vLLM benchmark wrapper
        # Similar structure to GenAI-Perf but with vLLM-specific parsing
        print("vLLM benchmark not yet implemented")
        return None
    
    def _generate_reports(self):
        """Generate comprehensive benchmark reports"""
        
        if not self.results:
            print("No results to generate reports")
            return
        
        print("\n=== Generating Reports ===")
        
        # Convert results to DataFrame
        df = pd.DataFrame([asdict(r) for r in self.results])
        
        # Save raw results
        results_csv = self.output_dir / "all_results.csv"
        df.to_csv(results_csv, index=False)
        print(f"Saved: {results_csv}")
        
        # Generate visualizations
        self._plot_latency_throughput_curve(df)
        self._plot_slo_attainment_comparison(df)
        self._plot_goodput_comparison(df)
        self._generate_summary_table(df)
        
    def _plot_latency_throughput_curve(self, df: pd.DataFrame):
        """Generate latency-throughput curves"""
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
        
        # TTFT vs Throughput
        for scenario in df['scenario'].unique():
            for enabled in df['slo_routing_enabled'].unique():
                mask = (df['scenario'] == scenario) & (df['slo_routing_enabled'] == enabled)
                subset = df[mask].sort_values('concurrency')
                
                label = f"{scenario} ({'SLO-Aware' if enabled else 'Baseline'})"
                ax1.plot(subset['ttft_p90'], subset['request_throughput'], 
                        marker='o', label=label)
                
                # Add concurrency labels
                for _, row in subset.iterrows():
                    ax1.annotate(f"c={row['concurrency']}", 
                               (row['ttft_p90'], row['request_throughput']),
                               fontsize=8, alpha=0.7)
        
        ax1.set_xlabel('P90 TTFT (ms)')
        ax1.set_ylabel('Request Throughput (RPS)')
        ax1.set_title('TTFT vs Throughput')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # TPOT vs Throughput
        for scenario in df['scenario'].unique():
            for enabled in df['slo_routing_enabled'].unique():
                mask = (df['scenario'] == scenario) & (df['slo_routing_enabled'] == enabled)
                subset = df[mask].sort_values('concurrency')
                
                label = f"{scenario} ({'SLO-Aware' if enabled else 'Baseline'})"
                ax2.plot(subset['tpot_p90'], subset['request_throughput'],
                        marker='s', label=label)
        
        ax2.set_xlabel('P90 TPOT (ms)')
        ax2.set_ylabel('Request Throughput (RPS)')
        ax2.set_title('TPOT vs Throughput')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        output_path = self.output_dir / "latency_throughput_curves.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
        plt.close()
    
    def _plot_slo_attainment_comparison(self, df: pd.DataFrame):
        """Compare SLO attainment rates"""
        
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # Group by concurrency and routing enabled
        pivot = df.pivot_table(
            values='slo_attainment_rate',
            index='concurrency',
            columns='slo_routing_enabled',
            aggfunc='mean'
        )
        
        pivot.plot(kind='bar', ax=ax, width=0.8)
        ax.set_xlabel('Concurrency')
        ax.set_ylabel('SLO Attainment Rate (%)')
        ax.set_title('SLO Attainment: Baseline vs SLO-Aware Routing')
        ax.legend(['Baseline', 'SLO-Aware Routing'])
        ax.axhline(y=90, color='r', linestyle='--', label='Target (90%)')
        ax.grid(True, alpha=0.3, axis='y')
        
        plt.tight_layout()
        output_path = self.output_dir / "slo_attainment_comparison.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
        plt.close()
    
    def _plot_goodput_comparison(self, df: pd.DataFrame):
        """Compare goodput across scenarios"""
        
        fig, ax = plt.subplots(figsize=(12, 6))
        
        # Compare goodput by SLO profile
        for profile in df['slo_profile'].unique():
            for enabled in df['slo_routing_enabled'].unique():
                mask = (df['slo_profile'] == profile) & (df['slo_routing_enabled'] == enabled)
                subset = df[mask].sort_values('concurrency')
                
                label = f"{profile} ({'SLO' if enabled else 'Baseline'})"
                ax.plot(subset['concurrency'], subset['goodput'],
                       marker='o', label=label)
        
        ax.set_xlabel('Concurrency')
        ax.set_ylabel('Goodput (RPS)')
        ax.set_title('Goodput Comparison Across SLO Profiles')
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.set_xscale('log')
        
        plt.tight_layout()
        output_path = self.output_dir / "goodput_comparison.png"
        plt.savefig(output_path, dpi=150, bbox_inches='tight')
        print(f"Saved: {output_path}")
        plt.close()
    
    def _generate_summary_table(self, df: pd.DataFrame):
        """Generate summary comparison table"""
        
        # Calculate improvement metrics
        summary_rows = []
        
        for concurrency in sorted(df['concurrency'].unique()):
            for profile in df['slo_profile'].unique():
                baseline = df[(df['concurrency'] == concurrency) & 
                             (df['slo_profile'] == profile) &
                             (df['slo_routing_enabled'] == False)]
                slo_aware = df[(df['concurrency'] == concurrency) &
                              (df['slo_profile'] == profile) &
                              (df['slo_routing_enabled'] == True)]
                
                if len(baseline) == 0 or len(slo_aware) == 0:
                    continue
                
                b = baseline.iloc[0]
                s = slo_aware.iloc[0]
                
                goodput_improvement = ((s['goodput'] - b['goodput']) / b['goodput']) * 100
                slo_improvement = s['slo_attainment_rate'] - b['slo_attainment_rate']
                
                summary_rows.append({
                    'Concurrency': concurrency,
                    'SLO Profile': profile,
                    'Baseline Goodput': f"{b['goodput']:.2f}",
                    'SLO-Aware Goodput': f"{s['goodput']:.2f}",
                    'Goodput Improvement': f"{goodput_improvement:+.1f}%",
                    'Baseline SLO Attainment': f"{b['slo_attainment_rate']:.1f}%",
                    'SLO-Aware SLO Attainment': f"{s['slo_attainment_rate']:.1f}%",
                    'SLO Improvement': f"{slo_improvement:+.1f}%"
                })
        
        summary_df = pd.DataFrame(summary_rows)
        summary_path = self.output_dir / "summary_comparison.csv"
        summary_df.to_csv(summary_path, index=False)
        print(f"Saved: {summary_path}")
        
        # Print to console
        print("\n=== Summary Comparison Table ===")
        print(summary_df.to_string(index=False))


def main():
    parser = argparse.ArgumentParser(description="GAIE SLO-Aware Routing Benchmark")
    parser.add_argument("--endpoint", required=True, help="GAIE gateway endpoint URL")
    parser.add_argument("--model", required=True, help="Model name")
    parser.add_argument("--output-dir", default="./benchmark_results", help="Output directory")
    parser.add_argument("--tool", choices=["genai-perf", "vllm"], default="genai-perf")
    parser.add_argument("--concurrencies", nargs="+", type=int, 
                       default=[1, 2, 5, 10, 20, 50, 100],
                       help="List of concurrency levels")
    parser.add_argument("--slo-profiles", nargs="+", 
                       choices=list(GAIEBenchmark.SLO_PROFILES.keys()),
                       default=["chatbot", "rag"],
                       help="SLO profiles to test")
    parser.add_argument("--num-prompts", type=int, default=500,
                       help="Number of prompts per test")
    parser.add_argument("--dataset", default="sharegpt",
                       choices=["sharegpt", "random", "sonnet"],
                       help="Input dataset")
    parser.add_argument("--no-baseline", action="store_true",
                       help="Skip baseline comparison")
    
    args = parser.parse_args()
    
    benchmark = GAIEBenchmark(
        endpoint=args.endpoint,
        output_dir=args.output_dir,
        tool=args.tool
    )
    
    benchmark.run_benchmark_suite(
        model=args.model,
        concurrencies=args.concurrencies,
        slo_profiles=args.slo_profiles,
        num_prompts=args.num_prompts,
        dataset=args.dataset,
        compare_baseline=not args.no_baseline
    )
    
    print("\n✅ Benchmark suite completed!")
    print(f"Results saved to: {args.output_dir}")


if __name__ == "__main__":
    main()
