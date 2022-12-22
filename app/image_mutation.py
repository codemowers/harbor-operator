import re

RE_IMAGE = re.compile("^((?:(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])"
"(?:(?:\\.(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]))+)?"
"(?::[0-9]+)?/)?[a-z0-9]+(?:(?:(?:[._]|__|[-]*)[a-z0-9]+)+)?"
"(?:(?:/[a-z0-9]+(?:(?:(?:[._]|__|[-]*)[a-z0-9]+)+)?)+)?)"
"(?::([\\w][\\w.-]{0,127}))?(?:@([A-Za-z][A-Za-z0-9]*"
"(?:[-_+.][A-Za-z][A-Za-z0-9]*)*[:][[:xdigit:]]{32,}))?$")


def parse_image(foo):
    m = RE_IMAGE.match(foo)
    if not m:
        raise ValueError("%s does not match Docker image regex" % repr(foo))
    image, tag, digest = m.groups()
    try:
        org, image = image.rsplit("/", 1)
    except ValueError:
        org = "library"
    try:
        registry, org = org.rsplit("/", 1)
    except ValueError:
        registry = "docker.io"
    if "/" in registry:
        raise ValueError("Won't allow caching Docker registry in image name")
    return registry, org, image, tag, digest


def mutate_image(foo, hostname, cached_registries):
    registry, org, image, tag, digest = parse_image(foo)
    j = "%s/%s/%s" % (registry, org, image)
    if tag:
        j = "%s:%s" % (j, tag)
    if digest:
        # TODO: Test this
        j = "%s@%s" % (j, digest)
    if registry in cached_registries:
        j = "%s/%s" % (hostname, j)
    return j


assert mutate_image("mongo:latest", "harbor.k-space.ee", ("docker.io")) == "harbor.k-space.ee/docker.io/library/mongo:latest"
assert mutate_image("mongo", "harbor.k-space.ee", ("docker.io")) == "harbor.k-space.ee/docker.io/library/mongo"
assert mutate_image("library/mongo", "harbor.k-space.ee", ("docker.io")) == "harbor.k-space.ee/docker.io/library/mongo"
assert mutate_image("docker.io/library/mongo", "harbor.k-space.ee", ("docker.io")) == "harbor.k-space.ee/docker.io/library/mongo"
assert mutate_image("docker.io/calico/typha:v3.24.5", "harbor.k-space.ee", ("docker.io")) == "harbor.k-space.ee/docker.io/calico/typha:v3.24.5"
