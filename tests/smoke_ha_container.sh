#!/usr/bin/env bash
# Simulate Supervisor's network and app data volume on an isolated Docker network.
set -euo pipefail

image=${1:?Provide the built application image}
name="deco-ha-smoke-$$"
network="$name-net"
volume="$name-data"
restored="$name-restored"
cleanup() {
    if (( $? != 0 )); then
        docker logs "$name" >&2 || true
    fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker volume rm "$volume" "$restored" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal --subnet 172.30.32.0/24 "$network" >/dev/null
docker volume create "$volume" >/dev/null
docker volume create "$restored" >/dev/null

start_app() {
    docker run -d --name "$name" --network "$network" --ip 172.30.32.2 -v "$1:/data" \
        -e DECO_HA_INGRESS=true -e DECO_WRITE_ENABLED=false "$image" >/dev/null
}
supervisor() {
    # A connection to this local address presents the simulated Supervisor IP.
    docker exec -i "$name" python - "http://172.30.32.2:8080" "$1"
}
check_app() {
    supervisor "$1" <<'PY'
import json
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

urlopen = build_opener(ProxyHandler({})).open

base, operation = sys.argv[1:]
for _ in range(40):
    try:
        with urlopen(base + "/api/state", timeout=1) as reply:
            state = json.load(reply)
        break
    except (URLError, TimeoutError) as error:
        last_error = error
        time.sleep(0.25)
else:
    raise SystemExit(f"Simulated Ingress did not become ready: {last_error}")

assert state["ha_ingress"] and state["mode"] == "mock" and not state["writes_enabled"]
if operation == "seed":
    request = Request(base + "/api/users", data=b'{"name":"Profilo CI"}',
                      headers={"Content-Type":"application/json", "X-Family-Action":"1"})
    with urlopen(request, timeout=3) as reply:
        assert reply.status == 200
else:
    assert [user["name"] for user in state["users"]] == ["Profilo CI"]
with urlopen(base + "/", timeout=3) as reply:
    assert b"Deco Family Control" in reply.read()
PY
}

start_app "$volume"
check_app seed
docker exec "$name" python -c '
from urllib.error import HTTPError
from urllib.request import urlopen
try:
    urlopen("http://127.0.0.1:8080/api/state", timeout=3)
except HTTPError as exc:
    assert exc.code == 403
else:
    raise SystemExit("An untrusted peer was accepted")
'
docker stop "$name" >/dev/null
docker rm "$name" >/dev/null
docker run --rm --network none -v "$volume:/data:ro" -v "$restored:/restored" \
    --entrypoint cp "$image" -a /data/simulation /restored/
start_app "$restored"
check_app restore
