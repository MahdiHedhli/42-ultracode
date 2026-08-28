# F017 D7 Sequence 21 security review R8

## Reviewed boundary

- Commit: `f01d584c1e69ca247256e5778c43c41a7f203376`
- Tree: `55aa9b3f7c2a2ded887e985c04f1063a16a86204`
- Repair implementation: `90cf725fe620d804231242dc1ecf4e121a8630cd`

## Terminal disposition

`REJECT`

Claude returned `REJECT`; authenticated AGY returned `ACCEPT`. The stronger
falsifying disposition governs. Both reviewers verified every R5 and R6
finding closed. Claude showed that one R7 manifestation remained reachable:
for exact-field JSON at nesting depths around 104,600 through 116,222,
`json.loads` succeeded but canonical re-encoding raised bare `RecursionError`.

The R8 depth regression was vacuous because it used the wrong field set and
short-circuited before canonical re-encoding. The bounded repair normalizes
canonical re-encoding failures and replaces that test with exact-field handoff
and observation payloads swept across the identified depth window.

No forged authority, digest bypass, lifecycle transition, or delivery
capability was produced.

## Local raw evidence integrity

Raw provider envelopes remain local because they contain provider session
metadata and machine-local paths.

- Claude envelope SHA-256:
  `095dea600175dbe76af1580738154139b27543a9ac7fe4d751843f454a7ffae6`
- AGY envelope SHA-256:
  `627f011a74409fbf5252ab567c424632a432e71cd9f4de1f418e581b69d27874`

Final state: `REJECTED_FOR_BOUNDED_R9_REPAIR`.
