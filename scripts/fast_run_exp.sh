#!/usr/bin/bash
#
# fast_run_exp.sh — Main pipeline orchestrator for SecIC3 experiments.
#
# Runs a configurable sequence of steps on a given design:
#   1. Flatten the Verilog netlist
#   2. Inject shortcut signals / predicates
#   3. Convert to AIGER format
#   4. Run a solver (ABC/PDR or rIC3)
#   5. Interpret results
#
# See usage() below for the full list of flags.

OPTIND=1

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------

usage() {
  echo "Usage: $0 [-fasrimpdxnkgvO suffix] <design>"
  echo "Options:"
  echo "  -f   Flatten the netlist"
  echo "  -a   Transform the netlist into AIGER format"
  echo "  -s   Add shortcut signals"
  echo "  -r   Run ABC with PDR"
  echo "  -i   Interpret the log"
  echo "  -m   Use symmetry"
  echo "  -p   Use predicate replacement"
  echo "  -d   Use iterative predicate replacement"
  echo "  -x   Use exhaustive predicate replacement"
  echo "  -n   Use existing aig, relation and map files"
  echo "  -k   Run shortcut_signals.py with semantic enforce option"
  echo "  -g   Run rIC3"
  echo "  -v   Verbose output"
  echo "  -O suffix  Specify a suffix for the output directory"
  exit 1
}

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------

flatten=false
aig=false
shortcut=false
pdr=false
interpret=false
symmetry=false
predicate=false
iterative=false
exhaustive=false
reuse=false
senmantic_enforce=false
rIC3=false
verbose=false
suffix=""

while getopts "fasrimpdxnkgvO:" opt; do
  case $opt in
    f) flatten=true ;;
    a) aig=true ;;
    s) shortcut=true ;;
    r) pdr=true ;;
    i) interpret=true ;;
    m) symmetry=true ;;
    p) predicate=true ;;
    d) iterative=true ;;
    x) exhaustive=true ;;
    n) reuse=true ;;
    k) senmantic_enforce=true ;;
    g) rIC3=true ;;
    v) verbose=true ;;
    O) suffix="_$OPTARG" ;;
    *) usage ;;
  esac
done
shift $((OPTIND - 1))

# Always enable semantic enforce (overrides -k flag).
senmantic_enforce=true

