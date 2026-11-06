#!/usr/bin/env python3
"""Disposable OpenShift feasibility checks. Credentials never enter the report."""
import argparse
import copy
import datetime
import json
from pathlib import Path
import subprocess
import signal
import tempfile
import time
import uuid

IMAGE = 'registry.access.redhat.com/ubi9/python-312@sha256:2e0c178f11ae69bc65cad2352c0832aadbc57d429e0160929ec436d6ba9b4e78'
LABEL = 'syskata.io/feasibility-run'


def resource(kind, name, spec=None, api='v1', **fields):
    obj = {'apiVersion': api, 'kind': kind, 'metadata': {'name': name}, **fields}
    if spec is not None:
        obj['spec'] = spec
    return obj


def workload():
    return resource('Deployment', 'web', {
        'replicas': 1, 'selector': {'matchLabels': {'app': 'web'}},
        'template': {'metadata': {'labels': {'app': 'web'}}, 'spec': {
            'automountServiceAccountToken': False,
            'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'web', 'image': IMAGE,
                'command': ['python3', '-m', 'http.server', '8080', '--directory', '/tmp'],
                'ports': [{'containerPort': 8080}],
                'securityContext': {'allowPrivilegeEscalation': False,
                                    'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'},
                              'limits': {'cpu': '250m', 'memory': '128Mi'}},
                'readinessProbe': {'httpGet': {'path': '/', 'port': 8080},
                                   'initialDelaySeconds': 1, 'periodSeconds': 2}}]}}}, 'apps/v1')


def learner_rules():
    return [
        {'apiGroups': [''], 'resources': ['pods', 'services'],
         'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']},
        {'apiGroups': [''], 'resources': ['pods/exec'], 'verbs': ['create']},
        {'apiGroups': [''], 'resources': ['pods/log'], 'verbs': ['get']},
        {'apiGroups': ['apps'], 'resources': ['deployments'],
         'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']},
        {'apiGroups': ['route.openshift.io'], 'resources': ['routes'],
         'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']},
        {'apiGroups': [''], 'resources': ['resourcequotas'], 'verbs': ['get', 'list', 'create']},
        {'apiGroups': [''], 'resources': ['resourcequotas'], 'resourceNames': ['exercise-budget'],
         'verbs': ['update', 'patch', 'delete']},
        {'apiGroups': ['rbac.authorization.k8s.io'], 'resources': ['roles', 'rolebindings'],
         'verbs': ['get', 'list', 'create', 'update', 'patch', 'delete']},
    ]


def policies():
    return [resource('NetworkPolicy', 'boundary', {
        'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'],
        'ingress': [{'from': [{'podSelector': {}}]},
                    {'from': [{'namespaceSelector': {'matchLabels': {
                        'policy-group.network.openshift.io/ingress': ''}}}],
                     'ports': [{'protocol': 'TCP', 'port': 8080}]}],
        'egress': [{'to': [{'podSelector': {}}]},
                   {'to': [{'namespaceSelector': {'matchLabels': {
                       'kubernetes.io/metadata.name': 'openshift-dns'}}}],
                    'ports': [{'protocol': p, 'port': 5353} for p in ['TCP', 'UDP']] +
                             [{'protocol': p, 'port': 53} for p in ['TCP', 'UDP']]}]
    }, 'networking.k8s.io/v1')]


