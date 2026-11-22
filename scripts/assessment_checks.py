"""Version-one outcome predicates. Inputs are trusted observations, never learner JSON."""
from decimal import Decimal

FAULT_IMAGE = 'registry.invalid/syskata/deliberate-rollout-fault:v1'


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v)
                                                for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            contains(a, b) for a, b in zip(actual, expected))
    return actual == expected


def current(deployment):
    status = deployment.get('status', {})
    return (deployment.get('spec', {}).get('replicas') == 1
            and status.get('observedGeneration', 0) >= deployment['metadata']['generation']
            and all(status.get(k, 0) == 1 for k in ('updatedReplicas', 'readyReplicas', 'replicas'))
            and status.get('unavailableReplicas', 0) == 0)


def quantity(value):
    value = str(value)
    for suffix, multiplier in [('Gi', 1024**3), ('Mi', 1024**2), ('Ki', 1024),
                               ('G', 10**9), ('M', 10**6), ('k', 1000), ('m', Decimal('.001'))]:
        if value.endswith(suffix):
            return Decimal(value[:-len(suffix)]) * multiplier
    return Decimal(value)


def manifest(deployment, desired):
    if not deployment:
        return False
    from copy import deepcopy
    actual, expected = deepcopy(deployment['spec']), deepcopy(desired['spec'])
    try:
        for value in (actual, expected):
            for container in value['template']['spec']['containers']:
                for limits in container.get('resources', {}).values():
                    for key in limits:
                        limits[key] = quantity(limits[key])
        return contains(actual, expected) and current(deployment)
    except (KeyError, ValueError, ArithmeticError):
        return False


def controlled_pods(deployment, replicasets, pods, namespace):
    """Follow controller UIDs, not labels alone, to the current Deployment pods."""
    uid = deployment.get('metadata', {}).get('uid')
    sets = {r['metadata']['uid'] for r in replicasets
            if r['metadata'].get('namespace') == namespace and any(
                o.get('uid') == uid and o.get('kind') == 'Deployment' and o.get('controller')
                for o in r['metadata'].get('ownerReferences', []))}
    return [p for p in pods if p['metadata'].get('namespace') == namespace and any(
        o.get('uid') in sets and o.get('kind') == 'ReplicaSet' and o.get('controller')
        for o in p['metadata'].get('ownerReferences', []))]


def service_matches(service, pods, slices, namespace):
    if not service or not pods:
        return False
    spec = service.get('spec', {})
    selector = spec.get('selector', {})
    if (spec.get('type') != 'ClusterIP' or spec.get('externalIPs') or not selector
            or not any(p.get('port') == 8080 and p.get('targetPort') == 8080
                       and p.get('protocol', 'TCP') == 'TCP' for p in spec.get('ports', []))):
        return False
    ready = {p['metadata']['uid']: p.get('status', {}).get('podIP') for p in pods
             if contains(p['metadata'].get('labels', {}), selector) and any(
                 c.get('type') == 'Ready' and c.get('status') == 'True'
                 for c in p.get('status', {}).get('conditions', []))}
    endpoints = []
    for item in slices:
        if item['metadata'].get('namespace') != namespace:
            return False
        if not any(o.get('uid') == service['metadata']['uid'] and o.get('kind') == 'Service'
                   and o.get('controller') for o in item['metadata'].get('ownerReferences', [])):
            return False
        if not any(p.get('port') == 8080 and p.get('protocol', 'TCP') == 'TCP'
                   for p in (item.get('ports') or [])):
            return False
        endpoints.extend(e for e in (item.get('endpoints') or []) if e.get('conditions', {}).get('ready'))
    return bool(endpoints) and all(
        e.get('targetRef', {}).get('kind') == 'Pod'
        and e['targetRef'].get('namespace') == namespace
        and e['targetRef'].get('uid') in ready
        and e.get('addresses') == [ready[e['targetRef']['uid']]] for e in endpoints)


def route_matches(route, host, service=None):
    spec = route.get('spec', {})
    target = spec.get('port', {}).get('targetPort')
    port_valid = target == 8080 or (isinstance(target, str) and any(
        p.get('name') == target and p.get('port') == 8080 for p in (service or {}).get('spec', {}).get('ports', [])))
    return (spec.get('host') == host and spec.get('to', {}).get('kind') == 'Service'
            and spec['to'].get('name') == 'web' and not spec.get('alternateBackends')
            and not spec.get('path') and spec.get('wildcardPolicy', 'None') == 'None'
            and port_valid
            and spec.get('tls', {}).get('termination') == 'edge'
            and spec['tls'].get('insecureEdgeTerminationPolicy') == 'Redirect'
            and any(i.get('host') == host and any(c.get('type') == 'Admitted'
                    and c.get('status') == 'True' for c in i.get('conditions', []))
                    for i in route.get('status', {}).get('ingress', [])))


def repaired(deployment, stage):
    return (stage.get('phase') == 'fault_observed'
            and deployment.get('metadata', {}).get('uid') == stage.get('uid')
            and stage.get('initialGeneration', 0) < stage.get('faultGeneration', 0)
            < deployment.get('metadata', {}).get('generation', 0) and current(deployment))


def quota_matches(quota):
    return (quota.get('spec', {}).get('hard') == {'pods': '1'}
            and quota.get('status', {}).get('hard') == {'pods': '1'}
            and quota.get('status', {}).get('used', {}).get('pods') == '1')
