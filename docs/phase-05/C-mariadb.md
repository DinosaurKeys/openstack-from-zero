# C — One database member stops

[Phase 5](README.md) · [Previous: API VIP](B-api-vip.md) · [Next: virtual router](D-router.md)

**Simple idea:** three MariaDB members keep synchronized copies of the cloud database. We test whether the other two continue working when one stops.

**Status: test plan.** Exact commands, database/API probes and restoration checks follow fresh healthy reports and approval.

| Step | What we do | What we check |
|---|---|---|
| 1 | Check all three members | Synced, Primary, ready, size 3 and a shared cluster UUID |
| 2 | Select one member; prepare its restart command; ask approval | Direct management access works |
| 3 | Stop only that MariaDB container | The remaining two stay Primary and ready with size 2 |
| 4 | Run agreed database/API probes | Record whether the tested operations continue and any interruption |
| 5 | Start the stopped member; wait for synchronization | All three return to Synced, Primary and size 3 |

Do not stop a second member while the first is unavailable. No database deletion, cluster bootstrap or data reset is part of this exercise.

**Pass:** the surviving cluster serves the tested operations and the restored member rejoins correctly. Return the report before D.
