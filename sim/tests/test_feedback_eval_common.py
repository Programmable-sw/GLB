#!/usr/bin/env python3

import importlib.util
import random
import tempfile
from collections import Counter
from dataclasses import FrozenInstanceError, is_dataclass
from itertools import islice
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "experiments/n-mrc/feedback_eval_common.py"


def load_common():
    spec = importlib.util.spec_from_file_location("feedback_eval_common", COMMON)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {COMMON}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_value_error(action, message):
    try:
        action()
    except ValueError:
        return
    raise AssertionError(message)


def expect_type_error(action, message):
    try:
        action()
    except TypeError:
        return
    raise AssertionError(message)


def assign_item(mapping, key, value):
    mapping[key] = value


def delete_item(mapping, key):
    del mapping[key]


def test_topologies(module):
    assert is_dataclass(module.Topology)
    assert module.TOPOLOGIES == {
        128: module.Topology(128, 64, 2, 64),
        256: module.Topology(256, 64, 4, 64),
        512: module.Topology(512, 64, 8, 64),
        1024: module.Topology(1024, 64, 16, 64),
        2048: module.Topology(2048, 64, 32, 64),
        4096: module.Topology(4096, 64, 64, 64),
        8192: module.Topology(8192, 64, 128, 64),
    }
    assert [module.TOPOLOGIES[nodes].paths
            for nodes in (256, 512, 1024, 2048, 4096, 8192)] == [
        64, 64, 64, 64, 64, 64
    ]
    try:
        module.TOPOLOGIES[128].nodes = 1
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("Topology must be immutable")
    expect_type_error(
        lambda: assign_item(
            module.TOPOLOGIES, 128, module.TOPOLOGIES[128]
        ),
        "TOPOLOGIES assignment must fail",
    )
    expect_type_error(
        lambda: delete_item(module.TOPOLOGIES, 128),
        "TOPOLOGIES deletion must fail",
    )


def test_topology_dimension_validation(module):
    malformed_dimensions = (
        (0, 8, 16, 8),
        (128, 0, 16, 8),
        (128, 8, 0, 8),
        (128, 8, 16, 0),
        (128.0, 8, 16, 8),
        (128, True, 16, 8),
        (127, 8, 16, 8),
        (128, 8, 16, 4),
    )
    for dimensions in malformed_dimensions:
        expect_value_error(
            lambda dimensions=dimensions: module.Topology(*dimensions),
            f"malformed topology must fail: {dimensions}",
        )

    malformed = object.__new__(module.Topology)
    object.__setattr__(malformed, "nodes", 127)
    object.__setattr__(malformed, "hosts_per_leaf", 8)
    object.__setattr__(malformed, "leaves", 16)
    object.__setattr__(malformed, "spines", 8)
    generator_calls = (
        lambda: module.permutation_flows(malformed, 13, 4096),
        lambda: module.tornado_flows(malformed, 4096),
        lambda: module.incast_flows(malformed, 8, 4096),
        lambda: module.mixed_flows(malformed, 4096, 13),
        lambda: module.websearch_flows(malformed, 13, 0.4, 100),
    )
    for action in generator_calls:
        expect_value_error(
            action, "generators must revalidate topology dimensions"
        )


def test_rtt_and_bdp(module):
    assert module.LINKSPEED_MBPS == 400000
    assert module.MTU_BYTES == 4096
    assert module.bdp_packets(400000, 7.0, 4096) == 86
    assert module.bdp_packets(400000, 8.43776, 4096) == 103
    assert module.theoretical_rtt_us(2, 0.5, 0.5) == 7.0
    assert module.theoretical_rtt_us(3, 0.5, 0.5) == 11.0
    expect_value_error(
        lambda: module.theoretical_rtt_us(4, 0.5, 0.5),
        "unsupported tier count must fail",
    )


def test_physical_input_validation(module):
    invalid_physical_values = (0, -1, float("nan"), float("inf"), -float("inf"))
    for bad_value in invalid_physical_values:
        for args in (
            (bad_value, 7.0, 4096),
            (400000, bad_value, 4096),
            (400000, 7.0, bad_value),
        ):
            expect_value_error(
                lambda args=args: module.bdp_packets(*args),
                f"invalid BDP input must fail: {args}",
            )

    assert module.theoretical_rtt_us(2, 0.0, 0.5) == 3.0
    assert module.theoretical_rtt_us(2, 0.5, 0.0) == 4.0
    for hop_latency, switch_latency in (
        (0.0, 0.0),
        (-0.1, 0.5),
        (0.5, -0.1),
        (float("nan"), 0.5),
        (0.5, float("nan")),
        (float("inf"), 0.5),
        (0.5, float("inf")),
    ):
        expect_value_error(
            lambda hop_latency=hop_latency, switch_latency=switch_latency:
                module.theoretical_rtt_us(
                    2, hop_latency, switch_latency
                ),
            "latencies must be finite, non-negative, and produce positive RTT",
        )


