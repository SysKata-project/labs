#!/usr/bin/env python3
"""Render operator-installed controller RBAC and a fail-closed write boundary."""
import json
import re
import sys


def render(namespace):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,61}[a-z0-9]', namespace):
        raise ValueError('invalid namespace')
    name = 'syskata-labs-controller-v1'
    username = f'system:serviceaccount:{namespace}:lab-controller'
    verbs = ['get', 'list', 'watch', 'create', 'update', 'patch', 'delete']
    role = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
            'metadata': {'name': name}, 'rules': [
        {'apiGroups': [''], 'resources': ['namespaces'], 'verbs': ['get', 'list', 'watch', 'create', 'delete']},
        {'apiGroups': [''], 'resources': ['pods', 'services', 'resourcequotas', 'serviceaccounts'], 'verbs': verbs},
        {'apiGroups': [''], 'resources': ['pods/exec', 'serviceaccounts/token'], 'verbs': ['create']},
        {'apiGroups': [''], 'resources': ['pods/log'], 'verbs': ['get']},
        {'apiGroups': ['apps'], 'resources': ['deployments'], 'verbs': verbs},
        {'apiGroups': ['route.openshift.io'], 'resources': ['routes'], 'verbs': verbs},
        {'apiGroups': ['rbac.authorization.k8s.io'], 'resources': ['roles', 'rolebindings'], 'verbs': verbs},
        {'apiGroups': ['networking.k8s.io'], 'resources': ['networkpolicies'], 'verbs': verbs},
        {'apiGroups': ['admissionregistration.k8s.io'],
         'resources': ['validatingadmissionpolicies', 'validatingadmissionpolicybindings'],
         'resourceNames': ['syskata-labs-boundary-v1', name], 'verbs': ['get']} ]}
    policy = {'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicy',
              'metadata': {'name': name}, 'spec': {
        'failurePolicy': 'Fail',
        'matchConstraints': {'resourceRules': [{'apiGroups': ['*'], 'apiVersions': ['*'],
            'operations': ['CREATE', 'UPDATE', 'DELETE', 'CONNECT'], 'resources': ['*/*']}]},
        'matchConditions': [{'name': 'controller-only', 'expression': 'request.userInfo.username == ' + json.dumps(username)}],
        'validations': [
            {'expression': "request.resource.resource == 'namespaces' ? ((request.operation == 'CREATE' ? object.metadata.name : oldObject.metadata.name).matches('^sk-(lab|term)-[a-f0-9]{32}-g[0-9]+$') && (request.operation == 'CREATE' ? object.metadata.labels : oldObject.metadata.labels)['syskata.io/lab-session'].matches('^[a-f0-9-]{36}$')) : (namespaceObject != null && namespaceObject.metadata.name.matches('^sk-(lab|term)-[a-f0-9]{32}-g[0-9]+$') && has(namespaceObject.metadata.labels) && 'syskata.io/lab-session' in namespaceObject.metadata.labels)",
             'message': 'The lab controller may write only its disposable lab namespaces'}]}}
    binding = {'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicyBinding',
               'metadata': {'name': name}, 'spec': {'policyName': name, 'validationActions': ['Deny']}}
    account = {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': 'lab-controller', 'namespace': namespace},
               'automountServiceAccountToken': False}
    assignment = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
                  'metadata': {'name': name}, 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': name},
                  'subjects': [{'kind': 'ServiceAccount', 'name': 'lab-controller', 'namespace': namespace}]}
    # Install admission first, grant controller permissions last.
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [policy, binding, account, role, assignment]}


if __name__ == '__main__':
    print(json.dumps(render(sys.argv[1]), indent=2))
