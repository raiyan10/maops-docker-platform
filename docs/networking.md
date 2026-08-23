# Networking (Day 3)

## Topology

```
host (127.0.0.1 only)
    |
    v
gateway  --edge-->  app  --backend-->  state
```

Two explicit Compose networks replace Day 2's single implicit default
network:

| Network   | Members           | `internal` | Purpose |
|-----------|--------------------|:----------:|---------|
| `edge`    | `gateway`, `app`   | no         | host-facing traffic path |
| `backend` | `app`, `state`     | yes        | persistence traffic path, never routed to the outside world |

`gateway` is on `edge` only. `state` is on `backend` only. `app` is the
only service on both, by design - it is the sole bridge between the
host-facing path and the persistence path. `gateway` and `state` share no
network at all.

## Why `backend` is `internal: true`

`internal: true` tells Docker not to give the network a default route to
the outside world. Two consequences matter here:

1. `state` (and anything else attached only to `backend`) cannot make
   arbitrary outbound connections, even if its own code tried to - there
   is no route.
2. Docker refuses to let a container attached only to an internal network
   be the target of a host-published port. Even a `compose.yaml` typo
   that gave `state` a `ports:` mapping would fail outright, because
   `state` never joins any non-internal network in the first place.

`edge` is a normal (non-internal) network, because `gateway` genuinely
needs a route out to the host's published port.

## Why gateway cannot directly reach state

Not by firewall rule - by construction. `gateway` never joins `backend`,
and `state` never joins `edge`, so there is no network on which the two
containers' IP addresses are both present. Docker's embedded DNS resolves
a service name only to addresses on a network the *querying* container
also belongs to; querying `state` from inside `gateway` fails DNS
resolution outright (`socket.gaierror`), not merely a connection refusal.
Symmetrically, `state` cannot resolve or reach `gateway`.

This is proven at runtime, not just declared - see "Runtime verification"
below.

## Service-name DNS, never a hardcoded IP

Every cross-service call in this platform uses Compose's embedded DNS by
service name (`app`, `state`) - never a static IP. No container in
`compose.yaml` declares a static IPv4 address, an `ipam.config` block, a
`macvlan`/`ipvlan` driver, or `network_mode: host`. `scripts/compose/
check_compose.py` structurally enforces the intended membership
(`check_network_membership`, `check_gateway_state_isolation`,
`check_top_level_networks`) and cross-checks that every declared upstream
target (`UPSTREAM_HOST`, `STATE_HOST`) both names a real service in the
compose file *and* shares a network with the consumer - a typo or a
service that forgot to join the right network fails `make compose-check`
immediately, not just at runtime.

## Only gateway publishes a host port

`gateway`'s `ports:` mapping is the only one in `compose.yaml`, always
bound to `127.0.0.1` (never `0.0.0.0`). `app` and `state` publish nothing.
This is unchanged from Day 2's `app`, extended to `state`.

## Runtime verification

`scripts/compose/compose_integration.py` (`make compose-test`) proves, on
a real running stack:

- `gateway` can resolve/reach `app`, and `app` can resolve/reach `state`
  (`socket.gethostbyname`, executed from inside the real containers).
- `gateway` cannot resolve `state`, and `state` cannot resolve `gateway`,
  in both directions - a real DNS resolution failure, not merely an
  assumption from network membership looking correct on paper.
- Each container's actual `NetworkSettings.Networks` membership (via
  `docker inspect`) matches the declared `edge`/`backend` topology.
- `backend`'s real, live `docker network inspect` output shows
  `Internal: true`, and `edge`'s shows `Internal: false`
  (`check_network_internal_flag()`) - a genuine `[C]`-tier check against
  the running Docker network object itself, not the `[A]`-tier check
  `scripts/compose/check_compose.py` already performs against the
  *rendered* `compose.yaml`. Day 4 added this specific check
  (`docs/engineering-reviews/day-03-security-review.md` finding M-2/A-3
  correctly found that, on Day 3, this exact bullet described a proof that
  did not yet exist in `compose_integration.py` - it does now).
- The end-to-end `gateway -> app -> state` HTTP path (`GET /state`,
  `POST /state/increment`) genuinely works across both networks.

No debugging package (`ping`, `curl`, `netcat`, `dnsutils`) was installed
to prove any of this - every check above uses stdlib `socket`/
`http.client`, already available in every container.

## Scope boundary

No resource limits, no seccomp/AppArmor profile changes, no service mesh,
no TLS between services - none of that is Day 3 scope. See
`docs/roadmap.md`.
