#!/usr/bin/env python3
import os
import kopf
from base64 import b64encode
from json import dumps
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio import client, config
from sanic import Sanic
from sanic.response import json
from image_mutation import mutate_image
from harbor_wrapper import Harbor

mutation_excluded_namespaces = set([
    # Do not fiddle with CNI stuff
    "kube-system", # kube-proxy hosted here
    "tigera-operator",
    "calico-system",

    # Do not fiddle with CSI stuff
    "longhorn-system",

    # Don't touch Harbor itself
    "harbor-operator",
])

harbor = Harbor(os.environ["HARBOR_URI"])
cached_registries = set()
app = Sanic("admission_control")


@app.post("/")
async def admission_control_handler(request):
    patches = []
    pod_namespace = request.json["request"]["object"]["metadata"]["namespace"]
    pod_name = request.json["request"]["object"]["metadata"].get("name", "")
    pod_ref = "%s/%s" % (pod_namespace, pod_name)
    if pod_namespace in mutation_excluded_namespaces:
        print("Pod %s not mutated by namespace exclusion" % pod_ref)
    else:
        for index, container in enumerate(request.json["request"]["object"]["spec"]["containers"]):
            mutated_image = mutate_image(container["image"], harbor.hostname, cached_registries)
            patches.append({
                "op": "replace",
                "path": "/spec/containers/%d/image" % index,
                "value": mutated_image,
            })
            print("Substituting %s with %s for pod %s" % (
                container["image"], mutated_image, pod_ref))
    response = {
        "apiVersion": "admission.k8s.io/v1",
        "kind": "AdmissionReview",
        "response": {
            "uid": request.json["request"]["uid"],
            "allowed": True,
            "patchType": "JSONPatch",
            "patch": b64encode(dumps(patches).encode("ascii")).decode("ascii")
        }
    }
    return json(response)


@kopf.on.resume("harborcredentials")
@kopf.on.create("harborcredentials")
async def credentialCreation(name, namespace, body, **kwargs):
    v1 = client.CoreV1Api()
    project_name = body["spec"]["project"]
    username = "harbor-operator_%s_%s" % (namespace, name)
    try:
        dockerconfig, username, password, robot_id = await harbor.create_robot_account(
            project_name,
            username,
            body["spec"]["permissions"])
    except Harbor.NoSuchProject:
        raise kopf.TemporaryError("PROJECT_MISSING", delay=300)
    except Harbor.RobotAccountAlreadyExists:
        # We can't read the password to retry, so just let's fail gracefully
        raise kopf.TemporaryError("ROBOT_ACCOUNT_ALREADY_EXISTS")
    else:
        data = {}
        data[body["spec"]["key"]] = b64encode(dockerconfig.encode("ascii")).decode("ascii")
        kwargs = {
            "api_version": "v1",
            "data": data,
            "kind": "Secret",
            "metadata": {
                "name": body["metadata"]["name"]
            }
        }
        if body["spec"].get("type"):
            kwargs["type"] = body["spec"]["type"]
        kopf.adopt(kwargs)
        await v1.create_namespaced_secret(body["metadata"]["namespace"],
            client.V1Secret(**kwargs))
    return {"state": "READY", "id": robot_id, "project": project_name}


@kopf.on.delete("harborcredentials")
async def credential_deletion(name, namespace, body, **kwargs):
    try:
        project_name = body["status"]["credentialCreation"]["project"]
        robot_id = body["status"]["credentialCreation"]["id"]
    except KeyError:
        pass
    else:
        await harbor.delete_robot_account(project_name, robot_id)


@kopf.on.resume("clusterharborprojects")
@kopf.on.create("clusterharborprojects")
async def projectCreation(name, namespace, body, **kwargs):
    kwargs = {
        "project_name": name,
        "public": body["spec"]["public"],
        "quota": body["spec"]["quota"],
    }
    if body["spec"]["cache"]:
        api_instance = client.CustomObjectsApi()
        try:
            registry_spec = await api_instance.get_cluster_custom_object("codemowers.io",
                "v1alpha1", "clusterharborregistries", name)
        except ApiException as e:
            if e.status == 404:
                raise kopf.TemporaryError("NO_REGISTRY")
        try:
            registry_id = registry_spec["status"]["registryCreation"]["id"]
        except KeyError:
            raise kopf.TemporaryError("REGISTRY_NOT_READY")
        kwargs["registry_id"] = registry_id
    project = await harbor.create_project(**kwargs)
    if body["spec"]["cache"]:
        cached_registries.add(name)
    return {"state": "READY", "id": project["project_id"]}


