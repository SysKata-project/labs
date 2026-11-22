#!/usr/bin/env python3
"""Synthetic HTTPS/OAuth receiver: verify real worker retries an immutable result."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


def run(config, directory, report, openapi):
    if config['LAB_DB_URL'] != 'jdbc:postgresql://127.0.0.1:55432/syskata_labs_assessment_live':
        raise ValueError('isolated synthetic database required')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    java = os.environ['JAVA_HOME'] + '/bin/'
    def sql(statement):
        result = subprocess.run(['psql', '-h', '127.0.0.1', '-p', '55432', '-U', 'syskata', '-d',
            'syskata_labs_assessment_live', '-Atc', statement], capture_output=True, text=True, check=True)
        return result.stdout
    records = json.loads(sql("SELECT coalesce(jsonb_object_agg(id::text,result),'{}') FROM assessments WHERE result IS NOT NULL"))
    if not records: raise ValueError('live results required')
    import yaml
    import jsonschema
    contract = yaml.safe_load(openapi.read_text())
    validator = jsonschema.Draft202012Validator({'$ref': '#/components/schemas/TrustedResult', 'components': contract['components']}, format_checker=jsonschema.FormatChecker())
    for value in records.values(): validator.validate(value)
    sql("UPDATE assessments SET delivered=false,next_delivery=now() WHERE result IS NOT NULL")
    token = secrets.token_urlsafe(32)
    received = {}; errors = []; grants = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def reply(self, status, body):
            data = json.dumps(body).encode(); self.send_response(status)
            self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if size > 16384: self.reply(413, {}); return
            data = self.rfile.read(size)
            if self.path == '/token':
                form = urllib.parse.parse_qs(data.decode())
                valid = (form.get('grant_type') == ['client_credentials']
                    and form.get('client_id') == [config['ASSESSMENT_CLIENT_ID']]
                    and form.get('client_secret') == [config['ASSESSMENT_CLIENT_SECRET']])
                grants.append(valid)
                self.reply(200 if valid else 401, {'access_token': token} if valid else {})
                return
            identifier = self.path.split('/')[-2]
            valid = (self.headers.get('Authorization') == 'Bearer ' + token
                and self.headers.get('Idempotency-Key') == identifier and identifier in records
                and json.loads(data) == records[identifier])
            if not valid:
                errors.append('result_or_auth_mismatch'); self.reply(401, {}); return
            received[identifier] = received.get(identifier, 0) + 1
            # The receiver durably applied the first delivery, but the sender lost its acknowledgment.
            if received[identifier] == 1: self.reply(503, {}); return
            self.reply(200, {'assessment_id': identifier, 'disposition': 'duplicate'})
    key, cert, trust = [directory / name for name in ('key.pem', 'cert.pem', 'trust.p12')]
    worker = None; server = None
    try:
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
            '-out', str(cert), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        key.chmod(0o600)
        subprocess.run([java + 'keytool', '-importcert', '-noprompt', '-alias', 'synthetic-receiver', '-file', str(cert),
            '-keystore', str(trust), '-storepass', 'synthetic-test-only'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        server = ThreadingHTTPServer(('127.0.0.1', 19443), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {k: v for k, v in {**os.environ, **config}.items() if not k.startswith('LAB_') or k.startswith('LAB_DB_') or k.startswith('LAB_ROUTE_')}
        with (directory / 'worker.log').open('w') as log:
            worker = subprocess.Popen([java + 'java', '-Dquarkus.profile=assessment', '-Djavax.net.ssl.trustStore=' + str(trust),
                '-Djavax.net.ssl.trustStorePassword=synthetic-test-only', '-jar', 'target/quarkus-app/quarkus-run.jar'],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        end = time.monotonic() + 90
        remaining = len(records)
        while time.monotonic() < end:
            remaining = int(sql('SELECT count(*) FROM assessments WHERE result IS NOT NULL AND NOT delivered').strip())
            if remaining == 0: break
            # Advance only this synthetic retry fixture's clock after the intentional lost acknowledgment.
            sql("UPDATE assessments SET next_delivery=now() WHERE result IS NOT NULL AND NOT delivered")
            time.sleep(2)
        passed = not errors and grants and all(grants) and remaining == 0 and all(received.get(k, 0) >= 2 for k in records)
        result = {'validation_passed': bool(passed), 'results': len(records), 'openapi_results_validated': len(records), 'authenticated_grants': len(grants),
            'immutable_redelivery_verified': all(received.get(k, 0) >= 2 for k in records), 'all_receipts_persisted': remaining == 0,
            'scope': 'Real HTTPS and worker transport with synthetic OAuth/receiver; not Keycloak/API #5 integration'}
        report.write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        if not passed: raise RuntimeError('delivery validation failed')
    finally:
        if worker:
            worker.terminate()
            try: worker.wait(timeout=15)
            except subprocess.TimeoutExpired: worker.kill()
        if server: server.shutdown(); server.server_close()
        for path in (key, cert, trust): path.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-config', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--openapi', type=Path, required=True)
    args = parser.parse_args()
    try: run(json.loads(args.runtime_config.read_text()), args.directory, args.report, args.openapi)
    except Exception:
        print('Synthetic delivery verification failed; no secrets emitted.', file=__import__('sys').stderr)
        raise SystemExit(1)
