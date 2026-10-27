# B — API VIP takeover

[Phase 5](README.md) · [Previous: readiness](A-readiness.md) · [Next: MariaDB](C-mariadb.md)

**Simple idea:** `192.168.0.151` is the cloud's front-door address. Another node should take ownership when the current owner's API Keepalived stops.

**Status: test plan.** Exact stop/start commands and timed probes are prepared after A's report is reviewed. No service interruption is authorized by reading this page.

| Step | What we do | What we check |
|---|---|---|
| 1 | Identify the current VIP owner; check HTTPS/API access | One owner and working access |
| 2 | Prepare direct SSH restoration and ask approval | A way to restart Keepalived without using the VIP |
| 3 | Stop only API Keepalived on that owner | Another node acquires `.151` |
| 4 | Probe authenticated HTTPS APIs during takeover | Record failed probes, recovery time and test limits |
| 5 | Start the original container again | Required containers healthy, one VIP owner and working APIs |

The VIP does not need to return to its original node. This is an API Keepalived test, not a host power-off or a separate HAProxy-failure test. The VM, database and virtual router are not deliberate targets.

**Pass:** a different owner takes over, HTTPS/API access recovers, and the restored setup is healthy. Return the report before C.
