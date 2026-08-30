"""Pure, Docker-free Debian package version comparison (Day 7).

SCOPE: implements exactly the algorithm Debian Policy §5.6.12 specifies
for comparing two version strings (the same ordering `dpkg
--compare-versions` and `apt` use) - not a general semver library, and
not every corner of `dpkg`'s own C implementation (e.g. it does not
reject malformed version strings the way `dpkg` itself would at package-
build time; scripts/security/patch_lifecycle_check.py is responsible for
treating an unparseable version as its own "cannot establish evidence"
failure rather than trusting this module to catch that).

Needed because `security/runtime-patches.lock` records `~deb13u2`-style
Debian revisions (e.g. `3.5.6-1~deb13u2` vs `3.5.7-1~deb13u2`), where a
naive string/tuple comparison gets the ordering wrong (`~` must sort
before EVERYTHING, including the end of a version part, so
`1~deb13u1 < 1`) - this project's own runtime-patch lifecycle check
(patch_lifecycle_check.py) depends on genuinely correct Debian version
ordering, not merely "looks smaller as a string".
"""

from __future__ import annotations


class DebianVersionError(ValueError):
    pass


def _order(c: str | None) -> int:
    """Mirrors dpkg's own `order()` (dpkg/lib/dpkg/version.c): digits sort
    as 0 (handled separately, by digit-run), letters sort by their own
    ASCII value, '~' sorts below everything (even the end of a string),
    and every other character sorts above all letters."""
    if c is None:
        return 0
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    """Compares one version component (upstream_version OR
    debian_revision - never a full version string with an epoch/hyphen
    still in it) per Debian Policy §5.6.12: alternating non-digit-run and
    digit-run comparison, non-digit runs compared via `_order`, digit runs
    compared numerically (leading zeros irrelevant, a missing run counts
    as 0)."""
    i = j = 0
    len_a, len_b = len(a), len(b)
    while i < len_a or j < len_b:
        while (i < len_a and not a[i].isdigit()) or (j < len_b and not b[j].isdigit()):
            ac = a[i] if i < len_a else None
            bc = b[j] if j < len_b else None
            oa, ob = _order(ac), _order(bc)
            if oa != ob:
                return -1 if oa < ob else 1
            if i < len_a:
                i += 1
            if j < len_b:
                j += 1

        start_i = i
        while i < len_a and a[i].isdigit():
            i += 1
        start_j = j
        while j < len_b and b[j].isdigit():
            j += 1

        na = int(a[start_i:i] or "0")
        nb = int(b[start_j:j] or "0")
        if na != nb:
            return -1 if na < nb else 1

    return 0


def _split_version(version: str) -> tuple[int, str, str]:
    """Splits a full Debian version string into (epoch, upstream_version,
    debian_revision) - epoch defaults to 0 (no `N:` prefix), debian_revision
    defaults to `"0"` (no `-` present, matching dpkg's own default)."""
    text = version.strip()
    if not text:
        raise DebianVersionError("empty Debian version string")

    if ":" in text:
        epoch_str, _, rest = text.partition(":")
        if not epoch_str.isdigit():
            raise DebianVersionError(f"non-numeric epoch in Debian version: {version!r}")
        epoch = int(epoch_str)
    else:
        epoch = 0
        rest = text

    if not rest:
        raise DebianVersionError(f"empty upstream_version in Debian version: {version!r}")

    if "-" in rest:
        upstream, _, revision = rest.rpartition("-")
    else:
        upstream, revision = rest, "0"

    if not upstream or not upstream[0].isdigit():
        raise DebianVersionError(
            f"Debian upstream_version must start with a digit: {version!r} (upstream={upstream!r})"
        )

    return epoch, upstream, revision


def compare_debian_versions(a: str, b: str) -> int:
    """Returns -1, 0, or 1 as `a` is older than, equal to, or newer than
    `b`, using real Debian version-ordering semantics (epoch, then
    upstream_version, then debian_revision - each compared via
    `_verrevcmp`, never plain string/tuple comparison). Raises
    DebianVersionError on a string that isn't a well-formed Debian
    version (an empty string, a non-numeric epoch, or an upstream_version
    not starting with a digit) - callers must treat that as "cannot
    establish evidence", never as "assume older"."""
    epoch_a, upstream_a, revision_a = _split_version(a)
    epoch_b, upstream_b, revision_b = _split_version(b)

    if epoch_a != epoch_b:
        return -1 if epoch_a < epoch_b else 1

    upstream_cmp = _verrevcmp(upstream_a, upstream_b)
    if upstream_cmp != 0:
        return upstream_cmp

    return _verrevcmp(revision_a, revision_b)


def is_older(a: str, b: str) -> bool:
    """True iff Debian version `a` is strictly older than `b`."""
    return compare_debian_versions(a, b) < 0
