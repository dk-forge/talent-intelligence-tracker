# Public origin endpoint limits

The talent/v1 public REST callbacks and public downloads share a budget of 120 requests per peer IP per 60 seconds. A signed-in
WordPress user also has 60 requests per 60 seconds across peer addresses. These
are policy defaults, not measurements of traffic. Paths, filters and User-Agent
changes do not create new quotas. Anonymous visitors are identified only by IP;
user IDs come from WordPress authentication, never request parameters.

The REST guard uses rest_dispatch_request after permission checks and before the
selected callback. Only the outer HTTP request is charged: internal cron/digest
REST dispatches and nested calls made by private routes do not spend public quota. Current public routes declare __return_true. Private keyed
callbacks and other plugins' namespaces retain their existing permission checks.
Tests audit permission declarations and every anonymous admin-post action so a
new entry point requires an explicit coverage decision. Signed confirmation and
unsubscribe flows remain unchanged; withdrawing consent is not put behind a new
public content quota. Existing tighter export and feed limits remain. Follow/signup forms on tracker
pages are owned by the sibling plugin and covered by its companion PR.

An exhausted shared quota returns 429 with Retry-After and Cache-Control no-store.
No service callback runs after refusal. Form error pages keep a visible, tagged
free-match CTA. Counter storage/lock failures return 503 with a retry hint rather
than silently allowing unlimited work.

## Storage and identity

Counters live in non-autoloaded wp_options rows named rl_tit_public_ plus a salted
HMAC. Values contain only expiry/count; IP addresses, user IDs, headers and bodies
are not stored or logged by this guard. A database named lock surrounds the direct
read/update so independent PHP workers share a quota. Normal response/object-cache
flushes cannot reset it. Existing MySQL/MariaDB GET_LOCK support is required; no
new database table, migration, dependency, key or service is introduced.

A WordPress hourly event removes expired counters; deactivation clears the event
and its remaining rows. This is separate from the browser/client bot read budget.
The guard does not cap private ingestion routes or change the client-side
exponential retries for authenticated WordPress reads.

REMOTE_ADDR is the trusted peer identity. Forwarded and client-supplied IP headers
are ignored. A deployment must verify that the host restores the real peer address
when behind its trusted proxy; this code does not configure Cloudflare or the host.
The guard bounds origin callbacks, not edge-cached responses, WordPress bootstrap
cost or all site traffic. No live load or production deployment is claimed.

## Validation and rollback

tests/test_public_rate_limits.py runs an offline WordPress-hook harness with a
real SQLite option store and a named-lock adapter. It tests concurrent processes,
IP/user rotation, all anonymous action hooks, expiry, cleanup, storage failure and
refusal before work. This proves the hook/storage contract, not a live-host load
test. CI also runs the repository suite. Revert the PR to roll back the guards;
deactivate before removing the module to clear scheduled cleanup and counter rows.

Apache cache overrides retain successful read caching. Late response-status
expressions remove the normal Cache-Control header and set no-store, private
in the always table for errors, including 429 and 503. Thus storage failures
cannot become shared-cacheable after PHP has returned its refusal.
