#!/usr/bin/env bash
# Run one LMCache multiprocess (MP) server next to the model server, in the
# same container, and tie their lifetimes together.
#
#   launch-with-lmcache-mp.sh <model server command> [args...]
#
# Why the same container: the MP server copies KV blocks between its CPU memory
# and the engine's GPU tensors, so it needs the GPUs of the engine. A second
# container does not get them through nvidia.com/gpu: a request of its own is
# given other GPUs.
#
# Why this script and not the connector's own auto-start
# (lmcache.mp.autostart): auto-start needs LMCache >= 0.5.6, and it does not
# stop or restart the model server when the MP server dies.
#
# Environment:
#   LMCACHE_MP_SERVER_ARGS  required. `lmcache server` flags separated by
#                           whitespace (no quoting), e.g.
#                           "--l1-size-gb 250 --eviction-policy LRU".
#   LMCACHE_MP_PORT         request (ZMQ) port, default 5555. Must match
#                           lmcache.mp.port in --kv-transfer-config.
#   LMCACHE_MP_HTTP_PORT    HTTP port (/healthcheck, /status, /metrics),
#                           default 8080.
#   LMCACHE_MP_STOP_TIMEOUT seconds the MP server gets to stop when the pod is
#                           deleted, before it is killed. Default 30.
#
# Both ports are bound to 127.0.0.1, after LMCACHE_MP_SERVER_ARGS so that they
# cannot be overridden: the request port has no authentication, and the HTTP
# API can clear the cache and, in LMCache releases up to 0.5.5 at least, run
# caller-supplied Python (POST /run_script).
#
# Lifetime:
#   - Either process exiting ends the container at once, so the kubelet
#     restarts both and a running container always has its cache server. The
#     other process is killed, not drained: a draining vLLM keeps answering its
#     readiness path while it rejects new requests, and here the pod is still
#     in rotation.
#   - SIGTERM to this script (pod deletion) is forwarded to the model server
#     only. It drains in-flight requests for as long as its own
#     --shutdown-timeout and the pod's terminationGracePeriodSeconds allow; the
#     MP server is stopped after the model server has exited.
set -euo pipefail
# Job control: each background job gets its own process group, so that a
# process tree that does not stop can be killed as a whole.
set -m

log() { echo "launch-with-lmcache-mp: $*" >&2; }

if [ "$#" -eq 0 ]; then
  log "usage: $0 <model server command> [args...]"
  exit 64
fi
# All whitespace separates flags, so the value may span several lines.
read -r -d '' -a server_args <<< "${LMCACHE_MP_SERVER_ARGS:-}" || true
if [ "${#server_args[@]}" -eq 0 ]; then
  log "LMCACHE_MP_SERVER_ARGS is not set (e.g. --l1-size-gb 250 --eviction-policy LRU)"
  exit 64
fi

# Installed before anything is started, so that a stop request is never lost.
stopping=0
engine_pid=
trap 'stopping=1; [ -z "${engine_pid}" ] || kill -TERM "${engine_pid}" 2>/dev/null || true' TERM INT

lmcache server "${server_args[@]}" \
  --host 127.0.0.1 --port "${LMCACHE_MP_PORT:-5555}" \
  --http-host 127.0.0.1 --http-port "${LMCACHE_MP_HTTP_PORT:-8080}" &
cache_pid=$!

# The connector waits for the MP server (lmcache.mp.mq_timeout, 300 s by
# default) and fails the model server's startup if it never answers, so the
# two can start together.
"$@" &
engine_pid=$!
# A stop request that arrived before the model server existed.
[ "${stopping}" -eq 0 ] || kill -TERM "${engine_pid}" 2>/dev/null || true

# Until either process is gone or, once asked to stop, until the model server
# is gone. Polled once a second, not `wait -n`: a child that is killed by a
# signal and reaped by bash just before the call is not seen by `wait -n`,
# which then blocks on the other child.
while kill -0 "${engine_pid}" 2>/dev/null &&
  { [ "${stopping}" -eq 1 ] || kill -0 "${cache_pid}" 2>/dev/null; }; do
  sleep 1 || true
done

status=0
if kill -0 "${engine_pid}" 2>/dev/null; then
  wait "${cache_pid}" || status=$?
  log "LMCache MP server exited (status ${status}); killing the model server"
  [ "${status}" -ne 0 ] || status=1
elif [ "${stopping}" -eq 1 ]; then
  wait "${engine_pid}" || status=$?
  log "model server stopped (status ${status}); stopping the LMCache MP server"
  kill -TERM "${cache_pid}" 2>/dev/null || true
  for ((waited = 0; waited < ${LMCACHE_MP_STOP_TIMEOUT:-30}; waited++)); do
    kill -0 "${cache_pid}" 2>/dev/null || break
    sleep 1 || true
  done
else
  wait "${engine_pid}" || status=$?
  log "model server exited (status ${status}); killing the LMCache MP server"
  [ "${status}" -ne 0 ] || status=1
fi

# Whatever is left of both process groups goes now. In a container this
# script is PID 1, so nothing outlives it either way. Reaping them here, with
# stderr discarded, keeps the shell's own "Killed" notices out of the pod log.
{
  kill -KILL -- "-${engine_pid}" "-${cache_pid}"
  wait "${engine_pid}" "${cache_pid}"
} 2>/dev/null || true
exit "${status}"
