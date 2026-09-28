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

## Picking `SRIOV_IPAM_RANGE`

The range you choose has to satisfy two separate constraints:

1. **It must sit inside the subnet the fabric actually routes.** Pod VFs are
   only reachable within the VLAN/subnet block the network owner set aside for
   them. A range picked outside that block will allocate addresses fine and
   then fail to carry traffic.
2. **It must not overlap another whereabouts pool.** Two pools with
   overlapping ranges hand the same IP to different pods. Nothing rejects this
   at apply time; it surfaces later as flaky or refused RDMA connections.

Confirm the reserved block with your cluster/network owner if you can. Failing
that, two commands are needed, because neither one sees everything.

First, the existing `SriovNetwork` resources — this is the only place the VLAN
is visible, and it includes ranges that are reserved but not yet in use:

```bash
oc get sriovnetwork -A \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.vlan}{"\t"}{.spec.ipam}{"\n"}{end}'
```

Example output:

```
compute-p1v0            {
  "type": "whereabouts",
  "range": "172.24.0.0/16",
  "range_start": "172.24.0.1",
  "range_end": "172.24.0.255"
}
```

Read `range_start`/`range_end` before assuming `range` is all taken. When
present they narrow allocation to a sub-block, and that sub-block is the only
part actually consumed — above, a `/16` is declared but only `172.24.0.0/24` is
ever handed out, leaving the rest of `172.24.0.0/16` available.

Second, the whereabouts pools. This matters because whereabouts is a generic
IPAM plugin: any `NetworkAttachmentDefinition` can use it, not just ones the
SR-IOV operator generates. Ranges consumed by macvlan/ipvlan/multi-nic
attachments are therefore **invisible** to the listing above, while showing up
here. Each pool is named after the CIDR it serves:

```bash
oc get ippools.whereabouts.cni.cncf.io -A
```

Example output:

```
NAMESPACE          NAME                AGE
openshift-multus   172.24.0.0-16       24d
openshift-multus   172.24.1.0-24       5h7m
openshift-multus   p1-172.23.0.0-16    240d
```

A range appearing here with no matching `SriovNetwork` is the normal case, not
an anomaly — it usually belongs to some other attachment type sharing the
cluster. It is still a collision if you pick into it.

Pools are also created lazily: whereabouts writes one only once a pod first
requests an address from that range, and deleting a `SriovNetwork` does not
remove the pool it fed. So this list can both miss a range that is reserved but
idle, and retain one no longer referenced by anything.

To see which pods hold addresses in a specific pool:

```bash
oc get ippools.whereabouts.cni.cncf.io 172.24.1.0-24 -n openshift-multus -o yaml
```

`spec.allocations` maps each allocated IP to the pod holding it, and is the only
ownership signal a pool carries — there is no back-reference to the
`SriovNetwork` that fed it. **An empty `allocations` means the range is free, even
though the pool object itself lingers.**


With the reserved block and the claimed ranges known, pick a `/24` (or smaller)
from inside the block that no existing pool covers.

## Usage

```bash
export SRIOV_IPAM_RANGE=  # your unused /24 inside the RoCE subnet

envsubst < ${REPO_ROOT}/helpers/sriov-network/sriovnetwork.yaml | kubectl apply -f -
```

Confirm the operator rendered the attachment definition into your namespace:

```bash
oc get net-attach-def ${NAMESPACE}-rdma -n ${NAMESPACE}
```

If that returns `NotFound`, the `SriovNetwork` was created but not reconciled —
usually `SRIOV_RESOURCE_NAME` not matching any policy's `spec.resourceName`.
