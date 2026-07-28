client {
  # Docker Desktop does not expose a usable CPU frequency to Nomad's
  # fingerprinter. This provides a small synthetic scheduling capacity for the
  # disposable test client.
  cpu_total_compute = 1000
}

plugin "raw_exec" {
  config {
    enabled = true
  }
}
