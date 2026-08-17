-- DuckDB SQL. Run from the repository root.
-- JSON counters come from the final per-seed results.csv diagnostics_json column.
WITH raw AS (
    SELECT
        scenario,
        scheme,
        expected_flows,
        CASE WHEN scenario = 'mixed_deployment_permutation_16mib'
            THEN main_max_fct_us ELSE primary_us END AS completion_value,
        ecmp_background_max_fct_us,
        CAST(json_extract(diagnostics_json,
            '$."HybridNmrcDiag.route_checks"') AS DOUBLE) AS route_checks,
        CAST(json_extract(diagnostics_json,
            '$."HybridNmrcDiag.reroutes"') AS DOUBLE) AS reroutes,
        CAST(json_extract(diagnostics_json,
            '$."HybridNmrcDiag.all_cooling_fallbacks"') AS DOUBLE) AS all_cooling,
        CAST(json_extract(diagnostics_json,
            '$."QueueDiag.composite_ecn_marks"') AS DOUBLE) AS ecn_marks,
        CAST(json_extract(diagnostics_json,
            '$."QueueDiag.composite_trims"') AS DOUBLE) AS trims,
        CAST(json_extract(diagnostics_json,
            '$."QueueCvDiag.spine_queue_avg"') AS DOUBLE) AS queue_mean
    FROM read_csv_auto(
        'experiments/n-mrc/output/final_packet_lb_comparison_20260720/results.csv',
        header = true,
        all_varchar = true
    )
    WHERE scheme IN ('mrc', 'n-mrc')
      AND scenario IN (
        'healthy_p2p_websearch_100pct',
        'healthy_alltoall_1024mib_p16',
        'asymmetric_p2p_tornado_16mib',
        'mixed_deployment_permutation_16mib'
      )
), agg AS (
    SELECT
        scenario,
        scheme,
        exp(avg(ln(CAST(completion_value AS DOUBLE)))) AS completion_geomean,
        exp(avg(ln(NULLIF(CAST(ecmp_background_max_fct_us AS DOUBLE), 0))))
            AS background_geomean,
        sum(CAST(expected_flows AS DOUBLE)) AS flows,
        sum(coalesce(route_checks, 0)) AS route_checks,
        sum(coalesce(reroutes, 0)) AS reroutes,
        sum(coalesce(all_cooling, 0)) AS all_cooling,
        sum(coalesce(ecn_marks, 0)) AS ecn_marks,
        sum(coalesce(trims, 0)) AS trims,
        avg(queue_mean) AS queue_mean
    FROM raw
    GROUP BY scenario, scheme
), paired AS (
    SELECT
        n.scenario,
        100 * (n.completion_geomean / m.completion_geomean - 1)
            AS performance_delta_pct,
        n.route_checks / n.flows AS route_checks_per_flow,
        n.reroutes / n.flows AS reroutes_per_flow,
        n.all_cooling / n.flows AS all_cooling_per_flow,
        n.ecn_marks / NULLIF(m.ecn_marks, 0) AS ecn_mark_ratio,
        n.trims / NULLIF(m.trims, 0) AS trim_ratio,
        n.queue_mean / NULLIF(m.queue_mean, 0) AS spine_queue_mean_ratio,
        100 * (n.background_geomean / NULLIF(m.background_geomean, 0) - 1)
            AS background_delta_pct
    FROM agg AS n
    JOIN agg AS m USING (scenario)
    WHERE n.scheme = 'n-mrc' AND m.scheme = 'mrc'
)
SELECT
    CASE scenario
        WHEN 'healthy_p2p_websearch_100pct' THEN '健康 WebSearch 100%'
        WHEN 'healthy_alltoall_1024mib_p16' THEN '健康 A2A 1024 MiB P=16'
        WHEN 'asymmetric_p2p_tornado_16mib' THEN '非对称 Tornado 16 MiB'
        ELSE '混合部署 Permutation 16 MiB'
    END AS scenario,
    round(performance_delta_pct, 2) AS performance_delta_pct,
    CASE scenario
        WHEN 'healthy_p2p_websearch_100pct' THEN 'p99 +20.89%'
        WHEN 'healthy_alltoall_1024mib_p16' THEN 'CCT +16.06%'
        WHEN 'asymmetric_p2p_tornado_16mib' THEN 'p99 -26.32%'
        ELSE printf('主流 +7.00%%；ECMP bg %.2f%%', background_delta_pct)
    END AS observed_effect,
    round(route_checks_per_flow, 2) AS route_checks_per_flow,
    round(reroutes_per_flow, 2) AS reroutes_per_flow,
    round(all_cooling_per_flow, 2) AS all_cooling_per_flow,
    round(ecn_mark_ratio, 3) AS ecn_mark_ratio,
    round(trim_ratio, 3) AS trim_ratio,
    round(spine_queue_mean_ratio, 3) AS spine_queue_mean_ratio,
    CASE scenario
        WHEN 'healthy_p2p_websearch_100pct'
            THEN '更均匀但更深的常驻队列；中位数改善、尾部恶化'
        WHEN 'healthy_alltoall_1024mib_p16'
            THEN '大量逐包改写与冷却；所有完成时间分位数同步恶化'
        WHEN 'asymmetric_p2p_tornado_16mib'
            THEN '少量定向绕路避开持久半速链路，ECN 明显下降'
        ELSE '保护固定 ECMP 背景流，但主流最大 FCT 出现代价'
    END AS interpretation
FROM paired;
