# Baseline build — `hardad:baseline`

## Build summary

| Field | Value |
|---|---|
| Image tag | `hardad:baseline` |
| Image digest | `7df47f8cacf9` |
| Platform | `linux/arm64` |
| Base image | `python:3.12-slim` (Debian 13 "trixie", Python 3.12.13) |
| Base image digest | `sha256:c0f9f8769445b9117692977abd450e79a6313139c8bedbb61c500b687f7b5ddf` |
| Build pattern | Multi-stage (`builder` → runtime), uv-installed venv copied across |
| Runtime user | non-root (`app`, uid 1000) |

## Headline metrics

These are the four numbers worth putting next to the hardened build's equivalents in `docs/diff.md` after Step 5.

| Metric | Baseline value |
|---|---|
| **Image size** | **86 MB** |
| **Total packages in SBOM** | **167** |
| **Total CVEs** | **33** |
| **Critical + High CVEs** | **3** (0 critical, 3 high) |

## CVE breakdown by severity

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 3 |
| Medium | 3 |
| Low | 25 |
| Unspecified | 2 |
| **Total** | **33** |

All 3 high-severity CVEs are in `openssl 3.5.5-1~deb13u1`:

- CVE-2026-28390 — fixed in `3.5.5-1~deb13u2`
- CVE-2026-28389 — fixed in `3.5.5-1~deb13u2`
- CVE-2026-28388 — fixed in `3.5.5-1~deb13u2`

The Debian base image hasn't yet absorbed the upstream openssl patch. Worth flagging in the writeup: this is the *normal* pattern for community base images — there's a real lag between an upstream fix landing and the base image picking it up. DHI's 7-day SLA on critical CVE remediation in the Enterprise tier is the direct counter-narrative.

## CVEs by package

| Package | Version | Total | C | H | M | L | Unspec |
|---|---|---|---|---|---|---|---|
| openssl | 3.5.5-1~deb13u1 | 8 | 0 | 3 | 0 | 3 | 2 |
| glibc | 2.41-12+deb13u2 | 7 | 0 | 0 | 0 | 7 | 0 |
| systemd | 257.9-1~deb13u1 | 4 | 0 | 0 | 0 | 4 | 0 |
| tar | 1.35+dfsg-3.1 | 3 | 0 | 0 | 2 | 1 | 0 |
| pip | 25.0.1 | 2 | 0 | 0 | 1 | 1 | 0 |
| coreutils | 9.7-3 | 2 | 0 | 0 | 0 | 2 | 0 |
| util-linux | 2.41-5 | 2 | 0 | 0 | 0 | 2 | 0 |
| sqlite3 | 3.46.1-7+deb13u1 | 2 | 0 | 0 | 0 | 2 | 0 |
| shadow | 1:4.17.4-2 | 1 | 0 | 0 | 0 | 1 | 0 |
| apt | 3.0.3 | 1 | 0 | 0 | 0 | 1 | 0 |
| perl | 5.40.1-6 | 1 | 0 | 0 | 0 | 1 | 0 |

## SBOM composition

The SBOM lists 167 components. Every one of them is a CVE surface and a thing that has to be tracked through every audit, every scanner, every patch cycle.

| Ecosystem | Package count | What's in there |
|---|---|---|
| `deb` (Debian) | 122 | Base OS userland — glibc, openssl, bash, apt, perl, systemd, coreutils, util-linux, etc. |
| `pypi` (Python) | 36 | The app's actual dependencies — fastapi, anthropic, sqlalchemy, psycopg, plus their transitives |
| `rpm` | 8 | Anomalous; investigate. Probably a base image quirk where Python's bundled tooling registers as RPM-style |
| `generic` | 1 | One uncategorised entry |

The signal worth pulling out: **122 of the 167 packages (73%) are base OS userland that the app code never touches.** The Python app calls into ~36 pypi packages. Everything else is the cost of building on `python:3.12-slim`.

A non-exhaustive sample of debian packages riding along that the Swedish tutor will never use:

`apt`, `gcc-14`, `perl`, `systemd`, `tar`, `passwd`, `shadow`, `login`, `bash`, `sed`, `grep`, `ncurses`, `readline-common`, `libsystemd0`, `libgmp10`, `libbsd0`, `libsepol`, `liblz4-1`, `libseccomp2`, `libzstd1`, `libxxhash0`, `libapt-pkg7.0`, `libpam-modules-bin`, `bsdutils`, `sysvinit-utils`, `netbase`, `base-files`...

This is the "distroless philosophy in quantified form" — every package above is a candidate for elimination when we switch to a DHI image.

## Scout's recommendations

Scout's own `recommendations` output suggests two paths to mitigate without leaving the community-image world:

- Bump to `python:3.13-slim` — eliminates 7 vulnerabilities (3 high, 1 medium, 3 low). Same package count (124 in the base image), similar size (46 MB).
- Bump to `python:3.14-slim` — same delta, slightly larger (47 MB), runtime 3.14.4.

Both are real improvements but represent *patching at the package level inside an unchanged base*. The DHI swap in Step 5 is the categorical move: a different *kind* of base image, not a fresher version of the same one. Worth holding both options open in the writeup so the comparison isn't "DHI vs do nothing" — it's "DHI vs the most diligent thing you could do without leaving community Debian."

## Build context

| Field | Value |
|---|---|
| Build provenance URL | `https://github.com/LennartOnGit/h-rdad.git` |
| Source commit | `bdfe48be0594f7f198411a0cea161e8685ea41ec` |
| Scan date | 2026-04-15 |
| SBOM formats produced | SPDX 2.3 (`sbom.spdx.json`), CycloneDX (`sbom.cdx.json`) |

## Files in this baseline directory

- `cves.txt` — full `docker scout cves` output
- `sbom.spdx.json` — full SPDX SBOM
- `sbom.cdx.json` — full CycloneDX SBOM
- `recommendations.txt` — full Scout recommendations output
- `baseline.md` — this file

## Notes for the Step 5 comparison

When the hardened build lands, the diff to capture in `docs/diff.md`:

- **Image size** — community 86 MB vs DHI Python (likely 60–100 MB depending on variant; *might not shrink dramatically* because Python runtimes are themselves bulky — the win is in package count and CVEs, not necessarily megabytes)
- **Package count** — community 167 vs DHI (expect a meaningful drop, especially in the `deb` ecosystem; pypi count should be unchanged because the app's dependencies don't change)
- **Total CVE count** — community 33 vs DHI (expect near-zero)
- **Critical + High CVEs** — community 3 vs DHI (expect 0)
- **Time-to-remediation** — community openssl CVEs sit unpatched at base; DHI Enterprise commits to <7 days. Worth surfacing as a 5th metric even though it's qualitative.

The narrative for the LinkedIn post writes itself from this diff: *"Same FastAPI app, same dependencies, same code. One line change in the Dockerfile. Image dropped from 167 packages to N. CVEs dropped from 33 to 0. The application doesn't know the difference; the security posture does."*
