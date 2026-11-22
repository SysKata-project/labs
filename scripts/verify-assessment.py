#!/usr/bin/env python3
"""Run bounded real worker fixtures on an explicitly approved ephemeral allocation."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]


class Validation:
    def __init__(self, config, report, scenario="all"):
        self.scenario = scenario
        self.config = config
        self.path = report
        self.report = {'checks': [], 'validation_passed': False, 'cleanup_passed': False,
                       'limitations': ['Callback receiver/progression belongs to api #5; delivery transport tested separately locally']}
        self.sessions = []
        self.processes = []
        self.env = {**os.environ, **config}
        self.url = 'http://127.0.0.1:8093'
        if config['LAB_DB_URL'] != 'jdbc:postgresql://127.0.0.1:55432/syskata_labs_assessment_live':
            raise ValueError('isolated synthetic database required')

    def check(self, label, passed):
        self.report['checks'].append({'name': label, 'passed': bool(passed)})
        self.path.write_text(json.dumps(self.report, indent=2) + '\n')
        if not passed:
            raise RuntimeError('Validation assertion failed: ' + label)
        print('PASS ' + label, flush=True)

    def request(self, path, data=None):
        request = urllib.request.Request(self.url + '/internal' + path,
            data=None if data is None else json.dumps(data).encode(), headers={
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.config['LAB_INTERNAL_TOKEN']})
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(65536)
            return json.loads(body) if body else None

    def wait(self, call, predicate, seconds=150):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            result = call()
            if predicate(result): return result
            time.sleep(2)
        raise RuntimeError('Bounded live wait expired')

    def start(self):
        private = self.path.parent
        java = os.environ['JAVA_HOME'] + '/bin/java'
        for name in ('gateway', 'worker'):
            if name == 'gateway':
                env = {k: v for k, v in self.env.items() if not k.startswith('ASSESSMENT_') and k != 'LAB_ROUTE_CA'}
                args = []
            else:
                env = {k: v for k, v in self.env.items() if not k.startswith('LAB_') or k.startswith('LAB_DB_') or k.startswith('LAB_ROUTE_')}
                args = ['-Dquarkus.profile=assessment']
            with (private / (name + '.log')).open('w') as log:
                self.processes.append(subprocess.Popen([java, *args, '-jar', 'target/quarkus-app/quarkus-run.jar'],
                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
        def health():
            try:
                with urllib.request.urlopen(self.url + '/q/health/ready', timeout=2) as response:
                    return response.status == 200
            except OSError: return False
        self.wait(health, bool, 60)
        self.check('separate worker and gateway started', all(p.poll() is None for p in self.processes))

    def oc(self, config, *args, data=None, check=True):
        result = subprocess.run(['oc', '--request-timeout=20s', '--kubeconfig=' + str(config), *args],
            input=None if data is None else json.dumps(data), env=self.env, text=True, capture_output=True, timeout=150)
        if check and result.returncode: raise RuntimeError('Scoped cluster command failed: ' + args[0])
        return result

    def lab(self, scenario):
        identifier = str(uuid.uuid4())
        self.sessions.append(identifier)
        lab = self.request('/labs', {'key': str(uuid.uuid4()), 'id': identifier, 'subject': 'assessment-live', 'scenario': scenario})
        lab = self.wait(lambda: self.request('/labs/' + identifier + '?subject=assessment-live'), lambda x: x['state'] != 'provisioning')
        self.check(scenario + ' restricted-controller provisioning', lab['state'] == 'ready')
        namespace = 'sk-lab-' + uuid.UUID(identifier).hex + '-g1'
        controller = self.config['LAB_KUBECONFIG']
        token = self.oc(controller, 'create', 'token', 'learner', '-n', namespace, '--duration=20m').stdout.strip()
        cfg = json.loads(self.oc(controller, 'config', 'view', '--minify', '--raw', '--flatten', '-o', 'json').stdout)
        cfg['users'] = [{'name': 'learner', 'user': {'token': token}}]
        cfg['contexts'][0]['context'].update(user='learner', namespace=namespace)
        client = self.path.parent / (scenario + '-learner.kubeconfig')
        client.write_text(json.dumps(cfg)); client.chmod(0o600)
        return lab, namespace, client

    def apply(self, client, namespace, obj):
        self.oc(client, 'apply', '-n', namespace, '-f', '-', data=obj)

    def patch(self, client, namespace, kind, obj):
        self.oc(client, 'patch', kind, 'web', '-n', namespace, '--type=merge', '-p', json.dumps(obj))

    def rollout(self, client, namespace):
        self.oc(client, 'rollout', 'status', 'deployment/web', '-n', namespace, '--timeout=120s')

    def assessment(self, lab, retries=3):
        request = {'id': str(uuid.uuid4()), 'labId': lab['id'], 'subject': 'assessment-live', 'generation': lab['generation'],
            'sessionId': str(uuid.uuid4()), 'instanceId': str(uuid.uuid4()), 'attemptId': str(uuid.uuid4()),
            'versions': {'schema_version': '1.0.0', 'bundle_version': '0.3.0', 'policy_version': '1.0.0'},
            'exerciseRevision': 1, 'evaluator': 'openshift.' + lab['scenario'], 'evaluatorVersion': 1}
        self.request('/assessments', request)
        job = self.wait(lambda: self.request('/assessments/' + request['id'] + '?subject=assessment-live'), lambda x: x['state'] != 'pending')
        if job['state'] == 'infrastructure_error' and retries:
            self.check('infrastructure outcome contains no learner grade', 'criteria' not in job['result'])
            time.sleep(5)
            return self.assessment(lab, retries - 1)
        self.check('real worker evaluated ' + lab['scenario'], job['state'] == 'evaluated')
        replay = self.request('/assessments', request)
        self.check('assessment replay preserves result', replay['result'] == job['result'])
        return {c['criterion_id'].removeprefix('criterion.'): c['passed'] for c in job['result']['criteria']}

    def foundations(self):
        lab, ns, client = self.lab('foundations')
        profile = json.loads((ROOT / 'assessment/profiles/foundations-v1.json').read_text())
        self.apply(client, ns, profile['deployment']); self.rollout(client, ns)
        self.oc(client, 'expose', 'deployment', 'web', '-n', ns, '--port=8080', '--target-port=8080')
        self.oc(client, 'create', 'route', 'edge', 'web', '--service=web', '--port=8080', '--insecure-policy=Redirect', '-n', ns)
        self.check('healthy final state without fault history is incomplete', not self.assessment(lab)['repair'])
        self.request('/labs/' + lab['id'] + '/fault-stage', {'subject': 'assessment-live', 'generation': 1})
        stage = self.wait(lambda: self.request('/labs/' + lab['id'] + '/fault-stage?subject=assessment-live&generation=1'),
            lambda x: x['state'] in ('complete', 'infrastructure_error'), 180)
        self.check('trusted initial success and controlled fault observed', stage['state'] == 'complete')
        criteria = self.assessment(lab)
        self.check('old available replica does not prove repair', not criteria['deployment'] and not criteria['repair'])
        self.apply(client, ns, profile['deployment']); self.rollout(client, ns)
        self.check('manifest repair satisfies every criterion', all(self.assessment(lab).values()))
        self.oc(client, 'set', 'image', 'deployment/web', 'web=registry.invalid/syskata/deliberate-rollout-fault:v1', '-n', ns)
        image = profile['deployment']['spec']['template']['spec']['containers'][0]['image']
        self.oc(client, 'set', 'image', 'deployment/web', 'web=' + image, '-n', ns)
        self.rollout(client, ns)
        self.check('alternate imperative repair accepted', all(self.assessment(lab).values()))
        self.processes[1].terminate(); self.processes[1].wait(timeout=15)
        env = {k: v for k, v in self.env.items() if not k.startswith('LAB_') or k.startswith('LAB_DB_') or k.startswith('LAB_ROUTE_')}
        with (self.path.parent / 'worker.log').open('a') as log:
            self.processes[1] = subprocess.Popen([os.environ['JAVA_HOME'] + '/bin/java', '-Dquarkus.profile=assessment', '-jar', 'target/quarkus-app/quarkus-run.jar'], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.check('worker restart preserves trusted stage evidence', all(self.assessment(lab).values()))
        for spec, failed in [({'selector': {'app': 'wrong'}}, 'service'), ({'ports': [{'port': 8080, 'targetPort': 9090}]}, 'service')]:
            self.patch(client, ns, 'service', {'spec': spec})
            self.check('incorrect Service rejected', not self.assessment(lab)[failed])
            self.patch(client, ns, 'service', {'spec': {'selector': {'app': 'web'}, 'ports': [{'port': 8080, 'targetPort': 8080}]}})
        self.check('repaired state remains valid', all(self.assessment(lab).values()))
        self.oc(client, 'delete', 'deployment', 'web', '-n', ns, '--wait=true')
        self.apply(client, ns, profile['deployment']); self.rollout(client, ns)
        self.check('recreated Deployment cannot forge repair history', not self.assessment(lab)['repair'])
        current = self.request('/labs/' + lab['id'] + '?subject=assessment-live')
        self.request('/labs/' + lab['id'] + '/reset', {'key': str(uuid.uuid4()), 'subject': 'assessment-live', 'generation': 1, 'revision': current['revision']})
        current = self.wait(lambda: self.request('/labs/' + lab['id'] + '?subject=assessment-live'), lambda x: x['state'] != 'resetting')
        self.check('reset provisions a clean generation', current['state'] == 'ready' and current['generation'] == 2)
        stage = self.request('/labs/' + lab['id'] + '/fault-stage?subject=assessment-live&generation=2')
        self.check('reset cannot reuse old stage evidence', stage['state'] == 'not_requested')
        try:
            self.request('/labs/' + lab['id'] + '/fault-stage', {'subject': 'assessment-live', 'generation': 1})
            rejected = False
        except urllib.error.HTTPError as error:
            rejected = error.code == 409
        self.check('stale generation request rejected', rejected)

    def administration(self):
        lab, ns, client = self.lab('administration')
        profile = json.loads((ROOT / 'assessment/profiles/administration-v1.json').read_text())
        self.apply(client, ns, profile['deployment']); self.rollout(client, ns)
        role = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': 'reader'},
                'rules': [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'list']}]}
        binding = {'apiVersion': role['apiVersion'], 'kind': 'RoleBinding', 'metadata': {'name': 'reader'},
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'reader'},
            'subjects': [{'kind': 'ServiceAccount', 'name': 'supplied-reader', 'namespace': ns}]}
        quota = {'apiVersion': 'v1', 'kind': 'ResourceQuota', 'metadata': {'name': 'exercise-budget'}, 'spec': {'hard': {'pods': '1'}}}
        for obj in (role, binding, quota): self.apply(client, ns, obj)
        time.sleep(4)
        self.check('administration valid manifest access and quota', all(self.assessment(lab).values()))
        role['rules'] = [{'apiGroups': [''], 'resources': ['pods'], 'verbs': [verb]} for verb in ('get', 'list')]
        self.apply(client, ns, role)
        self.check('alternate split Role accepted', all(self.assessment(lab).values()))
        role['rules'][0]['verbs'].append('delete'); self.apply(client, ns, role)
        self.check('widened permissions rejected', not self.assessment(lab)['access'])
        role['rules'][0]['verbs'].remove('delete'); self.apply(client, ns, role)
        binding['subjects'][0]['name'] = 'default'; self.apply(client, ns, binding)
        self.check('wrong role subject rejected', not self.assessment(lab)['access'])
        binding['subjects'][0]['name'] = 'supplied-reader'; self.apply(client, ns, binding)
        quota['spec']['hard']['pods'] = '2'; self.apply(client, ns, quota)
        self.check('two-pod quota rejected', not self.assessment(lab)['quota'])
        probe = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'assessment-denial-probe', 'namespace': ns},
                 'spec': profile['deployment']['spec']['template']['spec']}
        result = self.oc(self.config['ASSESSMENT_KUBECONFIG'], 'create', '-f', '-', data=probe, check=False)
        self.check('evaluator cannot create persisted probe pods', result.returncode != 0 and 'Evaluator writes are restricted' in result.stderr)
        quota['spec']['hard']['pods'] = '1'; self.apply(client, ns, quota)
        desired = deepcopy(profile['deployment']); desired['spec']['template']['metadata']['labels']['exercise'] = 'wrong'
        self.apply(client, ns, desired)
        self.check('incorrect desired manifest rejected', not self.assessment(lab)['manifest'])
        evaluator = self.config['ASSESSMENT_KUBECONFIG']
        for resource in ('secrets', 'pods/exec', 'serviceaccounts/token'):
            verb = 'get' if resource == 'secrets' else 'create'
            parts = resource.split('/')
            args = ['auth', 'can-i', verb, parts[0], '-n', ns]
            if len(parts) == 2: args.append('--subresource=' + parts[1])
            result = self.oc(evaluator, *args, check=False)
            self.check('evaluator denied ' + resource, result.stdout.strip() == 'no')
        result = self.oc(client, 'auth', 'can-i', 'create', 'localsubjectaccessreviews', '-n', ns, check=False)
        self.check('learner cannot act as evaluator', result.stdout.strip() == 'no')
        terminal = 'sk-term-' + uuid.UUID(lab['id']).hex + '-g1'
        pod = json.loads(self.oc(self.config['LAB_KUBECONFIG'], 'get', 'pod', 'terminal', '-n', terminal, '-o', 'json').stdout)
        spec = pod['spec']
        self.check('terminal has no evaluator credential mount or environment', spec.get('automountServiceAccountToken') is False
            and not spec.get('volumes') and all(not c.get('env') and not c.get('envFrom') for c in spec['containers']))

    def cleanup(self):
        for identifier in self.sessions:
            lab = self.request('/labs/' + identifier + '?subject=assessment-live')
            self.request('/labs/' + identifier + '/close', {'key': str(uuid.uuid4()), 'subject': 'assessment-live',
                'generation': lab['generation'], 'revision': lab['revision']})
            self.wait(lambda: self.request('/labs/' + identifier + '?subject=assessment-live'), lambda x: x['cleanupComplete'], 180)
        self.report['cleanup_passed'] = True

    def run(self):
        try:
            self.start()
            if self.scenario in ('all', 'foundations'): self.foundations()
            if self.scenario in ('all', 'administration'): self.administration()
            self.report['validation_passed'] = True
        finally:
            try: self.cleanup()
            finally:
                for process in self.processes: process.terminate()
                for process in self.processes:
                    try: process.wait(timeout=15)
                    except subprocess.TimeoutExpired: process.kill()
                self.path.write_text(json.dumps(self.report, indent=2) + '\n')
                for path in self.path.parent.glob('*-learner.kubeconfig'): path.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-config', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--scenario', choices=('all', 'foundations', 'administration'), default='all')
    args = parser.parse_args()
    try:
        Validation(json.loads(args.runtime_config.read_text()), args.report, args.scenario).run()
    except Exception:
        print('Live assessment validation stopped; inspect sanitized report and retain protections if cleanup failed.', file=__import__('sys').stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
