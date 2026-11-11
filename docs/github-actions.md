# Running the pipeline on GitHub Actions

The pipeline no longer needs a server. `.github/workflows/commerce.yml` runs
one cycle (`cycle.sh`) every Monday at 14:00 UTC, and whenever you press
**Run workflow** on the Actions tab.

| Job | Where it runs now |
|---|---|
| Design, build, publish draft, SEO, storefront, health | This workflow (`cycle.sh`) |
| Buyer delivery (Canva template link email) | n8n "Shop · Flow Starter delivery", on the Stripe webhook |
| Sales sync and daily digest | n8n |
| Database backup | Every run commits the state to the private state repo |
| Phase 1 and 2 research (CrewAI) | Retired. Keep `outputs/brand_guide.md` and `outputs/master.txt` in the state repo by hand, using the Product Lab brief |

## Why a separate state repo

Runners keep nothing between runs, and this repo is public. The database,
designs, spend ledger and full run logs live in a **private** repo
(default `khaneilcampbell/commerce-state`). Each run checks it out, restores
`outputs/`, `02_Products/`, `03_Canva_Exports/`, `04_Assets/` and `logs/`,
and commits them back afterwards (`scripts/actions_state.py`).

Logs of a public repo are public, so the Actions log only shows the
`[cycle]` summary lines. The full log is in `logs/runs/` in the state repo.

## Setup

1. Create a private repo named `commerce-state` (it can start empty, with a README).
2. Create a fine-grained token with access to this repo, `commerce-state` and
   the storefront repo. Permissions: Contents read and write, Secrets read and
   write. Save it as the secret `COMMERCE_STATE_TOKEN`. It saves the state,
   pushes the storefront and stores Canva's new refresh token after each run.
3. Secrets (Settings, Secrets and variables, Actions, Secrets):
   `ANTHROPIC_API_KEY` (from a workspace with its own spend limit),
   `STRIPE_SECRET_KEY` (a restricted key), `CANVA_CLIENT_ID`,
   `CANVA_CLIENT_SECRET`, `CANVA_REFRESH_TOKEN`. Optional: `SERPER_API_KEY`,
   `ATLAS_FEED_TOKEN`.
4. Variables (same page, Variables tab): `STOREFRONT_BASE_URL`,
   `STOREFRONT_GIT_REMOTE`, `BRAND_NAME`. Optional: `STOREFRONT_GIT_BRANCH`,
   `STOREFRONT_TAGLINE`, `SUPPORT_EMAIL`, `ATLAS_FEED_URL`,
   `MAX_ANTHROPIC_USD_PER_DAY`, `MAX_ANTHROPIC_USD_PER_MONTH`,
   `COMMERCE_STATE_REPO`.
5. Put `outputs/brand_guide.md` and `outputs/master.txt` in the state repo.
6. Turn it on: add the variable `COMMERCE_LOOP_ENABLED` = `true`.

## Stopping it

- Set `COMMERCE_LOOP_ENABLED` to `false`. The next run does nothing.
- Or Actions tab, Commerce cycle, `...`, **Disable workflow**.
- A run in progress can be cancelled from its page.

Spend caps still apply on every model call (`scripts/spend_cap.py`, $2 a day
and $15 a month by default), and the Anthropic workspace limit is the hard stop.

## Approving a draft

Actions tab, Commerce cycle, **Run workflow**, type the exact product name in
**approve**. That creates the Stripe Payment Link and redeploys the storefront.
Then add a row to the n8n `shop_settings` table with the new `payment_link_id`
and the product's `template_url` so n8n delivers it to buyers.
