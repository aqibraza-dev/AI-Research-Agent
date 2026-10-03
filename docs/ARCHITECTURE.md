# Architecture and API

## Data flow

The browser uses Supabase only for authentication/session management. It sends the current access token as `Authorization: Bearer ...` to FastAPI. The backend validates it through Supabase Auth's `/auth/v1/user`; expired/revoked sessions are rejected. Backend authorization uses the protected profile row, not user-editable token metadata.

FastAPI owns data mutations. PostgreSQL is authoritative for profiles, reports, messages, schedules, durable jobs, quota reservations, and audit records. Browser-readable tables have owner-only RLS. Jobs, usage-event responses, credentials, administration, and internal counters are backend-only. Never connect the frontend using the database password or a Supabase service key.

One worker claims work with `FOR UPDATE SKIP LOCKED`, a lease token, a two-minute lease, and a heartbeat. It checkpoints completed pipeline stages and each model response. A restart resumes completed steps; a provider call left without a confirmed result is conservatively charged and not automatically replayed. A job can be claimed at most three times before an explicit user retry is required. Finishing a job and saving its output is atomic.

## Research and chat

Standard research uses one basic Tavily search and two model calls: draft and corrected final review. Deep research adds one search and one revision call. Sources include numbered IDs, titles, URLs, excerpts, and retrieval times. The renderer validates that citation numbers and generated URLs belong to retrieved evidence. This establishes provenance, not proof that every claim is true; review reports before relying on them.

Redis caches search evidence per user/topic for ten minutes. All scheduled executions, including Run now, bypass this cache. No report or conversation is shared across users.

Chat retains complete persisted history but sends only a bounded recent context (up to 16 messages / 12,000 characters), plus up to 7,000 characters of an attached owner-verified report. Context truncation reduces provider requests and memory use; older messages remain available in history/export. A conversation permits one active reply at a time. Retry applies to failed/cancelled replies and does not duplicate the user message.

## Token accounting

A transaction locks the user's profile and daily usage row. A conservative UTF-8-byte-based estimate plus output cap is reserved before each model request. The application may reject a request even when its eventual actual usage would have fit, because free models use different tokenizers. Actual provider prompt/completion counts replace the reservation when available. Missing usage or ambiguous timeout is recorded as estimated. Rejected provider requests release reservations. External custom-target calls are logged separately and do not consume platform tokens.

Usage is charged to the UTC day on which the call was reserved, even if it finishes after midnight. Deleting a report/conversation/job cannot erase the ledger. Operator reconciliation of estimated events is explicit and audited. Unknown costs are shown as unknown; the cost aggregate is labeled known model cost and does not include hosting bills.

## Scheduling semantics

Recurrences use an IANA timezone and wall-clock start time. Weekly weekdays are ISO 1=Monday through 7=Sunday. Monthly day 29–31 is clamped to month-end. Nonexistent DST times are skipped; repeated DST times run once using PostgreSQL's standard-time interpretation. Preview uses the same database function as execution.

New/edited/resumed schedules begin with their next future occurrence. During downtime, older missed occurrences become skipped and only the latest due occurrence runs. Uniqueness on schedule/time prevents duplicate jobs. Backlogs are bounded per transaction. Suspended users and disabled research/scheduling features do not enqueue work. Deleting a schedule preserves existing reports and jobs but removes that schedule's run-history rows.

## API map

Interactive OpenAPI documentation is available at backend `/docs`. List endpoints use `page` (one-based) with page sizes 20, or 50 for conversation messages. Dates use ISO 8601 with timezone offsets. Errors have shape `{"error":{"code":"...","message":"..."}}`.

| Resource | Operations |
| --- | --- |
| `/api/v1/profile` | GET current profile, allowance, charged/reserved tokens |
| `/api/v1/models` | GET read-only free catalog |
| `/api/v1/research` | POST topic/depth/model; GET filtered, paginated history |
| `/api/v1/research/{id}` | GET report/job; PATCH title; DELETE terminal report |
| `/api/v1/research/{id}/rerun` | POST new run |
| `/api/v1/research/{id}/diff?other=UUID` | GET owner-verified report diff |
| `/api/v1/jobs` and `/{id}` | GET history/status/progress/results |
| `/api/v1/jobs/{id}/cancel` | POST cooperative cancellation |
| `/api/v1/conversations` and `/{id}` | POST/GET conversations; GET/PATCH/DELETE single conversation |
| `/api/v1/conversations/{id}/messages` | POST message/model |
| `/api/v1/conversations/{id}/retry` | POST retry last failed/cancelled reply |
| `/api/v1/schedules` | GET/POST; PUT/DELETE `/{id}` |
| `/api/v1/schedules/preview` | POST proposed schedule; returns calculated next occurrence |
| `/api/v1/schedules/{id}/{action}` | POST pause, resume, duplicate, run-now |
| `/api/v1/schedules/{id}/runs` | GET occurrence history |
| `/api/v1/analytics` | GET period totals, daily series, breakdown, ledger; `days=1..366` |
| `/api/v1/notifications` and `/{id}/read` | GET inbox; POST mark read |
| `/api/v1/redteam` | POST platform/custom target test run |
| `/api/v1/exports/{kind}/{id}?format=pdf` | GET authenticated PDF/markdown; kinds research, conversation, message, redteam |
| `/api/v1/admin/*` | Overview, users, per-user analytics/limits/access, global settings, jobs, audit, health |
| `/api/v1/internal/wake` | POST secret-protected dispatch hint |

## Red-team boundary

Built-in suites are small deterministic test prompts. Multi-turn escalation shares prior responses within its suite. Results are refusal, potential_failure, inconclusive, or transport_error. This is not the full PyRIT framework or an automated guarantee of jailbreak detection.

Custom targets support only HTTPS OpenAI-compatible `/chat/completions`, bearer authentication, and public port 443. The connection's DNS resolver rejects private/local/link-local/mixed answers. IP literals are checked; redirects are disabled; TLS verification remains enabled. Each response is capped at 256 KB and 60 seconds. Credentials are Fernet-encrypted, expire after one hour, and are deleted when the job finishes/cancels or the next cleanup tick finds expiration. Ciphertext may remain while all services are down, but TTL validation prevents its use after expiration.

## Export behaviour

Markdown is canonical. PDF renders headings, paragraphs, lists, links and wrapped code with page numbers, using bundled DejaVu fonts in Docker. Common Latin/Greek/Cyrillic text is supported; CJK/emoji glyph coverage is not comprehensive. Images are represented by their alt text and remote images are never fetched. Very complex Markdown tables are retained as text rather than reproduced as a browser layout.
