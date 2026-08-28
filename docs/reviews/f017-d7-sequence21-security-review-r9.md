# F017 D7 Sequence 21 security review R9

## Reviewed boundary

- Commit: `0f9af8e1e1a83682f4c7f9a8edfaa36428a0aa91`
- Tree: `4a39b8f9da0f6c618c89da4c74d007ac72c1276c`
- Repair implementation: `96f5af17164d9c0c94d604f1b8b598ffe85716c8`
- Remote source base: `d06b03d9b61f0176d8c4f8ec2883eaf0331d83c2`

## Terminal disposition

`ACCEPT`

Claude and authenticated AGY independently returned terminal `ACCEPT` on the
same exact commit and tree. All R5 through R8 actionable findings are closed,
and neither reviewer found a new actionable defect.

Claude reproduced 430 repository tests and executed roughly 21,000 additional
falsification cases across CPython 3.11, 3.13, and 3.14. The review measured the
3.14 decoder/encoder nesting transition, proved the R9 depth sweep traverses
the formerly vulnerable re-encode window, and observed zero non-
`ReadinessError` escapes. All 30 cross-type swaps, sealed-record deletion and
forgery attempts, hostile string/byte inputs, registry tampering, lifecycle
violations, identity substitutions, and capability probes failed closed.

AGY independently inspected the exact boundary under a minimized environment,
unchanged permission configuration, read-only project access, and an explicit
prohibition on terminal, environment, network, and write tools. It accepted
all R5 through R8 closures with zero findings.

Non-actionable advisories remain accurately bounded:

- Exact depth transitions vary by CPython version, while the fail-closed
  invariant holds on all reviewed versions.
- Dynamic internal subclass construction does not bypass exact registered-type
  checks and remains inside the documented trusted-process boundary.
- The non-finite JSON hook works as intended but lacks a dedicated regression;
  downstream validation remains fail closed even without that redundant hook.

No browser, chat, transport, checkpoint, inference, posting, publication, or
runtime delivery capability was added.

## Local raw evidence integrity

Raw provider envelopes remain local because they contain provider session
metadata and machine-local paths.

- Claude envelope SHA-256:
  `7e4189bed41304e653bc34ca575e5289e54c1791d093bd27de01bc1df9cf9a9a`
- AGY envelope SHA-256:
  `a7939e527627a3365e07a255a8f65ced687ca892b08c43f934d5e1723b145a50`

Final state: `ACCEPTED_FOR_STRICT_FAST_FORWARD_PUBLICATION`.