def admission_resources(run_id):
    policy = resource('ValidatingAdmissionPolicy', run_id, {
        'failurePolicy': 'Fail',
        'matchConstraints': {
            'namespaceSelector': {'matchLabels': {LABEL: run_id}},
            'resourceRules': [{'apiGroups': [''], 'apiVersions': ['v1'],
                               'operations': ['CREATE', 'UPDATE'], 'resources': ['pods']}]},
        'validations': [
            {'expression': 'has(object.spec.automountServiceAccountToken) && object.spec.automountServiceAccountToken == false',
             'message': 'SysKata: automatic service account tokens are forbidden'},
            {'expression': '!has(object.spec.volumes) || object.spec.volumes.all(v, !has(v.projected) || v.projected.sources.all(s, !has(s.serviceAccountToken)))',
             'message': 'SysKata: projected service account tokens are forbidden'},
            {'expression': "!has(object.spec.serviceAccountName) || object.spec.serviceAccountName == 'default'",
             'message': 'SysKata: workloads must use the default unprivileged account'}]
    }, 'admissionregistration.k8s.io/v1')
    binding = resource('ValidatingAdmissionPolicyBinding', run_id, {
        'policyName': run_id, 'validationActions': ['Deny'],
        'matchResources': {'namespaceSelector': {'matchLabels': {LABEL: run_id}}}
    }, 'admissionregistration.k8s.io/v1')
    for obj in [policy, binding]:
        obj['metadata']['labels'] = {LABEL: run_id}
    return [policy, binding]


