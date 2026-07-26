# MRC inherent limitations: controlled experiment report

This is a full run at 128 nodes / 8 physical paths.

## Validation

- Validated cells: 102/102.
- Completed target-flow diagnostics: 2292990.
- Traffic is byte-identical inside every paired comparison.

## Experiment 1: flow lifetime and feedback value

- Overall actionable-feedback fraction: 0.2874.
- Fraction of paired flows with MRC slowdown >5%: 0.2574.
- Interpret size-resolved results in `flow_size_summary.csv` and `paired_flow_metrics.csv`; a no-update flow is a no-learning-opportunity control.

![Experiment 1](figures/exp1_feedback_value_by_flow_size.png)

## Experiment 2: per-QP repeated exploration

Direct evidence is in `shared_key_metrics.csv` and the per-flow publication/consumption/redundancy counters. CCT is secondary.

![Experiment 2](figures/exp2_per_qp_repeated_exploration.png)

## Experiment 3: active EV count

The physical path namespace remains eight; only the endpoint active EV prefix changes.

![Experiment 3](figures/exp3_active_ev_coverage_and_fct.png)

## Caveats

- Failure injection is intentionally excluded.
- WebSearch uses the repository's digitized proxy CDF.
- Mechanism labels describe observed evidence, not unobserved counterfactual path state.
