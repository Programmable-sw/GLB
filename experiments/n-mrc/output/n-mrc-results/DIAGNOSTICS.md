# Completion diagnostics

The final matrix is complete: 690/690 raw cells passed the runner's identity,
configuration, flow-completion, mixed-label, and finite-primary-metric checks.

## Retried cell

The first attempt of
`asymmetric_alltoall_background_1024mib_p16 / mrc / seed 13` exited with
SIGSEGV after 8,083/16,256 flows. Its original summary, command, and compressed
stdout are retained under `diagnostics/initial_mrc_seed13_crash/` and are not
part of the final 690-cell result table.

An exact retry used the same simulator binary, traffic matrix, command, and
fingerprint (`528315ea863dbe003d068d0b21c3411e8ce3f44fd3d6e5118a841397d155cb58`).
It exited successfully with 16,256/16,256 unique flow completions in 742.93 s;
its all-to-all CCT was 31,364.8 us. The successful stdout replaced only that
cell's failed raw artifact before the canonical runner revalidated the complete
matrix.

The production simulator SHA-256 is
`1869868e9325bf91d47f7712d68284d4efb63eea2fb74f489e7e8cca39de2eb8`.

## Diagnostic metadata refresh

The first aggregation parser did not consume scientific-notation exponents in
diagnostic key/value fields. This did not affect flow completions or any primary
FCT/CCT metric. The parser was corrected and all 690 retained stdout logs were
reparsed: 18 diagnostic values across 10 cells changed. The exact before/after
inventory is retained in `diagnostics/diagnostic_parser_refresh.json`, and the
corrected values are present in each raw `summary.json` and in `results.csv`.

## Crash triage

The original crash was in `RoceSink::receivePacket` while accessing its
out-of-order packet map. Exact shorter replays with the production binary and
an isolated ASan/UBSan build passed the original simulated time and flow count;
the complete exact retry also passed. Source, layout, warning, and sanitizer
audits did not identify a deterministic writer of the corrupted map node, so no
speculative simulator fix was applied.

ASan did find an independent setup-time `new[]`/scalar-`delete` mismatch in
`main_roce.cpp`. A clean-build comparison also found a stale `tcp_transfer.o`
layout caused by an incomplete Makefile dependency. That class is not
instantiated by this `main_roce` experiment path, while the production and clean
`roce.o` and `network.o` runtime sections and the relevant `RoceSink` layout and
instructions match. These findings therefore do not explain the failed attempt,
but should be fixed before unrelated simulator development continues.

## Background-traffic model caveat

Periodic all-to-all background uses `TcpPacket` as a fixed-rate dummy packet.
Unlike `RocePacket`, `TcpPacket` does not reduce its byte size when
`CompositeQueue` marks it header-only. Under congestion, a trimmed 4,096-byte
background packet can therefore remain 4,096 bytes in the high-priority queue.
Results for `asymmetric_alltoall_background_*` should be interpreted under this
implemented background model; changing that behavior would define a different
experiment and require rerunning those cells.
