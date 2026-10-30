# CPU, memory and unused Heat check

Run this on **R2d2** before sizing the next VMs or deciding whether Heat cleanup is needed.

## 1. Read the current state

```bash
cd ~/openstack-from-zero
bash scripts/lab-resource-check.sh --check
```

Enter each node's sudo password when asked. The check reads all three nodes. It sends no service stop, restart or queue deletion command.

## 2. Read the output

| Output | Meaning |
|---|---|
| CPU threads | Logical host CPUs; this is not a count of free OpenStack vCPUs |
| CPU load averages | Work running or waiting over 1, 5 and 15 minutes; not CPU percentage |
| Memory `available` | RAM the host can use now; use this instead of `free` for an initial sizing check |
| Heat containers | Whether Heat containers exist and are running or stopped |
| Heat startup units | Whether installed Heat units are enabled at boot; `NONE found` is a normal result when Heat is absent |
| RabbitMQ resource usage | A current container CPU and memory sample |
| Total queues | Cluster-wide queue count in vhost `/`, read once through node1 |
| Empty Heat quorum queues with no consumers | Exact Heat-style UUID names to investigate; not automatic deletion approval |

`COLLECTED` means the observations completed. It does not mean the cluster is healthy or queue deletion is safe. `INCOMPLETE` names a node whose check could not finish; its missing values remain unknown. Other vhosts, if listed, need separate inspection.

## 3. Review before changing anything

Return the output. If Heat is present, first verify whether any projects, stacks or operations use it. Retire only confirmed unused Heat services. If abandoned Heat queues are then confirmed, review their exact names and fresh message/consumer state before a separately approved cleanup. A high queue count alone is not proof of stale queues.

If there are no Heat services and no Heat-named queues, there is no Heat retirement or Heat queue cleanup to perform based on this inventory. Continue with fresh OpenStack allocation/quota checks before approving Terraform VM sizes.

This is a current-state check. The previous repository's Heat incident is not evidence of the same problem in this rebuilt lab.

Command reference: [RabbitMQ queue inventory command](https://github.com/rabbitmq/rabbitmq-server/blob/v4.2.9/deps/rabbitmq_cli/lib/rabbitmq/cli/ctl/commands/list_queues_command.ex). The check also prints the actual RabbitMQ version for review.
