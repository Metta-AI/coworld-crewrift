# Crewrift Prime commissioner — filler-policy credential + deploy handoff

This doc is the **ready-to-run** handoff for finishing the "seat the configured
filler policy" work. The application code is done and merged-ready (see the two
PRs below); the only remaining steps require **cluster / registry access** that an
app engineer does not have. `treeform@softmax.com` (or whoever owns the tournament
EKS cluster + the Observatory image registry) can run these directly.

## What is already true (verified live, 2026-06-26)

- The **Crewrift Prime** league (`league_a12f5172-0907-4d04-8bcb-ca02f5360e3a`)
  has `filler_policy_version_ids = ["4e65356f-9a2c-4e6b-af27-bdb43515547b"]`.
- `GET …/observatory/v2/leagues/league_a12f5172-…/filler-policies` returns that id:
  **`crewborg-aaln` version 16** (`policy_version_id 4e65356f-9a2c-4e6b-af27-bdb43515547b`).
  So the admin config, the endpoint, and the token all work end to end.
- The commissioner code already resolves fillers (env → league-config API → none)
  and the league-id-prefix bug (PR #77) is committed on the deploy branch.
- The remaining blockers are purely deployment-side:
  1. the **deployed** canonical Crewrift Prime coworld (currently `crewrift_prime`
     v0.4.9) has an **empty commissioner `env` and no `secret_env`**, so the
     commissioner pod has **no `SOFTMAX_API_TOKEN`** and the league-config API call
     fails auth → empty filler list → no filler ever seated;
  2. the commissioner pod runs with `automount_service_account_token=False` and a
     sanitized plaintext env, so a token must arrive via a Kubernetes `secretKeyRef`.

## The mechanism that was added (code, merged-ready)

- **metta** (PR: commissioner `secret_env`): a new optional `secret_env` manifest
  field on a commissioner runnable is wired into the commissioner Job as a
  `valueFrom.secretKeyRef` against a cluster Secret named by the new
  `COWORLD_COMMISSIONER_SECRET_NAME` config (default `coworld-commissioner-secrets`).
  It bypasses the plaintext-env sanitizer entirely — the value never appears in the
  manifest, the plaintext env, or logs. `coworld patch-commissioner` also gained a
  `--secret-env NAME=SECRET_KEY` flag so the image bump and the secret wiring happen
  in one privileged command (the deployed canonical manifest has an empty env, so a
  plain image-only patch would NOT add the secret). The same PR relaxes
  `GET /v2/leagues/{id}/filler-policies` from full team-auth to authenticated-read
  (`SUBMITTER_AUTH`), so a least-privilege token can read fillers; `POST` stays
  team-guarded.
- **coworld-crewrift** (PR: manifest `secret_env`): declares
  `secret_env: {"SOFTMAX_API_TOKEN": "SOFTMAX_API_TOKEN"}` on the
  `among-them-commissioner` runnable in `coworld_manifest.crewrift_prime.json`, for
  any future full manifest upload. (The live deploy uses `patch-commissioner`, so the
  `--secret-env` flag above is what actually attaches it today — see step 2.)

## STEP 1 — Provision the `coworld-commissioner-secrets` Secret in the `jobs`
namespace (tournament cluster)

The commissioner Job runs in namespace **`jobs`** on the **tournament** EKS cluster
(see `devops/app-manifests/values.yaml` → `name: tournament, cluster: tournament,
namespace: jobs`). It needs a Secret named **`coworld-commissioner-secrets`** with
key **`SOFTMAX_API_TOKEN`** in that namespace.

`SOFTMAX_API_TOKEN` already exists in AWS Secrets Manager at
**`vault/softmax/disco/api-token`** (a plain-string secret, as used by the disco
ExternalSecret in `devops/charts/disco/templates/externalsecret.yaml`).

> OPEN QUESTION for treeform: confirm the **tournament** cluster runs External
> Secrets Operator with the `aws-secretsmanager` `ClusterSecretStore` and that its
> IAM can read `vault/softmax/disco/api-token`. The app-manifests list deploys the
> `external-secrets` app only to the main cluster; the tournament cluster only gets
> the `tournament` chart. If ESO is present on tournament, use **1a**; otherwise use
> **1b**. (Consider whether a commissioner-scoped, least-privilege token in SM is
> preferable to reusing the disco token, now that the GET is authenticated-read.)

### 1a — Preferred: ExternalSecret (GitOps, auto-rotating)

Add this template to the `tournament` chart (`devops/charts/tournament/templates/
externalsecret-commissioner.yaml`) so ArgoCD manages it on the tournament cluster:

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: coworld-commissioner-secrets
  namespace: jobs
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secretsmanager
    kind: ClusterSecretStore
  target:
    name: coworld-commissioner-secrets
    creationPolicy: Owner
    deletionPolicy: Retain
  data:
    - secretKey: SOFTMAX_API_TOKEN
      remoteRef:
        key: vault/softmax/disco/api-token
```

Then let ArgoCD sync the `tournament` app (or `argocd app sync tournament`).

### 1b — Fallback: create the Secret directly (no ESO on tournament)

Run against the tournament cluster's kube context (NEVER commit the token value):

```sh
# Pull the token from AWS Secrets Manager into a shell var (not echoed to history if
# you prefix with a space), then create the k8s Secret. Run with tournament-cluster
# kube context selected.
TOKEN="$(aws secretsmanager get-secret-value \
  --secret-id vault/softmax/disco/api-token \
  --query SecretString --output text)"
kubectl -n jobs create secret generic coworld-commissioner-secrets \
  --from-literal=SOFTMAX_API_TOKEN="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
unset TOKEN
```

## STEP 2 — Redeploy the commissioner with the #77 image + the secret wiring

CONFIRMED: the currently-deployed canonical commissioner image
(`img_98af83c6-627a-4806-9e3e-d90e33a5b0cc`, coworld `crewrift_prime` v0.4.9) was
built **2026-06-26 05:46 UTC**, ~10h BEFORE the #77 fix was committed
(`43e2d7f`, 2026-06-26 16:18 UTC). So the deployed image does **NOT** contain the
league-id-prefix fix — a fresh image build + redeploy is required, not just a
config patch.

The new image must be built from coworld-crewrift at/after commit `43e2d7f` (the
deploy branch `aaln/crewrift-prime-qualification-deploy` HEAD already includes it).
Build + push per the commissioner README "Build / wire" section (Docker build from
the repo ROOT so the Nim expander stage can reach the game source; requires
Observatory registry access), then patch the canonical coworld AND attach the
secret_env in one command (requires the new metta `--secret-env` flag from
Metta-AI/metta#16818, and a team `usr_` token — clear any active player session
first):

```sh
cd metta/packages/coworld
# Use the team token (clear any active ply_ player session so get-token returns usr_):
uv run python -c "from softmax.auth import clear_active_player_session; \
  clear_active_player_session(server='https://softmax.com/api')"

uv run coworld patch-commissioner crewrift_prime <image>:<tag-with-#77> \
  --runnable-id among-them-commissioner \
  --secret-env SOFTMAX_API_TOKEN=SOFTMAX_API_TOKEN
```

`<tag-with-#77>` must be a commissioner image built from coworld-crewrift at or
after commit `43e2d7f`. The patch bumps the canonical coworld version; the league
adopts it on its next 10-minute scheduling tick. No re-seed needed.

## STEP 3 — Verify live

1. Wait for / trigger a Crewrift Prime Competition round with fewer than 8 real
   entrants. Find its commissioner log stream in CloudWatch group
   `/coworld/commissioner`, stream `round/<round_id>` (see metta
   `container_lifecycle.commissioner_logs_key`). Confirm **no** `XpRequestAuthError`
   / HTTP 422 around the filler-policies fetch.
2. Confirm an episode in that round has `crewborg-aaln:v16`
   (`policy_version_id 4e65356f-9a2c-4e6b-af27-bdb43515547b`) in a filler seat,
   flagged `is_filler`, excluded from scoring/standings. You can inspect the round's
   episodes via the Observatory episode-requests API with a team token.

## SECURITY REMINDER

The `usr_` team+admin token used to diagnose/deploy was exposed in chat — **rotate
it now**. No token value is committed in either PR, this doc, the manifest, or any
test fixture; `secret_env` carries only the Secret **key name** (`SOFTMAX_API_TOKEN`),
never the value.
