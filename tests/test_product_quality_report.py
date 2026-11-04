import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from product_quality_report import review


class QualityReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assets = self.root / '04_Assets' / 'ReadyToUpload'
        self.product = self.assets / 'Sample Product'
        self.product.mkdir(parents=True)
        self.output = self.root / 'reports'
        self.state = self.root / 'state.json'
        self.listing = self.product / 'listing.json'
        self.listing.write_text(json.dumps({'title': 'Sample', 'description': 'Sample description', 'tags': ['sample'], 'price': 10, 'template_link': 'https://private.invalid/do-not-export'}))
        (self.product / 'preview.jpg').write_bytes(b'fixture-only: presence is not image verification')

    def test_success_duplicate_and_changed_draft(self):
        self.assertEqual(review(self.root, self.output, self.state)['products'][0]['result'], 'passed')
        stamp = self.state.stat().st_mtime_ns
        self.assertEqual(review(self.root, self.output, self.state)['status'], 'unchanged')
        self.assertEqual(stamp, self.state.stat().st_mtime_ns)
        self.listing.write_text(json.dumps({'title': None, 'description': [], 'tags': [], 'price': 0}))
        result = review(self.root, self.output, self.state)
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['products'][0]['result'], 'needs_review')

    def test_stop_skips_validation(self):
        with patch('product_quality_report.validate', side_effect=AssertionError('Unexpected validation')):
            self.assertEqual(review(self.root, self.output, self.state, stopped=True)['status'], 'stopped')
        self.assertFalse(self.state.exists())

    def test_no_drafts_never_claims_readiness(self):
        with tempfile.TemporaryDirectory() as empty:
            report = review(Path(empty), self.output, self.state)
        self.assertEqual(report['status'], 'incomplete')
        self.assertFalse(self.state.exists())

    def test_scan_deadline_is_unknown_and_can_recover(self):
        with patch('product_quality_report.time.monotonic', side_effect=[0, 11]):
            self.assertEqual(review(self.root, self.output, self.state)['status'], 'incomplete')
        self.assertFalse(self.state.exists())
        self.assertEqual(review(self.root, self.output, self.state)['status'], 'succeeded')

    def test_symlink_rejected_without_exporting_content(self):
        self.listing.unlink()
        secret = self.root / 'secret'
        secret.write_text('sensitive-marker')
        self.listing.symlink_to(secret)
        report = review(self.root, self.output, self.state)
        self.assertEqual(report['status'], 'incomplete')
        self.assertNotIn('sensitive-marker', (self.output / 'report.json').read_text())

    def test_report_does_not_export_template_links_or_change_inputs(self):
        before = self.listing.read_bytes()
        report = review(self.root, self.output, self.state)
        self.assertEqual(before, self.listing.read_bytes())
        self.assertEqual(report['model_calls'], 0)
        self.assertNotIn('private.invalid', (self.output / 'report.json').read_text())
        self.assertFalse((self.root / 'outputs' / 'pipeline.db').exists())
        with self.assertRaises(ValueError):
            review(self.root, self.product, self.state)

    def test_malformed_and_oversized_inputs_do_not_advance_cache(self):
        self.listing.write_text('x' * (128 * 1024 + 1))
        self.assertEqual(review(self.root, self.output, self.state)['status'], 'incomplete')
        self.assertFalse(self.state.exists())
        self.listing.write_text('{bad json')
        self.assertEqual(review(self.root, self.output, self.state)['products'][0]['result'], 'needs_review')
