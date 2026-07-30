# Remote-State Synchronization at 15 us: Design

## Goal

Change every periodic cross-node or remote-state synchronization cadence used
by the active packet-level load-balancing schemes from 5 us to 15 us, then
measure the network-performance change against an explicit 5 us baseline.

## Scope

The change covers four schemes and their remote-state paths:

- SGLB: downstream GCN export snapshots.
- n-MRC/NetAware: spine export snapshots and the full path-level vector sent
  from the source ToR to the source NIC.
- Avail: the shared STOR availability bitmap sent from the source ToR to the
  source NIC.
- Grade: the shared STOR graded path vector sent from the source ToR to the
  source NIC.

The following values change from 5 us to 15 us:

- `FatTreeSwitch::_sglb_gcn_update_interval`
- `FatTreeSwitch::_netaware_remote_update_interval`
- `FatTreeSwitch::_netaware_feedback_min_interval`
- `FatTreeSwitch::_netaware_feedback_max_interval`
- `FatTreeSwitch::_stor_feedback_min_interval`
- `FatTreeSwitch::_stor_feedback_max_interval`
- `FatTreeSwitch::_stor_trim_feedback_min_interval`

Active experiment commands, validation checks, tests, and current
documentation that define these cadences must use 15 us. Historical output
artifacts remain unchanged and retain their original labels.

Local 1 us quality/state refresh intervals, SGLB's 30 us remote-state aging
interval, scoring decay constants, hold-down timers, transport timers, and
logging sample periods are outside scope.

## Implementation

Update the simulator defaults and the active runner configuration from 5 us
to 15 us. Preserve explicit command-line controls so the comparison runner can
still request a 5 us baseline after the defaults change.

Add a dedicated paired comparison runner. It must construct both cadence
variants from the same simulator binary, traffic matrix, seed, topology,
transport configuration, scoring configuration, and runtime cutoff. The only
difference within a pair is the set of remote synchronization intervals:

- `remote5`: all seven scoped intervals are 5 us.
- `remote15`: all seven scoped intervals are 15 us.

The runner must validate the simulator's effective configuration from stdout,
fingerprint the binary, traffic matrix, and command, preserve commands and raw
logs, and reject incomplete or misconfigured runs.

## Experiment Matrix

Use 128 nodes and seeds 13, 29, and 47. Run these three representative
scenarios:

1. Healthy permutation: guardrail for overhead or instability without a
   persistent path-quality difference.
2. Asymmetric permutation: tests response to stable degraded paths.
3. All-to-All with background traffic: tests collective completion under
   remote, time-varying congestion.

Run SGLB, Avail, Grade, and n-MRC at both `remote5` and `remote15`.
The complete matrix contains:

`3 scenarios * 3 seeds * 4 schemes * 2 cadences = 72 runs`.

## Metrics and Reporting

For point-to-point scenarios, report average, p99, p99.9, and maximum FCT.
For All-to-All, report CCT. For every scenario also report RTO count,
retransmission ratio, TRIM and ECN counts, feedback count, remote-snapshot
availability diagnostics where the scheme exposes them, completed-flow
counts, and simulator wall-clock runtime.

The primary comparison is paired within the same scenario, seed, and scheme:

`change = (metric_remote15 / metric_remote5 - 1) * 100%`.

Summaries use the median across the three seeds and retain all per-seed values
and ranges. Lower FCT, CCT, retransmission, and recovery counts are better.
Feedback-count reduction is reported as synchronization overhead, not treated
as a performance win unless latency and completion guardrails also hold.

Artifacts include:

- one raw directory per run with command, stdout, return code, runtime, and
  fingerprint;
- one row-level CSV containing all 72 runs;
- one paired-comparison CSV;
- one concise Markdown report with per-scenario tables and an overall summary.

## Testing and Acceptance

Use test-first changes for simulator defaults, interval-boundary behavior,
runner command construction, configuration validation, pairing, and report
calculations.

Acceptance requires:

- all scoped defaults and active overrides are 15 us;
- unrelated local, aging, scoring, and transport intervals remain unchanged;
- focused unit and runner tests pass;
- the simulator builds successfully;
- all 72 runs complete with the expected flow count and effective cadence;
- the report contains every scenario, seed, scheme, and cadence pair;
- performance conclusions cite measured paired results and disclose
  regressions, incomplete runs, and seed sensitivity.
