# Dependency vulnerability audit

This file is a record of accepted risk, not a substitute for running the audit.
Every claim here comes with the command that proves it. Re-run them.

**Last verified: 2026-09-19.**

## Running the audit

CI runs exactly this (`.github/workflows/ci.yml:143`):

```bash
pip-audit -r requirements.txt --progress-spinner off
```

`pip-audit` is **not** installed in `.venv`, so a local run needs it first.
Install it somewhere throwaway rather than into the project venv:

```bash
python3 -m venv /tmp/auditvenv
/tmp/auditvenv/bin/pip install pip-audit
/tmp/auditvenv/bin/pip-audit -r requirements.txt --progress-spinner off
```

(The older version of this file told you to run `.venv/bin/python -m pip_audit`.
That command fails today with `No module named pip_audit`.)

## Current state: no open findings

Result of the command above, run 2026-09-19:

```
No known vulnerabilities found
```

Nothing in `requirements.txt` carries a known advisory right now. There is
nothing to accept, so there is nothing in an "accepted risk" section below.

## Closed: `cryptography` PYSEC-2026-3552/3553/3554

Keeping the history because the reasoning is the template for the next
finding, and because a CI comment still refers to it.

**What it was.** Three advisories against `cryptography`:

| ID | Affected code path | Fixed in |
|---|---|---|
| PYSEC-2026-3552 | PKCS#7 decryption (`pkcs7_decrypt_der` / `_pem` / `_smime`), a Bleichenbacher oracle on attacker-supplied `EnvelopedData` | 50.0.0 |
| PYSEC-2026-3553 | X.509 chain building, exponential blowup on chains with duplicated self-signed certs | 49.0.0 |
| PYSEC-2026-3554 | X.509 name constraints, a leaf wildcard SAN escaping a constrained CA's permitted DNS name | 49.0.0 |

**Why it was accepted at the time.** Two reasons were recorded on 2026-08-16.
Both were true then. One has since changed.

1. *The fix was not installable.* The advisories named 49.0.0 and 50.0.0, and
   the newest release on PyPI was 48.0.1.
2. *None of the affected code was reachable from this app.*

**What changed: reason 1 is dead. The fixed releases shipped.**

```bash
.venv/bin/python -m pip index versions cryptography
```

Output on 2026-09-19:

```
cryptography (50.0.1)
Available versions: 50.0.1, 50.0.0, 49.0.0, 48.0.1, ...
  INSTALLED: 50.0.1
  LATEST:    50.0.1
```

49.0.0 and 50.0.0 are published, 50.0.1 is current, and the local `.venv`
already has 50.0.1. All three advisories are fixed in the installed version.
Confirmed directly:

```bash
echo "cryptography==50.0.1" > /tmp/req.txt
/tmp/auditvenv/bin/pip-audit -r /tmp/req.txt --progress-spinner off
# → No known vulnerabilities found
```

**Reason 2 still holds, and is still worth having.** This application's entire
use of the library is three imports in `app/services/secure_store.py:66-68`:

```python
from cryptography.fernet import Fernet                        # symmetric encryption
from cryptography.hazmat.primitives import hashes             # SHA-256 for HKDF
from cryptography.hazmat.primitives.kdf.hkdf import HKDF      # key derivation
```

Re-run the reachability check:

```bash
grep -rn "from cryptography\|import cryptography" app/ scripts/ tests/
# → only the three lines above, all in app/services/secure_store.py

grep -rni "pkcs7\|x509\|EnvelopedData\|PolicyBuilder" app/ scripts/ tests/
# → no matches
```

This app never parses a certificate, never builds a trust chain, and never
decrypts PKCS#7. Fernet and HKDF were not implicated by any of the three
advisories. So even on an old install that never upgraded, the exposure was
zero.

## The real remaining gap: nothing is pinned

`requirements.txt` declares package **names** with no version constraint at
all. Not for `cryptography`, not for anything:

```bash
grep -vE "^\s*#|^\s*$" requirements.txt | grep -E "[=<>!~]"
# → no output: not one of the 17 packages carries a version specifier
```

Two consequences a reader should know about.

1. A fresh `pip install -r requirements.txt` today resolves `cryptography` to
   50.0.1 and is clean. Good by accident, not by construction.
2. Nothing stops an existing environment from sitting on an old, vulnerable
   version forever. There is no floor to violate. The old version of this file
   said to "raise the pinned floor" once the fix shipped. There was never a
   floor to raise.

If someone wants the guarantee rather than the luck, the fix is a floor:
`cryptography>=50.0.0`. That is a change to `requirements.txt`, which this
document does not make.

**After any upgrade of `cryptography`, check the encryption canary.** Fernet's
token format is stable across releases, but already-encrypted settings must
still decrypt or an install loses its stored secrets.
`tests/test_sms_secure_storage.py` covers the round-trip:

```bash
.venv/bin/python -m pytest tests/test_sms_secure_storage.py -q
```

## Why the CI job does not block

`dependency-audit` in `.github/workflows/ci.yml:128-153` is
`continue-on-error: true`, and both audit steps end in `|| true`. That is
deliberate.

An advisory can name a fix version that is not published yet. That is exactly
what happened above: for weeks there was a real advisory with no installable
fix. A build that hard-fails on an unfixable finding is a build nobody can
ship, and the usual response is that someone disables the check entirely. So
the job reports, uploads `pip-audit.json` as an artifact, and a human decides
whether a finding is reachable.

Note for whoever next edits CI: the comment at `ci.yml:123-127` says the
unpublished-fix problem "is the live situation for `cryptography` right now".
That is no longer true (see above). The comment is stale, the policy behind it
is not.

## The rule for the next finding

Every accepted risk in this file must carry the evidence for why it is
accepted. A grep or a command anyone can re-run, not an assertion. If you
cannot write the command, you have not verified the risk, you have assumed it.
