"""Manual local draft review. No publishing, database writes, network, or AI calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from itertools import islice
from pre_upload_validator import validate
from validation import validate_product_name, ValidationError

MAX_PRODUCTS = 50
MAX_LISTING_BYTES = 128 * 1024


def review(root: Path, output: Path, state: Path | None = None, *, stopped=False):
    root, output = root.resolve(), output.resolve()
    assets, products = root / '04_Assets' / 'ReadyToUpload', root / '02_Products'
    if output.is_relative_to(assets.resolve()) or output.is_relative_to(products.resolve()):
        raise ValueError('Report output must be outside product inputs')
    if state and (state.resolve().is_relative_to(assets.resolve()) or state.resolve().is_relative_to(products.resolve())):
        raise ValueError('State must be outside product inputs')
    now = datetime.now(timezone.utc).isoformat()
    report = {'agent_id': 'commerce-tests', 'observed_at': now, 'model_calls': 0,
              'status': 'stopped' if stopped else 'incomplete', 'products': [], 'unavailable': []}
    if not stopped:
        if not assets.is_dir() or assets.is_symlink() or not assets.resolve().is_relative_to(root):
            report['unavailable'].append('Local ReadyToUpload drafts unavailable; no VPS or live inventory was inspected.')
        else:
            entries = sorted(islice(assets.iterdir(), MAX_PRODUCTS + 1))
            if len(entries) > MAX_PRODUCTS:
                report['unavailable'].append('Directory sample limit reached; remaining entries were not reviewed.')
            start = time.monotonic()
            fingerprints = []
            for entry in entries:
                if time.monotonic() - start > 10:
                    report['unavailable'].append('Ten-second scan deadline reached.')
                    break
                if entry.is_symlink() or not entry.is_dir():
                    continue
                if len(report['products']) >= MAX_PRODUCTS:
                    report['unavailable'].append('Draft limit reached; remaining products not reviewed.')
                    break
                try:
                    validate_product_name(entry.name)
                    listing = entry / 'listing.json'
                    mockups = products / entry.name / 'Mockups'
                    images = [*islice(entry.glob('*.jpg'), 51), *islice(mockups.glob('*.jpg'), 51)]
                    if len(images) > 50:
                        raise ValueError('Image limit exceeded')
                    files = [listing, *sorted(images)]
                    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in [entry, mockups, *files]):
                        raise ValueError('Unsafe asset path')
                    if listing.exists() and listing.stat().st_size > MAX_LISTING_BYTES:
                        raise ValueError('Listing too large')
                    fingerprint = hashlib.sha256((listing.read_bytes() if listing.exists() else b'missing') + json.dumps([(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in files[1:]]).encode()).hexdigest()
                    result = validate(entry.name, assets_dir=assets, products_dir=products)
                    report['products'].append({'product': entry.name, 'result': 'passed' if result.passed else 'needs_review', 'failed_checks': len(result.errors)})
                    fingerprints.append((entry.name, fingerprint))
                except (OSError, ValueError, TypeError, ValidationError):
                    report['products'].append({'product': 'unreadable-draft', 'result': 'not_reviewed', 'failed_checks': None})
                    report['unavailable'].append('A draft was unreadable, oversized, or unsafe; its contents were not exported.')
            if not report['products']:
                report['unavailable'].append('No local drafts found; product readiness is unknown.')
            fingerprint = hashlib.sha256((Path(__file__).read_bytes() + Path(__file__).with_name('pre_upload_validator.py').read_bytes() + Path(__file__).with_name('validation.py').read_bytes()) + json.dumps(fingerprints).encode()).hexdigest()
            previous = None
            if state and state.exists():
                try:
                    previous = json.loads(state.read_text()).get('fingerprint')
                except (OSError, ValueError, AttributeError):
                    pass
            report['status'] = 'incomplete' if report['unavailable'] else 'unchanged' if previous == fingerprint else 'succeeded'
            if state and report['status'] == 'succeeded':
                state.parent.mkdir(parents=True, exist_ok=True)
                temp = state.with_suffix('.tmp')
                temp.write_text(json.dumps({'fingerprint': fingerprint}))
                temp.replace(state)
    report['next_action'] = 'Review local drafts and failed-check counts. Publishing requires separate approval.'
    report['limitations'] = 'Metadata and JPEG presence only; no image-content, copyright, template-link access, sales, or live readiness verification. A completed report may contain products needing review.'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Commerce draft quality report', '', f"Status: {report['status']}", f'Last checked: {now}', 'Source: local staged drafts · 0 model calls · No publishing', '']
    lines += [f"- {p['product']}: {p['result']} ({p['failed_checks']} failed checks)" for p in report['products']]
    lines += ['', *report['unavailable'], '', report['next_action'], report['limitations']]
    (output / 'report.md').write_text('\n'.join(lines) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    stop = os.environ.get('COMMERCE_QUALITY_STOP', 'false').lower()
    if stop not in ('true', 'false'):
        raise ValueError('Stop control must be true or false')
    report = review(args.root, args.output, args.state, stopped=stop == 'true')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            stream.write(f"changed={str(report['status'] == 'succeeded').lower()}\n")
    print(f"Draft quality report: {report['status']} · 0 model calls")
    if report['status'] == 'incomplete':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
