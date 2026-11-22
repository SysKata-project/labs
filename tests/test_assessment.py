import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import assessment_checks as checks
import assess
import evaluator_rbac


def fixture():
    desired = json.loads((ROOT / 'assessment/profiles/foundations-v1.json').read_text())['deployment']
    deployment = deepcopy(desired)
    deployment['metadata'].update(namespace='test', uid='deployment', generation=3, resourceVersion='123')
    deployment['status'] = dict(observedGeneration=3, updatedReplicas=1, readyReplicas=1, replicas=1)
    rs = {'metadata': {'namespace': 'test', 'uid': 'rs', 'ownerReferences': [
        {'kind': 'Deployment', 'uid': 'deployment', 'controller': True}]}}
    pod = {'metadata': {'namespace': 'test', 'uid': 'pod', 'labels': {'app': 'web'},
        'ownerReferences': [{'kind': 'ReplicaSet', 'uid': 'rs', 'controller': True}]},
        'status': {'podIP': '10.0.0.1', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
    service = {'metadata': {'uid': 'service', 'namespace': 'test'}, 'spec': {
        'type': 'ClusterIP', 'selector': {'app': 'web'}, 'ports': [{'port': 8080, 'targetPort': 8080}]}}
    endpoint = {'metadata': {'namespace': 'test', 'ownerReferences': [
        {'kind': 'Service', 'uid': 'service', 'controller': True}]}, 'ports': [{'port': 8080}],
        'endpoints': [{'conditions': {'ready': True}, 'addresses': ['10.0.0.1'],
                       'targetRef': {'kind': 'Pod', 'namespace': 'test', 'uid': 'pod'}}]}
    return desired, deployment, rs, pod, service, endpoint


class ChecksTest(unittest.TestCase):
    def test_healthy_foundations_actually_probes_https(self):
        observer = assess.Observer('00000000-0000-0000-0000-000000000001', 1, 'foundations')
        _, dep, rs, pod, service, endpoint = fixture()
        for obj in (dep, rs, pod, service, endpoint): obj['metadata']['namespace'] = observer.namespace
        endpoint['endpoints'][0]['targetRef']['namespace'] = observer.namespace
        host = 'web-' + observer.namespace + '.apps.example.invalid'
        route = {'metadata': {'namespace': observer.namespace}, 'spec': {'host': host,
            'to': {'kind': 'Service', 'name': 'web'}, 'port': {'targetPort': 8080},
            'tls': {'termination': 'edge', 'insecureEdgeTerminationPolicy': 'Redirect'}},
            'status': {'ingress': [{'host': host, 'conditions': [{'type': 'Admitted', 'status': 'True'}]}]}}
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'<title>Directory listing for /</title><ul></ul>'
        opener = unittest.mock.MagicMock(); opener.open.return_value = response
        objects = {'service': service, 'route': route, 'endpointslices': {'items': [endpoint]}}
        with patch.object(observer, 'resources', return_value=(dep, [pod])), patch.object(observer, 'get', side_effect=lambda kind, *a, **k: objects[kind]), patch.dict(assess.os.environ, {'LAB_ROUTE_DOMAIN': 'apps.example.invalid'}), patch.object(assess.urllib.request, 'build_opener', return_value=opener):
            result, _ = observer.foundations({})
            self.assertTrue(result['response'])
            opener.open.assert_called_once_with('https://' + host + '/', timeout=10)
            response.read.return_value = b'other body'
            self.assertFalse(observer.foundations({})[0]['response'])

    def test_manifest_positive_alternate_quantity_and_negative(self):
        desired, dep, *_ = fixture()
        self.assertTrue(checks.manifest(dep, desired))
        dep['spec']['template']['spec']['containers'][0]['resources']['requests']['cpu'] = '0.05'
        self.assertTrue(checks.manifest(dep, desired))
        for status in ('observedGeneration', 'updatedReplicas', 'readyReplicas'):
            wrong = deepcopy(dep); wrong['status'][status] = 0
            self.assertFalse(checks.manifest(wrong, desired))
        wrong = deepcopy(dep); wrong['spec']['template']['spec']['containers'][0]['image'] = checks.FAULT_IMAGE
        self.assertFalse(checks.manifest(wrong, desired))
        wrong = deepcopy(dep); wrong['spec']['template']['spec']['automountServiceAccountToken'] = True
        self.assertFalse(checks.manifest(wrong, desired))
        self.assertFalse(checks.manifest({}, desired))

    def test_backend_uid_chain_rejects_forged_and_foreign_targets(self):
        _, dep, rs, pod, service, endpoint = fixture()
        pods = checks.controlled_pods(dep, [rs], [pod], 'test')
        self.assertTrue(checks.service_matches(service, pods, [endpoint], 'test'))
        for mutation in ('owner', 'address', 'target', 'port', 'namespace'):
            bad = deepcopy(endpoint)
            if mutation == 'owner': bad['metadata']['ownerReferences'][0]['uid'] = 'other'
            if mutation == 'address': bad['endpoints'][0]['addresses'] = ['10.0.0.2']
            if mutation == 'target': bad['endpoints'][0]['targetRef']['uid'] = 'other'
            if mutation == 'port': bad['ports'][0]['port'] = 9090
            if mutation == 'namespace': bad['metadata']['namespace'] = 'foreign'
            self.assertFalse(checks.service_matches(service, pods, [bad], 'test'), mutation)
        for selector in ({}, {'app': 'wrong'}):
            bad = deepcopy(service); bad['spec']['selector'] = selector
            self.assertFalse(checks.service_matches(bad, pods, [endpoint], 'test'))
        empty = deepcopy(endpoint); empty['ports'] = None; empty['endpoints'] = []
        self.assertFalse(checks.service_matches(service, pods, [empty], 'test'))
        rs['metadata']['ownerReferences'][0]['uid'] = 'other'
        self.assertEqual([], checks.controlled_pods(dep, [rs], [pod], 'test'))

    def test_fault_requires_same_uid_and_later_generation(self):
        _, dep, *_ = fixture()
        stage = {'phase': 'fault_observed', 'uid': 'deployment', 'initialGeneration': 1, 'faultGeneration': 2}
        self.assertTrue(checks.repaired(dep, stage))
        for bad in ({}, {**stage, 'uid': 'deleted'}, {**stage, 'faultGeneration': 3}, {**stage, 'phase': 'armed'}):
            self.assertFalse(checks.repaired(dep, bad))

    def test_route_requires_actual_admission_tls_and_target(self):
        route = {'spec': {'host': 'assigned', 'to': {'kind': 'Service', 'name': 'web'},
            'port': {'targetPort': 8080}, 'tls': {'termination': 'edge', 'insecureEdgeTerminationPolicy': 'Redirect'}},
            'status': {'ingress': [{'host': 'assigned', 'conditions': [{'type': 'Admitted', 'status': 'True'}]}]}}
        self.assertTrue(checks.route_matches(route, 'assigned'))
        for key, value in [('host', 'foreign'), ('tls', {}), ('alternateBackends', [{'name': 'other'}]),
                           ('port', {'targetPort': 9090}), ('to', {'kind': 'Service', 'name': 'other'})]:
            bad = deepcopy(route); bad['spec'][key] = value
            self.assertFalse(checks.route_matches(bad, 'assigned'))

    def test_quota_requires_accounted_hard_and_usage(self):
        quota = {'spec': {'hard': {'pods': '1'}}, 'status': {'hard': {'pods': '1'}, 'used': {'pods': '1'}}}
        self.assertTrue(checks.quota_matches(quota))
        for field in ('hard', 'used'):
            bad = deepcopy(quota); bad['status'][field]['pods'] = '2'
            self.assertFalse(checks.quota_matches(bad))

    def test_effective_access_accepts_split_roles_without_reading_rule_spelling(self):
        observer = assess.Observer('00000000-0000-0000-0000-000000000001', 1, 'administration')
        response = SimpleNamespace(stdout=json.dumps({'status': {'allowed': True}}))
        with patch.object(assess, 'oc', return_value=response) as command:
            self.assertTrue(observer.access('get', 'pods', observer.namespace))
            spec = command.call_args.kwargs['data']['spec']
            self.assertIn('system:authenticated', spec['groups'])
            self.assertTrue(spec['user'].endswith(':supplied-reader'))
        response.stdout = json.dumps({'status': {'allowed': False, 'evaluationError': 'timeout'}})
        with patch.object(assess, 'oc', return_value=response):
            with self.assertRaises(assess.InfrastructureError): observer.access('get', 'pods', observer.namespace)

    def test_adapter_failures_are_not_verified_absence(self):
        observer = assess.Observer('00000000-0000-0000-0000-000000000001', 1, 'foundations')
        with patch.object(assess.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')):
            with self.assertRaises(assess.InfrastructureError): observer.get('deployment', 'web')
        with patch.object(assess.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')):
            self.assertEqual({}, observer.get('deployment', 'web'))

    def test_quota_rbac_error_is_infrastructure_not_success(self):
        observer = assess.Observer('00000000-0000-0000-0000-000000000001', 1, 'administration')
        quota = {'spec': {'hard': {'pods': '1'}}, 'status': {'hard': {'pods': '1'}, 'used': {'pods': '1'}}}
        with patch.object(observer, 'resources', return_value=({}, [])), patch.object(observer, 'access', return_value=False), patch.object(observer, 'get', return_value=quota):
            for error in ('Forbidden by RBAC', 'connection refused'):
                with patch.object(assess, 'oc', return_value=SimpleNamespace(returncode=1, stderr=error)):
                    with self.assertRaises(assess.InfrastructureError): observer.administration()

    def test_evaluator_has_no_terminal_token_or_secret_access(self):
        installation = evaluator_rbac.installation('syskata-validation')
        docs = installation['items'] + evaluator_rbac.roles('test-namespace', 'syskata-validation')
        for obj in docs:
            for rule in obj.get('rules', []):
                self.assertNotIn('secrets', rule['resources'])
                self.assertNotIn('pods/exec', rule['resources'])
                self.assertNotIn('serviceaccounts/token', rule['resources'])
                self.assertNotIn('impersonate', rule['verbs'])
        self.assertIn('request.dryRun == true', installation['items'][0]['spec']['validations'][0]['expression'])


if __name__ == '__main__':
    unittest.main()
