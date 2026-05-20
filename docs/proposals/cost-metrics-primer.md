# Cost Metrics Primer

**Infrastructure Costs: Different Ways to Measure What You Spend**

The core question: what did you actually pay for?

When you run workloads on a Kubernetes cluster, you pay for the **nodes** (servers) whether they are busy or not. The question is how you attribute fixed and common costs across workloads.  And, how you reflect allocated resources that were under-utilized - i.e. wasted.

There are three distinct layers of cost:

1. **Used** — resources a workload reserved *and* actually consumed
2. **Wasted** — resources a workload reserved but did not use (over-provisioning)
3. **Idle** — resources that no workload reserved at all — spare cluster capacity
4. **Shared** - resources that are common to all workloads - [discussed here](#shared-costs)

  
### Reservation-based cost — max(request, usage)

Each workload pays for what it reserved. If App-a reserved 3 CPUs and used 1 — it pays for 3. The wasted 2 CPUs are baked into app-a's bill. The idle CPUs (unreserved by anyone) appear as a separate `__idle__` line item. The cluster bill always reconciles — every dollar is accounted for across workloads plus idle.

### Usage-only cost

Each workload pays only for what it actually consumed. App-a pays for 1 CPU, same as app-b. The wasted 2 CPUs (app-a's over-provisioning) now join the idle pool as unattributed cost. **The numbers no longer add up to your overall bill.**


### The practical difference - usage vs reservation based costs

|                              | Reservation-based                             | Usage-only                                          |
|------------------------------|-----------------------------------------------|-----------------------------------------------------|
| Reconciles to cloud bill     | Yes                                           | No — waste + idle are unattributed                  |
| Fair for team chargeback     | No — penalizes over-provisioning              | Yes                                                 |
| Incentivizes right-sizing    | Yes — you pay for what you reserve            | Less so                                             |
| Identifies over-provisioning | Not directly — baked into workload cost       | Yes — visible as the gap between the two cost figures |
| Identifies spare capacity    | Yes — explicit idle line item                 | Waste and idle merged into one unattributed pool    |

Neither is wrong — they answer different questions. Reservation-based answers *"who caused this bill?"* Usage-only answers *"who consumed what?"* The over-provisioning gap is the key distinction: reservation-based assigns it to the workload responsible, usage-only leaves it unattributed alongside genuinely idle capacity.

### Example:

  In this example we focus only on CPU costs to simplify the discussion.

  A cluster has one node provisioned with 8 CPUs at **$10/hr per CPU**. Two workloads are running:

  - namespace/app-a: requested 3 CPUs out of 8 available → pays for 3/8 = 37.5% of CPU
  - namespace/app-b: requested 1 CPU → pays for 1/8 = 12.5% of CPU
  - 4 CPUs are idle (nobody requested them) → 50% of CPU cost = $5/hr

  CPU Cost represents the cost of usage + waste.

  CPU Usage Cost is only the usage without taking into account the waste or idle.

  For idle, there are two ways to attribute the cost of the idle CPUs.  Either across workloads or as a separate line item.

  **Separating out Idle:**

  
  | Workload   | Requested | Used | Billed cores | CPU cost (of $10/hr) | CPU Usage Cost | Wasted |
  |------------|-----------|------|--------------|----------------------|----------------|--------|
  | app-a      | 3         | 1    | 3            | $3.75                | $1.25          | $2.50  |
  | app-b      | 1         | 1    | 1            | $1.25                | $1.25          | —      |
  | \_\_idle__ | —         | —    | 4            | **$5.00**            | —              | —      |


  **Weighted Distribution of Idle Costs:**
  Each workload incorporates its relative percentage of the idle cost.  In our example 75% of the idle cost is attributed to app-a since it allocated 75% of the CPUs, and 25% of the idle cost is attributed to app-b since it allocated only one of the 4 CPUs.

  | Workload | Requested | Used | Billed cores | CPU cost (of $10/hr) | CPU Usage Cost | Wasted |
  |----------|-----------|------|--------------|----------------------|----------------|--------|
  | app-a    | 3         | 1    | 3            | **$7.50**            | $1.25          | $2.50  |
  | app-b    | 1         | 1    | 1            | **$2.50**            | $1.25          | —      |
  
    Usage only cost:         app-a=$1.25, app-b=$1.25, unattributed=$7.50  → total $10.00 ✓
    Reservation based cost:  app-a=$3.75, app-b=$1.25, idle=$5.00  → total $10.00 ✓

### Shared Costs
  
  OpenCost has three distinct mechanisms for handling shared ccosts, each for a different scenario.

  1. Shared namespaces

  Workloads in designated namespaces (e.g. monitoring, logging, ingress) are treated as infrastructure
  serving everyone. Their entire cost is removed from their namespace and distributed across all other
  workloads.
  
  Example: A Prometheus stack in the monitoring namespace costs $50/day. With
  SharedNamespaces=["monitoring"], that $50 is spread across all other namespaces — no workload "owns"
  it outright.
  
  2. Shared labels

  Same idea but scoped by label rather than namespace. Workloads matching a given label key/value are
  treated as shared infrastructure.

  Example: Pods labelled team=platform are shared services. Their cost is distributed to all other
  workloads regardless of which namespace they live in.

  3. Shared overhead (flat hourly cost)

  A fixed dollar-per-hour cost injected directly — no Kubernetes workload behind it. Used for costs that
   exist outside the cluster entirely: software licenses, support contracts, a shared database, a
  managed service.
  
  Example: SharedHourlyCosts={"datadog": 2.08} injects $50/day as a synthetic allocation that gets
  distributed like any other shared cost.


  Example: SharedHourlyCosts={"datadog": 2.08} injects $50/day as a synthetic allocation that gets distributed like any other shared cost.

  4. Shared load balancer (sharelb=true)

  Load balancer costs are typically attributed to the workload behind the LB. With sharelb=true, LB costs are pooled and distributed across all
  workloads instead — useful when a single LB fronts multiple services.

  ---
  Distribution methods

  Once a cost is identified as shared, it's distributed across remaining workloads using ShareSplit:

  ShareEven — each workload gets an equal slice regardless of size. 3 workloads → each gets 1/3.

  ShareWeighted — each workload gets a slice proportional to its own total cost. A workload that already costs $90 out of a $100 total gets 90% of
  the shared cost. This is the default and generally fairer.

  ---
  What it looks like in the API

  ```
  GET /allocation?window=1d
    &aggregate=namespace
    &shareIdle=true
    &shareSplit=weighted
```
  Each allocation in the result gets a sharedCost field showing the total shared cost attributed to it, and if includeSharedCostBreakdown=true is
  set, a sharedCostBreakdown map showing exactly which shared sources contributed what:
```
  "app-a": {
    "cpuCost": 3.75,
    "sharedCost": 4.20,
    "sharedCostBreakdown": {
      "monitoring": { "totalCost": 3.10 },
      "datadog":    { "totalCost": 1.10 }
    },
    "totalCost": 7.95
  }
```
  
  The key design point: shared costs are always distributed after the primary cost allocation is computed, so they don't affect cpuCost, ramCost, etc. — they accumulate separately in sharedCost. This means you can always see what a workload cost on its own vs. what it cost once shared infrastructure is factored in.
