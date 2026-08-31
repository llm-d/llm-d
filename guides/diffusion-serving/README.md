# Diffusion Serving in llm-d

Diffusion models generate media such as images, audio, and video by iterative denoising rather than autoregressive token generation. The text-to-image path in these guides exposes no prefix-cache or KV-cache signal, so routing falls back to load: the EPP prefers the endpoint with the fewest in-flight requests. Multi-stage pipelines can also include autoregressive stages, such as an LLM text encoder or a TTS talker, where prefix caching applies and richer routing signals become possible.

## Status

> [!WARNING]
> Diffusion serving is **experimental**. The routing configuration is an early baseline, and the manifests and router values may change in upcoming releases.

## Guide Index

* **[Text-to-Image Guide](./text-to-image/README.md)**: Generate images from text prompts over the OpenAI-compatible `POST /v1/images/generations` endpoint, serving on vLLM-Omni or SGLang.
* **[Text-to-Video Guide](./text-to-video/README.md)**: Generate video clips from text prompts over the OpenAI-compatible asynchronous `/v1/videos` endpoint family, which carries `multipart/form-data`, serving on vLLM-Omni.
