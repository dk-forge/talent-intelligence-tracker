# Tracker launch code review, 23 November 2026

Scope: dk-forge/todo#1881 and the visible tracked free-match CTA requirement.
This is a repository review, not a new live-performance measurement.

| Item | Code status |
| --- | --- |
| Contextual CTA | Existing company and facet helpers retained. Standalone market-trend embed now has a free-match CTA too. |
| UTM scheme | Existing header and body attribution retained; new CTA uses the same helper and a separate campaign. |
| Phone first screen | Existing phone CTA and cookie compacting retained. New standalone CTA appears in page flow on phone and desktop. |
| Cache and filter bursts | Existing cold-compute cap, single-flight, coalescing and superseded-request cancellation retained. |
| Cross-links | Existing reviewed slug maps retained. No company pairing or data judgment added. |
| Layout shifts and loading | Existing early template-part rendering retained. No new live CLS or LCP claim. |
| Map dependencies | ALT has pinned self-hosted assets and a fallback table. No floating dependency introduced here. |
| Console and date label | Existing interactivity ordering and Last 90 days label retained. Wordfence remains an operational setting. |
| Accessibility | Existing role and target fixes retained. New CTA is named and has a 44px minimum target. |
| Key numbers and press | Existing dated figures and press surface retained. No number or aggregate added. |

The earlier code work is in PRs 362, 364, 365, 366, 367. The focused regression coverage passes:
44 targeted tests. The new standalone CTA regression failed before the fix. Full-suite
results and environment limitations are recorded in the PR; focused results
must not be read as a full-suite or live-site pass.

Manual follow-ups stay separate: Wordfence loopback/PHP investigation is
tracked in todo#2728 and the empty Complianz link setting in todo#2725.
Host cache configuration is an operator task. No settings, production data,
DNS, mail, deployment or model call is changed. Version 1.122.4 is prepared
for a later release only.
