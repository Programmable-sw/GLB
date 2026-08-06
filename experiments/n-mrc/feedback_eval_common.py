#!/usr/bin/env python3

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import math
import os
from pathlib import Path
import random
import re
import tempfile
from types import MappingProxyType


LINKSPEED_MBPS = 400000
MTU_BYTES = 4096
MIB = 1 << 20
INT32_MAX = (1 << 31) - 1
ALLTOALL_MAX_CONNECTIONS = 999_999_999
ALLTOALL_PAIR_MATERIALIZATION_LIMIT = 100_000
ALLTOALL_WRITE_CONNECTION_LIMIT = 1_000_000

# A digitized standard WebSearch proxy from the approved evaluation plan. This
# is not an exact machine-readable REPS artifact.
WEBSEARCH_CDF = (
    (6 * 1024, 0.15),
    (13 * 1024, 0.20),
    (19 * 1024, 0.30),
    (33 * 1024, 0.40),
    (53 * 1024, 0.53),
    (133 * 1024, 0.60),
    (667 * 1024, 0.70),
    (1333 * 1024, 0.80),
    (3333 * 1024, 0.90),
    (6667 * 1024, 0.97),
    (20000 * 1024, 0.99),
    (30000 * 1024, 1.00),
)


def _positive_byte_size(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _connection_matrix_integer(value, name, minimum):
    if (isinstance(value, bool) or not isinstance(value, int)
            or not minimum <= value <= INT32_MAX):
        raise ValueError(
            f"{name} must be an integer in [{minimum}, {INT32_MAX}]"
        )
    return value


def _validate_alltoall_connection_count(connection_count):
    if connection_count > ALLTOALL_MAX_CONNECTIONS:
        raise ValueError(
            "all-to-all connection count "
            f"{connection_count} exceeds PacketFlow manual-ID maximum "
            f"{ALLTOALL_MAX_CONNECTIONS}"
        )


def _finite_float(value, name):
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _nonnegative_float(value, name):
    result = _finite_float(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


@dataclass(frozen=True)
class Flow:
    src: int
    dst: int
    size_bytes: int
    start_us: float = 0.0
    role: str = "target"
    rate_mbps: float = 0

    def __post_init__(self):
        if (isinstance(self.src, bool) or not isinstance(self.src, int)
                or isinstance(self.dst, bool) or not isinstance(self.dst, int)):
            raise ValueError("flow endpoints must be integers")
        _positive_byte_size(self.size_bytes, "flow size")
        _nonnegative_float(self.start_us, "flow start")
        _nonnegative_float(self.rate_mbps, "flow rate")
        if not isinstance(self.role, str) or not self.role:
            raise ValueError("flow role must be non-empty")


@dataclass(frozen=True)
class Scenario:
    name: str
    traffic: str
    size_bytes: int
    opportunity: str
    degraded: bool = False
    mixed: bool = False
    incast_fanin: int = 0
    hotspot_rate_gbps: float = 0
    hotspot_on_us: float = 0
    hotspot_off_us: float = 0
    start_us: float = 0
    end_us: float = 10000
    slow_uplinks: int = 0
    offered_load: float = 0.0

    def __post_init__(self):
        for value, label in (
                (self.name, "scenario name"),
                (self.traffic, "scenario traffic"),
                (self.opportunity, "scenario opportunity")):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} must be non-empty")
        _positive_byte_size(self.size_bytes, "scenario size")
        if (isinstance(self.incast_fanin, bool)
                or not isinstance(self.incast_fanin, int)
                or self.incast_fanin < 0):
            raise ValueError("incast fanin must be a non-negative integer")
        if (isinstance(self.slow_uplinks, bool)
                or not isinstance(self.slow_uplinks, int)
                or self.slow_uplinks < 0):
            raise ValueError("slow-uplink override must be non-negative")
        offered_load = _finite_float(self.offered_load, "offered load")
        if (self.traffic == "standard_websearch_proxy"
                and not 0 < offered_load <= 1):
            raise ValueError(
                "WebSearch scenario offered load must be in (0, 1]"
            )
        start = _nonnegative_float(self.start_us, "scenario start")
        end = _finite_float(self.end_us, "scenario end")
        if end <= start:
            raise ValueError("scenario end must be after its start")

        rate = _finite_float(self.hotspot_rate_gbps, "hotspot rate")
        on_us = _finite_float(self.hotspot_on_us, "hotspot ON time")
        off_us = _finite_float(self.hotspot_off_us, "hotspot OFF time")
        has_hotspot = any(value != 0 for value in (rate, on_us, off_us))
        if has_hotspot and (rate <= 0 or on_us <= 0 or off_us < 0):
            raise ValueError(
                "hotspot requires positive rate and ON time, and "
                "non-negative OFF time"
            )


def _validate_topology_dimensions(topology):
    for field in ("nodes", "hosts_per_leaf", "leaves", "spines"):
        value = getattr(topology, field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"topology {field} must be a positive integer")
    if topology.nodes != topology.leaves * topology.hosts_per_leaf:
        raise ValueError("topology nodes must equal leaves * hosts_per_leaf")
    if topology.hosts_per_leaf != topology.spines:
        raise ValueError(
            "full-bisection topology requires hosts_per_leaf == spines"
        )


@dataclass(frozen=True)
class Topology:
    nodes: int
    hosts_per_leaf: int
    leaves: int
    spines: int

    def __post_init__(self):
        _validate_topology_dimensions(self)

    @property
    def paths(self):
        return self.spines


TOPOLOGIES = MappingProxyType({
    128: Topology(128, 64, 2, 64),
    256: Topology(256, 64, 4, 64),
    512: Topology(512, 64, 8, 64),
    2048: Topology(2048, 64, 32, 64),
})


@dataclass(frozen=True)
class AllToAllPlan:
    nodes: int
    group_size: int
    parallel: int
    flow_size_bytes: int
    start_us: float
    seed: int

    def __post_init__(self):
        _connection_matrix_integer(self.nodes, "all-to-all nodes", 1)
        if (isinstance(self.group_size, bool)
                or not isinstance(self.group_size, int)
                or not 2 <= self.group_size <= self.nodes):
            raise ValueError(
                "all-to-all group size must be an integer in [2, nodes]"
            )
        if self.nodes % self.group_size:
            raise ValueError(
                "all-to-all nodes must be divisible by group size"
            )
        _validate_alltoall_connection_count(self.connection_count)
        if (isinstance(self.parallel, bool)
                or not isinstance(self.parallel, int)
                or not 1 <= self.parallel <= self.group_size - 1):
            raise ValueError(
                "all-to-all parallel must be an integer in "
                "[1, group_size - 1]"
            )
        _connection_matrix_integer(
            self.flow_size_bytes, "all-to-all flow size", 1
        )
        object.__setattr__(
            self, "start_us",
            _nonnegative_float(self.start_us, "all-to-all start"),
        )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("all-to-all seed must be an integer")

    @property
    def group_count(self):
        return self.nodes // self.group_size

    @property
    def connection_count(self):
        return self.group_count * self.group_size * (self.group_size - 1)

    @property
    def batches_per_source(self):
        return math.ceil((self.group_size - 1) / self.parallel)

    @property
    def trigger_count(self):
        return self.nodes * (self.batches_per_source - 1)

    @property
    def max_parallel_per_source(self):
        return self.parallel


@dataclass(frozen=True)
class AllToAllConnection:
    src: int
    dst: int
    flow_id: int
    size_bytes: int
    start_ps: int = None
    trigger: int = None
    send_done_trigger: int = None
    batch_index: int = 0


@dataclass(frozen=True)
class AllToAllMatrixEstimate:
    connections: int
    triggers: int
    total_lines: int
    estimated_serialized_bytes: int


@dataclass(frozen=True)
class AllToAllMatrixManifest:
    path: Path
    nodes: int
    group_size: int
    groups: int
    parallel_per_source: int
    flow_size_bytes: int
    connections: int
    triggers: int
    total_lines: int
    serialized_bytes: int


def alltoall_plan(nodes, group_size, parallel, flow_size_bytes,
                  start_us=0, seed=0):
    return AllToAllPlan(
        nodes, group_size, parallel, flow_size_bytes, start_us, seed
    )


def _require_alltoall_plan(plan):
    if not isinstance(plan, AllToAllPlan):
        raise ValueError("plan must be an AllToAllPlan")


def iter_alltoall_connections(plan):
    _require_alltoall_plan(plan)
    members = list(range(plan.nodes))
    random.Random(plan.seed).shuffle(members)
    start_ps = int(round(plan.start_us * 1_000_000))
    transitions_per_source = plan.batches_per_source - 1
    flow_id = 0
    source_ordinal = 0

    for group_start in range(0, plan.nodes, plan.group_size):
        group = members[group_start:group_start + plan.group_size]
        for source_index, src in enumerate(group):
            first_trigger = source_ordinal * transitions_per_source + 1
            for batch_index, offset_start in enumerate(
                    range(1, plan.group_size, plan.parallel)):
                trigger = (
                    first_trigger + batch_index - 1
                    if batch_index else None
                )
                send_done_trigger = (
                    first_trigger + batch_index
                    if batch_index < transitions_per_source else None
                )
                offset_stop = min(
                    offset_start + plan.parallel, plan.group_size
                )
                for destination_offset in range(offset_start, offset_stop):
                    flow_id += 1
                    dst = group[
                        (source_index + destination_offset) % plan.group_size
                    ]
                    yield AllToAllConnection(
                        src=src,
                        dst=dst,
                        flow_id=flow_id,
                        size_bytes=plan.flow_size_bytes,
                        start_ps=start_ps if batch_index == 0 else None,
                        trigger=trigger,
                        send_done_trigger=send_done_trigger,
                        batch_index=batch_index,
                    )
            source_ordinal += 1


def alltoall_ordered_pairs(plan):
    _require_alltoall_plan(plan)
    if plan.connection_count > ALLTOALL_PAIR_MATERIALIZATION_LIMIT:
        raise ValueError(
            "refusing to materialize "
            f"{plan.connection_count} all-to-all ordered pairs"
        )
    return tuple(
        (record.src, record.dst)
        for record in iter_alltoall_connections(plan)
    )


def _alltoall_connection_line(record):
    line = f"{record.src}->{record.dst} id {record.flow_id}"
    if record.trigger is None:
        line += f" start {record.start_ps}"
    else:
        line += f" trigger {record.trigger}"
    line += f" size {record.size_bytes}"
    if record.send_done_trigger is not None:
        line += f" send_done_trigger {record.send_done_trigger}"
    return line + "\n"


def _integer_digit_sum(limit):
    total = 0
    first = 1
    digits = 1
    while first <= limit:
        last = min(limit, first * 10 - 1)
        total += (last - first + 1) * digits
        first *= 10
        digits += 1
    return total


def estimate_alltoall_matrix(plan):
    _require_alltoall_plan(plan)
    connections = plan.connection_count
    triggers = plan.trigger_count
    total_lines = 3 + connections + triggers
    start_ps = int(round(plan.start_us * 1_000_000))

    serialized_bytes = sum(len(line.encode("utf-8")) for line in (
        f"Nodes {plan.nodes}\n",
        f"Connections {connections}\n",
        f"Triggers {triggers}\n",
    ))

    endpoint_digits = sum(len(str(node)) for node in range(plan.nodes))
    serialized_bytes += 2 * (plan.group_size - 1) * endpoint_digits
    serialized_bytes += connections * (
        len("->") + len(" id ") + len(" size ")
        + len(str(plan.flow_size_bytes)) + len("\n")
    )
    serialized_bytes += _integer_digit_sum(connections)

    transitions_per_source = plan.batches_per_source - 1
    for source_ordinal in range(plan.nodes):
        first_trigger = source_ordinal * transitions_per_source + 1
        for batch_index, offset_start in enumerate(
                range(1, plan.group_size, plan.parallel)):
            batch_size = min(
                plan.parallel, plan.group_size - offset_start
            )
            if batch_index == 0:
                serialized_bytes += batch_size * (
                    len(" start ") + len(str(start_ps))
                )
            else:
                trigger = first_trigger + batch_index - 1
                serialized_bytes += batch_size * (
                    len(" trigger ") + len(str(trigger))
                )
            if batch_index < transitions_per_source:
                send_done_trigger = first_trigger + batch_index
                serialized_bytes += batch_size * (
                    len(" send_done_trigger ")
                    + len(str(send_done_trigger))
                )

    serialized_bytes += triggers * (
        len("trigger id ") + len(" multishot\n")
    )
    serialized_bytes += _integer_digit_sum(triggers)
    return AllToAllMatrixEstimate(
        connections=connections,
        triggers=triggers,
        total_lines=total_lines,
        estimated_serialized_bytes=serialized_bytes,
    )


def write_alltoall_matrix(path, plan, allow_large=False):
    _require_alltoall_plan(plan)
    if not isinstance(allow_large, bool):
        raise ValueError("allow_large must be boolean")
    _validate_alltoall_connection_count(plan.connection_count)
    if (plan.connection_count > ALLTOALL_WRITE_CONNECTION_LIMIT
            and not allow_large):
        raise ValueError(
            f"refusing to write {plan.connection_count} connections; "
            "pass allow_large=True explicitly"
        )

    path = Path(path)
    temp_path = None
    serialized_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
                delete=False) as handle:
            temp_path = Path(handle.name)
            for line in (
                    f"Nodes {plan.nodes}\n",
                    f"Connections {plan.connection_count}\n",
                    f"Triggers {plan.trigger_count}\n"):
                serialized_bytes += len(line.encode("utf-8"))
                handle.write(line)
            for record in iter_alltoall_connections(plan):
                line = _alltoall_connection_line(record)
                serialized_bytes += len(line.encode("utf-8"))
                handle.write(line)
            for trigger_id in range(1, plan.trigger_count + 1):
                line = f"trigger id {trigger_id} multishot\n"
                serialized_bytes += len(line.encode("utf-8"))
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

    return AllToAllMatrixManifest(
        path=path,
        nodes=plan.nodes,
        group_size=plan.group_size,
        groups=plan.group_count,
        parallel_per_source=plan.parallel,
        flow_size_bytes=plan.flow_size_bytes,
        connections=plan.connection_count,
        triggers=plan.trigger_count,
        total_lines=3 + plan.connection_count + plan.trigger_count,
        serialized_bytes=serialized_bytes,
    )


