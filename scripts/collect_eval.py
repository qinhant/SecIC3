#!/bin/python3
"""Batch evaluation runner for SecIC3 verification experiments.

Iterates over benchmark designs and verification techniques, runs the
fast_run_exp.sh pipeline for each combination, and collects solver
results into a timestamped log file.

Usage:
    python3 scripts/collect_eval.py [--timeout MINUTES]
"""

import argparse
import datetime
import os
import subprocess
import sys
import time
from typing import Callable

# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------

parse = argparse.ArgumentParser()
parse.add_argument(
    "--timeout", dest="timeout", default="60", help="timeout in minutes"
)
args = parse.parse_args()

USER_TIMEOUT = datetime.timedelta(minutes=float(args.timeout)).seconds
# Add a small grace period (5 %, capped at 60 s) so the subprocess has time
# to clean up before the outer timeout fires.
TIMEOUT = USER_TIMEOUT + min(60, round(USER_TIMEOUT * 0.05))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

scripts_folder = os.path.relpath(os.path.dirname(__file__))
eval_script = os.path.join(scripts_folder, "fast_run_exp.sh")

cwd = os.path.join(scripts_folder, "..")
output_folder = os.path.relpath(os.path.join(cwd, "output"))
log_filename = os.path.join(
    output_folder, datetime.datetime.now().strftime("eval_%Y%m%d_%H%M.log")
)

# ---------------------------------------------------------------------------
# Benchmark designs  (short name -> verilog stem used by fast_run_exp.sh)
# ---------------------------------------------------------------------------

examples = {
    "multiplier": "multiplier_miter",
    "sodor": "sodor5_miter_clean",
    "rocket": "rocket_clean",
    "modexp": "rsa_modexp_miter",
    "secenclave": "SE_leakymul_miter",
    "cache": "cache_miter",
    "gcd": "gcd_miter",
    "fp_divider": "single_divider_ws_miter",
    "fp_multiplier": "single_multiplier_ws_miter",
    "fp_adder": "single_adder_ws_miter",
}

# ---------------------------------------------------------------------------
# Technique configurations
#
# base_flags are prepended to every run:
#   f = flatten, a = AIGER conversion, y = verify invariant
#
# Each technique entry maps a label to the additional flags passed to
# fast_run_exp.sh.  Prefixes:  abc_ = ABC/PDR back-end, ric3_ = rIC3 back-end
#   sc  = shortcut signals (m flag)
#   ept = predicate replacement with eq_init (spk flags)
#   epi = iterative predicate replacement (spdk flags)
#   epx = exhaustive predicate replacement (skpx flags)
# ---------------------------------------------------------------------------

base_flags = "fa"

technique_flags = {
    # ABC/PDR-based techniques
    "abc_orig": "ri",
    "sc": "rim",
    "ept": "rispk",
    "epi": "rispdk",
    "sc_ept": "rimspk",
    "sc_epi": "rimspdk",
    "epx": "riskpx",
    "sc_epx": "rimskpx",
    # rIC3-based techniques
    "ric3_orig": "g",
    "ric3_sc": "gm",
    "ric3_ept": "gskp",
    "ric3_sc_ept": "gmskp",
    "ric3_epi": "gskpd",
    "ric3_sc_epi": "gmskpd",
    "ric3_epx": "gskpx",
    "ric3_sc_epx": "gmskpx",
}

