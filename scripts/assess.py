#!/usr/bin/env python3
"""Restricted OpenShift adapter; JSON stdin/stdout is bounded trusted worker IPC."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import ssl
import uuid
import evaluator_rbac
from assessment_checks import (contains, controlled_pods, manifest, quota_matches,
                               repaired, route_matches, service_matches, FAULT_IMAGE)


class InfrastructureError(Exception):
    pass


def oc(*args, data=None, check=True):
    result = subprocess.run(['oc', '--request-timeout=15s', *args],
                            input=None if data is None else json.dumps(data),
                            text=True, capture_output=True, timeout=20)
    if len(result.stdout) > 1048576 or (check and result.returncode):
        raise InfrastructureError()
    return result


class Observer:
    def __init__(self, session, generation, scenario):
        self.session = str(uuid.UUID(session))
        if not isinstance(generation, int) or generation < 1:
            raise InfrastructureError()
        self.namespace = f'sk-lab-{uuid.UUID(session).hex}-g{generation}'
        self.foreign = f'sk-term-{uuid.UUID(session).hex}-g{generation}'
        if scenario not in ('foundations', 'administration'):
            raise InfrastructureError()
        self.scenario = scenario
        self.profile = json.loads((Path(__file__).resolve().parents[1] /
                                  f'assessment/profiles/{scenario}-v1.json').read_text())

    def get(self, kind, name=None, selector=None):
        args = ['get', kind, '-n', self.namespace, '-o', 'json']
        if name:
            args += [name, '--ignore-not-found']
        if selector:
            args += ['-l', selector]
        value = oc(*args).stdout
        return json.loads(value) if value.strip() else {}

    def boundary(self):
        username = oc('whoami').stdout.strip()
        if not re.fullmatch(r'system:serviceaccount:[a-z0-9-]+:lab-evaluator', username):
            raise InfrastructureError()
        control = username.split(':')[2]
        for expected in evaluator_rbac.installation(control)['items'][:2]:
            actual = json.loads(oc('get', expected['kind'], expected['metadata']['name'], '-o', 'json').stdout)
            if not contains(actual.get('spec', {}), expected['spec']):
                raise InfrastructureError()
        for name in (self.namespace, self.foreign):
            ns = json.loads(oc('get', 'namespace', name, '-o', 'json').stdout)
            if (ns['metadata'].get('labels', {}).get('syskata.io/lab-session') != self.session
                    or ns.get('status', {}).get('phase') != 'Active'):
                raise InfrastructureError()

    def resources(self):
        deployment = self.get('deployment', 'web')
        pods = controlled_pods(deployment, self.get('replicasets')['items'],
                               self.get('pods')['items'], self.namespace)
        # An approved image failing to pull is environmental, not a learner mistake.
        approved = self.profile['deployment']['spec']['template']['spec']['containers'][0]['image']
        for pod in pods:
            for container in pod.get('status', {}).get('containerStatuses', []):
                if (container.get('image') == approved and container.get('state', {}).get(
                        'waiting', {}).get('reason') in ('ErrImagePull', 'ImagePullBackOff')):
                    raise InfrastructureError()
        return deployment, pods

    def foundations(self, stage):
        deployment, pods = self.resources()
        service = self.get('service', 'web')
        route = self.get('route', 'web')
        domain = os.environ['LAB_ROUTE_DOMAIN']
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+[a-z0-9]', domain):
            raise InfrastructureError()
        host = f'web-{self.namespace}.{domain}'
        checks = {'context': all(x.get('metadata', {}).get('namespace') == self.namespace
                                 for x in (deployment, service, route)),
                  'deployment': manifest(deployment, self.profile['deployment']),
                  'service': service_matches(service, pods, self.get('endpointslices',
                      selector='kubernetes.io/service-name=web')['items'], self.namespace),
                  'route': route_matches(route, host, service)}
        reachable_resources = all(checks.values())
        checks['response'] = False
        if reachable_resources:
            # Never follow redirects to learner-controlled destinations or return response text.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            context = ssl.create_default_context(cafile=os.environ.get('LAB_ROUTE_CA') or None)
            opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context))
            with opener.open(f'https://{host}/', timeout=10) as response:
                body = response.read(16385)
                checks['response'] = (response.status == 200 and len(body) <= 16384
                    and b'Directory listing for /' in body and b'<li>' not in body)
        checks['repair'] = checks['deployment'] and repaired(deployment, stage)
        return checks, deployment

    def access(self, verb, resource, namespace):
        user = f'system:serviceaccount:{self.namespace}:supplied-reader'
        review = {'apiVersion': 'authorization.k8s.io/v1', 'kind': 'LocalSubjectAccessReview',
                  'metadata': {'namespace': namespace}, 'spec': {'user': user,
                  'groups': ['system:serviceaccounts', f'system:serviceaccounts:{self.namespace}',
                             'system:authenticated'],
                  'resourceAttributes': {'namespace': namespace, 'verb': verb, 'resource': resource}}}
        status = json.loads(oc('create', '-f', '-', '-o', 'json', data=review).stdout)['status']
        if status.get('evaluationError') or not isinstance(status.get('allowed'), bool):
            raise InfrastructureError()
        return status['allowed']

    def administration(self):
        deployment, _ = self.resources()
        checks = {'context': deployment.get('metadata', {}).get('namespace') == self.namespace,
                  'manifest': manifest(deployment, self.profile['deployment'])}
        checks['access'] = all([self.access('get', 'pods', self.namespace),
            self.access('list', 'pods', self.namespace),
            not self.access('delete', 'pods', self.namespace),
            not self.access('get', 'secrets', self.namespace),
            not self.access('list', 'secrets', self.namespace),
            not self.access('list', 'pods', self.foreign)])
        checks['quota'] = quota_matches(self.get('resourcequota', 'exercise-budget'))
        if checks['quota']:
            pod = {'apiVersion': 'v1', 'kind': 'Pod',
                   'metadata': {'generateName': 'assessment-quota-', 'namespace': self.namespace},
                   'spec': self.profile['deployment']['spec']['template']['spec']}
            result = oc('create', '--dry-run=server', '-f', '-', '-o', 'json', data=pod, check=False)
            if not result.returncode:
                checks['quota'] = False
            elif 'Forbidden' not in result.stderr or 'exceeded quota: exercise-budget' not in result.stderr:
                raise InfrastructureError()
        return checks

    def stage(self, stage):
        if not stage:
            checks, deployment = self.foundations({})
            if not all(v for k, v in checks.items() if k != 'repair'):
                return {'phase': 'waiting'}
            return {'phase': 'armed', 'uid': deployment['metadata']['uid'],
                    'initialGeneration': deployment['metadata']['generation']}
        if stage['phase'] == 'fault_observed':
            return stage
        deployment, pods = self.resources()
        meta = deployment.get('metadata', {})
        if meta.get('uid') != stage['uid']:
            raise InfrastructureError()
        if meta['generation'] == stage['initialGeneration']:
            patch = [{'op': 'test', 'path': '/metadata/uid', 'value': stage['uid']},
                     {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
                     {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': FAULT_IMAGE}]
            oc('patch', 'deployment', 'web', '-n', self.namespace, '--type=json', '-p', json.dumps(patch))
            return stage  # Persisted intent already exists; re-observe on the next tick.
        if (meta['generation'] != stage['initialGeneration'] + 1
                or deployment['spec']['template']['spec']['containers'][0]['image'] != FAULT_IMAGE):
            raise InfrastructureError()
        failed = any(c.get('image') == FAULT_IMAGE and c.get('state', {}).get('waiting', {}).get(
            'reason') in ('ErrImagePull', 'ImagePullBackOff') for p in pods
            for c in p.get('status', {}).get('containerStatuses', []))
        return {**stage, 'phase': 'fault_observed', 'faultGeneration': meta['generation']} if failed else stage


def execute(request):
    observer = Observer(request['lab'], request['generation'], request['scenario'])
    observer.boundary()
    if request['operation'] == 'stage':
        return {'stage': observer.stage(request.get('stage', {}))}
    checks = (observer.foundations(request.get('stage', {}))[0]
              if observer.scenario == 'foundations' else observer.administration())
    return {'criteria': [{'criterion_id': 'criterion.' + k, 'passed': v} for k, v in checks.items()]}


if __name__ == '__main__':
    try:
        value = sys.stdin.buffer.read(16385)
        if len(value) > 16384:
            raise InfrastructureError()
        result = execute(json.loads(value))
    except Exception:
        result = {'failure_code': 'cluster_unavailable'}
    print(json.dumps(result, separators=(',', ':')))
