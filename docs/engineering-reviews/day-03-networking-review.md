# Day 3 Independent Docker Networking & Segmentation Review

Repository: `maops-docker-platform`
Branch: `feature/day-3-network-config-persistence`
Target: v0.3.0
Reviewer: independent Day 3 networking/segmentation review agent (review-only)
Scope: `compose.yaml`'s network/volume/config topology,
`scripts/compose/check_compose.py` (static), `scripts/compose/compose_integration.py`
(runtime), and the real Compose-managed `gateway -> app -> state` stack
they claim to prove — per `.claude/CLAUDE.md` and `docs/roadmap.md`'s
Day 3 scope. Does not re-litigate container hardening ([C]/[D] capability/
rootfs/UID findings), which is `docs/engineering-reviews/day-03-security-review.md`'s
territory; overlap is corroborated, not re-scored, where this review's own
hands-on testing touches it.

This review did not trust `check_compose.py`'s "14 structural checks" or
`compose_integration.py`'s PASS output at face value. Every claim below
was independently re-derived: reversible byte-for-byte-restored mutations
of the tracked `compose.yaml` (19 distinct scenarios, `diff`-verified
restored after each), fresh uniquely-named Compose stacks brought up and
torn down independently of the project's own scripts, `docker exec`
socket/HTTP probes written from scratch (not reusing
`compose_integration.py`'s helper functions), raw-IP TCP connect attempts
(not merely DNS resolution) to test real L3 reachability, `/proc/net/route`
inspection inside containers, and a deliberate mutation of
`condition: service_healthy` -> `condition: service_started` to test
whether the Day 2 ordering finding is genuinely closed.

---

## Finding counts

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 0 |
| Medium   | 1 |
| Low      | 3 |

No Critical or High findings. The declared `edge`/`backend` segmentation,
the `depends_on: condition: service_healthy` startup-ordering chain, and
the failure/recovery behavior all independently reproduced exactly as
`docs/networking.md`/`docs/persistence.md` describe. All findings below
are gaps in what the *automated verification* independently proves, or a
latency characteristic of the failure-propagation design — not defects in
the platform's declared network topology or its actual isolation.

---

## Findings

### M-1 (Medium): cross-hop dependency timeouts are not budgeted, so a client with a timeout near the single-hop 3.0s bound can observe a raw connection timeout instead of a clean 503 during a `state` outage

**Where**: `gateway/config.py` (`upstream_timeout_seconds`) and
`app/config.py` (`state_timeout_seconds`) both resolve independently from
the *same* `config/platform.json` field, `dependency_timeout_seconds`
(3.0s), via each service's own `platform_config.py`. `gateway`'s call to
`app`'s `/readyz` and `app`'s call to `state`'s `/readyz` each apply this
3.0s budget to their own hop, with no awareness of how much of that
budget the hop below has already spent.

**Reproduced**: stood up an independent stack, stopped `state`, and
polled `gateway`'s `/readyz` from the host. Actual observed round-trip
times varied run-to-run: sometimes ~1s (a fast `ECONNREFUSED` once
`state`'s process had fully exited), and sometimes the full ~3.0s+ (a
window, immediately after `docker compose stop state`, where a new TCP
SYN into the just-stopped container's backend-network IP is not
immediately answered with RST and instead hangs until `app`'s own 3.0s
`state` timeout fires). A client-side probe using a 3.0s timeout — equal
to the single-hop budget — raced against the server and observed a raw
socket timeout instead of `gateway`'s intended clean `503`, in 15/15
consecutive attempts in one run. Raising the client timeout to 6s (still
below `compose_integration.py`'s own, more generous, `REQUEST_TIMEOUT_SECONDS
= 5.0` design choice) produced the clean, documented `503
{"status": "not-ready", "error": "upstream unavailable"}` reliably.

**Impact**: this is not a segmentation or correctness defect — `app` and
`gateway` both stay alive and eventually answer correctly, exactly as
`docs/persistence.md`/`docs/networking.md` claim, and
`compose_integration.py`'s own `REQUEST_TIMEOUT_SECONDS=5.0` was
evidently chosen with enough headroom over `dependency_timeout_seconds`
to avoid exactly this race, so the project's own test suite is not
flaky. The finding is that the platform's advertised single-hop bound
(`dependency_timeout_seconds`, 3.0s) is not, in practice, the bound a
caller two hops away experiences during the failure window — an external
health-check/monitoring client configured to trust that number at face
value (e.g. a 3s liveness probe timeout pointed at `gateway`) could see
a raw connection failure rather than a clean, documented `503` during a
`state` outage.

**Recommendation** (not applied — review only): either document that the
effective worst-case failure-detection latency for the outermost caller
is up to ~2x `dependency_timeout_seconds` (not 1x), or have `gateway`
apply a shorter per-hop budget than `app` so the two timeouts are
strictly nested rather than equal.

---

### L-1 (Low): `check_compose.py`'s `check_top_level_networks` cannot detect a genuinely extra, unattached top-level network — `docker compose config` itself prunes it first

**Where**: `scripts/compose/check_compose.py`, `check_top_level_networks`
— compares `set(config.get("networks", {}).keys())` against
`EXPECTED_NETWORK_NAMES`, but `config` is `docker compose config
--format json`'s *rendered* output.

**Reproduced**: added a top-level `extra: {}` network to a temporary copy
of `compose.yaml`, attached to no service. `docker compose config
--format json` (Docker Compose v5.4.0) silently omits any top-level
network no service actually joins — confirmed independently by rendering
both `--format json` and the plain YAML form; `extra` is absent from
both. `check_compose.py` therefore reported `OK (14 structural checks
passed)` against this mutated file — a genuine miss, the only one out of
19 independently attempted mutation scenarios (extra service, missing
service, gateway-on-backend, state-on-edge, app-missing-edge,
app-missing-backend, backend-internal-false, app/state host ports,
gateway 0.0.0.0 bind, `UPSTREAM_HOST`/`STATE_HOST` typos, a valid-service-
but-no-shared-network target, gateway/state network sharing, an
unexpected Docker-socket bind mount, a misattached volume, a wrong config
mount target, and version-fallback drift — all 18 of the other 19 were
correctly detected; full list in "Structural-check quality" below).

**Impact**: low, deliberately — an extra network with zero service
members grants no additional reachability and is not itself a security
issue; this is purely a fidelity gap between the check's "expected
exactly" claim and what it can actually observe, since Compose elides
the evidence before the checker ever sees it. A meaningfully dangerous
variant of "extra network" (one a service actually joins) *is* caught,
via `check_network_membership`'s exact-set comparison for that service.

**Recommendation** (not applied): parse `compose.yaml`'s own top-level
`networks:` keys directly (regex/minimal YAML-key scan, matching this
script's existing `check_version_fallback_defaults` pattern of reading
raw source text for exactly this reason) rather than relying solely on
the rendered config for this one check.

---

### L-2 (Low): `compose_integration.py`'s gateway<->state isolation proof is DNS-failure-only; it does not independently rule out an L3-routable path via a hardcoded IP

**Where**: `scripts/compose/compose_integration.py`, `dns_resolves()` /
the `gateway_to_state_blocked` / `state_to_gateway_blocked` checks —
both directions are proven exclusively via `socket.gethostbyname`
failing (`socket.gaierror`). No check in this script attempts a raw
`socket.connect()` to the peer's actual Docker-assigned IP address,
which would be a strictly stronger proof: DNS failure alone does not by
itself demonstrate there is no network-layer route between the two
containers — only that the embedded resolver won't hand out an address.

**Reproduced (independently, not by finding a live defect)**: wrote a
separate probe that first read each container's real
`NetworkSettings.Networks[...].IPAddress` via `docker inspect`, then
attempted a raw TCP `connect()` from `gateway` directly to `state`'s real
`backend`-network IP (bypassing DNS entirely), and symmetrically from
`state` to `gateway`'s real `edge`-network IP. Both connection attempts
failed — `gateway -> state`: `BLOCKED timed out`; `state -> gateway`:
`BLOCKED [Errno 101] Network is unreachable` — confirming the isolation
is real at the network layer, not merely a DNS artifact. Also inspected
`/proc/net/route` inside `state`'s container directly: zero default-route
entries (consistent with `backend: internal: true` giving the network no
gateway), versus `app` (on both `edge` and `backend`), which does have a
default route. A further probe from inside `state` toward an arbitrary
public IP (`93.184.216.34:80`, no live-internet dependency — only the
`BLOCKED` outcome was asserted) also failed with `Network is
unreachable`, consistent with `internal: true` semantics.

**Impact**: none on the actual security property — this review's own
stronger, independent test confirms real isolation holds. The gap is
purely that the *shipped* runtime proof (`compose_integration.py`) is
one evidentiary tier short of what it could easily assert, and
`docs/networking.md`'s own wording ("DNS resolution fails ... not merely
an assumption from network membership looking correct on paper") is
accurate to what is actually tested, but a reader could reasonably infer
a broader L3 guarantee than the script itself demonstrates.

**Recommendation** (not applied): add a raw-IP `socket.connect()` attempt
(reading the real IP via `docker inspect` first, exactly as this
review's ad hoc probe did) to `compose_integration.py`'s isolation
checks, alongside the existing DNS check, to close this evidentiary gap
directly in the shipped test rather than relying on independent review
verification each time.

---

### L-3 (Low): `compose_integration.py`'s project-prefix stripping for network names is correct today only because Compose project names in this codebase never contain an underscore

**Where**: `scripts/compose/compose_integration.py`, `get_network_names()`:
`{name.split("_", 1)[-1] if "_" in name else name for name in networks.keys()}`.
Docker's real network name is `<project>_<network>` (e.g.
`maops-compose-eeead3af2766_backend`); this strips everything up to and
including the *first* underscore.

**Reproduced (by inspection, not by finding a live break)**: every
project name actually produced by this codebase
(`f"maops-compose-{uuid.uuid4().hex[:12]}"` in
`compose_integration.py`, and the Makefile's `maops-compose-*` cleanup
filter) is hyphen-only — `uuid4().hex` never emits an underscore — so
`split("_", 1)` always finds exactly one underscore (the separator
Compose inserts) and the logic is correct in every case this project can
actually produce today. Confirmed this review's own independently-named
`maops-review-<hex>` projects (a different prefix, same hex-suffix
convention) round-trip correctly through the equivalent logic in this
review's own probe script.

**Impact**: low and currently latent — this is a silent assumption
(project names never contain `_`) rather than an explicit guard. If a
future script or manual invocation ever used a Compose project name
containing an underscore (e.g. `maops_compose_debug`), `get_network_names`
would silently strip too much and the network-membership assertions
could pass or fail incorrectly rather than raising a clear error.

**Recommendation** (not applied): strip by the known, exact
`f"{project}_"` prefix (the project name is already in scope in
`main()`) rather than by first-underscore, removing the implicit
naming-convention assumption entirely.

---

## Static topology verdict

**Sound.** `compose.yaml` declares exactly the three services, two
networks, and memberships the network contract specifies: `gateway` on
`edge` only, `app` on `edge`+`backend`, `state` on `backend` only,
`backend: internal: true`, `edge` not internal, and `gateway` and `state`
sharing no network. `check_compose.py`'s 14 structural checks are real,
field-level, non-vacuous comparisons against `docker compose config
--format json` — confirmed by reading every check function directly, not
merely trusting the printed count.

## Runtime topology verdict

**Sound, independently reproduced.** A separately-scripted, uniquely
named stack (not `compose_integration.py`) showed real `docker inspect
.NetworkSettings.Networks` membership matching the declared topology for
all three containers, real `docker network inspect` output confirming
`backend.Internal == true` and `edge.Internal == false`, and — beyond
what the shipped test proves — real route-table evidence
(`/proc/net/route`) that `state`'s container has no default gateway at
all while `app`'s does.

## gateway -> app proof

**Real.** From inside a live `gateway` container: `socket.gethostbyname("app")`
resolved to `app`'s actual `edge`-network IP, and a genuine
`http.client` `GET /healthz` over that connection returned `200
{"status": "ok"}` — a real HTTP round trip, not a stub or a ping.

## app -> state proof

**Real.** From inside a live `app` container: `socket.gethostbyname("state")`
resolved to `state`'s actual `backend`-network IP, and a genuine
`http.client` `GET /healthz` returned `200 {"status": "ok"}`. The full
`gateway -> app -> state` chain was further proven end-to-end via real
`GET /state` / `POST /state/increment` calls through the public gateway
port, with the counter value correctly incrementing.

## gateway -> state isolation

**Real, and proven at a stronger evidentiary tier than the shipped test.**
DNS resolution fails (`socket.gaierror`), as `compose_integration.py`
itself proves — and, independently, a raw TCP `connect()` to `state`'s
real IP (bypassing DNS) also fails (`timed out` — no route). See L-2 for
the gap in the *shipped* test's evidentiary tier; the underlying property
itself holds.

## state -> gateway isolation

**Real, symmetric to the above.** DNS resolution fails, and a raw TCP
`connect()` to `gateway`'s real `edge`-network IP fails with `Network is
unreachable` (consistent with `state`'s container having zero default
route at all, confirmed via `/proc/net/route`).

## backend internal verdict

**Genuine, and correctly scoped.** `internal: true` is confirmed both as
declared config ([A]) and, more importantly, via `docker network
inspect`'s real `Internal: true` ([C]), and — the strongest tier this
review could practically apply without a live internet dependency — via
an actual blocked outbound connection attempt from `state` to a public
IP and an empty default-route table inside `state`'s container ([D]).
This review does not claim more than Docker's internal-network semantics
actually guarantee: `internal: true` prevents a default route out, not a
guarantee against every conceivable host-level routing misconfiguration
outside Compose's control (e.g. a manually added host route) — a
distinction this review makes explicitly per Docker's own documented
semantics, not to weaken the finding.

## host exposure verdict

**Correct.** `app` and `state` publish no host port in both rendered
config and live `docker inspect .HostConfig.PortBindings` (empty `{}`
for both). `gateway` is the sole publisher, bound to `127.0.0.1` only
(never `0.0.0.0`), confirmed both in rendered config and via `docker
port` against a live container returning the real OS-assigned dynamic
port. The `gateway 0.0.0.0 bind` and `app`/`state` host-port mutation
scenarios were all independently reproduced and correctly caught by
`check_compose.py`.

## DNS / service-discovery verdict

**Clean.** No hardcoded IPv4 address, `ipam.config`, static
`ipv4_address`, or explicit network `aliases:` exists anywhere in
`compose.yaml`, `app/`, `gateway/`, or `state/` (grepped directly). Every
cross-service call resolves the peer by Compose service name through the
embedded DNS resolver. `UPSTREAM_HOST=app` and `STATE_HOST=state`
resolve to the real service containers, proven via live `socket.gethostbyname`
calls from inside the real containers (see "gateway -> app proof" /
"app -> state proof" above), not merely asserted from config.

## Startup-ordering closure verdict

**Genuinely closed for Day 3 — this review's central objective was
satisfied.** `compose_integration.py`'s `check_startup_ordering` compares
`get_first_healthy_at(dependency)` (the earliest `ExitCode == 0` entry in
`docker inspect`'s `.State.Health.Log` — the real moment Docker's health
status actually transitioned to `healthy`, per Docker's own
one-success-triggers-healthy semantics) against
`get_started_at(dependent)` (`.State.StartedAt`) — correctly avoiding the
"simplistic `StartedAt` vs `StartedAt`" pitfall this review was
specifically asked to rule out, by anchoring one side of the comparison
to the dependency's actual health transition rather than to when it
merely started running.

Mutating `condition: service_healthy` -> `condition: service_started` for
`app`'s dependency on `state`, then independently re-running
`compose_integration.py` against the real, mutated stack, produced a
genuine `FAIL`: `app` started at `16:25:40.26`, ~5.3s **before** `state`
first reported healthy at `16:25:45.60` — the ordering check correctly
flagged this (`FAIL app did not start before state was Docker-healthy`),
and the script's overall exit reflected the failure. The identical
mutation for `gateway`'s dependency on `app` produced the same result
(`gateway` started at `16:26:17.63`, before `app`'s health at
`16:26:23.06` — correctly flagged `FAIL`). Both mutations were also
independently caught by `check_compose.py`'s
`check_dependency_conditions`. Unlike the Day 2 finding this closes,
**the runtime script itself — not only the static checker — now fails**
when the health-gated guarantee is silently weakened, which was the
explicit bar this review was asked to hold it to.

## Upstream-target closure verdict

**Genuinely closed, and widened correctly to both hops.**
`check_upstream_targets` cross-checks both `gateway`'s `UPSTREAM_HOST`
(against `app`) and `app`'s `STATE_HOST` (against `state`) for: naming a
real service in the compose file, matching the expected target exactly,
the target port matching the target service's own bound port, and the
consumer and target sharing a real network. Independently reproduced
detection of: a bad `UPSTREAM_HOST`/`STATE_HOST` typo (both), and the
specific "names a real service but shares no network with it" scenario
(pointed `UPSTREAM_HOST` at the valid service name `state`, which shares
no network with `gateway` — correctly caught both the wrong-target and
the no-shared-network condition in the same finding).

## Failure/recovery verdict

**Real, matching the documented behavior exactly**, once the polling
client's own timeout is given adequate headroom over the platform's
single-hop `dependency_timeout_seconds` (see M-1). Independently
verified via a freshly stood-up, separately scripted stack: stopping
`state` left `app` and `gateway` processes running and their own
liveness (`/healthz`) healthy throughout; `gateway`'s `/readyz` degraded
to a controlled `503 {"status": "not-ready", ...}`; `GET /state` via
`gateway` returned a controlled `503` (no traceback, no hang); restarting
`state` brought it back to Docker-`healthy`, and `gateway`'s `/readyz`
recovered to `200 {"status": "ready"}` without `app` or `gateway`'s
containers being recreated (`StartedAt` unchanged for both, captured
directly). See M-1 for the one caveat: the actual failure-detection
latency for the outermost caller during the transition window is not as
tightly single-hop-bounded as the platform's advertised
`dependency_timeout_seconds` alone would suggest.

## Structural-check quality

**High — 18 of 19 independently attempted detection scenarios caught,
the one miss (L-1) explained by a Compose-itself pruning behavior rather
than a logic bug in the checker.** Full scoreboard from this review's own
mutation-testing harness (each mutation applied to the real tracked
`compose.yaml`, then restored byte-for-byte, verified via `diff`):

| Scenario | Result |
|---|---|
| extra service | DETECTED |
| missing service | DETECTED (Compose itself refuses: `depends on undefined service`) |
| extra (unattached) network | **MISSED** (L-1) |
| gateway accidentally on backend | DETECTED |
| state accidentally on edge | DETECTED |
| app missing edge | DETECTED |
| app missing backend | DETECTED |
| backend `internal: false` | DETECTED |
| app host port | DETECTED |
| state host port | DETECTED |
| gateway `0.0.0.0` bind | DETECTED |
| `UPSTREAM_HOST` typo | DETECTED |
| `STATE_HOST` typo | DETECTED |
| valid target, no shared network | DETECTED |
| gateway/state share a network | DETECTED |
| unexpected Docker socket mount | DETECTED |
| wrong volume attachment | DETECTED |
| wrong config mount target | DETECTED |
| version-fallback drift | DETECTED |

`check_gateway_state_isolation`'s direct assertion is logically subsumed
by `check_network_membership`'s exact-set comparison for `gateway` and
`state` individually (any shared network would already violate one of
those two exact sets) — still worth keeping as an explicit,
self-documenting invariant rather than one a reader has to derive from
two other checks, per the script's own docstring rationale.

## Cleanup/flakiness analysis

**Clean on every path tested.** Every stack this review created — the
project's own `compose_integration.py` run, this review's independent
segmentation probe, the independent failure/recovery probe, and five
additional ad hoc manual debugging stacks — used a unique
`maops-compose-*`/`maops-review-*` project name and was torn down via
`docker compose ... down -v` in a `finally`/unconditional path. Verified
via `docker ps -a`/`docker network ls`/`docker volume ls` filtered by
name after every single run: zero residue in every case, including after
the deliberately-mutated (failing) `compose_integration.py` runs. No
global prune was run at any point in this review. The one genuine
flakiness risk identified is M-1 (cross-hop timeout stacking), which
affects an *external client's* observed behavior, not the platform's own
test suite (whose `REQUEST_TIMEOUT_SECONDS=5.0` already has adequate
headroom) or its cleanup guarantees.

## Release blockers

**None.** M-1 and L-1..L-3 are all either verification-tooling coverage
gaps against a property that independently, genuinely holds, or a
latency characteristic worth documenting rather than a live defect.
Nothing found in this review contradicts a claim in
`docs/networking.md`, `docs/configuration.md`, or `docs/persistence.md`.

## Final networking verdict

**Yes — Day 3 genuinely demonstrates real network segmentation, not
merely two network names in a YAML file.** `gateway`/`app`/`state` are
truly isolated exactly as designed: `edge` and `backend` are real,
separate Docker networks; `backend`'s `internal: true` is enforced at
the kernel/route level, not just declared; `gateway` and `state` share
no network and cannot reach each other by DNS *or* by a raw IP connect
attempt bypassing DNS; the `state -> app -> gateway` health-gated startup
ordering is a real, timestamp-proven guarantee whose regression the
runtime test itself (not only the static checker) now catches — closing
the Day 2 M-1 finding this review was specifically tasked to challenge;
and the `UPSTREAM_HOST`/`STATE_HOST` cross-checks close the Day 2 L-1
finding for both hops. The findings recorded here are refinements to an
already-sound design and its verification tooling, not corrections to a
broken one.
