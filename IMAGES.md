# Container Images

## Prod

| Name                        | Connectors | LMCache commit SHA | vLLM commit SHA  | vLLM branch | Changes |
|-----------------------------|-|-|-|-|-|
| `quay.io/llm-d/llm-d:0.0.5` | LMCache/NIXL | a3a7d7fe0f3a349227cb1cfbe8ccc16eb560a06b | disagg_pd_dev | - | - |
| `quay.io/llm-d/llm-d:0.0.4` | LMCache only | 71d41f0f9161b2d2362157d3c1bbf185e2d3a807 | - | pd_scheduling_lmcache | H100 support |
| `quay.io/llm-d/llm-d:0.0.3` | LMCache only | 71d41f0f9161b2d2362157d3c1bbf185e2d3a807 | - | - | - |

## Dev

| Name                        | Connectors | LMCache commit SHA | vLLM branch           | vLLM commit SHA | Changes
|-----------------------------|-|-|-----------------------|-|-|
| `quay.io/llm-d/llm-d:0.0.7` | LMCache/NIXL | 4c842ad3a309bc33a07505f178ffd471dbb8c430 | dev                   | 5941e0b7ea5f5204f01ed3ff69d0965bdf9106a8 | - |
| `quay.io/llm-d/llm-d:0.0.6` | LMCache/NIXL | a3a7d7fe0f3a349227cb1cfbe8ccc16eb560a06b | disagg_pd_dev         | - | - |
| `quay.io/llm-d/llm-d:0.0.5` | LMCache only |71d41f0f9161b2d2362157d3c1bbf185e2d3a807 | pd_scheduling_lmcache | - | H100 support |
| `quay.io/llm-d/llm-d:0.0.4` | LMCache only | 71d41f0f9161b2d2362157d3c1bbf185e2d3a807 | -                     | 1c2bc7ead019cdf5b04b2f1d07b00982352f85ef |