def alltoall_background_args(flow_size_bytes, enabled):
    if not isinstance(enabled, bool):
        raise ValueError("all-to-all background enabled must be boolean")
    if not enabled:
        return []
    tau_by_size = {
        64 << 20: "200",
        256 << 20: "400",
        1 << 30: "1000",
    }
    if (isinstance(flow_size_bytes, bool)
            or flow_size_bytes not in tau_by_size):
        raise ValueError(
            "all-to-all background requires a 64 MiB, 256 MiB, or 1 GiB flow"
        )
    tau_us = tau_by_size[flow_size_bytes]
    return [
        "-sglb_background",
        "-sglb_bg_links_per_direction", "2",
        "-sglb_bg_rate_gbps", "350",
        "-sglb_bg_on_us", tau_us,
        "-sglb_bg_off_us", tau_us,
    ]


def _validate_flow_topology(topology):
    if not isinstance(topology, Topology):
        raise ValueError("topology must be a Topology")
    _validate_topology_dimensions(topology)
    if topology.nodes < 2:
        raise ValueError("traffic generation requires at least two nodes")


def permutation_flows(topology, seed, size_bytes, start_us=0):
    _validate_flow_topology(topology)
    _positive_byte_size(size_bytes, "flow size")
    _nonnegative_float(start_us, "flow start")
    rng = random.Random(seed)
    destinations = list(range(topology.nodes))
    while True:
        rng.shuffle(destinations)
        if all(src != destinations[src] for src in range(topology.nodes)):
            return [
                Flow(src, destinations[src], size_bytes, start_us)
                for src in range(topology.nodes)
            ]


