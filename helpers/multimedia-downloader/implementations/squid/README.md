# Squid Proxy: Multimedia Cache

[Squid](https://www.squid-cache.org/)  is a powerful, open-source caching proxy for HTTP, HTTPS, and FTP traffic, engineered to accelerate content delivery and minimize bandwidth consumption.

Key Capabilities:

📊 Intelligent Eviction - Advanced algorithms ensure optimal use of available cache space.

🚀 Bandwidth Reduction - Utilizes collapsed forwarding to cut origin server load.

💾 Versatile Storage - Supports memory-only, disk-based, or hybrid caching configurations.

📈 Massive Scalability - Maximize single-node hardware with concurrent SMP workers, or distribute load globally via cache hierarchies and external load balancing.

Deployment: Primarily operates as a forward proxy.


## 🚀 Automated Deployment and Testing

Execute the [deploy-and-test-squid.sh](test/deploy-and-test-squid.sh) script to run cache validations and inspect the resulting logs.

### kind

Run HTTP test (spins up a temporary kind cluster, tests, and tears it down):

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh
```

To retain resources after testing for manual review and debugging add `--skip-cleanup` flag:

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --skip-cleanup
```

### OpenShift

Target an OpenShift cluster (uses the openshift overlay):

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --openshift
```

To target a specific OpenShift context (implies `--openshift`):

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --context <ctx>
```

To leave OpenShift resources in place after testing for manual inspection add `--skip-cleanup` flag:

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --openshift --skip-cleanup
```

### Understanding Log Results

- **TCP_HIT:** Served fast from disk cache.
- **TCP_MEM_HIT:** Served ultra-fast from memory cache.
- **TCP_MISS:** Downloaded from the origin server (not in cache).
- **TCP_TUNNEL:** Encrypted HTTPS traffic passed through blindly (not cached).

For more detailed explanations of log statuses and monitoring cache hit rates, see the [Squid monitoring guide](https://oneuptime.com/blog/post/2026-03-20-squid-monitor-cache-hit-rates-ipv4/view).

## 🔒 HTTPS Caching

By default, proxies cannot see inside encrypted HTTPS traffic. Here is how Squid manages encrypted flows depending on your configuration:

* **Blind Tunneling (CONNECT):** Passes encrypted TCP traffic through an opaque tunnel. 
    * *Trade-off:* Zero visibility; no caching or granular filtering is possible.
* **Full Decryption ([SSL Bump](https://wiki.squid-cache.org/Features/SslBump) MITM):** Intercepts, decrypts, and re-encrypts traffic using a custom Root CA. 
    * *Trade-off:* Enables full inspection and caching, but requires complex certificate management and raises privacy/legal risks.
* **Smart Inspection ([Peek & Splice](https://wiki.squid-cache.org/Features/SslPeekAndSplice)):** Inspects the unencrypted SNI (Server Name Indication) during the TLS handshake. 
    * *Trade-off:* Allows domain-based filtering without requiring full decryption.

> **Note:** While Squid supports TLS 1.3, new privacy standards like ECH (Encrypted Client Hello) and ESNI encrypt the destination domain itself. Since the proxy cannot see the target to apply policy, these connections must be spliced (passed through blindly) to prevent connection failure.

> **Warning:**  SSL Bump breaks end-to-end trust. Always ensure you have legal and compliance approval before intercepting HTTPS traffic.

### 🚀 Automated Deployment and Testing

#### kind

Run SSL Bump test (builds image, generates CA, deploys, and verifies cache):

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --mode ssl-bump
```

To retain resources after testing for manual inspection add `--skip-cleanup` flag:

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --mode ssl-bump --skip-cleanup
```

#### OpenShift

To use the SSL Bump configuration, the custom Squid image must be hosted in a registry that your cluster can access.

**Step 1 - Set your target registry**

```bash
export SQUID_IMAGE_REGISTRY="image-registry"
```

**Step 2 — Build and push the image**

Run this step to build the image and push it to your registry. You only need to do this once, or whenever the image configuration changes. (Note: The --build-push flag automatically sets --mode ssl-bump).

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --build-push
```

Make the pushed image accessible from your cluster, then proceed to step 2.

**Step 3 — deploy and test**

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --mode ssl-bump --openshift
```

To leave OpenShift resources in place after testing for manual inspection add `--skip-cleanup` flag:

```bash
./helpers/multimedia-downloader/implementations/squid/test/deploy-and-test-squid.sh --mode ssl-bump --openshift --skip-cleanup
```

(Note: You can always pass --registry <url> manually if you prefer not to use the environment variable).

## 🛠️ Manual Deployment

> **Note:** The following steps assume your target cluster is already running and your active `kubectl` context is set correctly.

### Standard HTTP Proxy

**Step 1 — deploy**

#### kind

```bash
kubectl apply -k helpers/multimedia-downloader/implementations/squid/http/overlays/kind
kubectl apply -f helpers/multimedia-downloader/service.yaml
kubectl rollout status deployment/multimedia-downloader --timeout=120s
```

#### OpenShift

```bash
kubectl apply -k helpers/multimedia-downloader/implementations/squid/http/overlays/openshift
kubectl apply -f helpers/multimedia-downloader/service.yaml
kubectl rollout status deployment/multimedia-downloader --timeout=120s
```

**Step 2 — verify**

Run two requests through the proxy. The first should be a cache miss, the second a cache hit:

```bash
kubectl run curl-test --image=curlimages/curl:8.11.1 --restart=Never -- sleep 30
kubectl wait --for=condition=Ready pod/curl-test --timeout=60s
kubectl exec curl-test -- curl -sf -x http://multimedia-downloader:80 -o /dev/null -w "miss: %{http_code}\n" http://images.cocodataset.org/val2017/000000039769.jpg
kubectl exec curl-test -- curl -sf -x http://multimedia-downloader:80 -o /dev/null -w "hit:  %{http_code}\n" http://images.cocodataset.org/val2017/000000039769.jpg
```

### SSL Bump Proxy (Advanced)

Because SSL Bump intercepts encrypted traffic, it requires a custom CA certificate, a specialized container image, and platform-specific overlays.

| Overlay | Path | Image |
|---------|------|-------|
| `kind` | `https-ssl-bump/overlays/kind` | `squid-ssl-bump:local` (locally built) |
| `openshift` | `https-ssl-bump/overlays/openshift` | your registry — set `$SQUID_IMAGE_REGISTRY` or `--registry` |

**Step 1 — generate the CA certificate**

Because the Squid deployment mounts the certificate secrets, they must exist in the cluster before the deployment process begins.

| Secret | Contents | Mounted by |
|--------|----------|------------|
| `squid-ssl-certs` | CA cert + private key | Squid proxy (signs intercepted TLS) |
| `squid-ca-public-cert` | CA cert only | Client pods (trust the proxy CA) |


To automatically generate the secrets run:
```bash
./helpers/multimedia-downloader/implementations/squid/test/generate-ssl-certs.sh
```

**Step 2 — deploy**

First build the [Dockerfile](docker/Dockerfile.squid-ssl-bump) (required for both platforms):

```bash
docker build -t squid-ssl-bump:local \
  -f helpers/multimedia-downloader/implementations/squid/docker/Dockerfile.squid-ssl-bump \
  helpers/multimedia-downloader/implementations/squid/docker/
```

Next run the following commands depending on the platform:

#### kind

```bash
kind load docker-image squid-ssl-bump:local
kubectl apply -k helpers/multimedia-downloader/implementations/squid/https-ssl-bump/overlays/kind
kubectl apply -f helpers/multimedia-downloader/service.yaml
kubectl rollout status deployment/multimedia-downloader --timeout=120s
```

#### OpenShift

```bash
export SQUID_IMAGE_REGISTRY=<your-registry>
```

```bash
docker tag squid-ssl-bump:local ${SQUID_IMAGE_REGISTRY}/squid-ssl-bump:dev
docker push ${SQUID_IMAGE_REGISTRY}/squid-ssl-bump:dev
kubectl apply -k helpers/multimedia-downloader/implementations/squid/https-ssl-bump/overlays/openshift
kubectl set image deployment/multimedia-downloader squid=${SQUID_IMAGE_REGISTRY}/squid-ssl-bump:dev
kubectl apply -f helpers/multimedia-downloader/service.yaml
kubectl rollout status deployment/multimedia-downloader --timeout=120s
```

**Step 3 — verify**

Deploy the CA-trusting test pod and run two requests. The first should be a cache miss, the second a cache hit:

```bash
kubectl apply -f helpers/multimedia-downloader/implementations/squid/test/curl-test-pod.yaml
kubectl wait --for=condition=Ready pod/curl-ssl-test-pod --timeout=120s
kubectl exec curl-ssl-test-pod -- curl -sf -o /dev/null -w "miss: %{http_code}\n" https://images.dog.ceo/breeds/poodle-standard/n02113799_2280.jpg
kubectl exec curl-ssl-test-pod -- curl -sf -o /dev/null -w "hit:  %{http_code}\n" https://images.dog.ceo/breeds/poodle-standard/n02113799_2280.jpg
```

#### Configuring Client Pods (Production)

To route a real workload's traffic through the SSL Bump proxy, you must inject the Squid CA and proxy environment variables. Patch your client's Kustomize deployment:

```yaml
patches:
  - path: path/to/https-ssl-bump/patch-client-deployment.yaml
    target:
      kind: Deployment
      name: <your-client-deployment-name>
```
