# WO-WEB-013-H8M result

## Result

`LOCAL_CERTIFIED_TWO_PASS`

The H8L returned-object validator defect is corrected and locally certified using fake fixtures only. The mutation-counter object is now validated by exact key-set equality and exact integer-zero values, independent of member order. The exact mapper-canonical alphabetical order and the mapper source insertion order both pass. The fixed 19-stage order and every other schema, identity, source, classification, bounds, framing, and duplicate-key gate remain strict.

The two designated clean roots each passed validator `55/55`, unchanged H8K mapper regression `24/24`, and unchanged G2 regression `28/28`. Their four receipt files are byte-identical. Authentication/session/profile/network/live/remote/write/mutation/retry counters are all zero. No H8L raw output was reconstructed or inferred.

## Evidence

The order was verified as 5,172 bytes, SHA-256 `CF9C90A37048501DDC9222EAC6AACE29B0520AF5BA5457736AA0FD85D19F89DD`. Locked inputs reproduced exactly:

- H8K handoff commit `5bf74999e7b4a248c97afafeb745e421b7c850ff`; handoff 5,953 bytes / `7DED11664D8F166D3EF9C1B3D6370CA4968BCB2C773091A060F73B8DE757C7FA`.
- H8K mapper 17,009 bytes / `F3C4567CD4C036CBFB2CB2562CBBF9F867C55583157684B5015E08E72465AFBE`.
- H8K transport 929 bytes / `DE76F53778C93C4A879DA5E32AC87CA5EB2C573C1EE3F051B6B0AF3D9F028383`.
- H8L handoff commit `977f0fe246e67919ef922aa2c42e625a07566b76`; handoff 7,656 bytes / `A120DBF0F9E63B7382AE5A8A6649102CDEFDE0AA94D3AD8BBF3ADD02E1699BC5`.
- H8L evidence manifest 1,147 bytes / `DCCBBCC767E239DF36E8AEB59E255587172197A9C6F7B8BD9DA86F535524B47B`; call receipt 1,065 bytes / `94881B28C06F74CA51152F778ED70890B1C3788EAB641754FDCE178C1C238E8E`.

Protected root: `C:\Users\dmessana\Desktop\all servers\Hoardarr-website-evidence\WO-WEB-013-H8M\WO-WEB-013-H8M-20260827T030444Z`. ACL inheritance is disabled. Explicit FullControl is limited to `NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators`, and `DESKTOP-6U8VLDH\dmessana`. All six frozen candidate files are read-only; no `__pycache__` exists.

Candidate identity: 6 files / 35,339 bytes / tree SHA-256 `B9DF691CC52654A36D43B2F0C232AD06B4889EDF147FB9FB0AC79BC276BC6C69`:

| File | Bytes | SHA-256 | Implementation delta |
|---|---:|---|---|
| `remote_mapper.py` | 17,009 | `F3C4567CD4C036CBFB2CB2562CBBF9F867C55583157684B5015E08E72465AFBE` | Exact H8K bytes; unchanged |
| `transport.py` | 929 | `DE76F53778C93C4A879DA5E32AC87CA5EB2C573C1EE3F051B6B0AF3D9F028383` | Exact H8K bytes; unchanged |
| `validator.py` | 4,250 | `C6BE4B913A3BA1A406B958CB09CE43A56B20B320E9C156CF8CCCAE2C854C5B19` | New isolated return-map validator; counter members use exact unordered set plus `type(value) is int and value == 0` |
| `fixtures.py` | 1,431 | `3257A3E59B60A42E3F0FA6C5E998FE0E05C8678BE2F6229AD744F4F1C4172E67` | New deterministic fake fixture emitted through exact H8K mapper |
| `tests.py` | 5,494 | `8671B12BC75B23124B24322E89DC7D6C734709604A96296664E17A1BA83B61B7` | New 55-case pass/adversarial matrix |
| `certify.py` | 6,226 | `74FC8C6D6E34D54006E981E851FA899361D1643CF1F279A449F36BEDE86646C4` | New deterministic static/child/regression certifier |

