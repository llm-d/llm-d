# llm-d 架构图

## 系统总体架构

```mermaid
graph TB
    subgraph Client["客户端"]
        C[Client Application]
    end

    subgraph K8s["Kubernetes 集群"]
        subgraph Gateway["推理网关层 (Kubernetes Inference Gateway)"]
            GW[Gateway\nLoadBalancer/ClusterIP]
            HR[HTTPRoute]
            EPP[Endpoint Picker\nllm-d-inference-scheduler]
        end

        subgraph InferencePool["推理池 (InferencePool)"]
            subgraph StandardMode["标准推理调度模式"]
                VS1[vLLM Server\nReplica 1]
                VS2[vLLM Server\nReplica 2]
                VSN[vLLM Server\nReplica N]
            end

            subgraph PDMode["Prefill/Decode 分离模式"]
                PF1[Prefill Server 1]
                PF2[Prefill Server 2]
                DC1[Decode Server 1]
                DC2[Decode Server 2]
            end

            subgraph WideEP["Wide Expert Parallelism (MoE)"]
                LWS[LeaderWorkerSet\nController]
                LP[Leader Pod]
                WP1[Worker Pod 1\nExperts Shard]
                WP2[Worker Pod 2\nExperts Shard]
                WPN[Worker Pod N\nExperts Shard]
            end
        end

        subgraph Cache["KV 缓存层级"]
            HBM[加速器 HBM\n最快/最小]
            CPU[CPU RAM\nvLLM 原生]
            SSD[本地 SSD]
            Remote[远程存储\nLustre/CephFS/S3]
        end

        subgraph Autoscaling["工作负载自动扩缩容"]
            HPA[HPA + IGW Metrics\n同构硬件]
            WVA[Workload Variant\nAutoscaler\n异构硬件]
        end

        subgraph Observability["可观测性"]
            Prom[Prometheus]
            Graf[Grafana]
            OTEL[OpenTelemetry\nCollector]
        end
    end

    subgraph Models["模型来源"]
        HF[HuggingFace Hub]
        LocalStorage[本地存储]
    end

    subgraph Hardware["加速器硬件"]
        NVIDIA[NVIDIA GPU\nCUDA]
        AMD[AMD GPU\nROCm]
        Intel[Intel XPU/HPU\nGaudi]
        TPU[Google TPU\nv5e+]
        CPU2[CPU Only]
    end

    %% 请求流
    C -->|HTTP/OpenAI API| GW
    GW --> HR
    HR --> EPP
    EPP -->|路由决策| VS1
    EPP -->|路由决策| VS2
    EPP -->|路由决策| VSN
    EPP -->|Prefill 请求| PF1
    EPP -->|Prefill 请求| PF2
    PF1 -->|KV Cache 传输\nNIXL/RDMA| DC1
    PF2 -->|KV Cache 传输\nNIXL/RDMA| DC2

    %% LWS 连接
    LWS --> LP
    LP <-->|RDMA/DeepEP\nAll-to-All| WP1
    LP <-->|RDMA/DeepEP\nAll-to-All| WP2
    LP <-->|RDMA/DeepEP\nAll-to-All| WPN

    %% 缓存层级
    VS1 <--> HBM
    HBM -->|溢出| CPU
    CPU -->|溢出| SSD
    SSD -->|溢出| Remote

    %% 调度器指标反馈
    VS1 -->|队列深度/缓存指标| EPP
    VS2 -->|队列深度/缓存指标| EPP
    VSN -->|队列深度/缓存指标| EPP

    %% 自动扩缩容
    Prom -->|指标| HPA
    Prom -->|指标| WVA
    HPA -->|扩缩容| StandardMode
    WVA -->|扩缩容| StandardMode

    %% 可观测性
    VS1 -->|Prometheus Metrics| Prom
    VS2 -->|Prometheus Metrics| Prom
    EPP -->|调度指标| Prom
    VS1 -->|Traces| OTEL
    Prom --> Graf

    %% 模型加载
    HF -->|下载模型权重| VS1
    HF -->|下载模型权重| VS2
    LocalStorage -->|加载模型权重| VS1

    %% 硬件
    VS1 --- NVIDIA
    VS2 --- AMD
    VS2 --- Intel
    VS1 --- TPU
    VSN --- CPU2
```

---

## 请求处理数据流

```mermaid
sequenceDiagram
    participant Client as 客户端
    participant GW as Kubernetes Gateway
    participant EPP as Endpoint Picker (调度器)
    participant vLLM as vLLM 模型服务器
    participant Cache as KV 缓存 (HBM/CPU/SSD)

    Client->>GW: HTTP POST /v1/completions
    GW->>EPP: 路由请求
    EPP->>vLLM: 查询各 replica 指标\n(队列深度、缓存状态)
    vLLM-->>EPP: 返回指标数据
    EPP->>EPP: 执行调度策略\n(缓存感知、SLA、公平性)
    EPP->>GW: 返回最优 backend 地址
    GW->>vLLM: 转发推理请求

    vLLM->>Cache: 检查 KV 缓存命中
    alt 缓存命中
        Cache-->>vLLM: 返回缓存 KV
    else 缓存未命中
        vLLM->>vLLM: 计算 KV (prefill)
        vLLM->>Cache: 存储 KV 缓存
    end

    vLLM->>vLLM: Decode 生成 token
    vLLM-->>GW: 流式返回 tokens
    GW-->>Client: 返回响应
```

