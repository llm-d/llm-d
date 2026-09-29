# Moved — now `keda-epp`

This guide moved to **[keda-epp/README.md](keda-epp/README.md)**.

The old name `README.hpa-epp.md` described the **queue-based KEDA + EPP**
autoscaling path (KEDA creates the HPA; it is not a hand-written HPA). That path
is now the default signal in the consolidated `keda-epp/` guide - apply the
`k8s-queue` (or `ocp-queue`) overlay. This stub remains only for inbound-link
compatibility.
