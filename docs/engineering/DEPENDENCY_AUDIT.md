# Dependency vulnerability audit

Command (run it yourself; this file is a record, not a substitute):

```bash
.venv/bin/python -m pip_audit --progress-spinner off
```

Latest run: **2026-08-16** — 3 known vulnerabilities in 1 package.

## Open findings

### `cryptography` 48.0.1 — 3 advisories, all UNREACHABLE from this app

| ID | Affected code path | Fix version |
|---|---|---|
| PYSEC-2026-3552 | `pkcs7_decrypt_der` / `_pem` / `_smime` — Bleichenbacher oracle when decrypting attacker-supplied `EnvelopedData` | 50.0.0 |
| PYSEC-2026-3553 | X.509 chain building — exponential blowup on chains with duplicated self-signed certs | 49.0.0 |
| PYSEC-2026-3554 | X.509 name constraints — a leaf wildcard SAN can escape a constrained CA's permitted DNS name | 49.0.0 |

**Status: accepted risk, with evidence.**

Two facts, both verifiable:

1. **The fix is not installable.** The advisories name 49.0.0 and 50.0.0. The
   newest version on PyPI today is **48.0.1** — the fixed releases are not
   published yet. `pip install "cryptography>=49"` fails with "no matching
   distribution". There is no upgrade to take.

2. **None of the affected code is reachable here.** This application's entire
   use of the library is three imports in `app/services/secure_store.py`:

   ```python
   from cryptography.fernet import Fernet                        # symmetric encryption
   from cryptography.hazmat.primitives import hashes             # SHA-256 for HKDF
   from cryptography.hazmat.primitives.kdf.hkdf import HKDF      # key derivation
   ```

   Verify with:

   ```bash
   grep -rn "from cryptography\|import cryptography" app/ scripts/ tests/
   grep -rn "pkcs7\|PKCS7\|EnvelopedData" app/ scripts/ tests/   # → no matches
   grep -rn "x509\|X509\|PolicyBuilder" app/ scripts/ tests/     # → no matches
   ```

   All three advisories are in PKCS#7 decryption and X.509 certificate-chain
   verification. This app does neither: it never parses a certificate, never
   builds a trust chain, and never decrypts PKCS#7. Fernet and HKDF are not
   implicated by any of the three.

**Action when the fix ships:** upgrade to `cryptography>=50.0.0`, re-run
`pip_audit`, and delete this section. The pinned floor in `requirements.txt`
should be raised at the same time.

**Note on the canary:** Fernet's token format is stable across releases, but
after any upgrade of this library, confirm that already-encrypted settings
still decrypt before trusting the install — `tests/test_sms_secure_storage.py`
covers the round-trip.

## How this is meant to be used

`pip_audit` is a **reporting** gate, not a blocking one, for a reason: an
advisory can name a fix version that does not exist yet (exactly the case
above), and a build that hard-fails on that is a build nobody can ship. CI runs
it and surfaces the result; a human decides whether a finding is reachable.

Every accepted risk in this file must carry the evidence for why it is
accepted — a grep anyone can re-run, not an assertion.