---

## Prefill/Decode 分离架构

```mermaid
graph LR
    subgraph Prefill["Prefill 服务器组"]
        P1[Prefill\nServer 1\n高算力 GPU]
        P2[Prefill\nServer 2\n高算力 GPU]
    end

    subgraph Transfer["KV 传输层"]
        NIXL[NIXL 传输\nRDMA / InfiniBand\n/ RoCE / TCP]
    end

    subgraph Decode["Decode 服务器组"]
        D1[Decode\nServer 1\n高显存 GPU]
        D2[Decode\nServer 2\n高显存 GPU]
    end

    Request[推理请求] --> P1
    Request --> P2
    P1 -->|序列化 KV Cache| NIXL
    P2 -->|序列化 KV Cache| NIXL
    NIXL -->|点对点传输| D1
    NIXL -->|点对点传输| D2
    D1 -->|生成 tokens| Response[响应]
    D2 -->|生成 tokens| Response
```

---

## 组件交互关系

```mermaid
graph TD
    subgraph Control["控制平面"]
        IGW[Kubernetes Inference\nGateway API]
        IP[InferencePool CRD]
        IM[InferenceModel CRD]
    end

    subgraph DataPlane["数据平面"]
        Envoy[Envoy Proxy\nIstio / KGateway / GKE]
        EPP2[llm-d-inference-scheduler\nEndpoint Picker]
    end

    subgraph Serving["模型服务"]
        vLLM2[vLLM Pods]
        SGLang[SGLang Pods\n可选]
    end

    subgraph Scaling["弹性扩缩容"]
        LWS2[LeaderWorkerSet\n多节点 MoE]
        HPA2[HPA\nKV缓存/队列指标]
        KEDA[KEDA / WVA\n高级自动扩缩]
    end

    subgraph Infra["基础设施"]
        DRA[Dynamic Resource\nAllocation GPU]
        RDMA[RDMA Networking\nIB / RoCE]
        Storage[分布式存储\nLustre/Ceph/NFS]
    end

    IGW --> Envoy
    IGW --> EPP2
    IP --> EPP2
    IM --> EPP2
    EPP2 --> vLLM2
    EPP2 --> SGLang
    Envoy --> EPP2

    vLLM2 --> LWS2
    vLLM2 --> HPA2
    vLLM2 --> KEDA

    LWS2 --> RDMA
    vLLM2 --> DRA
    vLLM2 --> Storage
```

---

## 部署路径 (Well-Lit Paths)

| 路径 | 适用场景 | 组件 | 硬件要求 |
|------|---------|------|---------|
| **推理调度** | 通用生产环境 | vLLM + 智能负载均衡 | 2×TP GPU，8+ replicas |
| **P/D 分离** | 大模型 (120B+)，长 Prompt | Prefill/Decode 分离 + NIXL | RDMA 互联 |
| **Wide EP** | 超大 MoE 模型 (DeepSeek-R1) | LeaderWorkerSet + DeepEP | 32+ GPU + 全互联 RDMA |
| **分级 KV 缓存** | 高并发，长 Prompt 重用 | vLLM KVConnector + 多级存储 | 大容量 SSD / 远程存储 |
| **工作负载自动扩缩** | 流量波动，混合硬件 | HPA / WVA | 取决于上述模式 |

---

## 容器镜像与硬件支持矩阵

| 镜像 | 硬件 | 框架 |
|------|------|------|
| `Dockerfile.cuda` | NVIDIA GPU | CUDA |
| `Dockerfile.rocm` | AMD GPU | ROCm |
| `Dockerfile.xpu` | Intel GPU | XPU (i915/xe) |
| `Dockerfile.hpu` | Intel Gaudi | HPU |
| `Dockerfile.cpu` | x86/ARM CPU | PyTorch CPU |

---

## 可观测性架构

```mermaid
graph LR
    subgraph Sources["指标来源"]
        vLLMM[vLLM\nPrometheus Metrics\n请求延迟/吞吐量/KV缓存]
        EPPM[Endpoint Picker\n调度指标/队列深度]
        GWM[Gateway\n流量指标]
    end

    subgraph Collection["采集层"]
        PM[PodMonitor /\nServiceMonitor]
        PromS[Prometheus Server]
    end

    subgraph Visualization["可视化"]
        GrafD[Grafana Dashboard\nLLM 专属面板]
    end

    subgraph Tracing["链路追踪"]
        OTELAgent[OpenTelemetry\nAgent Sidecar]
        OTELCol[OpenTelemetry\nCollector]
        Jaeger[Jaeger /\nTempo]
    end

    subgraph Alerting["告警"]
        AlertM[Alertmanager]
    end

    vLLMM --> PM
    EPPM --> PM
    GWM --> PM
    PM --> PromS
    PromS --> GrafD
    PromS --> AlertM

    vLLMM --> OTELAgent
    OTELAgent --> OTELCol
    OTELCol --> Jaeger
```
