# Kubernetes & OpenShift (Helm)

MoE Sovereign ships a modular Helm chart at `charts/moe-sovereign/` that targets
vanilla Kubernetes, k3s, and Red Hat OpenShift from the same source tree.

!!! warning "Community Validation Requested"
    The MoE Sovereign Helm chart is architecturally prepared for Kubernetes and OpenShift
    (OCI-compliant, non-root, read-only rootfs, NetworkPolicy, HPA, PDB). However, no
    formal production validation has been completed on these targets. K3s/Kubernetes
    is **Planned** and OpenShift is **Untested** in the deployment matrix.

    If you successfully deploy on K8s/OpenShift, please share your experience via a
    [GitHub Discussion](https://github.com/moe-sovereign/moe-sovereign/discussions) — your
    feedback directly drives the validation milestone.

## Chart structure

```mermaid
---
config:
  look: classic
---
flowchart TB
    subgraph C["charts/moe-sovereign"]
        CY["Chart.yaml<br/>(+ 4 conditional deps)"]
        V0["values.yaml<br/>profile: enterprise"]
        V1["values-solo.yaml<br/>profile: solo"]
        V2["values-team.yaml<br/>profile: team"]
        subgraph T["templates/"]
            SA[ServiceAccount]
            SEC["Secret<br/>(JWT public key)"]
            CM1[ConfigMap — env]
            CM2["ConfigMap — experts<br/>(Files.Glob configs/experts/*.yaml)"]
            D1[Deployment — orchestrator]
            D2[Deployment — mcp]
            D3[Deployment — admin]
            SVC[3× Service]
            IG[Ingress<br/>k8s only]
            RT[Route<br/>OpenShift only]
            NP[NetworkPolicy]
            HPA["HorizontalPodAutoscaler<br/>(optional)"]
            PDB[PodDisruptionBudget]
            AL["Alloy DaemonSet<br/>(optional)"]
        end
    end

    subgraph DEP["Conditional subcharts<br/>(Bitnami / Neo4j)"]
        PG[(postgresql)]
        KA[(kafka)]
        VK[(valkey)]
        NJ[(neo4j)]
    end

    CY --> PG & KA & VK & NJ

    classDef ch fill:#eef2ff,stroke:#6366f1;
    classDef tp fill:#f0fdf4,stroke:#16a34a;
    classDef dp fill:#fef3c7,stroke:#d97706;
    class CY,V0,V1,V2 ch;
    class SA,SEC,CM1,CM2,D1,D2,D3,SVC,IG,RT,NP,HPA,PDB,AL tp;
    class PG,KA,VK,NJ dp;
```

Every subchart is **conditional**: set `postgresql.enabled=true` (etc.) to let
Helm manage the data tier, leave it `false` to point at an existing cluster via
`external.postgresUrl`.

## Three profiles, three values files

```mermaid
---
config:
  look: classic
---
flowchart LR
    subgraph E["values.yaml<br/>(enterprise default)"]
        E1[all subcharts DISABLED]
        E2[external.*Url required]
        E3[2 replicas, HA-ready]
    end
    subgraph S["values-solo.yaml"]
        S1[subcharts ENABLED,<br/>dimensioned for ~4 GiB RAM]
        S2[1 replica, no PDB]
        S3[postgresql 384 MiB,<br/>kafka 512 MiB, valkey 128 MiB]
    end
    subgraph T["values-team.yaml"]
        T1[subcharts ENABLED,<br/>production-sized]
        T2[2+ replicas]
        T3[20 GiB PVCs]
    end

    classDef lo fill:#ecfdf5,stroke:#059669;
    classDef mi fill:#fef9c3,stroke:#ca8a04;
    classDef hi fill:#fef2f2,stroke:#dc2626;
    class S,S1,S2,S3 lo;
    class T,T1,T2,T3 mi;
    class E,E1,E2,E3 hi;
```

## Install — homelab / solo (k3s)

```bash
curl -sfL https://get.k3s.io | sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add neo4j   https://helm.neo4j.com/neo4j
helm dependency update charts/moe-sovereign

helm install moe charts/moe-sovereign \
    -f charts/moe-sovereign/values-solo.yaml \
    --set-file auth.jwt_public_key.pem=/path/to/jwt_pubkey.pem \
    --create-namespace --namespace moe

kubectl -n moe wait --for=condition=ready pod -l app.kubernetes.io/component=orchestrator --timeout=5m
kubectl -n moe port-forward svc/moe-moe-sovereign-orchestrator 8000:8000
```

## Install — enterprise with external data tier

```bash
helm install moe charts/moe-sovereign \
    --namespace moe \
    --set-file auth.jwt_public_key.pem=/path/to/jwt_pubkey.pem \
    --set external.postgresUrl='postgres://moe:pw@pg.prod:5432/moe' \
    --set external.kafkaUrl='kafka.prod:9092' \
    --set external.valkeyUrl='redis://valkey.prod:6379/0' \
    --set external.neo4jUri='bolt://neo4j.prod:7687' \
    --set orchestrator.autoscaling.enabled=true \
    --set orchestrator.autoscaling.maxReplicas=8
```

## Install — OpenShift

The chart auto-detects OpenShift via the presence of `route.openshift.io/v1`,
but you can force it:

```bash
helm install moe charts/moe-sovereign \
    --set openshift.enabled=true \
    --set route.host=moe.apps.ocp.example.com \
    --set-file auth.jwt_public_key.pem=/path/to/jwt_pubkey.pem \
    -f charts/moe-sovereign/values-team.yaml \
    --namespace moe
```

What changes automatically on OpenShift:

| Behaviour | k8s (default) | OpenShift (`openshift.enabled=true`) |
|---|---|---|
| Ingress object | `Ingress` (`networking.k8s.io/v1`) | `Route` (`route.openshift.io/v1`) |
| Pod `runAsUser` | `1001` (from values) | **omitted** — SCC injects the namespace's UID range |
| Pod `fsGroup` | `0` | omitted |
| Container `readOnlyRootFilesystem` | `true` | `true` (unchanged) |
| Dropped capabilities | `ALL` | `ALL` (unchanged) |

This satisfies the `restricted-v2` SCC out of the box. No cluster admin action
is required.

## Security posture

```mermaid
---
config:
  look: classic
---
flowchart TB
    subgraph P[Pod]
        direction TB
        SC1["runAsNonRoot: true"]:::ok
        SC2["runAsUser: 1001<br/>(or SCC-injected)"]:::ok
        SC3["readOnlyRootFilesystem: true"]:::ok
        SC4["allowPrivilegeEscalation: false"]:::ok
        SC5["capabilities.drop: [ALL]"]:::ok
        SC6["seccompProfile: RuntimeDefault"]:::ok
        V1[emptyDir: logs]:::wr
        V2[emptyDir: cache]:::wr
        V3[emptyDir: tmp]:::wr
        V4[ConfigMap: experts<br/>read-only]:::ro
        V5[Secret: JWT pubkey<br/>read-only]:::ro
    end

    classDef ok fill:#ecfdf5,stroke:#059669,color:#065f46;
    classDef wr fill:#fef9c3,stroke:#ca8a04;
    classDef ro fill:#eef2ff,stroke:#6366f1;
```

## Verification

```bash
helm lint charts/moe-sovereign
helm template test charts/moe-sovereign -f charts/moe-sovereign/values-solo.yaml \
    | kubectl apply --dry-run=client -f -
kubectl -n moe get deploy,svc,ingress,networkpolicy,pdb,hpa
kubectl -n moe logs deploy/moe-moe-sovereign-orchestrator | head -20
```

The universal-deployment test suite (`tests/test_deployment_artifacts.py`)
asserts the lint + template output for enterprise, solo, and OpenShift modes
as part of every CI run.
