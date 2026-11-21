"""Offline boundary and cleanup tests; these never substitute for live isolation tests."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cluster', Path(__file__).parents[1] / 'scripts/cluster.py')
cluster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cluster)
SESSION = '11111111-1111-4111-8111-111111111111'


class ClusterTest(unittest.TestCase):
    def test_names_and_invalid_inputs(self):
        one = cluster.names(SESSION, 1)
        two = cluster.names(SESSION, 2)
        self.assertNotEqual(one, two)
        self.assertTrue(all(len(name) <= 63 for name in two))
        for bad in ['not-a-uuid', '../path']:
            with self.assertRaises(ValueError):
                cluster.names(bad, 1)
        with self.assertRaises(ValueError):
            cluster.names(SESSION, 0)

    def test_policy_scopes_and_external_exposure(self):
        policy, binding = cluster.policy_documents('apps.example.invalid')
        self.assertEqual('Fail', policy['spec']['failurePolicy'])
        self.assertEqual(['Deny'], binding['spec']['validationActions'])
        self.assertEqual({'matchLabels': {cluster.MANAGED: 'true'}},
                         policy['spec']['matchConstraints']['namespaceSelector'])
        expressions = '\n'.join(item['expression'] for item in policy['spec']['validations'])
        for required in ['automountServiceAccountToken', 'serviceAccountToken', 'ClusterIP',
                         'externalIPs', 'alternateBackends', 'Redirect', 'object.metadata.namespace']:
            self.assertIn(required, expressions)
        with self.assertRaises(ValueError):
            cluster.policy_documents("evil' || true")

    def test_cleanup_refuses_foreign_namespace(self):
        with patch.object(cluster, 'oc', return_value=SimpleNamespace(stdout=json.dumps({
                'metadata': {'labels': {cluster.LABEL: 'someone-else'}}}))) as call:
            with self.assertRaises(RuntimeError):
                cluster.close(SESSION, 1)
            self.assertEqual(1, call.call_count)

    def test_cleanup_requires_verified_absence(self):
        present = json.dumps({'metadata': {'labels': {cluster.LABEL: SESSION}}})
        with patch.object(cluster, 'oc', side_effect=[SimpleNamespace(stdout=present),
                SimpleNamespace(stdout=''), SimpleNamespace(stdout=present)]):
            with self.assertRaises(RuntimeError):
                cluster.close(SESSION, 1)
        with patch.object(cluster, 'oc', return_value=SimpleNamespace(stdout='')) as call:
            cluster.close(SESSION, 1)
            self.assertEqual(2, call.call_count)

    def test_provision_rejects_unpinned_image_before_mutation(self):
        with patch.dict(os.environ, {'LAB_TERMINAL_IMAGE': 'image:latest'}), patch.object(cluster, 'oc') as call:
            with self.assertRaises(ValueError):
                cluster.provision(SESSION, 1, 'foundations')
            call.assert_not_called()

    def test_provision_installs_boundary_before_credentials(self):
        created = []
        with patch.dict(os.environ, {'LAB_TERMINAL_IMAGE': 'registry.example/cli@sha256:' + '1' * 64,
                 'LAB_API_CIDR': '192.0.2.1/32'}), patch.object(cluster, 'verify_policy'), \
                patch.object(cluster, 'namespace'), patch.object(cluster, 'oc'), \
                patch.object(cluster, 'apply', side_effect=lambda obj, ns: created.append((obj, ns))):
            cluster.provision(SESSION, 1, 'administration')
        quota = created[0][0]['spec']['hard']
        self.assertEqual('4', quota['pods'])
        self.assertEqual('1', quota['count/routes.route.openshift.io'])
        terminal = created[-1][0]
        self.assertFalse(terminal['spec']['automountServiceAccountToken'])
        self.assertEqual(['ALL'], terminal['spec']['containers'][0]['securityContext']['capabilities']['drop'])
        self.assertFalse(any(obj['kind'] == 'Secret' for obj, ns in created))

    def test_runtime_refuses_cluster_admin_identity(self):
        with patch.object(cluster, 'oc', return_value=SimpleNamespace(stdout='system:admin')):
            with self.assertRaises(RuntimeError):
                cluster.verify_policy()


    def test_policy_comparison_allows_defaults_but_not_weakened_boundary(self):
        self.assertTrue(cluster.contains({'a': [{'b': 1, 'default': True}]}, {'a': [{'b': 1}]}))
        self.assertFalse(cluster.contains({'a': [{'b': 2}]}, {'a': [{'b': 1}]}))
        self.assertFalse(cluster.contains({'a': []}, {'a': [{'b': 1}]}))
        self.assertFalse(cluster.contains({}, {'a': 1}))

    def test_controller_has_no_secret_or_cluster_admin_permissions(self):
        source = Path(__file__).parents[1] / 'scripts/controller-rbac.py'
        rbac_spec = importlib.util.spec_from_file_location('rbac', source)
        rbac = importlib.util.module_from_spec(rbac_spec)
        rbac_spec.loader.exec_module(rbac)
        docs = rbac.render('syskata-system')['items']
        self.assertEqual('ValidatingAdmissionPolicy', docs[0]['kind'])
        self.assertEqual('Fail', docs[0]['spec']['failurePolicy'])
        self.assertIn('namespaceObject', docs[0]['spec']['validations'][0]['expression'])
        role = next(item for item in docs if item['kind'] == 'ClusterRole')
        for rule in role['rules']:
            self.assertNotIn('*', rule['verbs'])
            self.assertNotIn('secrets', rule['resources'])
            self.assertNotIn('impersonate', rule['verbs'])
            self.assertNotIn('bind', rule['verbs'])
            self.assertNotIn('escalate', rule['verbs'])
        with self.assertRaises(ValueError):
            rbac.render("bad'namespace")


if __name__ == '__main__':
    unittest.main()
