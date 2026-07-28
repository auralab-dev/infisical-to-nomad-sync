# infisical-to-nomad-sync

Copy Infisical secrets into a [Nomad Variable](https://developer.hashicorp.com/nomad/docs/concepts/variables) - one
shot, then exit. Auths to both services with the same Nomad workload identity JWT.

## Env vars

| Variable                               | Required | Default value                             |
|----------------------------------------|----------|-------------------------------------------|
| `NOMAD_TOKEN`                          | yes      | - (injected by `identity { env = true }`) |
| `NOMAD_ADDR`                           | yes      | -                                         |
| `NOMAD_VAR_PATH`                       | yes      | -                                         |
| `INFISICAL_IDENTITY_ID`                | yes      | -                                         |
| `INFISICAL_PROJECT_SLUG`               | one of*  | -                                         |
| `INFISICAL_PROJECT_ID`                 | one of*  | -                                         |
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
| `SYNC_MODE`                            | no       | `leave-orphans`                           |
| `HTTP_TIMEOUT_SECONDS`                 | no       | `15`                                      |
| `LOG_LEVEL`                            | no       | `INFO`                                    |

\* Configure a project slug (preferred) or project ID. If both are set, `INFISICAL_PROJECT_SLUG` takes precedence.

### Sync modes

- `leave-orphans` (default) updates keys from Infisical while preserving keys that exist only in the configured Nomad
  Variable.
- `full` makes the configured Nomad Variable exactly match the selected Infisical secrets. **It removes every key from
  the Nomad Variable at `NOMAD_VAR_PATH` in `NOMAD_NAMESPACE` which is not present in infisical.**

### Process exit codes

| Code | Meaning            |
|------|--------------------|
| `0`  | Success            |
| `1`  | Unexpected failure |
| `2`  | Config error       |
| `3`  | Infisical error    |
| `4`  | Unsafe secret data |
| `5`  | Nomad error        |

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

## Example Nomad sync job

```hcl
job "infisical-sync" {
  namespace = "apps"
  type      = "batch"

  periodic {
    crons = [
      "0 */5 * * * *"
    ]
    time_zone        = "UTC"
    prohibit_overlap = true
  }

  group "sync" {
    task "sync" {
      driver = "docker"

      config {
        image = "ghcr.io/auralab-dev/infisical-to-nomad-sync:1.2.0"
      }

      identity { env = true }

      env {
        NOMAD_ADDR      = "http://<nomad-address>:4646"
        NOMAD_NAMESPACE = "apps"
        NOMAD_VAR_PATH  = "apps/secrets"

        INFISICAL_URL          = "https://app.infisical.com"
        INFISICAL_IDENTITY_ID  = "<identity-id>"
        INFISICAL_PROJECT_SLUG = "<project-slug>"
        INFISICAL_ENVIRONMENT  = "prod"
        INFISICAL_SECRET_PATH  = "/"

        INFISICAL_METADATA_FILTER = "key=branch,value=main"
      }

      resources {
        cpu    = 100
        memory = 64
      }
    }
  }
}
```

## Example ACL policy

Apply this workload-associated policy so the task's JWT gets access to its exact variable path:

`nomad-sync.policy.hcl`:

```hcl
namespace "apps" {
  variables {
    path "apps/secrets" {
      capabilities = ["read", "write"]
    }
  }
}
```

```sh
nomad acl policy apply \
  -namespace "apps" \
  -job "infisical-sync" \
  infisical-sync-apps \
  ./nomad-sync.policy.hcl
```

The task needs no long-lived ACL token - its workload JWT automatically gets the policy above.

## Example app

```hcl
job "alpine" {
  namespace = "apps"
  type      = "service"

  group "app" {
    task "app" {
      driver = "docker"

      identity {
        env = true
      }

      config {
        image   = "alpine:3.22"
        command = "/usr/bin/tail"
        args = [
          "-f", "/dev/null",
        ]
      }

      template {
        destination = "/local/.env"
        env         = true
        change_mode = "restart"

        data = <<EOH
{{ with nomadVar "apps/secrets@apps" }}
{{ range $key, $value := . }}
{{ $key }}={{ $value | toJSON }}
{{ end }}
{{ end }}
EOH
      }
    }
  }
}
```

Example app policy `apps-read-secrets.hcl`

```hcl
namespace "apps" {
  variables {
    path "apps/secrets" {
      capabilities = ["read"]
    }
  }
}
```

Attach policy to workload

```bash
nomad acl policy apply \
  -namespace "apps" \
  -job "alpine" \
  apps-read-secrets \
  ./apps-read-secrets.hcl
```

## Development

### Testing

```sh
./integration/run.sh
```

Spins up a disposable stack (Postgres, Redis, Infisical, Nomad), bootstraps fixtures, runs E2E tests, and tears down:

| Test               | What it checks                                              |
|--------------------|-------------------------------------------------------------|
| Full sync          | Removes and logs Nomad-only secrets                         |
| Leave-orphans sync | Preserves/logs orphans and updates Infisical-managed values |
| Unauthorized job   | Infisical rejects wrong job claim (exit code 3)             |

Pin versions in CI:

```sh
INFISICAL_IMAGE=infisical/infisical:<tag> NOMAD_VERSION=2.0.4 ./integration/run.sh
```

If interrupted, tear down manually:

```sh
docker compose --file integration/docker-compose.yml down --volumes --remove-orphans
```
