# HuggingFace Token

The following command creates the token in the current namespace using the name `llm-d-hf-token`, which can be used for pulling gated models from Hugging Face.

```bash
export HF_TOKEN=<from Huggingface>
export HF_TOKEN_NAME=${HF_TOKEN_NAME:-llm-d-hf-token}
kubectl create secret generic ${HF_TOKEN_NAME} \
    --from-literal="HF_TOKEN=${HF_TOKEN}" \
    --namespace "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -
```

> [!NOTE]
> For more information on getting a token, see [the huggingface docs](https://huggingface.co/docs/hub/en/security-tokens).