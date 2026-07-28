job "infisical-sync-e2e" {
  type        = "batch"
  datacenters = ["dc1"]

  group "sync" {
    restart {
      attempts = 0
      mode     = "fail"
    }

    reschedule {
      attempts = 0
    }

    task "sync" {
      driver = "raw_exec"

      config {
        command = "/usr/bin/python3"
        args = [
          "/workspace/infisical_to_nomad_sync.py",
        ]
      }

      identity {
        env = true
      }

      env {
        NOMAD_ADDR        = "http://nomad:4646"
        NOMAD_NAMESPACE   = "default"
        NOMAD_VAR_PREFIX  = "e2e/secrets"
        NOMAD_VAR_PATH    = "e2e/secrets/application"

        INFISICAL_URL         = "http://infisical:8080"
        INFISICAL_IDENTITY_ID = "__INFISICAL_IDENTITY_ID__"
        INFISICAL_PROJECT_ID  = "__INFISICAL_PROJECT_ID__"
        INFISICAL_ENVIRONMENT = "dev"
        INFISICAL_SECRET_PATH = "/apps"
        INFISICAL_METADATA_FILTER = "key=branch,value=main"

        INFISICAL_RECURSIVE         = "false"
        INFISICAL_INCLUDE_IMPORTS   = "false"
        INFISICAL_EXPAND_REFERENCES = "true"
        LOG_LEVEL                   = "INFO"
      }

      resources {
        cpu    = 100
        memory = 64
      }
    }
  }
}
