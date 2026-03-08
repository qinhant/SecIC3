
# SecIC3: Customizing IC3 for Hardware Security Verification

**Published at [DATE 2026](https://www.date-conference.com)** | [Paper (arXiv)](https://arxiv.org/abs/2601.21353)

Qinhan Tan, Akash Gaonkar, Yu-Wei Fan, Aarti Gupta, and Sharad Malik

*Princeton University*

> SecIC3 is a hardware model checking algorithm based on IC3 that is customized to exploit the self-composition structure used in non-interference verification. It introduces two complementary techniques — *symmetric state exploration* and *adding equivalence predicates* — that improve solver performance by leveraging the symmetry of self-composed designs. Implemented on top of ABC-PDR and rIC3 (winner of HWMCC 2024), SecIC3 achieves up to 49.3x proof speedup on a benchmark of 10 hardware designs.

A formal verification framework for hardware security analysis using IC3/PDR-based model checking enhanced with shortcut signals, predicate replacement, and smart duplication techniques.

SecIC3 verifies security properties (e.g., information flow, side-channel resistance) in digital circuits by transforming Verilog/SystemVerilog designs into AIGER format, injecting auxiliary predicates, and solving with multiple IC3 back-ends.

## Repository Structure

```
SecIC3/
├── verilog/             # Hardware designs and benchmarks (.sv)
│   └── design_info/     # Secret fanout metadata (JSON)
├── scripts/             # Python & Bash pipeline scripts, Yosys synthesis scripts (.ys)
├── solvers/             # Verification engines (git submodules)
│   ├── rIC3/            # Rust IC3 implementation
│   ├── rIC3_exp/        # Experimental rIC3 with predicate support
│   └── abc_exp/         # Modified ABC solver
├── yosys_output/        # Intermediate synthesis outputs (.aig, .map)
├── abc_output/          # Solver output files (.cex, .pla)
├── docker/              # Dockerfile for reproducible builds
└── Cargo.toml           # Rust workspace configuration
```

### Key Scripts

| Script | Purpose |
|--------|---------|
| `fast_run_exp.sh` | Main pipeline orchestrator |
| `transform_verilog.py` | Verilog preprocessing and flattening |
| `shortcut_signals.py` | Injects shortcut/bypass predicates |
| `smart_duplication.py` | Smart register/signal duplication based on secret fanout |
| `pdr_interpreter.py` | Parses ABC PDR output into readable invariants |
| `get_secret_fanout.py` | Extracts secret signal fanout information |
| `collect_eval.py` | Aggregates evaluation results across benchmarks |

## Requirements

- [Yosys](https://github.com/YosysHQ/yosys) (via [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build))
- [ABC](https://github.com/berkeley-abc/abc)
- Python 3.11+
- Rust (nightly) — for building rIC3 solvers
- CMake and a C++ build toolchain — for building ABC

## Setup

### Docker (Recommended)

Build the Docker image:
```bash
docker build -t "shortcutlogic" docker/
```
For VSCode devcontainer support, the image must be named `shortcutlogic`.

### Manual

1. Install Yosys via [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) and ensure `yosys` is on your `PATH`.
2. Install Rust nightly: `rustup default nightly`
3. Clone with submodules:
   ```bash
   git clone --recurse-submodules <repo-url>
   ```
4. Build solvers:
   ```bash
   # Build rIC3
   cargo build --release

   # Build ABC
   cd solvers/abc_exp && make -j$(nproc)
   ```

## Usage

### Quick Start

Run the full pipeline on a design:
```bash
bash scripts/fast_run_exp.sh -fasrimp multiplier_miter
```

### Pipeline Options

```
Usage: fast_run_exp.sh [-fasrimpdxnkgvO suffix] <design>
```

| Flag | Description |
|------|-------------|
| `-f` | Flatten the netlist |
| `-a` | Convert to AIGER format |
| `-s` | Add shortcut signals |
| `-r` | Run ABC with PDR |
| `-i` | Interpret the solver log |
| `-m` | Enable symmetry detection |
| `-p` | Enable predicate replacement |
| `-d` | Enable iterative predicate replacement |
| `-x` | Enable exhaustive predicate replacement |
| `-n` | Reuse existing .aig, relation, and map files |
| `-k` | Enable semantic enforce option |
| `-g` | Run rIC3 |
| `-v` | Verbose output |
| `-O suffix` | Append a suffix to the output directory |

### Example Workflows

```bash
# Flatten + AIGER conversion + ABC/PDR with shortcut signals
bash scripts/fast_run_exp.sh -fasr multiplier_miter

# Full pipeline with smart duplication and predicate replacement
bash scripts/fast_run_exp.sh -fasrimp multiplier_miter

# Run with rIC3 instead of ABC
bash scripts/fast_run_exp.sh -fasg multiplier_miter
```

## Running a Full Evaluation

`scripts/collect_eval.py` automates a full evaluation by running every
verification technique on every benchmark design and collecting the results
into a single timestamped log file.

### Prerequisites

Both solver binaries must be on your `PATH`:

```bash
# ABC (build from the submodule, then symlink or add to PATH)
cd solvers/abc_exp && make -j$(nproc)
sudo ln -s $(realpath abc) /usr/local/bin/abc_exp

# rIC3 (build from the workspace root)
cargo build --release
sudo ln -s $(realpath target/release/rIC3_exp_latest) /usr/local/bin/rIC3_exp_latest
```

Verify both are accessible:
```bash
which abc_exp          # should print a path
which rIC3_exp_latest  # should print a path
```

### Running

```bash
# Default timeout: 60 minutes per run
python3 scripts/collect_eval.py

# Custom timeout (e.g., 30 minutes)
python3 scripts/collect_eval.py --timeout 30
```

### What It Does

The script iterates over all 10 benchmark designs and 16 technique
configurations (8 ABC-based + 8 rIC3-based):

| Technique | Flags | Description |
|-----------|-------|-------------|
| `abc_orig` | `ri` | Baseline ABC/PDR |
| `sc` | `rim` | + shortcut signals |
| `ept` | `rispk` | + predicate replacement |
| `epi` | `rispdk` | + iterative predicate replacement |
| `sc_ept` | `rimspk` | + shortcuts + predicate replacement |
| `sc_epi` | `rimspdk` | + shortcuts + iterative predicate replacement |
| `epx` | `riskpx` | + exhaustive predicate replacement |
| `sc_epx` | `rimskpx` | + shortcuts + exhaustive predicate replacement |
| `ric3_orig` | `g` | Baseline rIC3 |
| `ric3_sc` | `gm` | + shortcut signals |
| `ric3_ept` | `gskp` | + predicate replacement |
| `ric3_sc_ept` | `gmskp` | + shortcuts + predicate replacement |
| `ric3_epi` | `gskpd` | + iterative predicate replacement |
| `ric3_sc_epi` | `gmskpd` | + shortcuts + iterative predicate replacement |
| `ric3_epx` | `gskpx` | + exhaustive predicate replacement |
| `ric3_sc_epx` | `gmskpx` | + shortcuts + exhaustive predicate replacement |

For each (technique, design) pair the script:

1. Runs `fast_run_exp.sh` with the combined flags (`fa` base + technique flags)
2. Greps the solver output for summary statistics and verification status
3. Logs everything to `output/eval_YYYYMMDD_HHMM.log`

The full evaluation runs **160 experiments** (16 techniques x 10 designs).
Progress and results are printed to both stdout and the log file.

## Benchmarks

| Design file | Description |
|-------------|-------------|
| `multiplier_miter` | Multiplier |
| `sodor5_miter_clean` | Sodor 5-stage (RISC-V) |
| `rocket_clean` | Rocket (RISC-V) |
| `rsa_modexp_miter` | RSA modular exponentiation |
| `SE_leakymul_miter` | Side-channel leaky multiplier |
| `cache_miter` | Cache |
| `gcd_miter` | GCD |
| `single_divider_ws_miter` | FP divider |
| `single_multiplier_ws_miter` | FP multiplier |
| `single_adder_ws_miter` | FP adder |
