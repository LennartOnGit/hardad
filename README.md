# härdad — a Swedish tutor hardened all the way down

A conversational Swedish-language tutor (FastAPI + Anthropic Claude) built as an end-to-end demonstration of Docker's supply-chain security stack. The app is the vehicle — the interesting part is everything *attached* to the image: build-time SBOM, SLSA provenance, cosign keyless signatures, Docker Scout policy evaluation, and a signed-digest deployment onto a bare-metal Kubernetes cluster.

- **Live demo:** [lenie.selfhost.eu/docker_demo](https://lenie.selfhost.eu/docker_demo) (invite only)
- **Image:** [`lennartondocker/hardad:hardened`](https://hub.docker.com/r/lennartondocker/hardad) (multi-arch: `linux/amd64`, `linux/arm64`)
- **Context:** portfolio project for a Docker Senior Solutions Engineer role. Not production.

The full build-journal — measurement deltas, SBOM diffs, attestation internals, Scout findings, and the one honest gap — is rendered inside the running app (scroll past the chat at `/docker_demo`).

## The hardening delta

Same FastAPI app, byte-identical Python dependency closure, two base images:

| Metric                | `python:3.13-slim` | `dhi.io/python:3.13` | Δ          |
|---                    |---                 |---                   |---         |
| Image size            | 89 MB              | 44 MB                | **−51%**   |
| Total packages        | 167                | 91                   | −46%       |
| Debian packages       | 122                | 43                   | −65%       |
| Python packages       | 36                 | 36                   | unchanged  |
| Total CVEs            | 23                 | 16                   | −30%       |
| Critical + High CVEs  | 0                  | 2                    | *see note* |

The hardened scan surfaces `CVE-2026-6100` (Crit) and `CVE-2026-4786` (High) against CPython 3.13.13 — both unfixed upstream, both invisible to the baseline because its Python binaries aren't indexed as a scannable package. Docker's 7-day DHI patch SLA gives those a contractual resolution window.

## Supply-chain pipeline

All steps run in [`.github/workflows/docker-ci.yml`](.github/workflows/docker-ci.yml) on push to `main`:

1. **Test** — `uv run pytest` (22 tests)
2. **Build** — `docker/build-push-action@v6` with `platforms: linux/amd64,linux/arm64`, `sbom: true`, `provenance: mode=max`. The base image digest is resolved at build time and written onto the per-platform manifest descriptors as `org.opencontainers.image.base.{name,digest}`.
3. **Sign** — `cosign sign --yes` by digest under GitHub OIDC. Fulcio cert + Rekor transparency-log entry form the verifiable audit trail.
4. **Scout policy gate** — `docker/scout-action@v1` evaluates the signed digest against the org's policy set.

Scout today: **3 / 4 policies pass**, 1 returns "No data". Scout's base-image resolver uses layer-fingerprint matching against an internal catalog; `dhi.io/python:3.13` isn't visible to a free Scout org without a DHI entitlement linked to the account. No amount of OCI annotations, config LABELs, or provenance attestations changes that — the gap is left visible in the report rather than quietly removed from the policy set. Full write-up in the in-app `07-scout` slide.

## Verifying the signed image

```bash
cosign verify \
  --certificate-identity-regexp 'https://github\.com/LennartOnGit/hardad/\.github/workflows/docker-ci\.yml@.*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  lennartondocker/hardad:hardened

docker buildx imagetools inspect lennartondocker/hardad:hardened --format '{{ json .SBOM }}' | jq
docker buildx imagetools inspect lennartondocker/hardad:hardened --format '{{ json .Provenance }}' | jq
```

## Deployment

k3s on a bare-metal **Proxmox VE** cluster at home — 2 compute nodes + 1 quorum/arbiter, arm64. Three k3s VMs (1 server, 2 agents) are scheduled across the hypervisor fabric. The NodePort address `192.168.177.30:30080` is a VM NIC on the Proxmox LAN. Apache on the edge proxies `https://lenie.selfhost.eu/docker_demo` → that NodePort.

Helm chart in [`k8s/hardad/`](k8s/hardad/):
- 2 replicas, `imagePullPolicy: Always` + rolling mutable `hardened` tag
- Liveness / readiness probes on `/docker_demo/healthz`
- values.yaml for non-secrets; two Kubernetes Secrets (`hardad-secrets` for app, `postgres-auth` for DB) hold everything sensitive — neither is committed to git
- Bitnami PostgreSQL chart co-located in the `tutor` namespace, with `auth.existingSecret: postgres-auth`

```bash
# 1. PostgreSQL auth - Secret is consumed by the Bitnami chart via
#    auth.existingSecret in k8s/postgres-values.yaml. The password is
#    never written to the repo.
PG_PW="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
PG_ADMIN_PW="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
kubectl create namespace tutor --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic postgres-auth -n tutor \
  --from-literal=password="$PG_PW" \
  --from-literal=postgres-password="$PG_ADMIN_PW"

# 2. App secrets - consumed by the hardad Deployment via secretKeyRef.
kubectl create secret generic hardad-secrets -n tutor \
  --from-literal=ANTHROPIC_API_KEY='…' \
  --from-literal=ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --from-literal=DATABASE_URL="postgresql+psycopg://tutor:${PG_PW}@postgres-postgresql:5432/tutor"

# 3. Install Postgres, then the app.
helm upgrade --install postgres oci://registry-1.docker.io/bitnamicharts/postgresql \
  -n tutor -f k8s/postgres-values.yaml
helm upgrade --install hardad ./k8s/hardad -n tutor
```

## Local development

```bash
uv sync
export ANTHROPIC_API_KEY=…
export ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run uvicorn app.main:app --reload --port 8000   # → http://127.0.0.1:8000
```

`DATABASE_URL` is optional — SQLite is used when unset.

## Tutor internals

Thin REST surface over Claude, budget-metered per user (`check_budget` → 429 before any billable work starts):

| Endpoint           | Model            | Purpose                                                                  |
|---                 |---               |---                                                                       |
| `POST /messages`   | Sonnet           | Tutor turn — one call returns `reply`, `cefr_score`, and `corrections`   |
| `POST /translate`  | Haiku            | SV → EN for drag-selected phrases or full messages                       |
| `GET /dict/{word}` | Haiku (fallback) | Seed (~350 A1-B1 words) → DB cache → Haiku. Seed+cache hits are free.    |
| `GET /news`        | Haiku            | Three conversation-starter cards tuned to rolling CEFR                   |
| `GET /me`          | —                | Bootstrap payload for the React shell                                    |

CEFR scoring (0-100, bucketed A1-C2), grammar-correction single-pass design, and the dictionary-seed migration path toward [Folkets Lexikon](https://folkets-lexikon.csc.kth.se/) are documented in the in-app slides.

## Repository layout

```
app/                         FastAPI app, templates, static assets, slides.json
k8s/hardad/                  Helm chart
Dockerfile_hardened          DHI-based image (shipped artifact)
Dockerfile                   Baseline python:3.13-slim image (kept for comparison)
.github/workflows/docker-ci.yml   End-to-end supply-chain pipeline
docs/sbom/                   Rendered baseline vs hardened SBOMs (SVG)
```

## License

Apache 2.0.

## Related

- [Docker Hardened Images](https://docs.docker.com/dhi/)
- [Sigstore](https://www.sigstore.dev/) — Fulcio + Rekor + cosign
- [SLSA](https://slsa.dev/) — v0.2 provenance via BuildKit
- [Docker Scout](https://docs.docker.com/scout/)
