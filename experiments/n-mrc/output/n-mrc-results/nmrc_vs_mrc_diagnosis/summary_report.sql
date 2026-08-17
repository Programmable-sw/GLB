-- DuckDB SQL. Run from the repository root.
-- Input is the final, reviewed three-seed summary produced by the experiment runner.
WITH paired AS (
    SELECT
        n.scenario,
        n.family,
        n.primary_geomean_us / NULLIF(m.primary_geomean_us, 0) AS primary_ratio,
        n.main_max_geomean_us / NULLIF(m.main_max_geomean_us, 0) AS main_ratio,
        n.ecmp_background_max_geomean_us
            / NULLIF(m.ecmp_background_max_geomean_us, 0) AS background_ratio
    FROM read_csv_auto(
        'experiments/n-mrc/output/final_packet_lb_comparison_20260720/summary.csv',
        header = true
    ) AS n
    JOIN read_csv_auto(
        'experiments/n-mrc/output/final_packet_lb_comparison_20260720/summary.csv',
        header = true
    ) AS m USING (scenario)
    WHERE n.scheme = 'n-mrc' AND m.scheme = 'mrc'
), family_rows AS (
    SELECT family AS family_key, primary_ratio AS ratio
    FROM paired
    WHERE family <> 'mixed_deployment'
    UNION ALL
    SELECT 'mixed_main', main_ratio
    FROM paired
    WHERE family = 'mixed_deployment'
    UNION ALL
    SELECT 'mixed_background', background_ratio
    FROM paired
    WHERE family = 'mixed_deployment'
), family_agg AS (
    SELECT
        family_key,
        100 * (exp(avg(ln(ratio))) - 1) AS delta_pct,
        count(*) AS scenario_count,
        sum(CASE WHEN ratio < 1 THEN 1 ELSE 0 END) AS better_cells,
        sum(CASE WHEN ratio >= 1 THEN 1 ELSE 0 END) AS worse_or_tied_cells
    FROM family_rows
    GROUP BY family_key
)
SELECT
    CASE family_key
        WHEN 'healthy_p2p' THEN '健康 P2P'
        WHEN 'asymmetric_p2p' THEN '非对称 P2P'
        WHEN 'healthy_alltoall' THEN '健康 A2A'
        WHEN 'asymmetric_alltoall_background' THEN '非对称+周期背景 A2A'
        WHEN 'mixed_main' THEN '混合部署主流量'
        WHEN 'mixed_background' THEN '混合部署 ECMP 背景'
    END AS family,
    round(delta_pct, 2) AS delta_pct,
    scenario_count,
    better_cells,
    worse_or_tied_cells,
    CASE
        WHEN family_key IN ('healthy_p2p', 'asymmetric_p2p') THEN 'p99 FCT'
        WHEN family_key IN ('healthy_alltoall', 'asymmetric_alltoall_background') THEN 'CCT'
        WHEN family_key = 'mixed_main' THEN 'bg=0 最大 FCT'
        ELSE 'bg=1 最大 FCT'
    END AS primary_metric,
    CASE family_key
        WHEN 'healthy_p2p' THEN 'WebSearch 100%: +20.89%'
        WHEN 'asymmetric_p2p' THEN 'Tornado 16 MiB: -26.32%'
        WHEN 'healthy_alltoall' THEN '1024 MiB P=16: +16.06%'
        WHEN 'asymmetric_alltoall_background' THEN '64 MiB P=4: -15.56%'
        WHEN 'mixed_main' THEN 'Permutation 16 MiB: +7.00%'
        WHEN 'mixed_background' THEN '单配置改善 14.16%–35.83%'
    END AS representative_result,
    CASE family_key
        WHEN 'healthy_p2p' THEN '对称；长流与 WebSearch'
        WHEN 'asymmetric_p2p' THEN '3% ToR 上行半速'
        WHEN 'healthy_alltoall' THEN '对称；并发集体通信'
        WHEN 'asymmetric_alltoall_background' THEN '半速链路与周期背景并存'
        WHEN 'mixed_main' THEN '90% 被测算法 + 10% ECMP'
        ELSE '每第十条连接固定 ECMP'
    END AS traffic_structure,
    family_key IN ('asymmetric_p2p', 'asymmetric_alltoall_background') AS persistent_bad_link
FROM family_agg
ORDER BY CASE family_key
    WHEN 'healthy_p2p' THEN 1
    WHEN 'asymmetric_p2p' THEN 2
    WHEN 'healthy_alltoall' THEN 3
    WHEN 'asymmetric_alltoall_background' THEN 4
    WHEN 'mixed_main' THEN 5
    ELSE 6
END;

-- Headline strip, computed from the same paired table.
WITH paired AS (
    SELECT
        n.scenario,
        n.family,
        n.primary_geomean_us / NULLIF(m.primary_geomean_us, 0) AS primary_ratio,
        n.ecmp_background_max_geomean_us
            / NULLIF(m.ecmp_background_max_geomean_us, 0) AS background_ratio
    FROM read_csv_auto(
        'experiments/n-mrc/output/final_packet_lb_comparison_20260720/summary.csv',
        header = true
    ) AS n
    JOIN read_csv_auto(
        'experiments/n-mrc/output/final_packet_lb_comparison_20260720/summary.csv',
        header = true
    ) AS m USING (scenario)
    WHERE n.scheme = 'n-mrc' AND m.scheme = 'mrc'
)
SELECT
    round(100 * (max(primary_ratio) FILTER (
        WHERE scenario = 'healthy_p2p_websearch_100pct') - 1), 2)
        AS healthy_ws100_p99_delta_pct,
    round(100 * (exp(avg(ln(primary_ratio))) FILTER (
        WHERE family = 'healthy_alltoall') - 1), 2)
        AS healthy_a2a_geo_delta_pct,
    round(100 * (max(primary_ratio) FILTER (
        WHERE scenario = 'asymmetric_p2p_tornado_16mib') - 1), 2)
        AS asym_tornado16_p99_delta_pct,
    round(100 * (exp(avg(ln(background_ratio))) FILTER (
        WHERE family = 'mixed_deployment') - 1), 2)
        AS mixed_bg_geo_delta_pct
FROM paired;
