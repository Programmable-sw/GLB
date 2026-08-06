# Canonical 64-Spine Topology Design

## Goal

Make the generated two-tier topology use one canonical shape at every
supported scale. The default run uses 256 nodes. Larger runs change only the
number of Leaf switches; they do not change the number of Spine switches,
hosts per Leaf, or uplinks per Leaf.

## Canonical scales

The generated two-tier topology accepts exactly these node counts:

| Nodes | Leaves | Spines | Hosts per Leaf | Uplinks per Leaf |
| ---: | ---: | ---: | ---: | ---: |
| 256 | 4 | 64 | 64 | 64 |
| 512 | 8 | 64 | 64 | 64 |
| 1024 | 16 | 64 | 64 | 64 |
| 2048 | 32 | 64 | 64 | 64 |
| 4096 | 64 | 64 | 64 | 64 |
| 8192 | 128 | 64 | 64 | 64 |

The default node count is 256.

## Validation behavior

When `htsim_roce` generates a two-tier topology without a topology file, a
node count outside the six canonical values is a configuration error. The
program must print an actionable message listing the supported values and
exit nonzero. It must not silently fall back to the legacy generated topology.

Explicit topology configuration files remain supported and retain their own
topology parameters. Three-tier generated topologies are outside this change.

## Implementation boundary

The command-line program owns scale validation and selects the existing
fixed-radix two-tier topology path. `FatTreeTopology` continues to own the
construction invariant: 64 hosts per Leaf, 64 Leaf uplinks, and 64 Spines.
No load-balancing scheme receives topology-specific exceptions.

## Tests

The topology regression test will verify:

1. Omitting `-nodes` selects 256 nodes and produces 4 Leaves and 64 Spines.
2. Each of the six supported scales reports the expected Leaf count and fixed
   64-Spine/64-downlink/64-uplink shape.
3. A noncanonical generated two-tier scale, such as 128 or 432 nodes, exits
   nonzero with the supported-scale diagnostic.
4. Existing MRC, RR, SGLB, and SGLB-old focused tests continue to pass.

## Handoff state

The implementation changes will be left unstaged and uncommitted so the user
can review them and run `git add .` directly.
