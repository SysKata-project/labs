"""Operator/controller rendered assessment identity; no credential issuance here."""
import json
import re
import sys


def roles(namespace, control, terminal=False):
    for value in (namespace, control):
        if not re.fullmatch(r'[a-z][a-z0-9-]{1,61}[a-z0-9]', value):
            raise ValueError('invalid namespace')
    rules = [{'apiGroups': ['authorization.k8s.io'], 'resources': ['localsubjectaccessreviews'], 'verbs': ['create']}]
    if not terminal:
        rules += [
            {'apiGroups': [''], 'resources': ['pods', 'services', 'resourcequotas'], 'verbs': ['get', 'list']},
            {'apiGroups': ['apps'], 'resources': ['deployments', 'replicasets'], 'verbs': ['get', 'list']},
            {'apiGroups': ['discovery.k8s.io'], 'resources': ['endpointslices'], 'verbs': ['get', 'list']},
            {'apiGroups': ['route.openshift.io'], 'resources': ['routes'], 'verbs': ['get', 'list']},
            {'apiGroups': ['apps'], 'resources': ['deployments'], 'resourceNames': ['web'], 'verbs': ['patch']},
            {'apiGroups': [''], 'resources': ['pods'], 'verbs': ['create']}]
    role = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role',
            'metadata': {'name': 'lab-evaluator', 'namespace': namespace}, 'rules': rules}
    binding = {'apiVersion': role['apiVersion'], 'kind': 'RoleBinding', 'metadata': role['metadata'],
               'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'lab-evaluator'},
               'subjects': [{'kind': 'ServiceAccount', 'name': 'lab-evaluator', 'namespace': control}]}
    return [role, binding]


def installation(control):
    roles('validation-placeholder', control)
    name = 'syskata-labs-evaluator-v1'
    user = f'system:serviceaccount:{control}:lab-evaluator'
    policy = {'apiVersion': 'admissionregistration.k8s.io/v1', 'kind': 'ValidatingAdmissionPolicy',
        'metadata': {'name': name}, 'spec': {'failurePolicy': 'Fail',
        'matchConstraints': {'resourceRules': [{'apiGroups': ['*'], 'apiVersions': ['*'],
            'operations': ['CREATE', 'UPDATE', 'DELETE', 'CONNECT'], 'resources': ['*/*']}]},
        'matchConditions': [{'name': 'evaluator', 'expression': 'request.userInfo.username == ' + json.dumps(user)}],
        'validations': [{'expression': "namespaceObject != null && namespaceObject.metadata.name.matches('^sk-(lab|term)-[a-f0-9]{32}-g[0-9]+$') && has(namespaceObject.metadata.labels) && 'syskata.io/lab-session' in namespaceObject.metadata.labels && (request.resource.resource == 'localsubjectaccessreviews' || (request.resource.resource == 'pods' && request.dryRun == true) || (request.resource.resource == 'deployments' && request.name == 'web' && request.operation == 'UPDATE'))",
                         'message': 'Evaluator writes are restricted to assessment probes'}]}}
    binding = {'apiVersion': policy['apiVersion'], 'kind': 'ValidatingAdmissionPolicyBinding',
               'metadata': {'name': name}, 'spec': {'policyName': name, 'validationActions': ['Deny']}}
    account = {'apiVersion': 'v1', 'kind': 'ServiceAccount',
               'metadata': {'name': 'lab-evaluator', 'namespace': control}, 'automountServiceAccountToken': False}
    role = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
            'metadata': {'name': name}, 'rules': [{'apiGroups': [''], 'resources': ['namespaces'], 'verbs': ['get']},
        {'apiGroups': ['admissionregistration.k8s.io'], 'resources': ['validatingadmissionpolicies', 'validatingadmissionpolicybindings'],
         'resourceNames': [name], 'verbs': ['get']}]}
    assignment = {'apiVersion': role['apiVersion'], 'kind': 'ClusterRoleBinding', 'metadata': {'name': name},
                  'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': name},
                  'subjects': [{'kind': 'ServiceAccount', 'name': 'lab-evaluator', 'namespace': control}]}
    return {'apiVersion': 'v1', 'kind': 'List', 'items': [policy, binding, account, role, assignment]}


if __name__ == '__main__':
    print(json.dumps(installation(sys.argv[1]), indent=2))
