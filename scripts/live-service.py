#!/usr/bin/env python3
"""Start/stop only the isolated loopback service used by verify-lifecycle.py."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid


def main():
    runtime_path = Path(os.environ['SYSKATA_LIVE_RUNTIME_CONFIG']).resolve()
    runtime = json.loads(runtime_path.read_text())
    if runtime.get('LAB_DB_URL') != 'jdbc:postgresql://127.0.0.1:55433/syskata_labs':
        raise SystemExit('Only the isolated fixture database is supported')
    if runtime.get('QUARKUS_HTTP_HOST') != '127.0.0.1' or runtime.get('QUARKUS_HTTP_PORT') != '8089':
        raise SystemExit('Only loopback port 8089 is supported')
    state = runtime_path.parent / 'live-service-state.json'
    if sys.argv[1] == 'start':
        with socket.socket() as probe:
            probe.settimeout(1)
            if probe.connect_ex(('127.0.0.1', 8089)) == 0:
                raise SystemExit('Refusing to replace a service already listening on port 8089')
        root = Path(__file__).resolve().parents[1]
        java = Path(os.environ['JAVA_HOME']) / 'bin/java'
        marker = '-Dsyskata.validation-run=' + uuid.uuid4().hex
        with (runtime_path.parent / 'live-service.log').open('ab') as log:
            process = subprocess.Popen([str(java), marker, '-jar', str(root / 'target/quarkus-app/quarkus-run.jar')],
                cwd=root, env={**os.environ, **runtime}, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        state.write_text(json.dumps({'pid': process.pid, 'marker': marker}))
        for _ in range(60):
            if process.poll() is not None:
                raise SystemExit('Fixture service exited during startup')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8089/q/health/ready', timeout=2) as response:
                    if response.status == 200:
                        print('Fixture lab service ready')
                        return
            except Exception:
                time.sleep(1)
        raise SystemExit('Fixture service readiness timed out')
    elif sys.argv[1] == 'stop':
        if not state.exists():
            return
        owned = json.loads(state.read_text())
        probe = subprocess.run(['ps', '-p', str(owned['pid']), '-o', 'args='], capture_output=True, text=True)
        if probe.returncode == 0:
            if owned['marker'] not in probe.stdout:
                raise SystemExit('Refusing to stop an unrelated process')
            os.kill(owned['pid'], signal.SIGTERM)
        state.unlink()
        print('Fixture lab service stopped')
    else:
        raise SystemExit('Expected start or stop')


if __name__ == '__main__':
    main()
