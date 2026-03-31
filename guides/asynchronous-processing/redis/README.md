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
     
     # Create a secret for the Async Processor to use
     kubectl create secret generic redis-creds -n llm-d-async --from-literal=password=$REDIS_PASSWORD
     ```

## Configuration

The deployment uses environment variables to dynamically configure the Redis resources. You can configure it by setting the following environment variables:

- `REDIS_HOST` (Optional): The Redis server host. Defaults to `redis-master.redis.svc.cluster.local`.
- `REDIS_PORT` (Optional): The Redis server port. Defaults to `6379`.
- `REDIS_REQUEST_QUEUE_NAME` (Optional): The name of the sorted-set for the requests. Defaults to `request-sortedset`.
- `REDIS_RESULT_QUEUE_NAME` (Optional): The name of the list for the results. Defaults to `result-list`.
- `REDIS_AUTH_ENABLED` (Optional): Set to `true` to enable authentication. Defaults to `false`.
- `REDIS_SECRET_NAME` (Optional): The name of the Kubernetes secret containing Redis credentials. Defaults to `redis-creds`.
- `REDIS_USERNAME_KEY` (Optional): The key in the secret for the username. No default.
- `REDIS_PASSWORD_KEY` (Optional): The key in the secret for the password. No default.

Your `values.yaml.gotmpl` is configured as follows:

```yaml
ap:
  messageQueueImpl: "redis-sortedset"
  redis:
    enabled: true
    host: {{ env "REDIS_HOST" | default "redis-master.redis.svc.cluster.local" | quote }}
    port: {{ env "REDIS_PORT" | default "6379" | int }}
    requestPathURL: "/v1/completions"
    requestQueueName: {{ env "REDIS_REQUEST_QUEUE_NAME" | default "request-sortedset" | quote }}
    resultQueueName: {{ env "REDIS_RESULT_QUEUE_NAME" | default "result-list" | quote }}
    auth:
       enabled: {{ env "REDIS_AUTH_ENABLED" | default "false" }}
       secretName: {{ env "REDIS_SECRET_NAME" | default "redis-creds" | quote }}
{{- if env "REDIS_USERNAME_KEY" }}
       usernameKey: {{ env "REDIS_USERNAME_KEY" | quote }}
{{- end }}
{{- if env "REDIS_PASSWORD_KEY" }}
       passwordKey: {{ env "REDIS_PASSWORD_KEY" | quote }}
{{- end }}
```

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
