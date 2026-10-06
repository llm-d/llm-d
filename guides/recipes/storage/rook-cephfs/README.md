# Rook CephFS Storage Recipe

This recipe installs a [Rook](https://rook.io)-managed Ceph cluster on the GPU nodes' own NVMe drives and exposes a CephFS filesystem, tuned for KV-cache offloading, as the `rook-cephfs-fast` ReadWriteMany StorageClass.

It is a building block, not a guide. The [Hyperconverged CephFS KV-Cache Offloading guide](../../../hyperconverged-cephfs/README.md) uses it for its shared cache tier.

> [!IMPORTANT]
> The filesystem keeps a **single copy** of its data and of its metadata. Losing one OSD can lose cached objects or the whole filesystem. Use this StorageClass only for data that can be recomputed, such as KV cache. Do not store model weights, checkpoints or user data on it.

## What it installs

| Component | Configuration |
| --------- | ------------- |
| Rook operator | Chart `rook-ceph`, values in [`rook-operator.values.yaml`](./rook-operator.values.yaml) |
| Ceph cluster | Ceph 19.2.4 on the host network, 3 monitors, 2 managers, OSDs on the labelled nodes |
| Filesystem | `kvcache-fs`: data pool with 1 copy and 128 placement groups, metadata pool with 1 copy, 2 active MDS daemons with standby-replay, spread over the storage nodes |
| StorageClass | `rook-cephfs-fast`, mounted with `noatime`, `nodiratime` and 64 MiB `rsize`/`wsize` |
| Tuning | OSD, MDS, client and messenger settings in [`rook-cluster.values.yaml`](./rook-cluster.values.yaml) |

## Prerequisites

- `helm` and `kubectl`, with cluster-admin access.
- At least three nodes with unused NVMe devices. Ceph takes every unused device that matches `deviceFilter` (default `^nvme[1-7]n1$`); check `lsblk` on each node first.
- A fast network between the storage nodes. Ceph runs on the host network. If you use jumbo frames, the MTU must be the same on every hop (node interfaces, bridges and switches): the smallest one caps the whole path.

## Install

Run the install script **once**, with every option you need combined in a single command. Decide on the options first.

### 1. Write your site-specific settings

Settings that depend on your cluster go in your own values file, which the script layers on top of the recipe's values. At minimum, set the subnets of your high-speed links:

```yaml
# my-ceph.values.yaml
cephClusterSpec:
  network:
    addressRanges:
      public:
        - 10.243.65.0/24  # management subnet (monitors)
        - 10.0.0.0/16     # data NIC 1
        - 10.1.0.0/16     # data NIC 2
  storage:
    deviceFilter: "^nvme[1-7]n1$"  # only if the default does not fit your nodes
```

Set `addressRanges` **before installing**. Without it the OSDs advertise each node's management address, and switching a running cluster to other addresses breaks monitor quorum.

Your file complements the recipe's values; it does not replace them. The two files are merged with Helm's rules, and yours wins where they overlap:

| What you set | Result |
| ------------ | ------ |
| A single value (for example `deviceFilter`, or one key under `cephConfig`) | Replaces that value. Everything else in the recipe's values stays. |
| A map (for example `cephClusterSpec.resources.osd`) | Merged key by key with the recipe's map. |
| A list (for example `addressRanges.public`, or `cephFileSystems`) | Replaces the recipe's whole list. |

The list rule matters for `cephFileSystems`: to change anything about the filesystem, its pools or its StorageClass, copy the complete `cephFileSystems` entry from [`rook-cluster.values.yaml`](./rook-cluster.values.yaml) into your file and edit the copy. A partial entry drops the rest of the recipe's filesystem definition and the install fails.

### 2. Choose the options

All options can be combined. Each one also has an environment variable, which the flag overrides.

| Option | Environment variable | Default | Purpose |
| ------ | -------------------- | ------- | ------- |
| `-N`, `--nodes "A B C"` | `STORAGE_NODES` | none | Label these nodes as storage nodes before installing. Omit it if the nodes already carry the storage label. At least three are required. |
| `-f`, `--values FILE` | `ROOK_CLUSTER_EXTRA_VALUES` | none | Your site-specific values file from step 1. |
| `-n`, `--namespace NAME` | `ROOK_NAMESPACE` | `rook-ceph` | Namespace for Rook and Ceph. |
| | `ROOK_CHART_VERSION` | `v1.20.2` | Rook chart version. |
| | `ROOK_CHART_REPO` | `https://charts.rook.io/release` | Helm repository for the Rook charts. |
| | `STORAGE_NODE_LABEL` | `llm-d.ai/ceph-storage=true` | Label that selects the storage nodes. If you change it, change the node affinity in the values files to match. |
| `-u`, `--uninstall` | | | Destroy the Ceph cluster and remove Rook; see [Uninstall](#uninstall). |
| `-y`, `--yes` | | | Skip the confirmation prompt of `--uninstall`. |
| `-h`, `--help` | | | Print the usage. |

### 3. Run the install

A typical install labels the nodes and applies the site-specific settings in one command:

```bash
export REPO_ROOT=$(realpath $(git rev-parse --show-toplevel))
${REPO_ROOT}/guides/recipes/storage/rook-cephfs/install-rook-cephfs.sh \
  --nodes "node-a node-b node-c" \
  --values my-ceph.values.yaml
```

The script labels the nodes, installs the Rook operator, creates the Ceph cluster and the filesystem, and waits until both are ready. It then mutes the health warning for the intentionally unreplicated pools, and only that warning.

The script is safe to run again with the same options, for example after a timeout or to apply a changed values file. Do not use a second run to change the network address ranges of a running cluster.

## Verify

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph status
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph fs status kvcache-fs
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd pool get kvcache-fs-data0 all
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd dump | grep '^osd\.'
```

Expect `HEALTH_OK` with every OSD `up` and `in`, two active MDS daemons, `size: 1` and `pg_num: 128` on the data pool, `size: 1` on `kvcache-fs-metadata`, and OSD addresses on your high-speed subnets.

## Use

Request a ReadWriteMany volume from the StorageClass:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: llm-d-kv-cache-storage
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: rook-cephfs-fast
  resources:
    requests:
      storage: 3Ti
```

Mount options apply at mount time: restart the pods that use the volume after changing them.

## Tuning

The values file carries the settings that mattered on the reference cluster (three nodes, 21 NVMe OSDs, six 200 Gb/s links per node), where they raised synthetic CephFS throughput from about 2 GB/s to more than 20 GB/s.

| Setting | Value | Purpose |
| ------- | ----- | ------- |
| Data and metadata pool `size` | 1 | No replication cost for recomputable data |
| Data pool `pg_num` | 128 | Spread objects over all OSDs; one placement group sends all I/O to one OSD |
| Active MDS daemons | 2 | Parallel metadata work |
| `mds_cache_memory_limit` | 4 GiB | Metadata cache |
| `osd_memory_target` | 8 GiB | OSD cache, below the 12 GiB container limit |
| `osd_op_num_shards_ssd` × `osd_op_num_threads_per_shard_ssd` | 16 × 4 | OSD concurrency |
| `osd_mclock_profile` | `high_client_ops` | Favor client I/O over recovery and scrub |
| `ms_async_op_threads`, `ms_tcp_rcvbuf` | 5, 4 MiB | Messenger concurrency and receive buffer |
| `client_readahead_max_bytes`, `objecter_inflight_ops` | 32 MiB, 4096 | Client read-ahead and operations in flight |
| Mount `rsize`/`wsize`, `noatime` | 64 MiB | Fewer, larger requests; no access-time writes |

These are calibrated for nodes with many cores and fast NVMe. Watch OSD CPU and latency before reusing them on smaller nodes. `high_client_ops` makes recovery slower.

Three further steps depend on your hardware inventory and are manual:

- **Spread OSDs over several NICs.** By default every OSD on a node binds to the first matching interface. On the reference cluster, binding the seven OSDs of each node across the six links (two on the first, one on each of the others) raised read throughput by 8% to 79%, depending on the test. For each OSD, one at a time, checking `ceph status` in between:

  ```bash
  kubectl exec -n rook-ceph deploy/rook-ceph-tools -- \
    ceph config set osd.<OSD_ID> public_addr "v2:<OSD_IP>:0/0"
  kubectl rollout restart -n rook-ceph deploy/rook-ceph-osd-<OSD_ID>
  kubectl rollout status -n rook-ceph deploy/rook-ceph-osd-<OSD_ID> --timeout=10m
  ```

- **Calibrate the OSD scheduler per drive.** Set `osd_mclock_max_capacity_iops_ssd` on each OSD from that drive's measured IOPS; the reference drives ranged from about 44,000 to 79,000. Do not give every OSD the highest value.
- **Choose the replication.** Raise the pools' `size` to 2 or 3 if warm cache must survive a node failure or a rolling upgrade. The pools are part of the `cephFileSystems` list, so copy the whole entry into your values file to change them. This costs capacity and write throughput. Raising only the metadata pool to 3 is cheap and keeps the filesystem itself alive when an OSD is lost, at the price of some cached objects.

## Uninstall

```bash
${REPO_ROOT}/guides/recipes/storage/rook-cephfs/install-rook-cephfs.sh --uninstall
```

This destroys the Ceph cluster and everything stored in it, after asking for confirmation. Delete the volumes that use the StorageClass first. The NVMe devices keep their Ceph labels afterwards; see the first entry below before reusing them.

## Troubleshooting

- **No OSDs are created.** Either `deviceFilter` matched nothing, or the devices belonged to an earlier Ceph cluster: the OSD prepare job then logs `belonging to a different ceph cluster`. Ceph 19 writes its device label at four offsets, so wiping the start and end of a drive is not enough. Run `ceph-volume lvm zap --destroy /dev/<device>` from a privileged Ceph pod on that node.
- **The operator stays in `Init` or its jobs never complete.** One storage node with a broken pod network is enough: the operator's init jobs get pinned to it. Cordon or taint that node, or move the operator off it, repair the node, and add it back.
- **Throughput is far below what the links allow.** Check the MTU on every storage node (`ip -o link show`) and on the switch. One interface at 1500 caps the whole Ceph path. Also check with `ceph osd dump` that the OSDs advertise addresses on the high-speed subnets.
- **Monitors fail to start after a reinstall.** A previous install left `/var/lib/rook` on the nodes. Delete that directory on each storage node.
- **The cluster never becomes ready on a network with restricted node ports.** Ceph on the host network listens on ports 3300, 6789 and 6800-7300 of the node addresses. The operator and CSI pods run on the host network to reach them (`enforceHostNetwork` in [`rook-operator.values.yaml`](./rook-operator.values.yaml)); open those ports between the storage nodes if a firewall or security group sits between them.
- **A volume stays in `ExternalProvisioning` and no CephFS CSI pods exist.** On Rook 1.20 the reference cluster had to create the CephFS `Driver` resource, the `cephfs-ctrlplugin-sa` and `cephfs-nodeplugin-sa` ServiceAccounts, and their RBAC by hand. Check `kubectl get driver.csi.ceph.io -n rook-ceph` and the pod events of the `ceph-csi-controller-manager`.
- **The filesystem has to be recreated.** Order matters on Ceph 19: mark it failed (`ceph fs fail kvcache-fs`), remove it (`ceph fs rm kvcache-fs --yes-i-really-mean-it`), allow pool deletion (`ceph config set mon mon_allow_pool_delete true`), remove the metadata pool and then the data pool with `ceph osd pool rm`, and only then delete and recreate the `CephFilesystem` resource.
- **A volume stays `Pending` or pods cannot mount it.** The CephFS CSI node plugin must run on every node that mounts the volume. If those nodes are tainted, add a matching toleration to the CephFS `Driver` resource.
