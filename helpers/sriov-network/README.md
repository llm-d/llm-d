# SR-IOV RDMA Network

Gives every pod in a namespace a dedicated SR-IOV VF (its own netdev, MAC, and
IP) on a RoCE-capable NIC, via a Multus `SriovNetwork`. Used by any guide that
needs RDMA between its own pods without sharing a NIC pool with unrelated
workloads on the node.

Applying this creates one `SriovNetwork` named `${NAMESPACE}-rdma`, scoped to
`${NAMESPACE}` via `spec.networkNamespace` — pods in other namespaces can't
reference it. The SR-IOV operator then renders it into a
`NetworkAttachmentDefinition` of the same name inside `${NAMESPACE}`, which is
what pods actually attach to.

## Prerequisites

A cluster admin must have already created a `SriovNetworkNodePolicy`. That
policy is what carves physical NICs into VFs and advertises them to the
scheduler as an `openshift.io/<resourceName>` extended resource. This helper
does **not** create one — it only builds a namespaced network on top of a pool
that already exists.

List the policies on the cluster together with the resource name each one
advertises:

```bash
oc get sriovnetworknodepolicy -n openshift-sriov-network-operator \
  -o custom-columns=NAME:.metadata.name,RESOURCE_NAME:.spec.resourceName
```

Example output:

```
NAME                              RESOURCE_NAME
p0-sriov-network-policy           p0_sriov_nodepolicy
p1-compute-sriov-network-policy   p1_sriov_nodepolicy
```

The two columns are deliberately both shown: `SRIOV_RESOURCE_NAME` must be the
**right-hand** value (`spec.resourceName`), which is a separate field and not
derivable from the policy's own name. Pick the pool covering the NICs you want,
then export it along with your namespace:

```bash
export NAMESPACE=your-namespace
export SRIOV_RESOURCE_NAME=p1_sriov_nodepolicy   # RESOURCE_NAME column above
```

## Picking the IP range

Each pod's VF needs an IP address on the RoCE fabric. You set three values:

| Variable | Meaning | Example |
|---|---|---|
| `SRIOV_IPAM_RANGE` | The fabric's whole subnet | `172.24.0.0/16` |
| `SRIOV_IPAM_RANGE_START` | First address this network may hand out | `172.24.0.1` |
| `SRIOV_IPAM_RANGE_END` | Last address this network may hand out | `172.24.0.255` |

Why the subnet and a start/end, instead of just a small CIDR: whereabouts
(the IPAM plugin) keeps one pool per `range` value, named after it. Networks
with the same `range` share that pool, so whereabouts never gives the same IP
to two pods, even when their start/end slices overlap. A network with a
different `range`, even one inside the same subnet, gets a separate pool that
can't see the others' allocations, and duplicate IPs only show up later as
flaky or refused RDMA connections.

### Find the fabric's subnet and the slice for your NIC

Confirm with your cluster or network owner if you can. Otherwise, list the
existing `SriovNetwork` resources:

```bash
oc get sriovnetwork -A \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.resourceName}{"\t"}{.spec.ipam}{"\n"}{end}'
```

Example output (trimmed):

```
compute-p1v0   p1_sriov_nodepolicy   { "type": "whereabouts", "range": "172.24.0.0/16",
                                       "range_start": "172.24.0.1", "range_end": "172.24.0.255" }
compute-p2v0   p2_sriov_nodepolicy   { "type": "whereabouts", "range": "172.24.0.0/16",
                                       "range_start": "172.24.1.1", "range_end": "172.24.1.255" }
```

Read it as:

- `range` is the fabric's subnet. Use the same value for `SRIOV_IPAM_RANGE`.
- Each NIC (`p1`, `p2`, ...) has its own slice, given by `range_start` and
  `range_end`. Use the slice of the network whose `resourceName` matches your
  `SRIOV_RESOURCE_NAME`. With per-NIC routing, a VF on `p1` is only reachable
  with an address from `p1`'s slice, so don't take another NIC's slice.

Sharing that slice with the existing network is safe, because both use the
same `range` and therefore the same pool.

### Check what is already allocated

Whereabouts pools are listed with:

```bash
oc get ippools.whereabouts.cni.cncf.io -A
```

Each pool is named after its `range`, so `172.24.0.0/16` appears as
`172.24.0.0-16`. Pools also include ranges used by non-SR-IOV attachments
(macvlan, ipvlan, multi-nic), which the `SriovNetwork` listing doesn't show.
Never pick a `range` that overlaps one of these unless it is exactly the same
value.

To see which pods hold addresses in a pool:

```bash
oc get ippools.whereabouts.cni.cncf.io 172.24.0.0-16 -n openshift-multus -o yaml
```

`spec.allocations` maps each allocated address to the pod holding it.

## Usage

```bash
export SRIOV_IPAM_RANGE=172.24.0.0/16
export SRIOV_IPAM_RANGE_START=172.24.0.1
export SRIOV_IPAM_RANGE_END=172.24.0.255
: "${NAMESPACE:?}" "${SRIOV_RESOURCE_NAME:?}" "${SRIOV_IPAM_RANGE:?}" \
  "${SRIOV_IPAM_RANGE_START:?}" "${SRIOV_IPAM_RANGE_END:?}"

envsubst '${NAMESPACE} ${SRIOV_RESOURCE_NAME} ${SRIOV_IPAM_RANGE} ${SRIOV_IPAM_RANGE_START} ${SRIOV_IPAM_RANGE_END}' \
  < ${REPO_ROOT}/helpers/sriov-network/sriovnetwork.yaml | kubectl apply -f -
```

Confirm the operator rendered the attachment definition into your namespace:

```bash
oc get net-attach-def ${NAMESPACE}-rdma -n ${NAMESPACE}
```

If that returns `NotFound`, the `SriovNetwork` was created but not reconciled —
usually `SRIOV_RESOURCE_NAME` not matching any policy's `spec.resourceName`.

## Consuming the network

Pods attach it through a Multus annotation and request one VF from the pool
by its full resource name, which carries the operator's `openshift.io/`
prefix. Guides that use this helper take both as variables:

```bash
export RDMA_NETWORK=${NAMESPACE}-rdma
export RDMA_VF_RESOURCE=openshift.io/${SRIOV_RESOURCE_NAME}
```
