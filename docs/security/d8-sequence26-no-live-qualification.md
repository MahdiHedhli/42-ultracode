# D8 Sequence 26 no-live qualification

Sequence 26 qualification uses deterministic in-memory fake peers only. Production `subprocess.Popen` is monkeypatched to fail if reached during the 20-run reconstruction test. The fixture exercises complete `archived:false` and `archived:true` pagination, exact source/cwd/status checks, read/resume/final-read revalidation, one durable attempt marker, one turn, and terminal completion.

Sequence 27 executable coverage adds target absence, greater-than-64-KiB stderr rejection, real harmless Darwin child cleanup, descendant containment after parent reap, and exact pre/post-write status-notification orderings. Archive membership inversion, duplicate identities, source/cwd drift, cursor cycles, strict version parsing, malformed envelopes, partial and silent reads, executable drift, capability replay, and journal corruption remain covered. No real task identifier or non-target metadata is returned or persisted.

The six schema digest literals are change-detector pins copied from the planner-reviewed generated-schema authority. This repository does not commit those external generated schema bytes, so the literal test does not claim to recompute or independently verify their hashes.

This evidence is checkpoint-free and no-live. It supports planner review only and does not authorize a live pilot.