def tornado_flows(topology, size_bytes, start_us=0):
    _validate_flow_topology(topology)
    _positive_byte_size(size_bytes, "flow size")
    _nonnegative_float(start_us, "flow start")
    shift = topology.nodes // 2
    return [
        Flow(src, (src + shift) % topology.nodes, size_bytes, start_us)
        for src in range(topology.nodes)
    ]


def incast_flows(topology, fanin, size_bytes, dst=0, start_us=250):
    _validate_flow_topology(topology)
    _positive_byte_size(size_bytes, "flow size")
    _nonnegative_float(start_us, "flow start")
    if (isinstance(fanin, bool) or not isinstance(fanin, int)
            or fanin <= 0 or fanin >= topology.nodes):
        raise ValueError("incast fanin must be positive and less than nodes")
    if (isinstance(dst, bool) or not isinstance(dst, int)
            or not 0 <= dst < topology.nodes):
        raise ValueError("incast destination is out of range")
    dst_leaf = dst // topology.hosts_per_leaf
    remote_leaves = [
        leaf for leaf in range(topology.leaves) if leaf != dst_leaf
    ]
    senders = [
        leaf * topology.hosts_per_leaf + offset
        for offset in range(topology.hosts_per_leaf)
        for leaf in remote_leaves
    ]
    senders.extend(
        dst_leaf * topology.hosts_per_leaf + offset
        for offset in range(topology.hosts_per_leaf)
        if dst_leaf * topology.hosts_per_leaf + offset != dst
    )
    return [
        Flow(src, dst, size_bytes, start_us) for src in senders[:fanin]
    ]