def test_runtime_rtt_parser(module):
    output = "RoCE estimated RTT 7us, BDP 350000 bytes\n"
    assert module.parse_runtime_rtt_us(output) == 7.0
    assert module.parse_runtime_rtt_us("RoCE estimated RTT 7us") == 7.0
    assert module.parse_runtime_rtt_us("RoCE estimated RTT 7us\n") == 7.0
    assert module.parse_runtime_rtt_us(
        "prefix\nRoCE estimated RTT 7.25us, BDP 362500 bytes\nsuffix\n"
    ) == 7.25
    for malformed in (
        "no runtime config",
        "RoCE estimated RTT=7us",
        "RoCE estimated RTT 7 us",
        "RoCE estimated RTT 1.2.3us",
        "RoCE estimated RTT 7us.5",
        "RoCE estimated RTT 0us",
        "RoCE estimated RTT -1us",
        "RoCE estimated RTT nanus",
        "RoCE estimated RTT infus",
    ):
        expect_value_error(
            lambda malformed=malformed: module.parse_runtime_rtt_us(malformed),
            f"malformed RTT output must fail: {malformed}",
        )


def test_number(module):
    assert module.number(5) == "5"
    assert module.number(7.0) == "7"
    assert module.number(7.25) == "7.25"
    assert module.number(1.0 / 3.0) == "0.333333333"
    for bad_value in (float("nan"), float("inf"), -float("inf")):
        expect_value_error(
            lambda bad_value=bad_value: module.number(bad_value),
            "non-finite CLI number must fail",
        )


def test_feedback_args(module):
    expected = {
        ("avail", "fixed5"): [
            "-stor_feedback_pkts", "1",
            "-stor_feedback_min_us", "5",
            "-stor_feedback_max_us", "5",
            "-stor_trim_feedback_min_us", "5",
        ],
        ("grade", "fixed_rtt"): [
            "-stor_feedback_pkts", "1",
            "-stor_feedback_min_us", "7",
            "-stor_feedback_max_us", "7",
            "-stor_trim_feedback_min_us", "7",
        ],
        ("grade", "bdp_triggered"): [
            "-stor_feedback_pkts", "86",
            "-stor_feedback_min_us", "5",
            "-stor_feedback_max_us", "14",
            "-stor_trim_feedback_min_us", "14",
        ],
    }
    for (scheme, cadence), args in expected.items():
        assert module.feedback_args(scheme, cadence, 7.0) == args

    assert module.feedback_args("avail", "fixed_rtt", 7.25) == [
        "-stor_feedback_pkts", "1",
        "-stor_feedback_min_us", "7.25",
        "-stor_feedback_max_us", "7.25",
        "-stor_trim_feedback_min_us", "7.25",
    ]
    expect_value_error(
        lambda: module.feedback_args("mrc", "fixed5", 7.0),
        "unknown scheme must fail",
    )
    expect_value_error(
        lambda: module.feedback_args("netaware", "fixed5", 7.0),
        "n-MRC cadence must remain fixed in the simulator",
    )
    expect_value_error(
        lambda: module.feedback_args("avail", "legacy_96", 7.0),
        "heuristic cadence must not be accepted",
    )
    for bad_rtt in (0, -1, float("nan"), float("inf"), -float("inf")):
        expect_value_error(
            lambda bad_rtt=bad_rtt: module.feedback_args(
                "avail", "fixed5", bad_rtt
            ),
            "feedback RTT must be finite and positive",
        )
    expect_value_error(
        lambda: module.feedback_args("grade", "bdp_triggered", 2.49),
        "BDP-triggered cadence must not have max below min",
    )
    assert module.feedback_args("grade", "bdp_triggered", 2.5) == [
        "-stor_feedback_pkts", "31",
        "-stor_feedback_min_us", "5",
        "-stor_feedback_max_us", "5",
        "-stor_trim_feedback_min_us", "5",
    ]


def test_flow_and_scenario_records(module):
    flow = module.Flow(0, 1, 4096)
    assert flow == module.Flow(0, 1, 4096, 0.0, "target", 0)
    assert is_dataclass(module.Flow)
    assert is_dataclass(module.Scenario)
    for record, field in ((flow, "size_bytes"), (
            module.Scenario("healthy", "permutation", 4096, "guardrail"),
            "name")):
        try:
            setattr(record, field, None)
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError(f"{type(record).__name__} must be immutable")

    for size_bytes in (0, -1):
        expect_value_error(
            lambda size_bytes=size_bytes: module.Flow(0, 1, size_bytes),
            "Flow size must be positive",
        )
        expect_value_error(
            lambda size_bytes=size_bytes: module.Scenario(
                "bad", "permutation", size_bytes, "guardrail"
            ),
            "Scenario size must be positive",
        )

    valid_hotspot = module.Scenario(
        "persistent_hotspot_target_4mib", "permutation", 4 << 20,
        "path_opportunity", hotspot_rate_gbps=300,
        hotspot_on_us=1000, hotspot_off_us=0,
    )
    assert valid_hotspot.hotspot_rate_gbps == 300
    for rate, on_us, off_us in ((0, 100, 0), (300, 0, 0), (300, 100, -1)):
        expect_value_error(
            lambda rate=rate, on_us=on_us, off_us=off_us:
                module.Scenario(
                    "bad_hotspot", "permutation", 4 << 20,
                    "path_opportunity", hotspot_rate_gbps=rate,
                    hotspot_on_us=on_us, hotspot_off_us=off_us,
                ),
            "hotspot parameters must describe a positive-rate ON period",
        )


