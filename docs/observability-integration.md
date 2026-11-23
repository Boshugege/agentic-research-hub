# Observability integration

On `personal`, the existing Blackbox Exporter is attached to `research-net` in addition to its `observability` network. Prometheus probes the three internal service aliases with the existing `http_2xx` module. The production host configuration is intentionally kept outside this public repository because it contains the private observability topology.