@kopf.on.delete("clusterharborprojects")
async def project_deletion(name, body, **kwargs):
    cached_registries.discard(name)
    try:
        project_id = body["status"]["projectCreation"]["id"]
    except KeyError:
        pass
    else:
        await harbor.delete_project_by_id(project_id)

HARBOR_ROLES = {
    "PROJECT_ADMIN": 1,
    "DEVELOPER": 2,
    "GUEST": 3,
    "MAINTAINER": 4,
}


@kopf.on.resume("clusterharborprojectmembers")
@kopf.on.create("clusterharborprojectmembers")
async def memberCreation(name, namespace, body, **kwargs):
    api_instance = client.CustomObjectsApi()
    try:
        project_spec = await api_instance.get_cluster_custom_object("codemowers.io",
            "v1alpha1", "clusterharborprojects", body["spec"]["project"])
    except ApiException as e:
        if e.status == 404:
            raise kopf.TemporaryError("NO_PROJECT")

    try:
        project_id = project_spec["status"]["projectCreation"]["id"]
    except KeyError:
        raise kopf.TemporaryError("PROJECT_NOT_READY")

    try:
        membership_id = await harbor.add_project_member(project_id,
            body["spec"]["username"], HARBOR_ROLES[body["spec"]["role"]])
    except Harbor.UserNotProvisioned:
        # User has not logged in yet with OIDC and we don't have mechanism
        # to provision OIDC user accounts either
        raise kopf.TemporaryError("USER_NOT_PROVISIONED", delay=300)
    return {"state": "READY", "id": membership_id, "project_id": project_id}


@kopf.on.delete("clusterharborprojectmembers")
async def member_deletion(name, body, **kwargs):
    try:
        membership_id = body["status"]["memberCreation"]["id"]
        project_id = body["status"]["memberCreation"]["project_id"]
    except KeyError:
        membership_id = 0
    if membership_id:
        await harbor.delete_project_member(project_id, membership_id)


@kopf.on.resume("clusterharborregistries")
@kopf.on.create("clusterharborregistries")
async def registryCreation(name, body, **kwargs):
    registry_id = await harbor.create_registry_endpoint(name,
        body["spec"]["type"], body["spec"]["endpoint"])
    return {"state": "READY", "id": registry_id}


@kopf.on.delete("clusterharborregistries")
async def registry_deletion(name, body, **kwargs):
    await harbor.delete_registry_endpoint(body["status"]["registryCreation"]["id"])


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    settings.scanning.disabled = True
    settings.posting.enabled = False
    settings.persistence.finalizer = "harbor-operator"
    print("Kopf operator starting up")


@app.listener("before_server_start")
async def setup_db(app, loop):
    if os.getenv("KUBECONFIG"):
        await config.load_kube_config()
    else:
        config.load_incluster_config()

    app.ctx.cached_registries = set()
    api_instance = client.CustomObjectsApi()

    resp = await api_instance.list_cluster_custom_object("codemowers.io",
        "v1alpha1", "clusterharborprojects")

    for body in resp["items"]:
        if not body["spec"]["cache"]:
            continue
        try:
            project_id = body["status"]["projectCreation"]["id"]
        except KeyError:
            project_id = 0
        if project_id:
            cached_registries.add(body["metadata"]["name"])
    print("Caching registries:", cached_registries)

    app.add_task(kopf.operator(
        clusterwide=True))


kwargs = {}
if os.path.exists("/tls"):
    kwargs["ssl"] = {"key": "/tls/tls.key", "cert": "/tls/tls.crt"}
app.run(host="0.0.0.0", port=3001, single_process=True,
    motd=False, **kwargs)