def test_deterministic_ordinary_flow_generators(module):
    topology = module.TOPOLOGIES[128]
    permutation = module.permutation_flows(
        topology, seed=13, size_bytes=4 << 20, start_us=7
    )
    assert permutation == module.permutation_flows(
        topology, seed=13, size_bytes=4 << 20, start_us=7
    )
    assert len(permutation) == topology.nodes
    assert [flow.src for flow in permutation] == list(range(topology.nodes))
    assert len({flow.dst for flow in permutation}) == topology.nodes
    assert all(
        flow.src != flow.dst and flow.size_bytes == 4 << 20
        and flow.start_us == 7 and flow.role == "target"
        for flow in permutation
    )

    tornado = module.tornado_flows(topology, 8 << 20, start_us=11)
    assert len(tornado) == topology.nodes
    assert all(
        flow.dst == (flow.src + topology.nodes // 2) % topology.nodes
        and flow.size_bytes == 8 << 20 and flow.start_us == 11
        for flow in tornado
    )

    incast = module.incast_flows(
        topology, fanin=8, size_bytes=16 << 20, dst=17
    )
    assert len(incast) == 8
    assert len({flow.src for flow in incast}) == 8
    assert all(
        flow.src != 17 and flow.dst == 17 and flow.start_us == 250
        for flow in incast
    )
    expect_value_error(
        lambda: module.incast_flows(
            topology, fanin=topology.nodes, size_bytes=4096
        ),
        "fanin >= nodes must fail",
    )


def test_incast_spans_remote_leaves_evenly(module):
    for topology in module.TOPOLOGIES.values():
        dst = topology.hosts_per_leaf + 1
        dst_leaf = dst // topology.hosts_per_leaf
        flows = module.incast_flows(
            topology, fanin=8, size_bytes=4 << 20, dst=dst
        )
        sender_leaves = [
            flow.src // topology.hosts_per_leaf for flow in flows
        ]
        assert len(flows) == 8
        assert len({flow.src for flow in flows}) == 8
        assert all(leaf != dst_leaf for leaf in sender_leaves)
        counts = Counter(sender_leaves)
        remote_counts = [
            counts[leaf] for leaf in range(topology.leaves)
            if leaf != dst_leaf
        ]
        assert max(remote_counts) - min(remote_counts) <= 1

    topology = module.Topology(24, 4, 6, 4)
    flows = module.incast_flows(
        topology, fanin=8, size_bytes=4096, dst=0
    )
    sender_leaves = [flow.src // topology.hosts_per_leaf for flow in flows]
    assert all(leaf != 0 for leaf in sender_leaves)
    assert sorted(Counter(sender_leaves).values()) == [1, 1, 2, 2, 2]
    assert len({flow.src % topology.hosts_per_leaf for flow in flows}) == 2


def test_mixed_flows(module):
    topology = module.TOPOLOGIES[128]
    flows = module.mixed_flows(topology, 4 << 20, seed=13)
    assert flows == module.mixed_flows(topology, 4 << 20, seed=13)
    different_order = module.mixed_flows(topology, 4 << 20, seed=29)
    assert flows != different_order
    assert set(flows) == set(different_order)
    target = [flow for flow in flows if flow.role == "target"]
    background = [flow for flow in flows if flow.role == "background"]
    assert len(target) == topology.leaves * (topology.hosts_per_leaf - 1)
    assert len(background) == topology.leaves * 3
    assert all(flow.start_us == 250 and flow.rate_mbps == 0 for flow in target)
    assert {
        flow.src % topology.hosts_per_leaf for flow in target
    } == set(range(1, topology.hosts_per_leaf))
    assert {
        flow.dst // topology.hosts_per_leaf
        for flow in target
        if flow.src // topology.hosts_per_leaf == 0
    } == {topology.leaves // 2}
    assert all(
        flow.src % topology.hosts_per_leaf == 0
        and flow.dst % topology.hosts_per_leaf == 0
        and flow.rate_mbps == 300000
        and flow.size_bytes == 3_750_000
        for flow in background
    )
    assert sorted({flow.start_us for flow in background}) == [0, 200, 400]


def test_topology_derived_network_conditions(module):
    assert [module.slow_uplink_count(module.TOPOLOGIES[nodes])
            for nodes in (128, 512, 2048)] == [4, 16, 62]
    assert [module.hot_spine_count(module.TOPOLOGIES[nodes])
            for nodes in (128, 512, 2048)] == [16, 16, 16]
    assert [module.expected_hotspot_sources(module.TOPOLOGIES[nodes])
            for nodes in (128, 512, 2048)] == [64, 256, 1024]

    topology = module.TOPOLOGIES[512]
    degraded = module.Scenario(
        "asymmetric_permutation_4mib", "permutation", 4 << 20,
        "path_opportunity", degraded=True,
    )
    assert module.network_condition_args(degraded, topology) == [
        "-slow_tor_uplinks", "16",
        "-slow_tor_uplink_divisor", "2",
        "-slow_tor_uplink_select", "random-sparse",
    ]
    hotspot = module.Scenario(
        "periodic_hotspot_target_4mib", "permutation", 4 << 20,
        "path_opportunity", hotspot_rate_gbps=300,
        hotspot_on_us=100, hotspot_off_us=100,
    )
    assert module.network_condition_args(hotspot, topology) == [
        "-path_hotspot_spines", "16",
        "-path_hotspot_bg_rate_gbps", "300",
        "-path_hotspot_bg_on_us", "100",
        "-path_hotspot_bg_off_us", "100",
    ]
    assert "-seed" not in module.network_condition_args(hotspot, topology)


def test_slow_uplink_override(module):
    diagnostic = module.Scenario(
        "diagnostic_single_slow_link_permutation_32mib",
        "permutation",
        32 << 20,
        "diagnostic",
        degraded=True,
        slow_uplinks=1,
    )
    for topology in module.TOPOLOGIES.values():
        assert module.network_condition_args(diagnostic, topology)[:2] == [
            "-slow_tor_uplinks", "1",
        ]

    scenarios = module.ordinary_scenarios()
    by_name = {scenario.name: scenario for scenario in scenarios}
    registry_diagnostic = by_name[
        "diagnostic_single_slow_link_permutation_32mib"
    ]
    assert registry_diagnostic.slow_uplinks == 1
    assert all(
        scenario.slow_uplinks == 0
        for scenario in scenarios
        if scenario.name.startswith("asymmetric_")
    )
    asymmetric = by_name["asymmetric_permutation_4mib"]
    assert [
        module.network_condition_args(
            asymmetric, module.TOPOLOGIES[nodes]
        )[1]
        for nodes in (128, 512, 2048)
    ] == ["4", "16", "62"]
    expect_value_error(
        lambda: module.Scenario(
            "bad_override", "permutation", 4 << 20,
            "path_opportunity", degraded=True, slow_uplinks=-1,
        ),
        "negative slow-uplink override must fail",
    )


def test_write_traffic_matrix(module):
    topology = module.Topology(8, 2, 4, 2)
    flows = [
        module.Flow(0, 1, 4096),
        module.Flow(2, 3, 3_750_000, 10.25, "background", 300000),
    ]
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "traffic.cm"
        module.write_traffic_matrix(path, topology, flows)
        assert path.read_text(encoding="utf-8") == (
            "Nodes 8\n"
            "Connections 2\n"
            "0->1 id 1 start 0 size 4096\n"
            "2->3 id 2 start 10250000 size 3750000 rate_mbps 300000\n"
        )
        for bad_flow in (
            module.Flow(0, 0, 4096),
            module.Flow(-1, 1, 4096),
            module.Flow(0, 8, 4096),
        ):
            expect_value_error(
                lambda bad_flow=bad_flow: module.write_traffic_matrix(
                    path, topology, [bad_flow]
                ),
                "invalid traffic-matrix endpoint must fail",
            )


def test_write_traffic_matrix_enforces_stoi_contract(module):
    topology = module.Topology(8, 2, 4, 2)
    int32_max = (1 << 31) - 1

    def flow(size_bytes=4096, rate_mbps=0):
        return SimpleNamespace(
            src=0, dst=1, size_bytes=size_bytes, start_us=0,
            role="target", rate_mbps=rate_mbps,
        )

    invalid = (
        flow(size_bytes=int32_max + 1),
        flow(size_bytes=0),
        flow(size_bytes=True),
        flow(size_bytes=4096.5),
        flow(rate_mbps=-1),
        flow(rate_mbps=True),
        flow(rate_mbps=300000.5),
        flow(rate_mbps=int32_max + 1),
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "traffic.cm"
        for bad_flow in invalid:
            path.write_text("sentinel\n", encoding="utf-8")
            expect_value_error(
                lambda bad_flow=bad_flow: module.write_traffic_matrix(
                    path, topology, [bad_flow]
                ),
                "connection_matrix integer contract must be enforced",
            )
            assert path.read_text(encoding="utf-8") == "sentinel\n"

        module.write_traffic_matrix(
            path, topology, [flow(size_bytes=int32_max, rate_mbps=int32_max)]
        )
        assert path.read_text(encoding="utf-8").endswith(
            f"size {int32_max} rate_mbps {int32_max}\n"
        )


def test_alltoall_plan_validation_and_counts(module):
    plan = module.alltoall_plan(
        nodes=8, group_size=8, parallel=4, flow_size_bytes=4096,
        start_us=1.25, seed=13,
    )
    assert is_dataclass(module.AllToAllPlan)
    assert plan == module.AllToAllPlan(8, 8, 4, 4096, 1.25, 13)
    assert plan.group_count == 1
    assert plan.connection_count == 56
    assert plan.batches_per_source == 2
    assert plan.trigger_count == 8
    assert plan.max_parallel_per_source == 4
    try:
        plan.nodes = 16
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("AllToAllPlan must be immutable")

    invalid = (
        (0, 2, 1, 4096, 0, 1),
        (True, 2, 1, 4096, 0, 1),
        (8.0, 8, 4, 4096, 0, 1),
        (8, 1, 1, 4096, 0, 1),
        (8, 9, 1, 4096, 0, 1),
        (8, 3, 1, 4096, 0, 1),
        (8, 8, 0, 4096, 0, 1),
        (8, 8, 8, 4096, 0, 1),
        (8, 8, 4.0, 4096, 0, 1),
        (8, 8, 4, 0, 0, 1),
        (8, 8, 4, True, 0, 1),
        (8, 8, 4, 4096.0, 0, 1),
        (8, 8, 4, module.INT32_MAX + 1, 0, 1),
        (8, 8, 4, 4096, -1, 1),
        (8, 8, 4, 4096, float("nan"), 1),
        (8, 8, 4, 4096, float("inf"), 1),
        (8, 8, 4, 4096, 0, True),
        (8, 8, 4, 4096, 0, 1.5),
    )
    for args in invalid:
        expect_value_error(
            lambda args=args: module.alltoall_plan(*args),
            f"invalid all-to-all plan must fail: {args}",
        )


def test_alltoall_plan_enforces_packet_flow_id_limit(module):
    plan_2048 = module.alltoall_plan(
        2048, 2048, 32, 1 << 30, 0, 13
    )
    assert plan_2048.connection_count == 4192256
    assert plan_2048.connection_count <= module.ALLTOALL_MAX_CONNECTIONS

    boundary_plan = module.alltoall_plan(
        31623, 31623, 32, 4096, 0, 13
    )
    assert boundary_plan.connection_count == 999982506
    assert boundary_plan.connection_count <= module.ALLTOALL_MAX_CONNECTIONS

    oversized_connections = 31624 * (31624 - 1)
    try:
        module.alltoall_plan(31624, 31624, 32, 4096, 0, 13)
    except ValueError as error:
        assert str(oversized_connections) in str(error)
        assert str(module.ALLTOALL_MAX_CONNECTIONS) in str(error)
    else:
        raise AssertionError(
            "plans with flow IDs in PacketFlow's dynamic range must fail"
        )


def test_alltoall_allow_large_cannot_bypass_flow_id_limit(module):
    oversized_plan = object.__new__(module.AllToAllPlan)
    for field, value in (
        ("nodes", 31624),
        ("group_size", 31624),
        ("parallel", 32),
        ("flow_size_bytes", 4096),
        ("start_us", 0.0),
        ("seed", 13),
    ):
        object.__setattr__(oversized_plan, field, value)

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "missing" / "oversized.cm"
        try:
            module.write_alltoall_matrix(
                path, oversized_plan, allow_large=True
            )
        except ValueError as error:
            assert str(oversized_plan.connection_count) in str(error)
            assert str(module.ALLTOALL_MAX_CONNECTIONS) in str(error)
        except Exception as error:
            raise AssertionError(
                "allow_large must not reach file creation for an "
                "oversized connection matrix"
            ) from error
        else:
            raise AssertionError(
                "allow_large must not bypass the hard flow-ID limit"
            )


def test_alltoall_pairs_are_deterministic_complete_and_bounded(module):
    plan = module.alltoall_plan(8, 8, 4, 4096, 0, 13)
    records = list(module.iter_alltoall_connections(plan))
    assert len(records) == 56
    assert records == list(module.iter_alltoall_connections(plan))
    assert len({(record.src, record.dst) for record in records}) == 56
    assert all(record.src != record.dst for record in records)
    assert module.alltoall_ordered_pairs(plan) == tuple(
        (record.src, record.dst) for record in records
    )

    active_counts = Counter()
    for record in records:
        gate = (record.src, record.start_ps, record.trigger)
        active_counts[gate] += 1
    assert max(active_counts.values()) == 4
    assert max(active_counts.values()) <= plan.parallel

    for nodes, parallel_values in ((6, (1, 2, 4, 5)), (7, (1, 3, 6))):
        for parallel in parallel_values:
            variant = module.alltoall_plan(
                nodes, nodes, parallel, 1024, 0, 47
            )
            pairs = [
                (record.src, record.dst)
                for record in module.iter_alltoall_connections(variant)
            ]
            assert len(pairs) == nodes * (nodes - 1)
            assert len(set(pairs)) == len(pairs)
            assert set(pairs) == {
                (src, dst) for src in range(nodes)
                for dst in range(nodes) if src != dst
            }


def test_alltoall_multi_group_membership_and_triggers(module):
    plan = module.alltoall_plan(12, 4, 2, 2048, 3, 19)
    records = list(module.iter_alltoall_connections(plan))
    assert plan.group_count == 3
    assert plan.connection_count == 36
    assert plan.batches_per_source == 2
    assert plan.trigger_count == 12
    assert len(records) == plan.connection_count

    shuffled = list(range(plan.nodes))
    random.Random(plan.seed).shuffle(shuffled)
    expected_groups = [
        frozenset(shuffled[offset:offset + plan.group_size])
        for offset in range(0, plan.nodes, plan.group_size)
    ]
    actual_peers = {
        src: frozenset(record.dst for record in records if record.src == src)
        for src in range(plan.nodes)
    }
    for group in expected_groups:
        for src in group:
            assert actual_peers[src] == group - {src}

    referenced = {
        trigger_id
        for record in records
        for trigger_id in (record.trigger, record.send_done_trigger)
        if trigger_id is not None
    }
    assert 0 not in referenced
    assert referenced == set(range(1, plan.trigger_count + 1))
    assert all(
        record.send_done_trigger is not None
        for record in records if record.batch_index < plan.batches_per_source - 1
    )
    assert all(
        record.send_done_trigger is None
        for record in records if record.batch_index == plan.batches_per_source - 1
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "multi-group.cm"
        manifest = module.write_alltoall_matrix(path, plan)
        assert manifest.nodes == 12
        assert manifest.group_size == 4
        assert manifest.groups == 3
        assert manifest.parallel_per_source == 2
        assert manifest.flow_size_bytes == 2048
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[:3] == ["Nodes 12", "Connections 36", "Triggers 12"]
        assert lines[-plan.trigger_count:] == [
            f"trigger id {trigger_id} multishot"
            for trigger_id in range(1, plan.trigger_count + 1)
        ]


def test_alltoall_trigger_chain_spans_multiple_batches(module):
    plan = module.alltoall_plan(6, 6, 2, 4096, 0, 23)
    records = list(module.iter_alltoall_connections(plan))
    assert plan.batches_per_source == 3
    assert plan.trigger_count == 12

    records_by_source = {
        src: [record for record in records if record.src == src]
        for src in range(plan.nodes)
    }
    for source_records in records_by_source.values():
        batches = {
            batch_index: [
                record for record in source_records
                if record.batch_index == batch_index
            ]
            for batch_index in range(plan.batches_per_source)
        }
        assert [len(batch) for batch in batches.values()] == [2, 2, 1]
        assert all(record.trigger is None for record in batches[0])
        assert all(
            record.send_done_trigger == batches[1][0].trigger
            for record in batches[0]
        )
        assert all(
            record.send_done_trigger == batches[2][0].trigger
            for record in batches[1]
        )
        assert all(record.send_done_trigger is None for record in batches[2])


def test_alltoall_exact_small_matrix_and_manifest(module):
    plan = module.alltoall_plan(4, 4, 2, 4096, 1.25, 0)
    expected = (
        "Nodes 4\n"
        "Connections 12\n"
        "Triggers 4\n"
        "2->0 id 1 start 1250000 size 4096 send_done_trigger 1\n"
        "2->1 id 2 start 1250000 size 4096 send_done_trigger 1\n"
        "2->3 id 3 trigger 1 size 4096\n"
        "0->1 id 4 start 1250000 size 4096 send_done_trigger 2\n"
        "0->3 id 5 start 1250000 size 4096 send_done_trigger 2\n"
        "0->2 id 6 trigger 2 size 4096\n"
        "1->3 id 7 start 1250000 size 4096 send_done_trigger 3\n"
        "1->2 id 8 start 1250000 size 4096 send_done_trigger 3\n"
        "1->0 id 9 trigger 3 size 4096\n"
        "3->2 id 10 start 1250000 size 4096 send_done_trigger 4\n"
        "3->0 id 11 start 1250000 size 4096 send_done_trigger 4\n"
        "3->1 id 12 trigger 4 size 4096\n"
        "trigger id 1 multishot\n"
        "trigger id 2 multishot\n"
        "trigger id 3 multishot\n"
        "trigger id 4 multishot\n"
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "alltoall.cm"
        manifest = module.write_alltoall_matrix(path, plan)
        assert path.read_text(encoding="utf-8") == expected
        assert manifest.path == path
        assert manifest.nodes == 4
        assert manifest.group_size == 4
        assert manifest.groups == 1
        assert manifest.parallel_per_source == 2
        assert manifest.flow_size_bytes == 4096
        assert manifest.connections == 12
        assert manifest.triggers == 4
        assert manifest.total_lines == 19
        assert manifest.serialized_bytes == len(expected.encode("utf-8"))
        assert list(path.parent.glob(f".{path.name}.*.tmp")) == []

        estimate = module.estimate_alltoall_matrix(plan)
        assert estimate.connections == manifest.connections
        assert estimate.triggers == manifest.triggers
        assert estimate.total_lines == manifest.total_lines
        assert estimate.estimated_serialized_bytes == manifest.serialized_bytes


def test_alltoall_scaling_estimate_streaming_and_resource_guard(module):
    plan_512 = module.alltoall_plan(512, 512, 16, 64 << 20, 0, 13)
    estimate_512 = module.estimate_alltoall_matrix(plan_512)
    assert estimate_512.connections == 261632
    assert estimate_512.triggers == 512 * (32 - 1)
    assert estimate_512.total_lines == (
        3 + estimate_512.connections + estimate_512.triggers
    )
    assert estimate_512.estimated_serialized_bytes > 0

    plan_2048 = module.alltoall_plan(2048, 2048, 32, 1 << 30, 0, 13)
    estimate_2048 = module.estimate_alltoall_matrix(plan_2048)
    assert estimate_2048.connections == 4192256
    assert estimate_2048.triggers == 2048 * (64 - 1)
    assert estimate_2048.total_lines == (
        3 + estimate_2048.connections + estimate_2048.triggers
    )
    assert estimate_2048.estimated_serialized_bytes > estimate_512.estimated_serialized_bytes

    iterator = module.iter_alltoall_connections(plan_512)
    assert iter(iterator) is iterator
    assert len(list(islice(iterator, 7))) == 7
    expect_value_error(
        lambda: module.alltoall_ordered_pairs(plan_512),
        "large ordered-pair materialization must be guarded",
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "too-large.cm"
        path.write_text("sentinel\n", encoding="utf-8")
        try:
            module.write_alltoall_matrix(path, plan_2048)
        except ValueError as error:
            assert "4192256" in str(error)
            assert "allow_large" in str(error)
        else:
            raise AssertionError("large writes must require allow_large=True")
        assert path.read_text(encoding="utf-8") == "sentinel\n"


def test_alltoall_background_args(module):
    for size_bytes in (1, 64 << 20, 256 << 20, 1 << 30):
        assert module.alltoall_background_args(size_bytes, False) == []

    for size_bytes, tau_us in (
        (64 << 20, "200"),
        (256 << 20, "400"),
        (1 << 30, "1000"),
    ):
        assert module.alltoall_background_args(size_bytes, True) == [
            "-sglb_background",
            "-sglb_bg_links_per_direction", "2",
            "-sglb_bg_rate_gbps", "350",
            "-sglb_bg_on_us", tau_us,
            "-sglb_bg_off_us", tau_us,
        ]

    for unsupported in (1, 4 << 20, 128 << 20, True):
        expect_value_error(
            lambda unsupported=unsupported:
                module.alltoall_background_args(unsupported, True),
            "unsupported enabled all-to-all background size must fail",
        )
    for bad_enabled in (0, 1, None, "yes"):
        expect_value_error(
            lambda bad_enabled=bad_enabled:
                module.alltoall_background_args(64 << 20, bad_enabled),
            "background enabled must be boolean",
        )


def test_standard_websearch_proxy(module):
    expected_cdf = (
        (6 * 1024, 0.15), (13 * 1024, 0.20),
        (19 * 1024, 0.30), (33 * 1024, 0.40),
        (53 * 1024, 0.53), (133 * 1024, 0.60),
        (667 * 1024, 0.70), (1333 * 1024, 0.80),
        (3333 * 1024, 0.90), (6667 * 1024, 0.97),
        (20000 * 1024, 0.99), (30000 * 1024, 1.0),
    )
    assert module.WEBSEARCH_CDF == expected_cdf
    first_rng = random.Random(13)
    second_rng = random.Random(13)
    first_samples = [module.sample_websearch_size(first_rng) for _ in range(50)]
    second_samples = [module.sample_websearch_size(second_rng) for _ in range(50)]
    assert first_samples == second_samples
    assert set(first_samples) <= {size for size, _ in expected_cdf}

    topology = module.Topology(8, 2, 4, 2)
    low = module.websearch_flows(topology, 13, 0.4, 500)
    assert low == module.websearch_flows(topology, 13, 0.4, 500)
    assert low
    assert all(
        0 <= flow.src < topology.nodes
        and 0 <= flow.dst < topology.nodes
        and flow.src != flow.dst
        and 0 < flow.start_us <= 500
        and flow.size_bytes in {size for size, _ in expected_cdf}
        for flow in low
    )
    high = module.websearch_flows(topology, 13, 1.0, 500)
    assert len(high) > len(low)
    for offered_load in (0, -0.1, 1.01):
        expect_value_error(
            lambda offered_load=offered_load: module.websearch_flows(
                topology, 13, offered_load, 500
            ),
            "offered load outside (0, 1] must fail",
        )
    expect_value_error(
        lambda: module.websearch_flows(topology, 13, 0.4, 0),
        "non-positive duration must fail",
    )


def test_websearch_scenario_offered_load(module):
    ordinary = module.Scenario(
        "healthy_permutation_4mib",
        "permutation",
        4 << 20,
        "guardrail",
    )
    assert ordinary.offered_load == 0.0

    for bad_load in (0, -0.1, 1.01, float("nan")):
        expect_value_error(
            lambda bad_load=bad_load: module.Scenario(
                "standard_websearch_proxy_bad",
                "standard_websearch_proxy",
                4096,
                "load_sweep",
                offered_load=bad_load,
            ),
            "WebSearch scenarios require offered load in (0, 1]",
        )

    websearch = [
        scenario for scenario in module.ordinary_scenarios()
        if scenario.traffic == "standard_websearch_proxy"
    ]
    assert all(
        scenario.offered_load == 0.0
        for scenario in module.ordinary_scenarios()
        if scenario.traffic != "standard_websearch_proxy"
    )
    assert [scenario.offered_load for scenario in websearch] == [
        0.4, 0.6, 0.8, 1.0,
    ]
    topology = module.Topology(8, 2, 4, 2)
    flow_sets = []
    for scenario, expected_load in zip(websearch, (0.4, 0.6, 0.8, 1.0)):
        flows_from_registry = module.websearch_flows(
            topology, 13, scenario.offered_load, 500
        )
        assert flows_from_registry == module.websearch_flows(
            topology, 13, expected_load, 500
        )
        flow_sets.append(flows_from_registry)
    assert [len(flows) for flows in flow_sets] == sorted(
        len(flows) for flows in flow_sets
    )


def test_websearch_offered_bytes_sanity(module):
    topology = module.TOPOLOGIES[128]
    offered_load = 0.6
    duration_us = 5000
    flows = module.websearch_flows(
        topology, seed=47, offered_load=offered_load,
        duration_us=duration_us,
    )
    actual_bytes = sum(flow.size_bytes for flow in flows)
    expected_bytes = (
        topology.nodes * offered_load * module.LINKSPEED_MBPS
        * duration_us / 8
    )
    relative_error = abs(actual_bytes - expected_bytes) / expected_bytes
    # The proxy is a compound-Poisson process with a heavy-tailed size CDF;
    # 25% is broad enough to avoid treating deterministic sampling noise as bias.
    assert relative_error <= 0.25


def test_ordinary_scenario_registry(module):
    scenarios = module.ordinary_scenarios()
    names = [scenario.name for scenario in scenarios]
    assert len(scenarios) == 26
    assert len(names) == len(set(names))
    for size_mib in (4, 8, 16):
        for traffic in ("permutation", "tornado"):
            assert f"healthy_{traffic}_{size_mib}mib" in names
            assert f"asymmetric_{traffic}_{size_mib}mib" in names
        assert f"incast_8to1_{size_mib}mib" in names
    assert "diagnostic_single_slow_link_permutation_32mib" in names
    for size_mib in (4, 16):
        assert f"mixed_fixed_background_target_{size_mib}mib" in names
        assert f"persistent_hotspot_target_{size_mib}mib" in names
        assert f"periodic_hotspot_target_{size_mib}mib" in names
    for load_pct in (40, 60, 80, 100):
        assert f"standard_websearch_proxy_{load_pct}pct" in names

    by_name = {scenario.name: scenario for scenario in scenarios}
    diagnostic = by_name[
        "diagnostic_single_slow_link_permutation_32mib"
    ]
    assert diagnostic.opportunity == "diagnostic"
    assert diagnostic.degraded
    assert by_name["incast_8to1_4mib"].incast_fanin == 8
    assert by_name["mixed_fixed_background_target_4mib"].mixed
    assert by_name["persistent_hotspot_target_4mib"].hotspot_off_us == 0
    assert by_name["periodic_hotspot_target_4mib"].hotspot_off_us > 0


def main():
    module = load_common()
    test_topologies(module)
    test_topology_dimension_validation(module)
    test_rtt_and_bdp(module)
    test_physical_input_validation(module)
    test_runtime_rtt_parser(module)
    test_number(module)
    test_feedback_args(module)
    test_flow_and_scenario_records(module)
    test_deterministic_ordinary_flow_generators(module)
    test_incast_spans_remote_leaves_evenly(module)
    test_mixed_flows(module)
    test_topology_derived_network_conditions(module)
    test_slow_uplink_override(module)
    test_write_traffic_matrix(module)
    test_write_traffic_matrix_enforces_stoi_contract(module)
    test_alltoall_plan_validation_and_counts(module)
    test_alltoall_plan_enforces_packet_flow_id_limit(module)
    test_alltoall_allow_large_cannot_bypass_flow_id_limit(module)
    test_alltoall_pairs_are_deterministic_complete_and_bounded(module)
    test_alltoall_multi_group_membership_and_triggers(module)
    test_alltoall_trigger_chain_spans_multiple_batches(module)
    test_alltoall_exact_small_matrix_and_manifest(module)
    test_alltoall_scaling_estimate_streaming_and_resource_guard(module)
    test_alltoall_background_args(module)
    test_standard_websearch_proxy(module)
    test_websearch_scenario_offered_load(module)
    test_websearch_offered_bytes_sanity(module)
    test_ordinary_scenario_registry(module)


if __name__ == "__main__":
    main()
