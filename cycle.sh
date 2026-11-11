#!/usr/bin/env bash
# cycle.sh — One pipeline cycle, for a scheduled GitHub Actions run
#
# The VPS loop (loop.sh) ran forever. This runs one cycle and exits, so a
# scheduler (.github/workflows/commerce.yml) decides how often it runs.
# Exit 0 = cycle finished or nothing to do. Non-zero = the run failed and
# GitHub emails the repo owner.
#
# Not here any more, because they moved:
#   Buyer delivery         -> n8n "Shop · Flow Starter delivery" (Stripe webhook)
#   Sales sync + digest    -> n8n (scheduled workflow)
#   Database backup        -> every run commits the state to the private state repo
#   Phase 1 + 2 research   -> retired; outputs/brand_guide.md and outputs/master.txt
#                             are kept by hand in the state repo (from Product Lab briefs)
#
# One cycle, in order:
#   1. Kill switch + preflight
#   2. MAINTAIN when this week's publish limit is reached: SEO refresh, storefront, health
#   3. Otherwise DESIGN the next product in master.txt (Phase 3 + 3B)
#   4. Then, if the publish cooldown has passed, BUILD and publish the next ready product
#      (Phase 4, Phase 5 as a Stripe draft, Phase 6, storefront, Phase 7)
#
# Usage:
#   ./cycle.sh            — one full cycle
#   ./cycle.sh maintain   — only the maintain steps (SEO, storefront, health)
#
# Spend: every model call goes through scripts/spend_cap.py ($2/day, $15/month
# by default). The ledger lives in logs/, which the workflow saves to the state repo.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-$SCRIPT_DIR/.venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
MODE="${1:-cycle}"
EX_CONFIG=78

mkdir -p logs outputs

_ts()  { date -u '+%Y-%m-%d %H:%M:%S UTC'; }
_log() { echo "[cycle] $(_ts) $*"; }
_err() { echo "[cycle][ERROR] $(_ts) $*" >&2; FAILED=1; }
_feed() { "$PYTHON" scripts/scoped_env.py scripts/atlas_feed.py "$@" >/dev/null 2>&1 || true; }
FAILED=0

_env_get() {
    "$PYTHON" -c 'import sys
from dotenv import dotenv_values
print(dotenv_values(".env").get(sys.argv[1]) or sys.argv[2])' "$1" "$2"
}

_py() { "$PYTHON" - "$@"; }

_run_phase() {
    local label="$1"; shift
    _log "Starting $label"
    local code=0
    "$@" || code=$?
    if [[ $code -eq 0 ]]; then
        _log "$label OK"
        _feed ok "$label"
        return 0
    fi
    _err "$label FAILED (exit $code)"
    _feed failed "$label" "Exit $code"
    return $code
}

_queue_failure() {
    local status
    status=$(_py "$1" "$2" <<'PYEOF'
import sys; sys.path.insert(0, 'scripts')
from db import record_queue_failure
print(record_queue_failure(sys.argv[1], sys.argv[2]))
PYEOF
)
    [[ "$status" == "failed" ]] && _err "Product '$1' failed 3 times ($2) and is now marked failed."
    return 0
}

# ── 1. Kill switch + preflight ────────────────────────────────────────────────
if why=$("$PYTHON" scripts/kill_switch.py); then
    :
else
    _log "Kill switch on — nothing runs. $why"
    _feed skipped "Cycle" "Kill switch on"
    exit 0
fi

