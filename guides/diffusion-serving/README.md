# Diffusion Serving in llm-d

Diffusion models generate media such as images, audio, and video by iterative denoising rather than autoregressive token generation. The text-to-image path in these guides exposes no prefix-cache or KV-cache signal, so routing falls back to load: the EPP prefers the endpoint with the fewest in-flight requests. Multi-stage pipelines can also include autoregressive stages, such as an LLM text encoder or a TTS talker, where prefix caching applies and richer routing signals become possible.

## Status

> [!WARNING]
> Diffusion serving is **experimental**. The routing configuration is an early baseline, and the manifests and router values may change in upcoming releases.

## Guide Index

### Single-Endpoint Guides

Each guide deploys a dedicated model serving a single generation endpoint:

* **[Text-to-Image Guide](./text-to-image/README.md)**: Generate images from text prompts over the OpenAI-compatible `POST /v1/images/generations` endpoint, serving on vLLM-Omni or SGLang.
* **[Image-to-Image Guide](./image-to-image/README.md)**: Edit an uploaded image over the OpenAI-compatible `POST /v1/images/edits` endpoint, which carries `multipart/form-data`, serving on vLLM-Omni or SGLang.
* **[Text-to-Speech Guide](./text-to-speech/README.md)**: Generate speech from text over the OpenAI-compatible `POST /v1/audio/speech` endpoint, which returns binary audio or streamed audio chunks, serving on vLLM-Omni.

### Omni Models

* **[Omni Model Serving](../omni-serving/README.md)**: Serve a single unified model (e.g. Qwen3-Omni) that answers **several modality endpoints** from one checkpoint behind one `InferencePool`, with exact-path HTTPRoutes to split endpoints across pools behind a Gateway. Published on llm-d.ai as the **Serve Omni Models** well-lit path.