eval_order = technique_flags.keys()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def log_eval(
    *,
    example: str,
    result_file: str,
    flags: str,
    output_label: str,
    timeout: str,
    valid_retcodes: dict[int, str] = {0: "ok"},
    log: Callable[[str], None],
):
    """Run a single evaluation and log its results.

    Executes fast_run_exp.sh with the given flags, then greps the result file
    for summary lines and verification status.
    """
    eval_args = f"-{flags} -O {output_label} {example}"
    cmd = f"{eval_script} {eval_args}"
    log(f">> {cmd}   ===> ")
    try:
        subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, check=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT ({USER_TIMEOUT}s)\n")
        return
    except subprocess.CalledProcessError as err:
        if err.returncode in valid_retcodes:
            log(f"{valid_retcodes[err.returncode]}\n")
        else:
            log(f"ERROR!!! (code {err.returncode})\n")
            log(repr(valid_retcodes))
            log(err.stderr.decode("utf-8"))
            return

    # Extract key-value summary lines from the result file
    cmd = f"grep ' : ' {result_file}"
    log(f">> {cmd}   ===> ")
    try:
        results = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True)
        log("ok\n")
        log(results.stdout.decode("utf-8"))
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT ({USER_TIMEOUT}s)\n")
    except subprocess.CalledProcessError as err:
        log(f"ERROR!!! (code {err.returncode})\n")
        log(err.stderr.decode("utf-8"))
    finally:
        log("\n\n")

    # Extract verification status or solver statistics
    cmd = (
        f"grep -E -A 200 -m 1 "
        f"'(Verification .* successful)|(Block =)|(SolverStatistic.*)' {result_file}"
    )
    log(f">> {cmd}   ===> ")
    try:
        results = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True)
        log("ok\n")
        log(results.stdout.decode("utf-8"))
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT ({USER_TIMEOUT}s)\n")
    except subprocess.CalledProcessError as err:
        log(f"ERROR!!! (code {err.returncode})\n")
        log(err.stderr.decode("utf-8"))
    finally:
        log("\n\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

with open(log_filename, "w") as log_file:

    def log(msg: str):
        """Write a message to both the log file and stdout."""
        log_file.write(msg)
        log_file.flush()
        print(msg, end="", flush=True)

    log(f"Performing evaluations with TIMEOUT {USER_TIMEOUT}s\n\n")

    # --- Check that abc_exp is available -----------------------------------
    log("#### Ensuring `abc_exp` is available ####\n")
    cmd = "which abc_exp"
    log(f">> {cmd}   ===>   ")
    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        log("ok\n")
    except subprocess.CalledProcessError:
        log("  ERROR: abc_exp is not in the path\n")

        solver_dir = os.path.join(cwd, "solvers/abc_exp")
        solver = os.path.join(solver_dir, "abc")
        cmd = f"realpath {solver}"
        log(f">> {cmd}   ===>   ")
        try:
            subprocess.run(cmd, shell=True, check=True, capture_output=True)
            log("ok\n")
        except subprocess.CalledProcessError:
            log("ERROR: abc_exp is not built \n")
            if input(f"Rebuild {solver} (via make)? [y/N] ").lower() in ["y", "yes"]:
                subprocess.run("make", shell=True, cwd=solver_dir, check=True)
                log("Built!\n\n")
            else:
                log("Quitting.\n")
                sys.exit(1)

        cmd = f"ln -s $(realpath {solver}) /bin/abc_exp"
        log("Can add `abc_exp` to $PATH via a symlink:\n")
        log(f">> {cmd}  # (proposed)\n")
        if input("Add `abc_exp` to $PATH? [y/N] ").lower() in ["y", "yes"]:
            log(f">> {cmd}   ===>   ")
            subprocess.run(cmd, shell=True, check=True)
            log("ok\n")
        else:
            log("Quitting.\n")
            sys.exit(1)

    log("\n\n")

    # --- Check that rIC3 is available --------------------------------------
    log("#### Ensuring `rIC3` is available ####\n")
    cmd = "which rIC3_exp_latest"
    log(f">> {cmd}   ===>   ")
    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        log("ok\n")
    except subprocess.CalledProcessError:
        log("  ERROR: rIC3 is not in the path\n")

    log("\n\n")

    # --- Run all technique × benchmark combinations -----------------------
    for tech in eval_order:
        for name, ex in examples.items():
            log(f"#### Evaluating technique {tech} on example {name} #### \n")

            if "ric3" in tech:
                result_file = os.path.join(
                    output_folder, f"{ex}_{tech}_exp", "*.log"
                )
                valid_retcodes = {0: "unknown", 10: "unsafe", 20: "safe"}
            else:
                result_file = os.path.join(
                    output_folder, f"{ex}_{tech}_exp", "*_interpreted.log"
                )
                valid_retcodes = {0: "ok"}

            # SE_leakymul_miter needs reuse flag (-n) to keep pre-built artifacts
            flags = base_flags
            if name == "secenclave":
                flags += "n"
            flags += technique_flags[tech]

            log_eval(
                example=ex,
                result_file=result_file,
                flags=flags,
                output_label=tech,
                timeout=TIMEOUT,
                valid_retcodes=valid_retcodes,
                log=log,
            )
            time.sleep(1)
