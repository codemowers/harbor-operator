# harbor-operator

## Background

This operator is higly opinionated way to deploy Harbor in a Kubernetes cluster:

* Only one Harbor instance per Kubernetes cluster
* Nearly all components deployed in HA fashion
* Automates Harbor project creation via `ClusterHarborProject` CRD
  * Create per user projects with quotas and password protection
  * Create proxy cache projects with quotas and password protection
* Designed to work in conjunction with
  [https://git.k-space.ee/k-space/sandbox-dashboard](sandbox-dashboard):
  * Sandbox template repository contains `HarborCredential` definitions
  * Sandbox dashboard adds `ClusterUser` resources when user logs in
* Automate push/pull credential provisioning using HarborCredential CRD-s,
  to simplify working with Skaffold
* [WIP] Pod admission mutation webhook to rewrite Pod images to use
  proxy caches defined via `ClusterHarborProject` definitions with `cache: true`.


## Instantiating Harbor projects

To instantiate proxy cache project:

```
---
apiVersion: codemowers.io/v1alpha1
kind: ClusterHarborRegistry
metadata:
  name: quay.io
spec:
  type: quay
  endpoint: https://quay.io
---
apiVersion: codemowers.io/v1alpha1
kind: ClusterHarborRegistry
metadata:
  name: docker.io
spec:
  type: docker-hub
  endpoint: https://docker.io
---
apiVersion: codemowers.io/v1alpha1
kind: ClusterHarborProject
metadata:
  name: docker.io
spec:
  cache: true
  public: true
  quota: 10737418240
---
apiVersion: codemowers.io/v1alpha1
kind: ClusterHarborProject
metadata:
  name: quay.io
spec:
  cache: true
  public: true
  quota: 10737418240
```


## Deploying push/pull secrets into namespaces

Once everything is running you can easily provision Harbor project
push and pull secrets into namespaces:

```
---
apiVersion: codemowers.io/v1alpha1
kind: HarborCredential
metadata:
  name: kaniko
spec:
  project: foobar
  key: config.json
  permissions:
    - resource: repository
      action: pull
    - resource: tag
      action: create
    - resource: repository
      action: push
---
apiVersion: codemowers.io/v1alpha1
kind: HarborCredential
metadata:
  name: regcred
spec:
  project: foobar
  type: kubernetes.io/dockerconfigjson
  key: .dockerconfigjson
  permissions:
    - resource: repository
      action: pull
```

## Uninstalling

The finalizers will likely block deletion of resources:

```
for j in $(
    kubectl get harborcredentials -o name;
    kubectl get clusterharborprojectmembers -o name;
    kubectl get clusterharborprojects -o name;
    kubectl get clusterharborregistries -o name ); do
  echo "Removing $j"
  kubectl patch $j --type json --patch='[ { "op": "remove", "path": "/metadata/finalizers" } ]'
  kubectl delete $j
done
```