class Validation:
    def __init__(self, config, output, concurrency, insecure_routes=False):
        self.config, self.output, self.concurrency = config, output, concurrency
        self.run_id = 'sk-feas-' + uuid.uuid4().hex[:10]
        self.namespaces = []
        self.admission = []
        self.insecure_routes = insecure_routes
        self.report = {'run_id': self.run_id, 'started_utc': self.now(),
                       'image': IMAGE, 'concurrency': concurrency, 'checks': []}

    @staticmethod
    def now():
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def save(self):
        self.output.write_text(json.dumps(self.report, indent=2) + '\n')

    def check(self, label, passed, **evidence):
        self.report['checks'].append({'check': label, 'passed': bool(passed), **evidence})
        self.save()
        print(('PASS ' if passed else 'FAIL ') + label, flush=True)
        if not passed:
            raise RuntimeError(label)

    def oc(self, *args, config=None, data=None):
        # Never echo commands, stdout or stderr: they may contain credentials.
        return subprocess.run(['oc', '--kubeconfig', str(config or self.config),
                               '--request-timeout=30s', *args],
                              input=json.dumps(data) if data is not None else None,
                              text=True, capture_output=True, timeout=240)

    def get(self, *args):
        result = self.oc(*args, '-o', 'json')
        if result.returncode:
            raise RuntimeError('read cluster resource failed')
        return json.loads(result.stdout)

    def create(self, obj, namespace=None, config=None, forbidden=False):
        args = ['create', '-f', '-']
        if namespace:
            args += ['-n', namespace]
        result = self.oc(*args, config=config, data=obj)
        if forbidden:
            return result.returncode != 0 and 'Forbidden' in result.stderr
        if result.returncode:
            raise RuntimeError('create ' + obj['kind'] + '/' + obj['metadata']['name'] + ' failed')
        return True

    def token_config(self, ns, account, directory):
        result = self.oc('create', '--raw', f'/api/v1/namespaces/{ns}/serviceaccounts/{account}/token',
                         '-f', '-', data={'apiVersion': 'authentication.k8s.io/v1',
                                          'kind': 'TokenRequest', 'spec': {'expirationSeconds': 600}})
        if result.returncode:
            raise RuntimeError('issue scoped credential failed')
        status = json.loads(result.stdout)['status']
        base = self.oc('config', 'view', '--minify', '--raw', '-o', 'json')
        if base.returncode:
            raise RuntimeError('read private kubeconfig failed')
        cfg = json.loads(base.stdout)
        cfg['users'] = [{'name': 'scoped', 'user': {'token': status['token']}}]
        cfg['contexts'][0]['context']['user'] = 'scoped'
        cfg['contexts'][0]['context']['namespace'] = ns
        path = Path(directory) / (ns + '-' + account + '.kubeconfig')
        path.write_text(json.dumps(cfg))
        path.chmod(0o600)
        return path, status['expirationTimestamp']

    def can(self, config, ns, verb, target):
        result = self.oc('auth', 'can-i', verb, target, '-n', ns, config=config)
        answer = result.stdout.strip()
        if answer not in ('yes', 'no') or result.returncode not in (0, 1):
            raise RuntimeError('authorization check did not return a decision')
        return answer == 'yes'

    def probe(self, ns, url):
        code = 'import urllib.request; urllib.request.urlopen(' + repr(url) + ', timeout=4).read()'
        return self.oc('exec', '-n', ns, 'deployment/web', '--', 'python3', '-c', code)

    def cleanup(self):
        ok = True
        for ns in self.namespaces:
            obj = self.oc('get', 'namespace', ns, '-o', 'json')
            if obj.returncode:
                ok = ok and 'NotFound' in obj.stderr
                continue
            if json.loads(obj.stdout)['metadata'].get('labels', {}).get(LABEL) != self.run_id:
                ok = False
                continue
            result = self.oc('delete', 'namespace', ns, '--wait=true', '--timeout=180s')
            absent = self.oc('get', 'namespace', ns)
            ok = ok and result.returncode == 0 and 'NotFound' in absent.stderr
        # Keep the boundary in place if namespace cleanup is incomplete.
        if ok:
            for kind, name in reversed(self.admission):
                obj = self.oc('get', kind, name, '-o', 'json')
                if obj.returncode:
                    ok = ok and 'NotFound' in obj.stderr
                    continue
                if json.loads(obj.stdout)['metadata'].get('labels', {}).get(LABEL) != self.run_id:
                    ok = False
                    continue
                result = self.oc('delete', kind, name)
                absent = self.oc('get', kind, name)
                ok = ok and result.returncode == 0 and 'NotFound' in absent.stderr
        self.report['cleanup_passed'] = ok
        self.save()
        print(('PASS ' if ok else 'FAIL ') + 'owned resource cleanup', flush=True)
        return ok

    def run(self):
        with tempfile.TemporaryDirectory(prefix='syskata-scoped-') as directory:
            try:
                self.execute(directory)
                self.report['validation_passed'] = True
            except (RuntimeError, subprocess.TimeoutExpired, ValueError, KeyboardInterrupt) as error:
                self.report['validation_passed'] = False
                # Only our own bounded labels, never subprocess exceptions or output.
                self.report['failure'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                print('Validation stopped; see sanitized report.', flush=True)
            finally:
                try:
                    self.cleanup()
                except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                    self.report['cleanup_passed'] = False
                    print('FAIL cleanup; inspect owned namespaces using run_id.', flush=True)
                self.report['finished_utc'] = self.now()
                self.save()
        return self.report.get('validation_passed', False) and self.report.get('cleanup_passed', False)

    def execute(self, directory):
        self.report['openshift_version'] = self.get('get', 'clusterversion', 'version')['status']['desired']['version']
        self.report['network_type'] = self.get('get', 'network.config.openshift.io', 'cluster')['spec']['networkType']
        self.save()
        for obj in admission_resources(self.run_id):
            self.admission.append((obj['kind'], obj['metadata']['name']))
            self.create(obj)
        clients = []
        starts = []
        for i in range(self.concurrency):
            ns = self.run_id + '-' + str(i)
            self.namespaces.append(ns)
            self.create({'apiVersion': 'v1', 'kind': 'Namespace',
                         'metadata': {'name': ns, 'labels': {LABEL: self.run_id}}})
            starts.append(time.monotonic())
            self.create(resource('ResourceQuota', 'outer-budget', {'hard': {
                'requests.cpu': '500m', 'requests.memory': '512Mi',
                'limits.cpu': '1', 'limits.memory': '1Gi', 'pods': '4'}}), ns)
            for policy in policies():
                self.create(policy, ns)
            for sa in ['learner', 'supplied-reader']:
                self.create(resource('ServiceAccount', sa, automountServiceAccountToken=False), ns)
            self.create(resource('Role', 'learner', api='rbac.authorization.k8s.io/v1', rules=learner_rules()), ns)
            self.create(resource('RoleBinding', 'learner', api='rbac.authorization.k8s.io/v1',
                subjects=[{'kind': 'ServiceAccount', 'name': 'learner', 'namespace': ns}],
                roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'learner'}), ns)
            client, expiry = self.token_config(ns, 'learner', directory)
            clients.append((client, expiry))
            self.create(workload(), ns, client)
            self.create(resource('Service', 'web', {'selector': {'app': 'web'},
                'ports': [{'port': 8080, 'targetPort': 8080}]}), ns, client)
            self.create(resource('Route', 'web', {'to': {'kind': 'Service', 'name': 'web'},
                'port': {'targetPort': 8080}, 'tls': {'termination': 'edge',
                'insecureEdgeTerminationPolicy': 'Redirect'}}, 'route.openshift.io/v1'), ns, client)

        for i, ns in enumerate(self.namespaces):
            ready = self.oc('rollout', 'status', 'deployment/web', '-n', ns, '--timeout=180s')
            self.check('deployment ready ' + str(i), ready.returncode == 0,
                       seconds=round(time.monotonic() - starts[i], 2))
            route = self.get('get', 'route', 'web', '-n', ns)['spec']['host']
            # TLS bypass is limited to this explicitly authorized ephemeral spike.
            route_start = time.monotonic()
            route_ok = False
            while time.monotonic() - route_start < 60:
                response = subprocess.run(['curl', *(['--insecure'] if self.insecure_routes else []), '--silent', '--max-time', '5',
                    '--output', '/dev/null', '--write-out', '%{http_code}', 'https://' + route],
                    text=True, capture_output=True, timeout=10)
                if response.returncode == 0 and response.stdout == '200':
                    route_ok = True
                    break
                time.sleep(2)
            self.check('route reachable ' + str(i), route_ok,
                       route_wait_seconds=round(time.monotonic() - route_start, 2))
            self.check('same namespace traffic ' + str(i), self.probe(ns, 'http://web:8080').returncode == 0)

        ns, other = self.namespaces[:2]
        client, expiry = clients[0]
        for verb, target in [('get', 'secrets'), ('create', 'serviceaccounts/token'),
                             ('create', 'networkpolicies'), ('delete', 'resourcequotas/outer-budget'),
                             ('patch', 'resourcequotas/outer-budget'), ('get', 'nodes'),
                             ('create', 'namespaces'), ('patch', 'namespaces'),
                             ('create', 'validatingadmissionpolicies'), ('impersonate', 'users'),
                             ('bind', 'clusterroles/cluster-admin'), ('escalate', 'roles')]:
            self.check('restricted ' + verb + ' ' + target, not self.can(client, ns, verb, target))
        denied = self.oc('get', 'pods', '-n', other, config=client)
        self.check('cross-project API denial', denied.returncode != 0 and 'Forbidden' in denied.stderr)
        for source, destination in [(ns, other), (other, ns)]:
            ip = self.get('get', 'pods', '-n', destination)['items'][0]['status']['podIP']
            denied = self.probe(source, 'http://' + ip + ':8080')
            self.check('cross-project network denial ' + source[-1],
                       denied.returncode != 0 and ('timed out' in denied.stderr or 'TimeoutError' in denied.stderr))
        api_ip = self.get('get', 'service', 'kubernetes', '-n', 'default')['spec']['clusterIP']
        denied = self.probe(ns, 'https://' + api_ip + ':443')
        self.check('pod egress to API denied', denied.returncode != 0 and
                   ('timed out' in denied.stderr or 'TimeoutError' in denied.stderr))
        # RBAC exercise: learner creates a non-escalating role and binds a supplied identity.
        self.create(resource('Role', 'exercise-reader', api='rbac.authorization.k8s.io/v1',
            rules=[{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'list']}]), ns, client)
        self.create(resource('RoleBinding', 'exercise-reader', api='rbac.authorization.k8s.io/v1',
            subjects=[{'kind': 'ServiceAccount', 'name': 'supplied-reader', 'namespace': ns}],
            roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'exercise-reader'}), ns, client)
        reader, _ = self.token_config(ns, 'supplied-reader', directory)
        self.check('supplied identity can read pods', self.oc('get', 'pods', '-n', ns, config=reader).returncode == 0)
        self.check('supplied identity cannot delete pods', not self.can(reader, ns, 'delete', 'pods'))
        escalation = resource('RoleBinding', 'escape', api='rbac.authorization.k8s.io/v1',
            subjects=[{'kind': 'ServiceAccount', 'name': 'supplied-reader', 'namespace': ns}],
            roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': 'cluster-admin'})
        self.check('role binding escalation rejected', self.create(escalation, ns, client, forbidden=True))
        for name, change, marker in [
            ('auto-token', {'automountServiceAccountToken': True}, 'automatic service account tokens'),
            ('project-token', {'volumes': [{'name': 'mint', 'projected': {'sources': [
                {'serviceAccountToken': {'path': 'token', 'expirationSeconds': 600}}]}}]}, 'projected service account tokens'),
            ('select-learner', {'serviceAccountName': 'learner'}, 'default unprivileged account')]:
            spec = copy.deepcopy(workload()['spec']['template']['spec'])
            spec.update(change)
            result = self.oc('create', '-n', ns, '-f', '-', config=client, data=resource('Pod', name, spec))
            self.check('admission rejects ' + name, result.returncode != 0 and marker in result.stderr)
        privileged = copy.deepcopy(workload()['spec']['template']['spec'])
        privileged['containers'][0]['securityContext']['privileged'] = True
        result = self.oc('create', '-n', ns, '-f', '-', config=client,
                         data=resource('Pod', 'privileged-escape', privileged))
        self.check('privileged workload rejected', result.returncode != 0 and
                   'Forbidden' in result.stderr and 'privileged' in result.stderr)
        self.create(resource('ResourceQuota', 'exercise-budget', {'hard': {'pods': '1'}}), ns, client)
        pod = resource('Pod', 'over-budget', copy.deepcopy(workload()['spec']['template']['spec']))
        result = self.oc('create', '-n', ns, '-f', '-', config=client, data=pod)
        self.check('exercise quota rejects extra pod', result.returncode != 0 and 'exceeded quota' in result.stderr)
        result = self.oc('patch', 'resourcequota', 'outer-budget', '-n', ns,
            '--type=merge', '-p', '{"spec":{"hard":{"pods":"100"}}}', config=client)
        self.check('outer quota cannot be changed', result.returncode != 0 and 'Forbidden' in result.stderr)
        # Wait for the actual server-issued expiration, not just requested lifetime.
        expiry_time = datetime.datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp()
        remaining = expiry_time - time.time()
        self.check('bounded credential lifetime', 0 < remaining <= 660, remaining_seconds=round(remaining))
        # Kubernetes uses go-jose's one-minute expiry leeway. Keep the identity
        # and its RBAC alive until the check, so teardown cannot explain rejection.
        self.report['expiry_leeway_seconds'] = 60
        self.save()
        print('Waiting for scoped credential expiry plus 60-second clock-skew leeway.', flush=True)
        while time.time() < expiry_time + 65:
            time.sleep(min(10, max(0.1, expiry_time + 65 - time.time())))
        expired = self.oc('get', 'pods', '-n', ns, config=client)
        self.check('expired credential rejected', expired.returncode != 0 and
                   ('Unauthorized' in expired.stderr or 'logged in' in expired.stderr),
                   seconds_after_expiration=round(time.time() - expiry_time, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--concurrency', type=int, default=2, choices=range(2, 11))
    parser.add_argument('--insecure-routes', action='store_true',
                        help='Explicitly skip HTTPS route certificate verification for an authorized test cluster')
    args = parser.parse_args()
    if not args.kubeconfig.is_file():
        parser.error('private kubeconfig must exist')
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(0 if Validation(args.kubeconfig, args.report, args.concurrency, args.insecure_routes).run() else 1)


if __name__ == '__main__':
    main()
