# Manual draft quality report

The existing Commerce Quality Engineer (`commerce-tests`, Atlas Marketplace Floor 1, Room 107) can review locally staged drafts with no model calls, publishing, network access, database writes, or customer messages. It reuses `pre_upload_validator.validate`; the normal publishing flow is preserved. Non-text titles and descriptions now fail validation instead of crashing it.

```sh
python3 scripts/product_quality_report.py --output /tmp/commerce-quality --state /tmp/commerce-quality-state.json
```

Run from a checkout containing actual `04_Assets/ReadyToUpload/<product>/listing.json` and staged JPEGs or `02_Products/<product>/Mockups/*.jpg`. The report only checks metadata and JPEG presence. It does not inspect image contents, copyright, links, sales, or runtime operations. It does not read the pipeline database or `.env`.

The checkout inspected for this change contains no staged drafts. One real manual local execution therefore reports **incomplete / drafts unavailable**, with zero model calls. The seven regression checks use temporary synthetic fixtures and are not evidence of live products. VPS access and its local drafts remain unverified; no access settings were changed.

## Atlas Output

The existing `tests.yml` workflow and Atlas's `commerce-tests` observation binding are retained. PR/push CI still runs the original tests. After merge, a manual **Tests** dispatch runs only the draft-report job and uploads its Markdown summary and JSON artifact for 14 days. Atlas's existing Output tab links that real run and its artifacts.

GitHub-hosted checkouts normally have no ignored production assets. Manual dispatch will honestly report incomplete unless approved draft inputs are already available there. No upload of private product files or VPS connection is introduced. For actual local drafts, use the local command above; it does not fabricate hosted telemetry. Do not dispatch or enable recurring execution without authorization.

## Controls

Set `COMMERCE_QUALITY_STOP=true` before the next invocation to produce a stopped report without inspecting drafts. Invalid values fail closed. Cancel an already-running GitHub job through GitHub if needed. No scheduled trigger is added.

The scan is bounded to 50 sampled products, 128 KiB per listing, 50 mockups per product, and a ten-second scan deadline checked between products; the hosted job has a two-minute deadline. Paths escaping the root, symlinks, unreadable and oversized inputs are not reviewed. Stop and incomplete states never advance the fingerprint cache. Repeated unchanged metadata/mockup file metadata uses the same fingerprint; a changed validator invalidates it. Cache loss can produce a repeat report without any business side effects. There are no network retries because the runner makes no network calls.

Report output and state must be outside product-input folders. Only product identifiers, pass/review state, and failed-check counts are exported; descriptions, prices, private template links, and credentials are not copied into artifacts. The report completing successfully does not mean all products passed; inspect each product's `needs_review` result. Detailed validation can be run locally against the relevant draft without exporting its full contents.

## Validation and rollout

Seven focused tests cover valid drafts, malformed metadata, unchanged and changed inputs, stop-before-validation, deadline recovery, missing drafts, unsafe paths, size bounds, and input preservation. Compilation passes. Existing CI performs the repository's Ruff lint and full pytest suite; no new dependencies are added.

Merge is separate approval. First validate on the machine holding approved drafts, inspect the local report, then separately authorize any publication. Revert this PR to remove the report command/manual workflow branch; it has not changed production access, schedules, database contents, or external services.
