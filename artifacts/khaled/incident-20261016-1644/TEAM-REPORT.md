# Group 13 - flag_1 incident
Khaled | 16 October 2026

Nicolas notified us at 16:07 that flag_1 had been captured. I checked the server logs, running code and relevant database records. I found suspicious requests, but I could not confirm how the flag was obtained or which group captured it. This report is a starting point for us to continue investigating.

## Event log
All times below are Stockholm time (UTC+2). The raw logs use UTC.

| Date / time | Source IP | What the logs show |
| --- | --- | --- |
| 15 Oct, 22:40:37-38 | 10.100.11.19 | Account creation and login, then 200 document-ID requests returning 404 and 200 version-list requests returning 200 with the same 16-byte response size. Attempts to access source files, .env and .git/config returned 404. |
| 16 Oct, 08:42:11 | 10.100.11.11 | Login, document listing and download of document 69 returned 200. The logs do not identify the account used. |
| 16 Oct, 09:39:00 | 10.100.11.11 | Account creation and login, followed by 15 watermark requests against different document IDs. Those watermark requests returned 404. |
| 16 Oct, 10:11:45 | 172.19.0.1 | Requests labelled tatou-forged-token returned 401. This is a Docker gateway address, so it does not identify the original source. |
| 16 Oct, 12:05:56-12:06:10 | 10.100.11.2 | RMAP handshake and version download returned 200. The downloaded version is recorded as intended for Group_01. |
| 16 Oct, 12:20 and 12:30 | Application | Shutdown/startup and flag-initialization messages. These do not confirm that the flag value changed. |
| 16 Oct, 16:07:22 | Nicolas's email | Notification that flag_1 was captured. The actual capture time was not given. |
| 16 Oct, 16:29-16:33 | 10.100.11.31 | Successful RMAP requests and a version download recorded as intended for Group_29. These happened after the notification. |

The RMAP identities identify PDF recipients, not the group responsible for capturing flag_1. Some requests may also be our own tests.

## What I checked
- flag_1 is stored at /app/flag, outside the document storage directory /app/storage.
- The current download code checks storage paths, and document downloads check the owner's numeric ID. Plugin loading is disabled.
- None of the 40 document paths or 79 version paths in the current database resolved outside storage.
- Document 69 belongs to user ID 61. The RMAP document is ID 10. I could not verify who made the document-69 request from the access logs.
- Running server.py matched the server checkout at ca22c7d. This confirms the current file, not the version running when the capture happened.
- The current flag value was not found in the 332 available Git commits. Previous flag values were not checked.

The logs do not include request bodies, response contents or account IDs. Database query logging was off. These checks do not establish the capture method or prove the current server is secure.

## What we should do next
1. Confirm that the new flag from Nicolas's email has been activated on the server. I have not verified rotation.
2. Ask Nicolas for the capture time and any available details, then identify the deployment running at that time.
3. Check which requests were our own tests. Review the successful document-69 download and earlier RMAP activity without assuming either caused the leak.
4. Record any confirmed cause, fix and verification in our journal. Disclose the incident in the course's existing incident thread without posting exploit details.

## Evidence
I saved 513 Loki server-log entries and 29 lines from the current container. The requested window was 15 October 14:00 UTC to 16 October 14:44 UTC; collection occurred shortly before the end of that window. This is the available export, not a guarantee that every event was logged.

Files in this folder:
- loki-server.json - Loki response.
- loki-server.log - entries sorted by timestamp.
- server.log - current container logs; this container started at 12:30 Stockholm time.
- running-server.py - source exported from the running container.
- INCIDENT-NOTES.md - detailed checks and limitations.
- SHA256SUMS.txt - hashes for the local evidence files.

I made no server changes during the investigation. This folder contains the preserved evidence for team review.
