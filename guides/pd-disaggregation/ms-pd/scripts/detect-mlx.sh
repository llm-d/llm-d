#!/bin/bash

# Get all network interfaces in the pod
mapfile -t POD_NETS < <(ls /sys/class/net/)

# Arrays to store MLX device info
declare -A NIC_NETIF NIC_PCI NIC_NUMA
declare -A NET_TO_MLX

echo "Detecting MLX devices in pod..."
echo

# Find all MLX devices
mapfile -t MLX < <(ls /sys/class/infiniband 2>/dev/null | grep mlx5 || true)

# Detect devices and map them by their netif
for mlx in "${MLX[@]}"; do
    devpath="/sys/class/infiniband/$mlx/device"
    pci=$(readlink -f "$devpath" | awk -F'/' '{print $NF}')
    numa=$(cat "$devpath/numa_node" 2>/dev/null || echo unknown)

    # Get network interfaces for this MLX device
    mapfile -t THIS_NETS < <(ls "$devpath/net/" 2>/dev/null || true)

    # Check if any of this device's netdevs are in the pod
    for nn in "${THIS_NETS[@]}"; do
        if printf '%s\n' "${POD_NETS[@]}" | grep -Fxq "$nn"; then
            NIC_NETIF[$mlx]="$nn"
            NIC_PCI[$mlx]="$pci"
            NIC_NUMA[$mlx]="$numa"
            NET_TO_MLX[$nn]="$mlx"
            break
        fi
    done
done

# Sort POD_NETS in natural order: net1, net2, net3 ...
IFS=$'\n' SORTED_NETS=($(printf "%s\n" "${POD_NETS[@]}" | sort -V))
unset IFS

# Print devices in natural order and build UCX_NET_DEVICES
UCX_NET_DEVICES=""
for net in "${SORTED_NETS[@]}"; do
    mlx=${NET_TO_MLX[$net]}
    [[ -z "$mlx" ]] && continue

    echo "  $mlx"
    echo "    NETIF = ${NIC_NETIF[$mlx]}"
    echo "    PCI   = ${NIC_PCI[$mlx]}"
    echo "    NUMA  = ${NIC_NUMA[$mlx]}"
    echo

    [[ -n "$UCX_NET_DEVICES" ]] && UCX_NET_DEVICES+=","
    UCX_NET_DEVICES+="$mlx:1"
done

export UCX_NET_DEVICES
echo "UCX_NET_DEVICES=$UCX_NET_DEVICES"
