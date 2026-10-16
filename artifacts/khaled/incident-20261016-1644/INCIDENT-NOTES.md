# Group 13 flag-capture investigation - 16 October 2026

Status: capture reported by Nicolas; exploitation mechanism NOT established.

## Collection
Read-only SSH inspection of softsec-group-13. Current server container logs and Loki server-service logs were exported locally. Query window: 2026-10-15 14:00:00 UTC through 2026-10-16 14:44:00 UTC (16:00 through 16:44 Stockholm on the respective dates). Loki selector: {compose_service="server"}, forward direction, limit 5000. Returned 513 entries, below the requested limit; this does not guarantee ingestion completeness or coverage of other services. Export occurred shortly before the window's upper bound.

Files are UTF-8 text exports, not byte-for-byte copies of Docker storage. SHA256SUMS.txt records local file integrity after collection. No credentials, environment contents, flag values, or database records were collected.

Current server container created: 2026-10-16 10:30:22 UTC; started 10:30:23 UTC. Its direct log export contains 29 lines and cannot cover preceding containers. Loki retains earlier entries.
Server checkout HEAD: ca22c7d606939c881a05fa039dde3f27f71b6dd6. Checkout status clean when inspected. This does not independently prove the container image contains that exact revision.

## Observed timeline (Europe/Stockholm, UTC+02:00)
- 15 October 22:40:37-38: 10.100.11.19 created an account and logged in; 200 get-document ID probes returned 404. 200 list-versions ID probes returned HTTP 200 with a constant 16-byte response. Response contents were not logged, so this does not prove disclosure. Traversal, /.env and /.git/config probes returned 404.
- 16 October 08:42:11: 10.100.11.11 logged in, listed documents (200), and requested document 69 (200). Ownership, body contents and relation to capture are unknown.
- 09:39:00: 10.100.11.11 created an account and logged in; 15 create-watermark ID probes returned 404.
- 10:11:45: forged-token-labelled list-documents probes returned 401. User-agent labels and source addresses do not establish actor identity; traffic may include authorized testing.
- 12:05:56-12:06:10: 10.100.11.2 completed RMAP initiate/get-link and a version GET with 200. No request or response content is available to attribute a leak.
- 12:20 and 12:30: application termination/startup and flag initialization messages. These do not prove flag rotation. No restart was performed during this investigation.
- 16:07:22: Nicolas's capture notification (from previously read email).
- 16:29-16:33: RMAP requests from 10.100.11.31 occurred AFTER the notification and therefore cannot alone explain the already reported capture.

## Limits and next investigation
No literal flag-exfiltration request, load-plugin call, or HTTP 500 was identified in this bounded server log export. This is not proof those paths were never exploited. Logs omit request bodies, tokens, response contents and account IDs, and do not cover database/SSH activity. Container log byte counts of zero are not proof empty files were returned.

Next: map flag_1 storage and read paths in the actual running application, correlate document 69 and RMAP access with ownership/authorization, and inspect relevant database and host audit evidence without exposing secrets. Confirm with team which probes were their own tests. A precise exploit/actor cannot be concluded from these logs alone.

## Changes
Only local evidence files were created. No flag rotation, service restart, deployment, database modification, commit, push, upload, or messages sent. Current local branch: feat/khaled-fuzzing.

## Follow-up: runtime flag access review
Read-only inspection performed 16 October 2026 after the initial collection.

- Running /app/src/server.py SHA-256: 0bd178652547833dbd51283eccf9fd250a5cb1628c908b3dc06c05b0b0ebc2a6. Matches server checkout source exactly. A UTF-8 local source export is saved as running-server.py; its local hash may differ due to newline serialization.
- FLAG_1 initializes /app/flag, outside configured storage root /app/storage. Permissions 0664, root:root. Container executes as root. These increase impact if a separate file-read or execution vulnerability exists; neither proves the capture route.
- get-document requires a signed token and constrains lookup by numeric owner ID. get-version intentionally uses the supplied link and resolves stored file paths under storage. Symlinks are resolved before containment checking.
- RMAP uses configured document 10 and creates a watermarked version under storage after protocol completion. Its library version is 2.0.0. HTTP 200 protocol responses alone do not establish a leak.
- load-plugin is authenticated and returns 410, without deserializing files.
- SELECT-only inspection: 40 document records and 79 version records; no stored path resolved outside storage. Document 69 belongs to numeric owner 61, size 1776, has no version records, and is not RMAP document 10. Request logs omit account IDs, so the successful download cannot be verified as authorized or unauthorized from these records alone. Current database state is not historical evidence of the state at capture time.
- MariaDB general query logging and binary logging are OFF; these sources cannot reconstruct the past SQL activity. No logging settings were changed.
- Runtime packages: rmap 2.0.0, PyMuPDF 1.28.2, Flask 3.1.3, Werkzeug 3.1.9. No vulnerability-free claim is made.
- Current FLAG_1 was compared internally against tracked HEAD and 332 available Git commits; no match. Its value was neither printed nor saved. This does not rule out a previous flag leak, external copies, untracked files, other refs or history absent from this checkout.
- The bounded journalctl SSH query returned no entries; this is not proof there were no SSH logins and may reflect retention/access/service logging differences.

Conclusion: no confirmed route to FLAG_1 was established. The present source and database checks do not substantiate attributing the capture to document 69, RMAP, disabled plugins, or the malformed-PDF crash. Do not report any of these as the root cause.

Next evidence required: confirm whether FLAG_1 was already rotated, establish capture time from the course capture record, identify the application version running then, and correlate any retained host/monitoring audit evidence for that interval. The capture report's receipt time is not the exploit timestamp.

All server operations in this follow-up were read-only. Local evidence remains uncommitted on feat/khaled-fuzzing; nothing pushed, uploaded, messaged, merged, deployed, or rotated.
