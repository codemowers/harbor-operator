import aiohttp
import re
from base64 import b64encode
from json import dumps
from urllib.parse import urlsplit


class Harbor(object):
    class Error(Exception):
        pass

    class NoSuchProject(Error):
        pass

    class RobotAccountAlreadyExists(Error):
        pass

    class UserAlreadyMember(Error):
        pass

    class UserNotProvisioned(Error):
        pass

    def __init__(self, base_url):
        self.base_url = base_url
        self.hostname = urlsplit(base_url).hostname

    async def delete_registry_endpoint(self, registry_id):
        async with aiohttp.ClientSession() as session:
            await session.request(
                "DELETE", "%s/api/v2.0/registries/%d" % (self.base_url, registry_id))

    async def create_registry_endpoint(self, reg_name, reg_type, reg_url):
        body = {
            "credential": {
                "access_key": "",
                "access_secret": "",
                "type": "basic"
            },
            "description": "",
            "name": reg_name,
            "type": reg_type,
        }
        if reg_url:
            body["url"] = reg_url
            body["insecure"] = False

        async with aiohttp.ClientSession() as session:
            resp = await session.request(
                "POST", "%s/api/v2.0/registries" % self.base_url, json=body)

        if resp.status not in (201, 409):
            raise self.Error("Unexpected status code %d for "
                "registry endpoint creation" % resp.status)

        async with aiohttp.ClientSession() as session:
            resp = await session.request(
                "GET", "%s/api/v2.0/registries" % self.base_url)
            if resp.status not in (200, 409):
                raise self.Error("Unexpected status code %d for "
                    "registry endpoint lookup" % resp.status)

        registries = await resp.json()
        for registry in registries:
            if registry["name"] == reg_name:
                return registry["id"]
        raise self.Error("Failed to lookup registry endpoint %s" %
            repr(reg_name))

    async def get_project(self, project_name):
        async with aiohttp.ClientSession() as session:
            resp = await session.request(
                "GET", "%s/api/v2.0/projects/%s" % (self.base_url, project_name))
        if resp.status == 200:
            return await resp.json()
        elif resp.status == 404:
            return None
        elif resp.status == 403:  # TODO: ??
            return None
        else:
            raise self.Error("Unexpected status code %d for "
                "project lookup" % resp.status)

    async def delete_project_by_id(self, project_id):
        async with aiohttp.ClientSession() as session:
            await session.request(
                "DELETE", "%s/api/v2.0/projects/%d" % (self.base_url, project_id))

    async def delete_project_by_name(self, project_name):
        async with aiohttp.ClientSession() as session:
            await session.request(
                "DELETE", "%s/api/v2.0/projects/%s" % (self.base_url, project_name))
            # TODO: Check status code

    async def delete_project_member(self, project_id, membership_id):
        async with aiohttp.ClientSession() as session:
            await session.request(
                "DELETE", "%s/api/v2.0/projects/%d/members/%d" % (self.base_url, project_id, membership_id))
            # TODO: Check status code

    async def delete_robot_account(self, project_name, membership_id):
        async with aiohttp.ClientSession() as session:
            await session.request(
                "DELETE", "%s/api/v2.0/projects/%s/robots/%d" % (self.base_url, project_name, membership_id))
            # TODO: Check status code

    async def create_project(self, project_name, public, quota, registry_id=None):
        async with aiohttp.ClientSession() as session:
            resp = await session.request(
                "POST", "%s/api/v2.0/projects" % self.base_url, json={
                    "metadata": {
                        "public": str(public).lower()
                    },
                    "project_name": project_name,
                    "storage_limit": quota,
                    "registry_id": registry_id
                })
        if resp.status not in (201, 409):
            raise self.Error("Unexpected status code %d for project "
                "creation" % resp.status)
        return await self.get_project(project_name)

    async def add_project_member(self, project_id, username, role_id):
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                "%s/api/v2.0/projects/%d/members" % (self.base_url, project_id),
                json={
                    "role_id": role_id,
                    "member_user": {
                        "username": username
                    }
                }
            )
        if response.status == 201:
            m = re.search("/members/([0-9]+)$", response.headers["Location"])
            return int(m.groups()[0])
        elif response.status == 409:
            return 0
        elif response.status == 404:
            raise self.UserNotProvisioned(username)
        raise self.Error("Got unexpected response from Harbor: %s" % response.status)

    async def create_robot_account(self, project_name, account_name, permissions):
        async with aiohttp.ClientSession() as session:
            response = await session.post(
                "%s/api/v2.0/robots" % self.base_url,
                json={
                    "name": account_name,
                    "duration": -1,
                    "description": "Robot account created by harbor-operator",
                    "disable": False,
                    "level": "project",
                    "permissions": [{
                        "namespace": project_name,
                        "kind": "project",
                        "access": permissions
                    }]
                }
            )
        if response.status == 201:
            response_json = await response.json()
            auth = response_json["name"].encode("ascii"), \
                response_json["secret"].encode("ascii")
            auths = {}
            auths[self.hostname] = {
                "auth": b64encode(b"%s:%s" % auth).decode("ascii")
            }
            dockerconfig = dumps({
                "auths": auths
            })
            m = re.search("/robots/([0-9]+)$", response.headers["Location"])
            robot_id = int(m.groups()[0])
            return dockerconfig, response_json["name"], response_json["secret"], robot_id
        elif response.status == 409:
            raise self.RobotAccountAlreadyExists()
        elif response.status == 403:
            raise self.NoSuchProject(project_name)
        raise self.Error("Got unexpected response from Harbor: %s" % response.status)