if [ $# -ne 1 ]; then
  usage
fi

# ---------------------------------------------------------------------------
# Setup paths
# ---------------------------------------------------------------------------

design=$1
output_dir="output/${design}${suffix}_exp"
top="top"

# Clean the output directory unless we are reusing previous artifacts.
if ! $reuse; then
  rm -f "${output_dir}"/*
fi
mkdir -p "${output_dir}"

# $file tracks the current intermediate filename stem as it flows through
# each pipeline stage (design → flatten → shortcut).
file="${design}"

# ---------------------------------------------------------------------------
# Step 1: Flatten the Verilog netlist
# ---------------------------------------------------------------------------

if $flatten; then
  echo "Flattening the netlist for ${design}..."
  if ! $reuse; then
    python3 scripts/transform_verilog.py \
      --input "verilog/${file}.sv" \
      --output "${output_dir}/flatten.sv" \
      --top "${top}" \
      --option flatten
  fi
  file="flatten"
fi

# ---------------------------------------------------------------------------
# Step 2: Add shortcut signals
# ---------------------------------------------------------------------------

if $shortcut; then
  echo "Adding shortcut signals to ${design}..."
  option="-s"
  if $senmantic_enforce; then
    option="${option}k"
  fi
  if ! $reuse; then
    python3 scripts/shortcut_signals.py \
      --input "${output_dir}/${file}.sv" \
      --output "${output_dir}/shortcut.sv" \
      --top "${top}" \
      "${option}"
  fi
  file="shortcut"
fi

# ---------------------------------------------------------------------------
# Step 3: Convert to AIGER format
# ---------------------------------------------------------------------------

if $aig && ! $reuse; then
  echo "Transforming Verilog to AIGER format for ${design}..."
  python3 scripts/transform_verilog.py \
    --input "${output_dir}/${file}.sv" \
    --output "${output_dir}/${file}.aig" \
    --top "${top}" \
    --option verilog_to_aig
fi

# ---------------------------------------------------------------------------
# Build ABC/PDR command string
# ---------------------------------------------------------------------------

pdr_commands="read ${output_dir}/${file}.aig;
    fold;
    pdr -v -d -T 3600 -I ${output_dir}/${file}.pla -R ${output_dir}/${file}.relation"
if $verbose;   then pdr_commands="${pdr_commands} -w"; fi
if $symmetry;  then pdr_commands="${pdr_commands} -s"; fi
if $predicate; then pdr_commands="${pdr_commands} -l"; fi
if $iterative; then pdr_commands="${pdr_commands} -b"; fi
if $exhaustive; then pdr_commands="${pdr_commands} -X"; fi
pdr_commands="${pdr_commands}; write_cex -n -m -f ${output_dir}/${file}.cex;"

# When reusing artifacts, copy pre-built .aig/.relation/.map from the cache.
version="original"
if $shortcut; then
  version="shortcut"
fi

if $reuse; then
  echo "Copying existing files for ${design}..."
  cp -f "output/file_reused/${design}/${version}.aig"      "${output_dir}/${file}.aig"
  cp -f "output/file_reused/${design}/${version}.relation"  "${output_dir}/${file}.relation"
  cp -f "output/file_reused/${design}/${version}.map"       "${output_dir}/${file}.map"
fi

# ---------------------------------------------------------------------------
# Step 4a: Run ABC with PDR
# ---------------------------------------------------------------------------

if $pdr; then
  pdr_commands="${pdr_commands} ps;"
  echo "Running ABC with PDR for ${design}..."
  echo "PDR commands are: ${pdr_commands}"
  abc_exp -c "${pdr_commands}" > "${output_dir}/pdr_${file}.log"
fi

# ---------------------------------------------------------------------------
# Step 4b: Run rIC3
# ---------------------------------------------------------------------------

# Configure Rust logging level.
if $verbose; then
  export RUST_LOG=trace
  export RUST_BACKTRACE=1
else
  export RUST_LOG=info
fi

# Build rIC3 option string.
map_path="--model-map ${output_dir}/${file}.map"
relation_path="--relation-file ${output_dir}/${file}.relation"

rIC3_options=""
if $symmetry;   then rIC3_options="${rIC3_options} --symmetry"; fi
if $predicate;  then rIC3_options="${rIC3_options} --equiv-predicate"; fi
if $iterative;  then rIC3_options="${rIC3_options} --iterative-predicate-replacement"; fi
if $exhaustive; then rIC3_options="${rIC3_options} --exhaustive-predicate-replacement"; fi

if $rIC3; then
  rIC3_command="rIC3_exp_latest --engine ic3 ${map_path} ${relation_path} ${rIC3_options} ${output_dir}/${file}.aig"
  echo "Running rIC3 for ${design}..."
  echo "$rIC3_command"
  eval "$rIC3_command" > "${output_dir}/ric3_${file}.log" 2>&1
fi

# ---------------------------------------------------------------------------
# Step 5: Interpret the PDR log
# ---------------------------------------------------------------------------

# Interpretation only makes sense after an ABC/PDR run.
if ! $pdr; then
  interpret=false
fi

if $interpret; then
  echo "Interpreting the PDR log for ${design}..."
  python3 scripts/pdr_interpreter.py \
    --log "${output_dir}/pdr_${file}.log" \
    --map "${output_dir}/${file}.map" \
    --inv "${output_dir}/${file}.pla" \
    --output "${output_dir}/pdr_${file}_interpreted.log" \
    --cex "${output_dir}/${file}.cex"
fi

# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------

if $pdr && $interpret; then
  grep " : " "${output_dir}/pdr_${file}_interpreted.log"
  grep -E -A 200 -m 1 "(Verification .* successful)|(Block =)|(SolverStatistic.*)" \
    "${output_dir}/pdr_${file}_interpreted.log"
fi

echo "Script completed."
