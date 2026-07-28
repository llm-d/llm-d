# Redis Sorted Set Implementation

This implementation uses Redis Sorted Sets as the backend for the request queue. This provides persistence and the ability to sort requests by priority (using the deadline as the score).

## Prerequisites

1. **Redis Server**: You need a running Redis instance accessible from your Kubernetes cluster.
   - **No Authentication**:

     ```bash
     helm repo add bitnami https://charts.bitnami.com/bitnami
     helm install redis bitnami/redis -n redis --create-namespace --set auth.enabled=false
     ```

   - **With Authentication**:

     ```bash
     helm repo add bitnami https://charts.bitnami.com/bitnami
     # Install Redis with a password
     export REDIS_PASSWORD=your-secure-password
     helm install redis bitnami/redis -n redis --create-namespace --set auth.enabled=true --set auth.password=$REDIS_PASSWORD

     # Create a secret holding the full connection URL for the Async Processor
     # (referenced via ap.redis.secretName / ap.redis.secretKey in values.yaml):
     kubectl create secret generic redis-creds -n llm-d-async \
       --from-literal=url="redis://:$REDIS_PASSWORD@redis-master.redis.svc.cluster.local:6379"
     ```

## Configuration and Deployment

We provide a `values.yaml` for this implementation in `guides/asynchronous-processing/redis/values.yaml`.

Edit the `values.yaml` file with your specific Redis connection:

```yaml
ap:
  redis:
    # No auth: the connection URL directly (the chart creates the Secret).
    url: "redis://redis-master.redis.svc.cluster.local:6379"
    # With auth: reference the Secret created above instead of `url`.
    # secretName: "redis-creds"
    # secretKey: "url"
```

For deployment instructions, please refer to the [main README](../README.md#installation).

## Testing

1. **Wait for Async Processor to be ready**:

   ```bash
   kubectl get pods -n llm-d-async
   ```

2. **Publish a message using Redis CLI**:

   ```bash
   export REDIS_IP=$(kubectl get svc -n redis redis-master -o jsonpath='{.spec.clusterIP}')
   # If you used authentication, pass the password using -a
   # kubectl run --rm -i -t publishmsgbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP -a $REDIS_PASSWORD ZADD request-sortedset 1999999999 '{"id" : "testmsg", "payload":{ "model":"your-model", "prompt":"Hi, good morning "}, "deadline" :"1999999999" }'
   # Otherwise:
   kubectl run --rm -i -t publishmsgbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP ZADD request-sortedset 1999999999 '{"id" : "testmsg", "payload":{ "model":"your-model", "prompt":"Hi, good morning "}, "deadline" :"1999999999" }'
   ```

3. **Check for results**:

   ```bash
   # If you used authentication, pass the password using -a
   # kubectl run --rm -i -t resultbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP -a $REDIS_PASSWORD RPOP result-list
   # Otherwise:
   kubectl run --rm -i -t resultbox --image=redis --restart=Never -- /usr/local/bin/redis-cli -h $REDIS_IP RPOP result-list
   ```