The complete validator matrix is:

| Matrix group | Cases | Result |
|---|---:|---|
| Exact mapper canonical, canonical copy, source insertion counter order | 3 | 3 passed |
| One missing mutation-counter key | 17 | 17 rejected with fixed counter-key stop |
| Extra key and wrong counter container | 2 | 2 rejected |
| Nonzero, negative, string, float, boolean, null counter values | 6 | 6 rejected |
| Duplicate top-level, counter, stage, and nested keys | 4 | 4 rejected recursively |
| Stage reorder, missing, extra, duplicate, wrong name/class, READ_ERROR schema, string/depth bounds | 9 | 9 rejected |
| Wrong schema, hostname, source, classification, missing/extra top key | 6 | 6 rejected |
| Invalid UTF-8/JSON, empty, oversized, trailing data, extra LF, CRLF, pretty whitespace | 8 | 8 rejected |
| **Total** | **55** | **55 passed** |

Every rejection produced one bounded fixed stop code, returned no partially validated map or map hash, and invoked no writer or live transport. Duplicate members are rejected at every JSON object depth through the decode hook. Exact framing remains one minified UTF-8 JSON object with one terminal LF under the 65,536-byte cap; arbitrary whitespace, CRLF, multiple LFs, and trailing data remain invalid.

Clean pass 1 and clean pass 2 produced identical receipts:

- `certification.json`: 2,607 bytes / `7AF9BF7FD4E824BFA6631125FC6F4496731438956ABE1E60261F7507B4FED778`.
- `child-validator-tests.json`: 557 bytes / `A261BCC6EAE12AE99CF486041BE4DB96E6B2FFF35864660C30932BEFD03AD7EE`.
- `child-h8k-tests.json`: 295 bytes / `6457CB6A1D4854654A72E112F19083D3EC4AD1968309B8820A818CF32A7A478A`.
- `child-g2-tests.json`: 318 bytes / `E5EE83D32156E487733C6D716F49DF03A88B91F6D03B14F0C265599572FDC409`.

The first non-designated certification attempt correctly failed locally: 39 validator cases stopped because the fake fixture had not injected the required `NGINX01` hostname. Only `fixtures.py` was corrected to inject that deterministic fake hostname; the complete matrix then passed before the two fresh designated roots were created. The failed attempt is retained as transparent non-certifying evidence.

The protected evidence set is 23 self-excluding files / 51,346 bytes / tree SHA-256 `27560FC7919449189B080F8C0A2128A4AEDA0844BA328A8690541FC4327971B0`. `evidence-manifest.json` is 4,248 bytes / SHA-256 `FADC6D929DF4E0EEAB5E8F611ABC471BFE7B1947050AFC40BE6824470FDF6EB7`. Static compile/AST/mutation/no-live checks passed. A marker-only scan covered the protected files without reading any profile or secret value and found zero private/credential markers.

## Defects

No defect remains in the bounded H8M local validator certification. The semantic correction is limited to mutation-counter member order; exact key membership and integer-zero values remain mandatory. H8K mapper and transport bytes are unchanged.

This result does not validate or recover the discarded H8L live map and makes no current production, DNS, certificate, listener, backup, or service-state claim.

## Blockers

There is no blocker to Supervisor review of the local candidate. H8M grants no authentication, profile, network, SSH, live read, remote write, cleanup, backup, certificate, DNS/configuration, reload, browser, or promotion authority. Trusted dev TLS, renewal, the 56 browser states, and physical-iPhone confirmation remain incomplete.

Commit and push are temporarily held by Supervisor coordination to avoid cancelling the active Storage A4 lifecycle workflow. The handoff is prepared but will not be staged, committed, or pushed until that hold is explicitly released.

## Next action

Supervisor QA should independently reproduce the protected candidate tree, unchanged H8K identities, both byte-identical receipt sets, and the 55/55 + 24/24 + 28/28 results. After the coordination hold is released, commit and push only this handoff. Any later one-call live map requires a separate explicit work order and fresh transaction budget; this local pass grants none.
