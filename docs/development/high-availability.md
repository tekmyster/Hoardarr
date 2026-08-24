# Two-node high-availability maturity

Hoardarr keeps storage-path redundancy, controlled node handoff, and automatic high availability
as separate guarantees. A successful multipath failover does not imply node fencing or automatic
service ownership.

The current persistent product surface is HA-3 peer awareness. An administrator configures exact
local and peer node IDs, names, FQDNs, IP addresses, desired active/passive roles, and an optional
future service IP. The authenticated peer heartbeat endpoint accepts observations only when the
node ID, FQDN, and IP match the configured peer. Local and peer IDs cannot be the same, addresses
cannot overlap, and exactly one configured role must be active.

`GET /api/v1/ha` reports persisted identity, current observed owner, peer reachability, last
heartbeat, synchronization state, failover readiness, storage-ownership state, and bounded durable
history. A peer heartbeat becomes stale after 45 seconds. Stale or absent data is reported as
unavailable/unknown, never healthy. `PUT /api/v1/ha/configuration` requires administrator scope;
`POST /api/v1/ha/heartbeat` requires operate scope.

The Settings page exposes the real stored configuration and the same status. It explicitly states
that automatic failover and fencing are not configured. The service IP is recorded for HA-5
planning but is not bound to an interface by HA-3. No ownership transition is initiated by a
heartbeat. HA-4 through HA-7 remain separate roadmap work with replication, endpoint transition,
automatic failure detection, and fencing requirements.
