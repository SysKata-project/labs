"""Regression checks for live-harness bookkeeping; no cluster or database access."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('live', Path(__file__).parents[1] / 'scripts/verify-lifecycle.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class LiveHarnessTest(unittest.TestCase):
    def test_account_cleanup_matches_identity_not_mutable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = {'LAB_DB_URL': 'jdbc:postgresql://127.0.0.1:55433/syskata_labs',
                       'LAB_TERMINAL_IMAGE': 'fixture-image'}
            validation = live.Validation(runtime, root / 'report.json', root / 'runtime.json', root / 'service.py')
            lab = {'id': '11111111-1111-4111-8111-111111111111', 'subject': 'validation-test', 'state': 'ready'}
            validation.labs = [{**lab, 'state': 'provisioning'}]
            validation.create = Mock(return_value=lab)
            validation.connect = Mock(return_value=Mock())
            validation.command = Mock(return_value=True)
            validation.boundary_probes = Mock()
            validation.request = Mock()
            validation.closed = Mock(return_value=True)
            with patch.object(live.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='t\n')):
                validation.account_disable()
            self.assertEqual([], validation.labs)
            self.assertTrue(validation.cleanup())

    def test_refuses_non_fixture_database(self):
        with self.assertRaises(ValueError):
            live.Validation({'LAB_DB_URL': 'jdbc:postgresql://production/learners'}, Path('unused'), Path('unused'), Path('unused'))
