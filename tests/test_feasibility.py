"""Offline tests of safety boundaries; these do not substitute for cluster checks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('feasibility', Path(__file__).parents[1] / 'scripts/validate-openshift.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def response(code=0, stdout='', stderr=''):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = m.Validation(Path('/unused'), Path(self.tmp.name) / 'report.json', 2)

    def test_transport_error_is_not_authorization_denial(self):
        self.run.oc = Mock(return_value=response(1, stderr='connection refused'))
        with self.assertRaises(RuntimeError):
            self.run.can('/unused', 'test', 'get', 'pods')

    def test_explicit_authorization_decisions(self):
        self.run.oc = Mock(side_effect=[response(0, 'yes\n'), response(1, 'no\n')])
        self.assertTrue(self.run.can('/unused', 'test', 'get', 'pods'))
        self.assertFalse(self.run.can('/unused', 'test', 'get', 'pods'))

    def test_cleanup_does_not_delete_foreign_namespace(self):
        self.run.namespaces = ['foreign']
        self.run.oc = Mock(return_value=response(stdout=json.dumps({'metadata': {'labels': {}}})))
        self.assertFalse(self.run.cleanup())
        self.assertEqual(self.run.oc.call_count, 1)

    def test_cleanup_requires_verified_absence(self):
        self.run.namespaces = ['owned']
        owned = {'metadata': {'labels': {m.LABEL: self.run.run_id}}}
        self.run.oc = Mock(side_effect=[response(stdout=json.dumps(owned)), response(), response(1, stderr='connection refused')])
        self.assertFalse(self.run.cleanup())

    def test_cleanup_accepts_already_absent_namespace(self):
        self.run.namespaces = ['gone']
        self.run.oc = Mock(return_value=response(1, stderr='NotFound'))
        self.assertTrue(self.run.cleanup())

    def test_unexpected_failure_still_cleans_up(self):
        self.run.execute = Mock(side_effect=RuntimeError('safe failure label'))
        self.run.cleanup = Mock(return_value=True)
        self.assertFalse(self.run.run())
        self.run.cleanup.assert_called_once()
        self.assertFalse(json.loads(self.run.output.read_text())['validation_passed'])

    def test_scoped_config_replaces_admin_credentials(self):
        base = {'users': [{'name': 'admin', 'user': {'exec': {'command': 'bad'}, 'password': 'test-only'}}],
                'contexts': [{'name': 'test', 'context': {'cluster': 'test', 'user': 'admin'}}],
                'clusters': [{'name': 'test', 'cluster': {'server': 'https://example.invalid'}}]}
        self.run.oc = Mock(side_effect=[response(stdout=json.dumps({'status': {'token': 'fixture',
                               'expirationTimestamp': '2000-01-01T00:00:00Z'}})),
                                       response(stdout=json.dumps(base))])
        path, _ = self.run.token_config('test', 'learner', self.tmp.name)
        cfg = json.loads(path.read_text())
        self.assertEqual(cfg['users'], [{'name': 'scoped', 'user': {'token': 'fixture'}}])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('test-only', self.run.output.read_text() if self.run.output.exists() else '')

    def test_admission_only_matches_owned_run_namespaces(self):
        policy, binding = m.admission_resources('owned-run')
        self.assertEqual(policy['spec']['matchConstraints']['namespaceSelector'],
                         {'matchLabels': {m.LABEL: 'owned-run'}})
        self.assertEqual(binding['spec']['matchResources']['namespaceSelector'],
                         {'matchLabels': {m.LABEL: 'owned-run'}})
        self.assertEqual(binding['spec']['validationActions'], ['Deny'])
        self.assertEqual(policy['spec']['failurePolicy'], 'Fail')

    def test_failed_namespace_cleanup_preserves_admission_boundary(self):
        self.run.namespaces = ['owned']
        self.run.admission = [('ValidatingAdmissionPolicy', self.run.run_id)]
        self.run.oc = Mock(return_value=response(1, stderr='unreachable'))
        self.assertFalse(self.run.cleanup())
        self.assertEqual(self.run.oc.call_count, 1)

    def test_learner_cannot_modify_boundary_resources(self):
        for rule in m.learner_rules():
            self.assertNotIn('*', rule['verbs'])
            self.assertFalse(set(rule['resources']) & {'secrets', 'networkpolicies', 'serviceaccounts', 'serviceaccounts/token'})
            if 'resourcequotas' in rule['resources'] and set(rule['verbs']) & {'update', 'patch', 'delete'}:
                self.assertEqual(rule['resourceNames'], ['exercise-budget'])


if __name__ == '__main__':
    unittest.main()
