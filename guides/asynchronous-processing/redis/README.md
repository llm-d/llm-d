# Redis Sorted Set Implementation

This implementation uses Redis Sorted Sets as the backend for the request queue. This provides persistence and the ability to sort requests by priority (using the deadline as the score).

## Prerequisites

1. **Redis Server**: You need a running Redis instance accessible from your Kubernetes cluster.
   - If you don't have one, you can install it via Helm:
     ```bash
     helm repo add bitnami https://charts.bitnami.com/bitnami
     helm install redis bitnami/redis -n redis --create-namespace --set auth.enabled=false
     ```

## Configuration

In your `values.yaml`, ensure the following parameters are set:

```yaml
ap:
  redis:
    enabled: true
    host: "redis-master.redis.svc.cluster.local" # Adjust as necessary
    port: 6379
    requestPathURL: "/v1/completions"
    messageQueueImpl: "redis-sortedset" 
```

### Key Parameters:
- `redis.ss.addr`: Address of the Redis server.
- `redis.ss.request-queue-name`: The name of the sorted-set for the requests (default: `request-sortedset`).
- `redis.ss.result-queue-name`: The name of the list for the results (default: `result-list`).

## Testing

1. **Wait for Async Processor to be ready**:
   ```bash
   kubectl get pods -n llm-d-async
   ```

2. **Publish a message using Redis CLI**:
   ```bash
   export REDIS_IP=$(kubectl get svc -n redis redis-master -o jsonpath='{.spec.clusterIP}')
   kubectl run --rm -i -t publishmsgbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP ZADD request-sortedset 1999999999 '{"id" : "testmsg", "payload":{ "model":"your-model", "prompt":"Hi, good morning "}, "deadline" :"1999999999" }'
   ```

3. **Check for results**:
   ```bash
   kubectl run --rm -i -t resultbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP RPOP result-list
   ```
