# WO-WEB-013-H8L result

## Result

`REMOTE_TRANSPORT_STOP`

The exact H8K-certified mapper was executed once through one approved authentication attempt, one pinned SSH session, and one noninteractive read-only command. The command returned status `0` with 3,031 stdout bytes and 0 stderr bytes; the session closed cleanly. The returned object passed UTF-8/JSON, top-level identity, source, classification, fixed-stage count/order, stage-schema, and bounded-value validation, then the H8L controller rejected it at `remote-map-mutation-counters`. No remote map was retained, no retry or reconnect occurred, and no live state was mutated.

Allowed-operation counters are exact: authentication attempts/sessions/remote calls `1/1/1`; privilege stdin writes `1`; retries/reconnects/second commands `0/0/0`; remote writes/uploads/SFTP/temp files `0/0/0/0`. Every certified mutation counter is zero. No raw stderr was retained, and the transient stdout was not persisted separately.

## Evidence

The order was verified as 7,175 bytes, SHA-256 `EF05315D235D22E60F1252CB6025780B47E430917B3B4CA031F6393DB52FCB00`. Local and origin HEAD at preflight were both `f251ba3a6e6283d70c7ac33391110a6e48da3536`; accepted H8K commit `5bf74999e7b4a248c97afafeb745e421b7c850ff` is an ancestor. Seventy-five inherited dirty/untracked paths were recorded and preserved.

All pre-authentication gates passed without rerunning tests or altering the certified candidate:

- H8K handoff: 5,953 bytes / SHA-256 `7DED11664D8F166D3EF9C1B3D6370CA4968BCB2C773091A060F73B8DE757C7FA`.
- H8K evidence: 21 files / 1,499,090 bytes / tree `21B9E4CF80EE184780D8951E33CFEEED2437296488E81EC88001A86B07A92B12`.
- Candidate: 4 files / 34,743 bytes / tree `DA82D82EA9CAA5F2D0FF68A665C2607A069893658370E49EEE4C58F5CFF5CE01`.
- Mapper: 17,009 bytes / SHA-256 `F3C4567CD4C036CBFB2CB2562CBBF9F867C55583157684B5015E08E72465AFBE`.
- Transport: 929 bytes / SHA-256 `DE76F53778C93C4A879DA5E32AC87CA5EB2C573C1EE3F051B6B0AF3D9F028383`.
- Encoded payload: 22,680 bytes / SHA-256 `7E5DD2E85DC2BC1A9A419BE21F70F5999948B1116347253AC6A9EB78A760F5BE`.
- Exact command: 22,872 bytes / SHA-256 `8C569E54348FCE618E857C6CA915CCD107B448F90269CF518295A6105D99430E`.
- Both accepted H8K receipt sets remained byte-identical: certification `97E202B04A2593FCADA3C791DE8296FBAD653BC766849F914C0795E71818D70C`, mapper child `60240DC27AE891B39CB251BAB6AF8F01A90049C08CB95AA4CA53C6D7C4C1AB73`, G2 child `9B52CE70187070216A995E332969A01175C2782728A5BB1A4F074B8C3D77182B`; their accepted results remain mapper `24/24`, G2 `28/28`, retries/violations/live/mutation calls zero.
- Approved `newnginxhost` four-field profile schema and accepted pinned host-key SHA-256 matched without persisting or hashing secret values.

Protected evidence root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8L\WO-WEB-013-H8L-20260827T025330Z`. ACL inheritance is disabled; explicit FullControl is limited to `NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators`, and `DESKTOP-6U8VLDH\dmessana`. The retained self-manifested evidence is 4 files / 4,854 bytes / tree SHA-256 `EDF0BA08807F47B520790114B06E23639502033AEC546868C0880BC0998FA281`. `evidence-manifest.json` is 1,147 bytes / SHA-256 `DCCBBCC767E239DF36E8AEB59E255587172197A9C6F7B8BD9DA86F535524B47B`. The secret scan compared four approved profile values in memory across three retained evidence files and found zero secret or private-marker matches; no value or value hash was retained.

The call receipt is 1,065 bytes / SHA-256 `94881B28C06F74CA51152F778ED70890B1C3788EAB641754FDCE178C1C238E8E`. It records the 65,536-byte cap, 180-second ceiling, status/counts, matching host pin, closed session, zero mutation counters, and the fixed stop code. It contains neither raw stdout nor raw stderr.

No per-stage current-state value can be accepted from the discarded transient object. Each certified stage reached and passed the bounded stage-schema gate before the later counter-order stop, but its value was intentionally not retained:

| Stage | H8L accepted state |
|---|---|
| `active_stream` | Not established; transient value discarded at fixed stop |
| `active_dev` | Not established; transient value discarded at fixed stop |
| `active_production` | Not established; transient value discarded at fixed stop |
| `root_staging` | Not established; transient value discarded at fixed stop |
| `verify_root` | Not established; transient value discarded at fixed stop |
| `remote_offhost_root` | Not established; transient value discarded at fixed stop |
| `backup_child` | Not established; transient value discarded at fixed stop |
| `backup_archive` | Not established; transient value discarded at fixed stop |
| `predecessor_backup` | Not established; transient value discarded at fixed stop |
| `user_uploads` | Not established; transient value discarded at fixed stop |
| `private_lineage` | Not established; transient value discarded at fixed stop |
| `listeners` | Not established; transient value discarded at fixed stop |
| `service_identity` | Not established; transient value discarded at fixed stop |
| `production_tls` | Not established; transient value discarded at fixed stop |
| `production_http` | Not established; transient value discarded at fixed stop |
| `public_dns` | Not established; transient value discarded at fixed stop |
| `ddns_records` | Not established; transient value discarded at fixed stop |
| `ddns_script` | Not established; transient value discarded at fixed stop |
| `ddns_timer` | Not established; transient value discarded at fixed stop |

Accordingly, H8L does not re-establish production-content/TLS or public-dev/challenge-DNS invariants. It establishes only that the certified read-only command completed with status zero, its identity/stage schema passed through the mutation-counter gate, the host pin matched, and all prohibited/mutation counters remained zero.

## Defects

The H8L controller imposed an extra order-sensitive check on `mutation_counters`: it compared decoded JSON key iteration order with the mapper's source insertion order. The certified mapper emits canonical JSON using `sort_keys=True`, so the same complete zero-valued counter set is decoded in alphabetical order and was rejected. The work order required every counter to exist and equal zero, not a particular key order. This local validation defect caused the fixed stop after a successful read-only command.

The defect was not corrected under H8L because the single session/call budget was consumed and retry, reconnect, candidate change, or reinterpretation was prohibited. No raw output exists from which to reconstruct or rebaseline the map.

## Blockers

The live read-only map is not accepted. Production and public-DNS invariants remain unproven in this result, and H8L grants no second call. Trusted `dev.hoardarr.com` TLS, renewal, all 56 browser states, and physical-iPhone confirmation remain incomplete.

There is no access or credential blocker. The sole blocker is the locally introduced order-sensitive mutation-counter validator combined with the exhausted one-call ceiling.

## Next action

Supervisor QA should verify the exact zero-mutation stop and evidence identities. A separately authorized local-only successor should minimally certify a set-based/exact-key-and-zero mutation-counter validator against canonical sorted JSON while preserving every H8K mapper/transport byte and adversarial gate. Any later live mapping attempt requires a new explicit one-session/one-call authority; this handoff authorizes no retry, cleanup, backup, certificate, DNS/config change, reload/restart, browser work, or promotion.