def mixed_flows(topology, target_size_bytes, seed):
    _validate_flow_topology(topology)
    _positive_byte_size(target_size_bytes, "target flow size")
    rng = random.Random(seed)
    flows = []
    for leaf in range(topology.leaves):
        dst_leaf = (leaf + topology.leaves // 2) % topology.leaves
        for offset in range(1, topology.hosts_per_leaf):
            flows.append(Flow(
                leaf * topology.hosts_per_leaf + offset,
                dst_leaf * topology.hosts_per_leaf + offset,
                target_size_bytes,
                start_us=250,
            ))

    background_rate_mbps = 300000
    background_on_us = 100
    background_size = background_rate_mbps * background_on_us // 8
    for start_us in (0, 200, 400):
        for leaf in range(topology.leaves):
            dst_leaf = (leaf + topology.leaves // 2) % topology.leaves
            flows.append(Flow(
                leaf * topology.hosts_per_leaf,
                dst_leaf * topology.hosts_per_leaf,
                background_size,
                start_us=start_us,
                role="background",
                rate_mbps=background_rate_mbps,
            ))
    rng.shuffle(flows)
    return flows


def slow_uplink_count(topology):
    return math.ceil(0.03 * topology.leaves * topology.spines)


def hot_spine_count(topology):
    return max(1, topology.spines // 4)


def expected_hotspot_sources(topology):
    return 2 * topology.leaves * hot_spine_count(topology)


def network_condition_args(scenario, topology):
    args = []
    if scenario.degraded:
        args.extend([
            "-slow_tor_uplinks", str(
                scenario.slow_uplinks or slow_uplink_count(topology)),
            "-slow_tor_uplink_divisor", "2",
            "-slow_tor_uplink_select", "random-sparse",
        ])
    if scenario.hotspot_rate_gbps:
        args.extend([
            "-path_hotspot_spines", str(hot_spine_count(topology)),
            "-path_hotspot_bg_rate_gbps", number(
                scenario.hotspot_rate_gbps),
            "-path_hotspot_bg_on_us", number(scenario.hotspot_on_us),
            "-path_hotspot_bg_off_us", number(scenario.hotspot_off_us),
        ])
    return args


def write_traffic_matrix(path, topology, flows):
    checked = []
    for flow in flows:
        if (isinstance(flow.src, bool) or not isinstance(flow.src, int)
                or isinstance(flow.dst, bool) or not isinstance(flow.dst, int)
                or not 0 <= flow.src < topology.nodes
                or not 0 <= flow.dst < topology.nodes):
            raise ValueError("flow endpoint is out of topology range")
        if flow.src == flow.dst:
            raise ValueError("self flows are not allowed")
        size_bytes = _connection_matrix_integer(
            flow.size_bytes, "flow size", 1)
        start_us = _nonnegative_float(flow.start_us, "flow start")
        rate_mbps = _connection_matrix_integer(
            flow.rate_mbps, "flow rate", 0)
        checked.append((
            flow, size_bytes, int(round(start_us * 1_000_000)), rate_mbps
        ))

    with Path(path).open("w", encoding="utf-8") as handle:
        print("Nodes", topology.nodes, file=handle)
        print("Connections", len(checked), file=handle)
        for flow_id, item in enumerate(checked, 1):
            flow, size_bytes, start_ps, rate_mbps = item
            line = (
                f"{flow.src}->{flow.dst} id {flow_id} "
                f"start {start_ps} size {size_bytes}"
            )
            if rate_mbps:
                line += f" rate_mbps {rate_mbps}"
            print(line, file=handle)


def sample_websearch_size(rng):
    rng = (rng if callable(getattr(rng, "random", None))
           else random.Random(rng))
    sample = rng.random()
    for size_bytes, cumulative_probability in WEBSEARCH_CDF:
        if sample < cumulative_probability:
            return size_bytes
    return WEBSEARCH_CDF[-1][0]


def _websearch_mean_size_bytes():
    mean_size = 0.0
    previous_probability = 0.0
    for size_bytes, cumulative_probability in WEBSEARCH_CDF:
        mean_size += size_bytes * (
            cumulative_probability - previous_probability
        )
        previous_probability = cumulative_probability
    return mean_size


WEBSEARCH_MEAN_SIZE_BYTES = _websearch_mean_size_bytes()


def websearch_flows(topology, seed, offered_load, duration_us):
    _validate_flow_topology(topology)
    load = _finite_float(offered_load, "offered load")
    duration = _finite_float(duration_us, "duration")
    if not 0 < load <= 1:
        raise ValueError("offered load must be in (0, 1]")
    if duration <= 0:
        raise ValueError("duration must be positive")

    arrival_rate_per_us = (
        load * LINKSPEED_MBPS / (8 * WEBSEARCH_MEAN_SIZE_BYTES)
    )
    flows = []
    for src in range(topology.nodes):
        rng = random.Random(f"{seed}:{src}")
        start_us = 0.0
        while True:
            start_us += rng.expovariate(arrival_rate_per_us)
            if start_us > duration:
                break
            dst = rng.randrange(topology.nodes - 1)
            if dst >= src:
                dst += 1
            flows.append(Flow(
                src,
                dst,
                sample_websearch_size(rng),
                start_us=start_us,
            ))
    return sorted(flows, key=lambda flow: (flow.start_us, flow.src, flow.dst))


def ordinary_scenarios():
    scenarios = []
    for size_mib in (4, 8, 16):
        size_bytes = size_mib * MIB
        for traffic in ("permutation", "tornado"):
            scenarios.append(Scenario(
                f"healthy_{traffic}_{size_mib}mib",
                traffic,
                size_bytes,
                "guardrail",
            ))
        scenarios.append(Scenario(
            f"incast_8to1_{size_mib}mib",
            "incast",
            size_bytes,
            "guardrail",
            incast_fanin=8,
            start_us=250,
        ))
        for traffic in ("permutation", "tornado"):
            scenarios.append(Scenario(
                f"asymmetric_{traffic}_{size_mib}mib",
                traffic,
                size_bytes,
                "path_opportunity",
                degraded=True,
            ))

    scenarios.append(Scenario(
        "diagnostic_single_slow_link_permutation_32mib",
        "permutation",
        32 * MIB,
        "diagnostic",
        degraded=True,
        slow_uplinks=1,
    ))
    for size_mib in (4, 16):
        size_bytes = size_mib * MIB
        scenarios.extend([
            Scenario(
                f"mixed_fixed_background_target_{size_mib}mib",
                "mixed",
                size_bytes,
                "path_opportunity",
                mixed=True,
                start_us=250,
            ),
            Scenario(
                f"persistent_hotspot_target_{size_mib}mib",
                "permutation",
                size_bytes,
                "path_opportunity",
                hotspot_rate_gbps=300,
                hotspot_on_us=1000,
                hotspot_off_us=0,
                start_us=250,
            ),
            Scenario(
                f"periodic_hotspot_target_{size_mib}mib",
                "permutation",
                size_bytes,
                "path_opportunity",
                hotspot_rate_gbps=300,
                hotspot_on_us=100,
                hotspot_off_us=100,
                start_us=250,
            ),
        ])

    for load_pct in (40, 60, 80, 100):
        scenarios.append(Scenario(
            f"standard_websearch_proxy_{load_pct}pct",
            "standard_websearch_proxy",
            int(round(WEBSEARCH_MEAN_SIZE_BYTES)),
            "load_sweep",
            offered_load=load_pct / 100,
        ))
    return tuple(scenarios)

RTT_RE = re.compile(
    r"RoCE estimated RTT ([0-9]+(?:\.[0-9]+)?)us(?=$|[,\s])"
)


def _finite_decimal(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be a finite number") from None
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return result


def _positive_decimal(value, name):
    result = _finite_decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _nonnegative_decimal(value, name):
    result = _finite_decimal(value, name)
    if result < 0:
        raise ValueError(f"{name} must be non-negative")
    return result


def bdp_packets(linkspeed_mbps, rtt_us, mtu_bytes):
    linkspeed = _positive_decimal(linkspeed_mbps, "link speed")
    rtt = _positive_decimal(rtt_us, "RTT")
    mtu = _positive_decimal(mtu_bytes, "MTU")
    packets = (
        linkspeed * rtt
        / (mtu * 8)
    )
    return int(packets.to_integral_value(rounding=ROUND_CEILING))


def theoretical_rtt_us(tiers, hop_latency_us, switch_latency_us):
    if tiers == 2:
        one_way_links, one_way_switches = 4, 3
    elif tiers == 3:
        one_way_links, one_way_switches = 6, 5
    else:
        raise ValueError(f"unsupported topology tier count {tiers}")
    hop_latency = _nonnegative_decimal(hop_latency_us, "hop latency")
    switch_latency = _nonnegative_decimal(
        switch_latency_us, "switch latency"
    )
    one_way_latency = (
        one_way_links * hop_latency
        + one_way_switches * switch_latency
    )
    if one_way_latency <= 0:
        raise ValueError("total RTT must be positive")
    return _finite_float(2 * one_way_latency, "total RTT")


def parse_runtime_rtt_us(text):
    match = RTT_RE.search(text)
    if match is None:
        raise ValueError("simulator output is missing RoCE estimated RTT")
    rtt = _positive_decimal(match.group(1), "runtime RTT")
    return _finite_float(rtt, "runtime RTT")


def number(value):
    return format(_finite_float(value, "CLI number"), ".9g")


def feedback_args(scheme, cadence, rtt_us):
    if scheme not in ("avail", "grade"):
        raise ValueError(f"unknown feedback scheme {scheme}")

    rtt = _positive_decimal(rtt_us, "feedback RTT")
    if cadence == "fixed5":
        packets, minimum, maximum = 1, 5.0, 5.0
    elif cadence == "fixed_rtt":
        packets, minimum, maximum = 1, rtt, rtt
    elif cadence == "bdp_triggered":
        packets = bdp_packets(LINKSPEED_MBPS, rtt, MTU_BYTES)
        minimum, maximum = Decimal("5"), 2 * rtt
        if maximum < minimum:
            raise ValueError("BDP-triggered maximum must be at least 5us")
    else:
        raise ValueError(f"unknown feedback cadence {cadence}")

    args = [
        "-stor_feedback_pkts", str(packets),
        "-stor_feedback_min_us", number(minimum),
        "-stor_feedback_max_us", number(maximum),
    ]
    trim_minimum = maximum if cadence == "bdp_triggered" else minimum
    args.extend([
        "-stor_trim_feedback_min_us", number(trim_minimum),
    ])
    return args