WEEKLY_LIMIT="${WEEKLY_PUBLISH_LIMIT:-$(_env_get WEEKLY_PUBLISH_LIMIT 5)}"
COOLDOWN_HOURS="${PUBLISH_COOLDOWN_HOURS:-$(_env_get PUBLISH_COOLDOWN_HOURS 24)}"
[[ "$WEEKLY_LIMIT" =~ ^[0-9]+$ ]] || { echo "WEEKLY_PUBLISH_LIMIT must be a whole number" >&2; exit "$EX_CONFIG"; }
[[ "$COOLDOWN_HOURS" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "PUBLISH_COOLDOWN_HOURS must be a number" >&2; exit "$EX_CONFIG"; }

_py <<'PYEOF' || { echo "Could not open the database" >&2; exit 1; }
import sys; sys.path.insert(0, 'scripts')
from db import init_db
init_db()
PYEOF

PREFLIGHT_CODE=0
"$PYTHON" scripts/scoped_env.py scripts/preflight.py || PREFLIGHT_CODE=$?
if [[ $PREFLIGHT_CODE -ne 0 ]]; then
    echo "[cycle] Preflight failed (exit $PREFLIGHT_CODE). Check the repo secrets." >&2
    _feed failed "Preflight" "Exit $PREFLIGHT_CODE"
    exit "$PREFLIGHT_CODE"
fi
_log "Anthropic spend: $("$PYTHON" scripts/spend_cap.py status 2>&1 | sed 's/^\[spend_cap\] //')"

# ── 2. Maintain ───────────────────────────────────────────────────────────────
_maintain() {
    _log "MAINTAIN — SEO refresh on the 10 listings reviewed longest ago"
    _py <<'PYEOF' || _err "Maintain SEO scan failed"
import sys, subprocess
sys.path.insert(0, 'scripts')
from db import get_conn
with get_conn() as c:
    rows = c.execute("""
        SELECT l.product_name FROM listings l
        LEFT JOIN (SELECT product_name, MAX(reviewed_at) AS last_reviewed
                   FROM seo_review GROUP BY product_name) s
          ON l.product_name = s.product_name
        ORDER BY COALESCE(s.last_reviewed, '1970-01-01') ASC LIMIT 10
    """).fetchall()
for r in rows:
    res = subprocess.run([sys.executable, 'scripts/scoped_env.py', 'scripts/seo_analyzer.py',
                          r['product_name']], capture_output=True, text=True, timeout=120)
    if res.stdout: print(res.stdout.strip())
    if res.returncode != 0:
        print(f"  [maintain] SEO error for {r['product_name']}: {res.stderr[:200]}")
PYEOF
    _run_phase "Storefront deploy" "$PYTHON" scripts/scoped_env.py scripts/storefront.py deploy
    _run_phase "Phase 7 (health dashboard)" "$PYTHON" scripts/scoped_env.py scripts/health_dashboard.py
}

if [[ "$MODE" == "maintain" ]]; then
    _maintain
    exit "$FAILED"
fi

PUBLISHED_THIS_WEEK=$(_py <<'PYEOF'
import sys; sys.path.insert(0, 'scripts')
from db import count_published_this_week
print(count_published_this_week())
PYEOF
)
[[ "$PUBLISHED_THIS_WEEK" =~ ^[0-9]+$ ]] || { echo "Could not read the weekly publish count" >&2; exit 1; }
_log "Weekly budget: ${PUBLISHED_THIS_WEEK}/${WEEKLY_LIMIT} published this week"

if [[ "$PUBLISHED_THIS_WEEK" -ge "$WEEKLY_LIMIT" ]]; then
    _maintain
    exit "$FAILED"
fi

# ── 3. Design the next product ────────────────────────────────────────────────
if [[ ! -f outputs/brand_guide.md || ! -f outputs/master.txt ]]; then
    _log "outputs/brand_guide.md or outputs/master.txt is missing. Add them to the state repo; nothing to design."
fi

DESIGN_PRODUCT=$(_py <<'PYEOF'
import sys, re
from pathlib import Path
outputs = Path('outputs')
master = outputs / 'master.txt'
if not master.exists() or not (outputs / 'brand_guide.md').exists():
    sys.exit(0)
names = re.findall(r'^\d+\.\s+(.+?)\s+\(\$[\d.]+\)', master.read_text(encoding='utf-8'), re.MULTILINE)
for name in names:
    # Names become folder names — never allow a path
    if '/' in name or '\\' in name or '..' in name:
        continue
    if not (outputs / f'{name.replace(" ", "_")}_canva.json').exists():
        print(name)
        break
PYEOF
)

if [[ -n "$DESIGN_PRODUCT" ]]; then
    _run_phase "Phase 3 (canva image generator)" \
        "$PYTHON" scripts/scoped_env.py scripts/canva_image_generator.py "$DESIGN_PRODUCT" || true
    if [[ ! -f "outputs/${DESIGN_PRODUCT// /_}_product.json" ]]; then
        _run_phase "Phase 3B (canva product creator)" \
            "$PYTHON" scripts/scoped_env.py scripts/canva_product_creator.py "$DESIGN_PRODUCT" || true
    fi
else
    _log "Phase 3 — every product in master.txt has designs."
fi

# ── 4. Build and publish, when the cooldown has passed ────────────────────────
COOLDOWN_OK=$(_py "$COOLDOWN_HOURS" <<'PYEOF'
import sys; sys.path.insert(0, 'scripts')
from db import hours_since_last_publish
elapsed = hours_since_last_publish()
print('yes' if elapsed >= float(sys.argv[1]) else f'no:{elapsed:.1f}')
PYEOF
)
if [[ "$COOLDOWN_OK" != "yes" ]]; then
    _log "Publish cooldown active (${COOLDOWN_OK#no:}h of ${COOLDOWN_HOURS}h). Cycle done."
    exit "$FAILED"
fi

PRODUCT=$(_py <<'PYEOF'
import sys; sys.path.insert(0, 'scripts')
from db import next_publishable_product
print(next_publishable_product())
PYEOF
)
if [[ -z "$PRODUCT" ]]; then
    _log "No product ready to publish. Cycle done."
    exit "$FAILED"
fi
_log "BUILD — publishing: $PRODUCT"

_step() {  # label, reason, script — stops the build on failure
    local label="$1" reason="$2" script="$3"
    _run_phase "$label" "$PYTHON" scripts/scoped_env.py "scripts/$script" "$PRODUCT" && return 0
    _queue_failure "$PRODUCT" "$reason"
    exit 1
}

if [[ ! -f "outputs/${PRODUCT// /_}_product.json" ]]; then
    _step "Phase 3B retry (canva product creator)" "phase 3B: no template link" canva_product_creator.py
fi
_step "Phase 4.1 (image processor)" "phase 4.1"            image_processor.py
_step "Phase 4.2 (listing builder)" "phase 4.2"            listing_builder.py
_step "Phase 4.3 (validator)"       "phase 4.3 validation" pre_upload_validator.py
_step "Phase 4.5 (file organizer)"  "phase 4.5"            file_organizer.py
_step "Phase 5 (stripe publisher)"  "phase 5 upload"       stripe_publisher.py

_run_phase "Phase 6 (seo analyzer)" "$PYTHON" scripts/scoped_env.py scripts/seo_analyzer.py "$PRODUCT" || true
_run_phase "Storefront deploy" "$PYTHON" scripts/scoped_env.py scripts/storefront.py deploy
_run_phase "Phase 7 (health dashboard)" "$PYTHON" scripts/scoped_env.py scripts/health_dashboard.py

_log "Cycle done — published $PRODUCT as a Stripe draft ($((PUBLISHED_THIS_WEEK + 1))/${WEEKLY_LIMIT} this week)."
_log "Approve it with: ./run.sh approve \"$PRODUCT\" (or the workflow's approve input)."
exit "$FAILED"
