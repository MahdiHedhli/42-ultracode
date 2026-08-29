# D8 Sequence 26 no-live qualification

Sequence 26 qualification uses deterministic in-memory fake peers only. Production `subprocess.Popen` is monkeypatched to fail if reached during the 20-run reconstruction test. The fixture exercises complete `archived:false` and `archived:true` pagination, exact source/cwd/status checks, read/resume/final-read revalidation, one durable attempt marker, one turn, and terminal completion.

Mutation coverage includes archive membership inversion, absent and duplicate target identities, cross-page duplicate identities, source/cwd drift, active status, cursor cycles, strict version parsing, malformed protocol envelopes, partial and silent transport reads, stderr sensitivity and bounds, executable identity drift, confirmation capability replay, journal corruption, and pre/post-attempt status races. No real task identifier or non-target metadata is returned or persisted.

This evidence is checkpoint-free and no-live. It supports planner review only and does not authorize a live pilot.
