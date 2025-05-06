# Container Images

## Prod

TBD.

## Dev

### Active Images

| Name | vLLM Repository | vLLM branch | LMCache Repository | LMCache branch | Description |
|-|-|-|-|-|-|
| [Dockerfile.lmcache](./Dockerfile.lmcache) | [Downstream vLLM](https://github.com/neuralmagic/vllm) | dev [pinned](https://github.com/neuralmagic/llm-d/blob/dev/Dockerfile.lmcache#L180) | [Downstream LMCache](https://github.com/neuralmagic/LMCache) | dev [pinned](https://github.com/neuralmagic/llm-d/blob/dev/Dockerfile.lmcache#L179) | KV Cache distribution/Offloading
| [Dockerfile.nixl](./Dockerfile.nixl) | [Downstream vLLM](https://github.com/neuralmagic/vllm) | disagg_pd_dev | [Upstream LMCache](https://github.com/LMCache/LMCache) | dev | P/D via NIXL connector


### Latest Dev Releases

| Container File Name | Tag | Digest
|-|-|-|
| [Dockerfile.lmcache](./Dockerfile.lmcache)| lmcache-0.0.6 | sha256:281e7ee67c8993d3f3f69ac27030fca3735be083056dd877b71861153d8da1e4 |
| [Dockerfile.nixl](./Dockerfile.nixl) | vllm-nixl-0.0.6 | sha256:d6d212de0d1dc0f6da9877eab21800f62d7dd32d825bae9bf1692c4f6e017109 |
| [Dockerfile.lmcache-nixl](./Dockerfile.lmcache-nixl)  | lmcache-nixl-0.0.6 | sha256:281e7ee67c8993d3f3f69ac27030fca3735be083056dd877b71861153d8da1e4 |


### Deprecated Images

| Name | vLLM Repository | vLLM branch | LMCache Repository | LMCache branch/commit |
|-|-|-|-|-|
| [Dockerfile.lmcache-nixl](./Dockerfile.lmcache-nixl) | [Tyler's vLLM](https://github.com/tlrmchlsmth/vllm) | async_pd |   [Tyler's LMCache](https://github.com/tlrmchlsmth/LMCache) | 71d41f0f9161b2d2362157d3c1bbf185e2d3a807
