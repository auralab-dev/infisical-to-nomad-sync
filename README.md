# infisical-to-nomad-sync

Copy Infisical secrets into a [Nomad Variable](https://developer.hashicorp.com/nomad/docs/concepts/variables) - one
shot, then exit. Auths to both services with the same Nomad workload identity JWT.

## Env vars

| Variable                               | Required | Default value                             |
|----------------------------------------|----------|-------------------------------------------|
| `NOMAD_TOKEN`                          | yes      | - (injected by `identity { env = true }`) |
| `NOMAD_ADDR`                           | yes      | -                                         |
| `NOMAD_VAR_PATH`                       | yes      | -                                         |
| `NOMAD_VAR_PREFIX`                     | yes      | -                                         |
| `INFISICAL_IDENTITY_ID`                | yes      | -                                         |
| `INFISICAL_PROJECT_ID`                 | yes      | -                                         |
| `INFISICAL_ENVIRONMENT`                | yes      | -                                         |
| `INFISICAL_SECRET_PATH`                | yes      | -                                         |
| `NOMAD_NAMESPACE`                      | no       | `default`                                 |
| `INFISICAL_URL`                        | no       | `https://app.infisical.com`               |
| `INFISICAL_ORGANIZATION_SLUG`          | no       | unset                                     |
| `INFISICAL_TAG_FILTERS`                | no       | empty (comma-separated slugs)             |
| `INFISICAL_METADATA_FILTER`            | no       | unset                                     |
| `INFISICAL_RECURSIVE`                  | no       | `false`                                   |
| `INFISICAL_INCLUDE_IMPORTS`            | no       | `false`                                   |
| `INFISICAL_INCLUDE_PERSONAL_OVERRIDES` | no       | `false`                                   |
| `INFISICAL_EXPAND_REFERENCES`          | no       | `true`                                    |
| `HTTP_TIMEOUT_SECONDS`                 | no       | `15`                                      |
| `LOG_LEVEL`                            | no       | `INFO`                                    |

### Process exit codes

| Code | Meaning            |
|------|--------------------|
| `0`  | Success            |
| `1`  | Unexpected failure |
| `2`  | Config error       |
| `3`  | Infisical error    |
| `4`  | Unsafe secret data |
| `5`  | Nomad error        |

## Example Nomad job

```hcl
job "infisical-sync" {
  namespace = "apps"

  group "sync" {
    task "sync" {
      driver = "docker"

      config {
        image   = "python:3.14-slim"
        command = "python"
        args = ["/app/infisical_to_nomad_sync.py"]
      }

      identity { env = true }

      env {
        NOMAD_ADDR       = "https://nomad.example:4646"
        NOMAD_NAMESPACE  = "apps"
        NOMAD_VAR_PREFIX = "apps/secrets"
        NOMAD_VAR_PATH   = "apps/secrets/application"

        INFISICAL_URL         = "https://app.infisical.com"
        INFISICAL_IDENTITY_ID = "<identity-id>"
        INFISICAL_PROJECT_ID  = "<project-id>"
        INFISICAL_ENVIRONMENT = "prod"
        INFISICAL_SECRET_PATH = "/"

        INFISICAL_METADATA_FILTER = "key=branch,value=main"
      }

      resources { cpu = 100; memory = 64 }
    }
  }
}
```

## Example ACL policy

Apply this workload-associated policy so the task's JWT gets write access to its variable prefix:

```sh
nomad acl policy apply \
  -namespace "apps" \
  -job "infisical-sync" \
  -group "sync" \
  -task "sync" \
  infisical-sync-writer \
  ./nomad-sync.policy.hcl
```

`nomad-sync.policy.hcl`:

```hcl
namespace "apps" {
  variables {
    path "apps/secrets/*" {
      capabilities = ["write", "read"]   # read needed for write response
    }
  }
}
```

The task needs no long-lived ACL token - its workload JWT automatically gets the policy above.

## Infisical setup

Create a machine identity with **JWT Auth**

| Field                       | Value                                                                      | Required |
|-----------------------------|----------------------------------------------------------------------------|----------|
| JWKS                        | `https://<nomad>:4646/.well-known/jwks.json` - publicly available endpoint | yes      |
| Issuer                      | Value from `nomad.hcl -> server -> oidc_issuer`                            | yes      |
| Audience                    | -                                                                          | no       |
| Claims -> `nomad_namespace` | -                                                                          | no       |
| Claims -> `nomad_job_id`    | -                                                                          | no       |
| Claims -> `nomad_task`      | -                                                                          | no       |

## Development

### Testing

```sh
./integration/run.sh
```

Spins up a disposable stack (Postgres, Redis, Infisical, Nomad), bootstraps fixtures, runs two E2E tests, and tears
down:

| Test             | What it checks                                     |
|------------------|----------------------------------------------------|
| Authorized job   | JWT auth, metadata filtering, variable replacement |
| Unauthorized job | Infisical rejects wrong job claim (exit code 3)    |

Pin versions in CI:

```sh
INFISICAL_IMAGE=infisical/infisical:<tag> NOMAD_VERSION=2.0.4 ./integration/run.sh
```

If interrupted, tear down manually:

```sh
docker compose --file integration/docker-compose.yml down --volumes --remove-orphans
```
