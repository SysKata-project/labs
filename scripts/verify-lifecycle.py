#!/usr/bin/env python3
"""Opt-in live lifecycle validation. Requires an isolated database and approved cluster setup."""
import argparse
import base64
import datetime
import hashlib
import json
import secrets
import subprocess
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class Validation:
    def __init__(self, runtime, report, runtime_path, service_control):
        if runtime.get('LAB_DB_URL') != 'jdbc:postgresql://127.0.0.1:55433/syskata_labs':
            raise ValueError('Only the isolated fixture database is supported')
        self.runtime = runtime
        self.runtime_path = runtime_path
        self.service_control = service_control
        self.report = report
        self.labs = []
        self.results = {'checks': [], 'terminal_image': runtime['LAB_TERMINAL_IMAGE']}
        self.base = 'http://127.0.0.1:8089/internal'

    def check(self, name, passed):
        self.results['checks'].append({'check': name, 'passed': bool(passed)})
        self.save()
        print(('PASS ' if passed else 'FAIL ') + name, flush=True)
        if not passed:
            raise RuntimeError(name)

    def save(self):
        self.report.write_text(json.dumps(self.results, indent=2) + '\n')

    def request(self, path, data=None, expected=200):
        req = urllib.request.Request(self.base + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={'Authorization': 'Bearer ' + self.runtime['LAB_INTERNAL_TOKEN'], 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as response:
            status, body = response.code, response.read()
        if status != expected:
            raise RuntimeError('unexpected HTTP status ' + str(status))
        return json.loads(body) if body else None

    def get(self, lab):
        return self.request('/labs/' + lab['id'] + '?subject=' + urllib.parse.quote(lab['subject']))

    def wait(self, lab, state, seconds=240):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            current = self.get(lab)
            if current['state'] == state:
                return current
            if current['state'] == 'infrastructure_error' and state == 'ready':
                raise RuntimeError('lab provisioning infrastructure error')
            time.sleep(1)
        raise RuntimeError('lab transition timed out')

    def create(self, scenario):
        payload = {'key': str(uuid.uuid4()), 'id': str(uuid.uuid4()),
                   'subject': 'validation-' + uuid.uuid4().hex, 'scenario': scenario}
        lab = self.request('/labs', payload)
        self.labs.append(lab)
        self.check('idempotent provision ' + scenario, self.request('/labs', payload) == lab)
        lab = self.wait(lab, 'ready')
        self.check('ready ' + scenario, lab['generation'] == 1)
        return lab

    def ticket(self, lab):
        ticket = secrets.token_urlsafe(32)
        expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=45)).isoformat()
        self.request('/labs/' + lab['id'] + '/tickets', {'subject': lab['subject'],
            'generation': lab['generation'], 'hash': hashlib.sha256(ticket.encode()).hexdigest(), 'expiresAt': expires}, 204)
        return {'type': 'auth', 'subject': lab['subject'], 'generation': lab['generation'], 'ticket': ticket}

    def connect(self, lab, auth=None):
        import websocket
        ws = websocket.create_connection('ws://127.0.0.1:8089/terminal/' + lab['id'], timeout=10)
        ws.send(json.dumps(auth or self.ticket(lab)))
        return ws

    def command(self, ws, command):
        marker = 'CHECK_' + uuid.uuid4().hex
        # Disable shell echo before sending the actual probe, and wait for its result marker.
        data = command + '; printf "\\n' + marker + ':%s\\n" "$?"\n'
        ws.send(json.dumps({'type': 'stdin', 'data': base64.b64encode(data.encode()).decode()}))
        output = ''
        until = time.monotonic() + 30
        while time.monotonic() < until:
            frame = ws.recv()
            if not frame:
                raise RuntimeError('terminal closed during probe')
            obj = json.loads(frame)
            output += base64.b64decode(obj['data']).decode(errors='replace')
            # The marker echoed as command text is followed by :%s, never :0.
            if marker + ':0' in output:
                return True
            for code in range(1, 256):
                if marker + ':' + str(code) + '\r' in output or marker + ':' + str(code) + '\n' in output:
                    return False
        raise RuntimeError('terminal probe timed out')

    def closed(self, ws):
        import websocket
        try:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                if not ws.recv():
                    return True
        except websocket.WebSocketConnectionClosedException:
            return True
        return False

    def apply_probe(self, ws, obj, denied_marker=None):
        encoded = base64.b64encode(json.dumps(obj).encode()).decode()
        command = 'printf %s ' + encoded + ' | base64 -d | oc create -f - >/tmp/syskata-probe 2>&1'
        if denied_marker:
            command += '; rc=$?; test "$rc" -ne 0 && grep -Eq ' + repr(denied_marker) + ' /tmp/syskata-probe'
        return self.command(ws, command)

    def boundary_probes(self, ws, lab):
        namespace = 'sk-lab-' + lab['id'].replace('-', '') + '-g' + str(lab['generation'])
        service = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'web'},
            'spec': {'selector': {'app': 'web'}, 'ports': [{'port': 8080, 'targetPort': 8080}]}}
        external = json.loads(json.dumps(service)); external['spec']['type'] = 'NodePort'
        self.check('NodePort rejected by admission', self.apply_probe(ws, external, 'Only internal ClusterIP'))
        external = json.loads(json.dumps(service)); external['spec']['externalIPs'] = ['192.0.2.2']
        self.check('external IP rejected by admission', self.apply_probe(ws, external, 'Only internal ClusterIP|externalIPs have been disabled'))
        self.check('ClusterIP service accepted', self.apply_probe(ws, service))
        route = {'apiVersion': 'route.openshift.io/v1', 'kind': 'Route', 'metadata': {'name': 'web'},
            'spec': {'to': {'kind': 'Service', 'name': 'web'}, 'port': {'targetPort': 8080},
                'tls': {'termination': 'edge', 'insecureEdgeTerminationPolicy': 'Redirect'}}}
        foreign = json.loads(json.dumps(route)); foreign['spec']['host'] = 'foreign.' + self.runtime['LAB_ROUTE_DOMAIN']
        self.check('foreign route host rejected', self.apply_probe(ws, foreign, 'Only the assigned HTTPS|permission to set the host'))
        self.check('assigned HTTPS route accepted', self.apply_probe(ws, route))
        pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'token-escape'}, 'spec': {
            'automountServiceAccountToken': True,
            'containers': [{'name': 'probe', 'image': self.runtime['LAB_TERMINAL_IMAGE'],
                'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'}, 'limits': {'cpu': '50m', 'memory': '64Mi'}},
                'securityContext': {'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}}]}}
        self.check('automatic pod token rejected', self.apply_probe(ws, pod, 'automatic service account tokens'))
        pod['spec']['automountServiceAccountToken'] = False
        pod['spec']['volumes'] = [{'name': 'credential', 'secret': {'secretName': 'probe-does-not-exist'}}]
        self.check('indirect Secret volume rejected', self.apply_probe(ws, pod, 'secret and external volume sources'))
        del pod['spec']['volumes']
        pod['spec']['containers'][0]['env'] = [{'name': 'PROBE', 'valueFrom': {
            'secretKeyRef': {'name': 'probe-does-not-exist', 'key': 'value'}}}]
        self.check('indirect Secret environment rejected', self.apply_probe(ws, pod, 'secret environment references'))

        self.check('learner cannot change outer quota', self.command(ws, 'test "$(oc auth can-i patch resourcequotas/outer-budget)" = no'))
        for index in range(1, 4):
            service['metadata']['name'] = 'quota-' + str(index)
            self.check('bounded service allocation ' + str(index), self.apply_probe(ws, service))
        service['metadata']['name'] = 'quota-overflow'
        self.check('service object-count quota enforced', self.apply_probe(ws, service, 'exceeded quota'))


    def generation_credential(self, lab):
        namespace = 'sk-lab-' + lab['id'].replace('-', '') + '-g' + str(lab['generation'])
        env = {**os.environ, 'KUBECONFIG': self.runtime['LAB_KUBECONFIG']}
        issue = subprocess.run(['oc', 'create', 'token', 'learner', '-n', namespace, '--duration=10m'],
                               env=env, capture_output=True, text=True, check=True)
        view = subprocess.run(['oc', 'config', 'view', '--minify', '--raw', '--flatten', '-o', 'json'],
                              env=env, capture_output=True, text=True, check=True)
        cfg = json.loads(view.stdout)
        cfg['users'] = [{'name': 'scoped', 'user': {'token': issue.stdout.strip()}}]
        cfg['contexts'][0]['context'].update({'user': 'scoped', 'namespace': namespace})
        path = self.runtime_path.parent / ('generation-' + lab['id'] + '.kubeconfig')
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(cfg, output)
        return path

    def credential_revoked(self, path):
        result = subprocess.run(['oc', '--kubeconfig', str(path), '--request-timeout=10s', 'get', 'pods'],
                                capture_output=True, text=True)
        return result.returncode != 0 and ('Unauthorized' in result.stderr or 'logged in' in result.stderr
                                           or 'provide credentials' in result.stderr)

    def fixture_clock(self, lab, field):
        # Deliberately scoped to this harness's loopback synthetic fixture database.
        if self.runtime['LAB_DB_URL'] != 'jdbc:postgresql://127.0.0.1:55433/syskata_labs':
            raise RuntimeError('refusing non-fixture database')
        identifier = str(uuid.UUID(lab['id']))
        assignment = {'lease': "expires_at=now()-interval '1 second'",
                      'idle': "last_activity=now()-interval '601 seconds'"}[field]
        sql = "UPDATE lab_sessions SET " + assignment + " WHERE id='" + identifier + "' AND subject LIKE 'validation-%'"
        result = subprocess.run(['podman', 'exec', 'syskata-labs-test-db', 'psql', '-U', 'syskata',
            '-d', 'syskata_labs', '-v', 'ON_ERROR_STOP=1', '-c', sql], capture_output=True)
        if result.returncode:
            raise RuntimeError('fixture clock update failed')

    def restart(self):
        for action in ['stop', 'start']:
            subprocess.run(['python3', str(self.service_control), action], check=True, capture_output=True,
                env={**os.environ, 'SYSKATA_LIVE_RUNTIME_CONFIG': str(self.runtime_path)})
            if action == 'stop':
                time.sleep(2)

    def failure_recovery(self):
        # A temporary adapter uses the real restricted controller to create one namespace,
        # then injects provisioning and cleanup failures. No mock cluster is involved.
        wrapper = self.runtime_path.parent / 'partial-failure.py'
        source = Path(self.runtime['LAB_CLUSTER_HELPER'])
        wrapper.write_text("import importlib.util,sys\n"
            + "s=importlib.util.spec_from_file_location('cluster'," + repr(str(source)) + ");m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            + "if sys.argv[1]=='provision':\n m.verify_policy();m.namespace(m.names(sys.argv[2],sys.argv[3])[0],sys.argv[2],True)\n"
            + "sys.exit(1)\n")
        changed = {**self.runtime, 'LAB_CLUSTER_HELPER': str(wrapper)}
        self.runtime_path.write_text(json.dumps(changed))
        try:
            self.restart()
            payload = {'key': str(uuid.uuid4()), 'id': str(uuid.uuid4()),
                       'subject': 'validation-' + uuid.uuid4().hex, 'scenario': 'foundations'}
            lab = self.request('/labs', payload); self.labs.append(lab)
            failed = self.wait(lab, 'infrastructure_error')
            self.check('partial provisioning failure is infrastructure state', not failed['cleanupComplete'])
            time.sleep(3)
            self.check('failed cleanup remains outstanding', not self.get(lab)['cleanupComplete'])
        finally:
            self.runtime_path.write_text(json.dumps(self.runtime))
            self.restart()
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline and not self.get(lab)['cleanupComplete']:
            time.sleep(1)
        self.check('restart reconciles partial resources and failed cleanup', self.get(lab)['cleanupComplete'])

    def account_disable(self):
        lab = self.create('administration')
        ws = self.connect(lab)
        self.check('deletion fixture terminal ready', self.command(ws, 'oc get pods >/dev/null'))
        self.boundary_probes(ws, lab)
        self.request('/accounts/disable', {'subject': lab['subject']}, 204)
        self.check('account disable disconnects terminal', self.closed(ws)); ws.close()
        self.request('/labs/' + lab['id'] + '?subject=' + lab['subject'], expected=403)
        self.check('disabled account cannot resume', True)
        identifier = str(uuid.UUID(lab['id']))
        sql = "SELECT cleanup_complete FROM lab_sessions WHERE id='" + identifier + "'"
        deadline = time.monotonic() + 150
        cleaned = False
        while time.monotonic() < deadline:
            result = subprocess.run(['podman', 'exec', 'syskata-labs-test-db', 'psql', '-U', 'syskata',
                '-d', 'syskata_labs', '-Atc', sql], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == 't':
                cleaned = True
                break
            time.sleep(2)
        self.check('disabled account resources cleaned', cleaned)
        self.labs = [item for item in self.labs if item['id'] != lab['id']]

    def execute(self):
        first = self.create('foundations')
        second = self.create('administration')
        self.request('/labs', {'key': str(uuid.uuid4()), 'id': str(uuid.uuid4()),
            'subject': 'validation-over-capacity', 'scenario': 'foundations'}, 429)
        self.check('two-session capacity enforced', True)
        self.request('/labs/' + first['id'] + '?subject=' + second['subject'], expected=404)
        self.check('cross-learner HTTP ownership', True)
        old_credential = self.generation_credential(first)
        auth = self.ticket(first)
        ws = self.connect(first, auth)
        self.check('real oc shell', self.command(ws, 'oc whoami >/dev/null'))
        self.check('no mounted controller token', self.command(ws, 'test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token'))
        other = 'sk-lab-' + second['id'].replace('-', '') + '-g1'
        self.check('cross-learner API denial from terminal', self.command(ws, 'test "$(oc auth can-i get pods -n ' + other + ')" = no'))
        self.check('terminal cannot read secrets', self.command(ws, 'test "$(oc auth can-i get secrets)" = no'))
        self.boundary_probes(ws, first)
        replay = self.connect(first, auth)
        self.check('ticket replay rejected', self.closed(replay)); replay.close()
        ws.close()
        ws = self.connect(first)
        self.check('reconnect with fresh ticket', self.command(ws, 'oc get pods >/dev/null'))
        payload = {'key': str(uuid.uuid4()), 'subject': first['subject'], 'revision': first['revision'], 'generation': 1}
        reset = self.request('/labs/' + first['id'] + '/reset', payload)
        self.check('reset preserves lease', reset['generation'] == 2 and reset['expiresAt'] == first['expiresAt'])
        self.check('reset replay', self.request('/labs/' + first['id'] + '/reset', payload) == reset)
        self.check('reset disconnects old terminal', self.closed(ws)); ws.close()
        reset = self.wait(first, 'ready')
        self.check('reset revokes old Kubernetes credential', self.credential_revoked(old_credential))
        old_credential.unlink()
        ws = self.connect(reset)
        self.check('new-generation terminal', self.command(ws, 'oc get pods >/dev/null'))
        self.fixture_clock(reset, 'lease')
        self.check('expired lease disconnects real terminal', self.closed(ws)); ws.close()
        self.wait(reset, 'expired')
        self.request('/labs/' + reset['id'] + '/tickets', {'subject': reset['subject'],
            'generation': reset['generation'], 'hash': '0' * 64,
            'expiresAt': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)).isoformat()}, 410)
        self.check('expired reconnect denied', True)
        ws = self.connect(second)
        self.check('administration terminal ready', self.command(ws, 'oc get pods >/dev/null'))
        self.fixture_clock(second, 'idle')
        self.check('idle limit disconnects real terminal', self.closed(ws)); ws.close()
        self.wait(second, 'expired')
        self.check('expired labs cleaned', self.cleanup())
        self.failure_recovery()
        self.account_disable()

    def cleanup(self):
        success = True
        for lab in self.labs:
            try:
                current = self.get(lab)
                self.request('/labs/' + lab['id'] + '/close', {'key': str(uuid.uuid4()),
                    'subject': lab['subject'], 'revision': current['revision'], 'generation': current['generation']})
                closed = self.wait(lab, 'closed')
                success = success and closed['cleanupComplete']
            except Exception:
                success = False
        self.results['cleanup_passed'] = success
        self.save()
        return success


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-config', type=Path, required=True)
    parser.add_argument('--feasibility-report', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--service-control', type=Path, required=True)
    parser.add_argument('--account-disable-only', action='store_true')
    args = parser.parse_args()
    baseline = json.loads(args.feasibility_report.read_text())
    if not baseline.get('validation_passed') or not baseline.get('cleanup_passed'):
        raise SystemExit('Feasibility and its cleanup must pass before lifecycle sessions start')
    validation = Validation(json.loads(args.runtime_config.read_text()), args.report, args.runtime_config, args.service_control)
    try:
        if args.account_disable_only:
            validation.account_disable()
        else:
            validation.execute()
        validation.results['validation_passed'] = True
    except Exception as failure:
        validation.results['validation_passed'] = False
        validation.results['failure_type'] = type(failure).__name__
        validation.save()
        print('Live validation failed; no terminal output or credentials were recorded.', flush=True)
    finally:
        cleanup = validation.cleanup()
    raise SystemExit(0 if cleanup and validation.results['validation_passed'] else 1)


if __name__ == '__main__':
    main()
