#!/usr/bin/env python3
"""Lab cluster boundary. Never emit credentials, commands, transcripts or raw errors."""
import base64
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

spec = importlib.util.spec_from_file_location('feasibility', Path(__file__).with_name('validate-openshift.py'))
feasibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feasibility)
LABEL = 'syskata.io/lab-session'
MANAGED = 'syskata.io/lab-managed'
POLICY = 'syskata-labs-boundary-v1'


def oc(*args, data=None, timeout=180, check=True):
    result = subprocess.run(['oc', '--request-timeout=30s', *args],
                            input=None if data is None else json.dumps(data),
                            text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('cluster operation failed')
    return result


def resource(kind, name, spec=None, api='v1', **fields):
    return feasibility.resource(kind, name, spec, api, **fields)


def names(session, generation):
    identifier = uuid.UUID(session).hex
    generation = int(generation)
    if generation < 1 or generation > 2147483647:
        raise ValueError('invalid generation')
    return f'sk-lab-{identifier}-g{generation}', f'sk-term-{identifier}-g{generation}'


def policy_documents(domain):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+[a-z0-9]', domain):
        raise ValueError('invalid route domain')
    docs = feasibility.admission_resources(POLICY)
    for doc in docs:
        doc['metadata']['labels'] = {'syskata.io/component': 'lab-boundary'}
    policy, binding = docs
    selector = {'matchLabels': {MANAGED: 'true'}}
    policy['spec']['matchConstraints']['namespaceSelector'] = selector
    binding['spec']['matchResources']['namespaceSelector'] = selector
    policy['spec']['matchConstraints']['resourceRules'] += [
        {'apiGroups': [''], 'apiVersions': ['v1'], 'operations': ['CREATE', 'UPDATE'], 'resources': ['services']},
        {'apiGroups': ['route.openshift.io'], 'apiVersions': ['v1'], 'operations': ['CREATE', 'UPDATE'], 'resources': ['routes']}]
    for validation in policy['spec']['validations']:
        validation['expression'] = "object.kind != 'Pod' || (" + validation['expression'] + ')'
    policy['spec']['validations'] += [
        {'expression': "object.kind != 'Service' || ((!has(object.spec.type) || object.spec.type == 'ClusterIP') && (!has(object.spec.externalIPs) || size(object.spec.externalIPs) == 0) && (!has(object.spec.externalName) || object.spec.externalName == ''))",
         'message': 'Only internal ClusterIP services are allowed'},
        {'expression': "object.kind != 'Route' || (object.metadata.name == 'web' && object.spec.host == 'web-' + object.metadata.namespace + '." + domain + "' && object.spec.to.kind == 'Service' && object.spec.to.name == 'web' && (!has(object.spec.alternateBackends) || size(object.spec.alternateBackends) == 0) && (!has(object.spec.wildcardPolicy) || object.spec.wildcardPolicy == 'None') && has(object.spec.tls) && object.spec.tls.termination == 'edge' && object.spec.tls.insecureEdgeTerminationPolicy == 'Redirect' && !has(object.spec.tls.key) && !has(object.spec.tls.certificate) && (!has(object.spec.path) || object.spec.path == ''))",
         'message': 'Only the assigned HTTPS web route is allowed'}]
    policy['spec']['validations'] += [
        {'expression': "object.kind != 'Pod' || (!has(object.spec.volumes) || object.spec.volumes.all(v, has(v.emptyDir) || has(v.configMap) || has(v.downwardAPI)))",
         'message': 'SysKata: secret and external volume sources are forbidden'},
        {'expression': "object.kind != 'Pod' || (object.spec.containers + (has(object.spec.initContainers) ? object.spec.initContainers : []) + (has(object.spec.ephemeralContainers) ? object.spec.ephemeralContainers : [])).all(c, (!has(c.env) || c.env.all(e, !has(e.valueFrom) || !has(e.valueFrom.secretKeyRef))) && (!has(c.envFrom) || c.envFrom.all(e, !has(e.secretRef))))",
         'message': 'SysKata: secret environment references are forbidden'}]
    # Mixed resource kinds require dynamic spec access; kind guards select each schema.
    for validation in policy['spec']['validations']:
        validation['expression'] = validation['expression'].replace('object.spec', 'dyn(object).spec')
    return docs


def verify_policy():
    username = oc('whoami').stdout.strip()
    match = re.fullmatch(r'system:serviceaccount:([a-z0-9-]+):lab-controller', username)
    if not match:
        raise RuntimeError('a dedicated controller identity is required')
    controller_spec = importlib.util.spec_from_file_location('controller', Path(__file__).with_name('controller-rbac.py'))
    controller = importlib.util.module_from_spec(controller_spec)
    controller_spec.loader.exec_module(controller)
    controller_docs = controller.render(match.group(1))['items'][:2]
    expected = policy_documents(os.environ['LAB_ROUTE_DOMAIN']) + controller_docs
    for doc in expected:
        actual = json.loads(oc('get', doc['kind'], doc['metadata']['name'], '-o', 'json').stdout)
        if not contains(actual['spec'], doc['spec']):
            raise RuntimeError('admission boundary mismatch')


def contains(actual, expected):
    """Allow server-added defaults, but require every configured boundary field."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and contains(actual[key], value)
                                                for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            contains(a, b) for a, b in zip(actual, expected))
    return actual == expected


def namespace(name, session, learner):
    existing = oc('get', 'namespace', name, '--ignore-not-found', '-o', 'json').stdout
    if existing:
        obj = json.loads(existing)
        if obj['metadata'].get('labels', {}).get(LABEL) != session or obj.get('status', {}).get('phase') == 'Terminating':
            raise RuntimeError('namespace ownership or state mismatch')
        return
    obj = resource('Namespace', name)
    obj['metadata']['labels'] = {LABEL: session, MANAGED: 'true' if learner else 'false'}
    oc('create', '-f', '-', data=obj)


def apply(obj, namespace):
    oc('apply', '-n', namespace, '-f', '-', data=obj)


def provision(session, generation, scenario):
    learner, terminal = names(session, generation)
    image = os.environ['LAB_TERMINAL_IMAGE']
    if not re.fullmatch(r'[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}', image):
        raise ValueError('terminal image must be pinned by digest')
    api_cidr = str(ipaddress.ip_network(os.environ['LAB_API_CIDR'], strict=True))
    verify_policy()
    namespace(learner, session, True)
    namespace(terminal, session, False)
    quota = {'requests.cpu': '500m', 'requests.memory': '512Mi', 'limits.cpu': '1',
             'limits.memory': '1Gi', 'pods': '4', 'count/services': '4',
             'count/deployments.apps': '4', 'count/replicasets.apps': '12',
             'count/routes.route.openshift.io': '1', 'count/roles.rbac.authorization.k8s.io': '8',
             'count/rolebindings.rbac.authorization.k8s.io': '8', 'count/resourcequotas': '2',
             'count/configmaps': '4', 'count/secrets': '4'}
    apply(resource('ResourceQuota', 'outer-budget', {'hard': quota}), learner)
    for policy in feasibility.policies():
        apply(policy, learner)
    for account in ['learner', 'supplied-reader']:
        apply(resource('ServiceAccount', account, automountServiceAccountToken=False), learner)
    role = resource('Role', 'learner', api='rbac.authorization.k8s.io/v1', rules=feasibility.learner_rules())
    apply(role, learner)
    apply(resource('RoleBinding', 'learner', api='rbac.authorization.k8s.io/v1',
                   roleRef={'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'learner'},
                   subjects=[{'kind': 'ServiceAccount', 'name': 'learner', 'namespace': learner}]), learner)
    apply(resource('ResourceQuota', 'terminal-budget', {'hard': {
        'pods': '1', 'requests.cpu': '50m', 'requests.memory': '128Mi',
        'limits.cpu': '250m', 'limits.memory': '256Mi'}}), terminal)
    apply(resource('NetworkPolicy', 'terminal-boundary', {
        'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [],
        'egress': [{'to': [{'ipBlock': {'cidr': api_cidr}}], 'ports': [{'protocol': 'TCP', 'port': 6443}]},
                   {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'openshift-dns'}}}],
                    'ports': [{'protocol': protocol, 'port': port} for protocol in ['TCP', 'UDP'] for port in [53, 5353]]}]
    }, 'networking.k8s.io/v1'), terminal)
    pod = resource('Pod', 'terminal', {
        'automountServiceAccountToken': False,
        'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'terminal', 'image': image,
            'command': ['/bin/sh', '-c', 'exec sleep infinity'],
            'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'}, 'limits': {'cpu': '250m', 'memory': '256Mi'}}}]})
    apply(pod, terminal)
    oc('wait', '-n', terminal, 'pod/terminal', '--for=condition=Ready', '--timeout=120s')
    oc('exec', '-n', terminal, 'terminal', '--', 'oc', 'version', '--client')
    # OpenShift commonly uses split DNS. Check the pod-visible API, not workstation DNS.
    server = oc('config', 'view', '--minify', '-o', 'jsonpath={.clusters[0].cluster.server}').stdout
    endpoint = urlparse(server)
    if endpoint.scheme != 'https' or not endpoint.hostname:
        raise ValueError('HTTPS API endpoint required')
    oc('exec', '-n', terminal, 'terminal', '--', 'timeout', '10', 'bash', '-c',
       'exec 3<>/dev/tcp/"$1"/"$2"', '--', endpoint.hostname, str(endpoint.port or 443))


def close(session, generation):
    learner, terminal_name = names(session, generation)
    owned = []
    for name in [terminal_name, learner]:
        existing = oc('get', 'namespace', name, '--ignore-not-found', '-o', 'json').stdout
        if not existing:
            continue
        if json.loads(existing)['metadata'].get('labels', {}).get(LABEL) != session:
            raise RuntimeError('refusing unowned cleanup')
        owned.append(name)
    if learner in owned:
        # Revoke the credential identity before waiting for workload termination.
        oc('delete', 'serviceaccount', 'learner', '-n', learner, '--ignore-not-found', '--wait=false')
    for name in owned:
        oc('delete', 'namespace', name, '--wait=false')
    deadline = time.monotonic() + 120
    while owned:
        owned = [name for name in owned
                 if oc('get', 'namespace', name, '--ignore-not-found', '-o', 'json').stdout]
        if owned:
            if time.monotonic() >= deadline:
                raise RuntimeError('cleanup not confirmed')
            time.sleep(2)


def terminal(session, generation):
    learner, target = names(session, generation)
    for name in [learner, target]:
        obj = json.loads(oc('get', 'namespace', name, '-o', 'json').stdout)
        if obj['metadata'].get('labels', {}).get(LABEL) != session:
            raise RuntimeError('invalid owner')
    token = json.loads(oc('create', '--raw', f'/api/v1/namespaces/{learner}/serviceaccounts/learner/token',
        '-f', '-', data={'apiVersion': 'authentication.k8s.io/v1', 'kind': 'TokenRequest',
                        'spec': {'expirationSeconds': 1800}}).stdout)['status']['token']
    config = json.loads(oc('config', 'view', '--minify', '--raw', '--flatten', '-o', 'json').stdout)
    config['users'] = [{'name': 'learner', 'user': {'token': token}}]
    config['contexts'][0]['context'].update({'user': 'learner', 'namespace': learner})
    payload = base64.b64encode(json.dumps(config).encode()) + b'\n'
    # No credentials in argv, environment, files on the gateway host, or output.
    command = 'umask 077; d=$(mktemp -d); trap \'rm -rf "$d"\' EXIT; read -r cfg; printf %s "$cfg" | base64 -d > "$d/config"; unset cfg; export KUBECONFIG="$d/config"; /bin/bash -i'
    process = subprocess.Popen(['oc', '--request-timeout=0', 'exec', '-i', '-n', target, 'terminal', '--', '/bin/sh', '-c', command],
                               stdin=subprocess.PIPE, stderr=subprocess.STDOUT)
    def terminate(signum, frame):
        process.terminate()
        raise SystemExit(1)
    signal.signal(signal.SIGTERM, terminate)
    try:
        process.stdin.write(payload)
        process.stdin.flush()
        while True:
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                break
            process.stdin.write(chunk)
            process.stdin.flush()
    finally:
        process.stdin.close()
        process.terminate()
        process.wait(timeout=10)


def main():
    action = sys.argv[1]
    if action == 'policy':
        print(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': policy_documents(os.environ['LAB_ROUTE_DOMAIN'])}))
        return
    session, generation, scenario = sys.argv[2:5]
    names(session, generation)
    if scenario not in ('foundations', 'administration'):
        raise ValueError('unknown scenario')
    if action == 'provision':
        provision(session, generation, scenario)
    elif action == 'close':
        close(session, generation)
    elif action == 'terminal':
        terminal(session, generation)
    else:
        raise ValueError('invalid action')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        sys.exit(1)
