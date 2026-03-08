"""Verilog preprocessing utilities.

Provides functions to flatten Verilog designs, convert Verilog to AIGER format,
create miter circuits for equivalence checking, and remove $fatal/$fwrite blocks.
All transformations delegate to Yosys via temporary scripts.
"""

import sys
import argparse
import os
import re


def flatten_verilog(input_path, output_path, top, no_assumption):
    # Create a temporary yosys script using the input arguments
    with open("scripts/flatten_verilog.ys", "r") as file:
        content = file.read()
    content = content.replace("input_path", input_path)
    content = content.replace("output_path", output_path)
    content = content.replace("top_module", top)
    file = open("scripts/flatten_verilog_temp.ys", "w")
    file.write(content)
    file.close()

    # Run yosys with the temporary script and remove the temporary script
    if no_assumption:
        flatten_command = f"yosys -D ASSUME_ON=0 -s scripts/flatten_verilog_temp.ys"
    else:
        flatten_command = f"yosys -D ASSUME_ON=1 -s scripts/flatten_verilog_temp.ys"
    os.system(flatten_command)
    os.system("rm scripts/flatten_verilog_temp.ys")
    print(flatten_command)


def verilog_to_aig(input_path, output_path, top):
    # Create a temporary yosys script using the input arguments
    with open("scripts/verilog_to_aig.ys", "r") as file:
        content = file.read()
    content = content.replace("input_path", input_path)
    content = content.replace("output_path", output_path)
    content = content.replace("top_module", top)
    content = content.replace("map_path", output_path.replace(".aig", ".map"))
    with open("scripts/verilog_to_aig_temp.ys", "w") as file:
        file.write(content)

    # Run yosys with the temporary script and remove the temporary script
    os.system("yosys -s scripts/verilog_to_aig_temp.ys")
    os.system("rm scripts/verilog_to_aig_temp.ys")

    # Collect all the predicate variables and write to a file
    map_path = output_path.replace(".aig", ".map")
    with open(map_path, "r") as file:
        content = file.read()
        content = content.split("\n")
        predicates = set()
        for line in content:
            if line.startswith("latch") and line.find("shortcut.neq_") >= 0:
                predicates.add(line.split(" ")[1])
        predicates = list(predicates)
        predicates.sort()
        with open(map_path.replace(".map", ".priority"), "w") as file:
            for pred in predicates:
                file.write(pred + "\n")

    # Collect the relation between variables and write to a file
    class Latch:
        id: int
        bit_index: int
        signal_name: str

        def __init__(self, id: int, bit_index: int, signal_name: str):
            self.id = id
            self.bit_index = bit_index
            self.signal_name = signal_name

        def var_name(self):
            return f"{self.signal_name}[{self.bit_index}]"

        @staticmethod
        def parse_line(line):
            sig_type, sig_id, bit_index, sig_name = line.split(" ")
            return Latch(int(sig_id), int(bit_index), sig_name)

    with open(map_path, "r") as file:
        latches = [Latch.parse_line(l.strip()) for l in file.readlines() if l.startswith("latch")]

    latch_to_var: dict[int, Latch] = dict()
    var_to_latch: dict[(str, int), Latch] = dict()
    latch_symmetry: dict[int, int] = dict()
    latch_to_equiv_predicate: dict[int, int] = dict()
    latch_to_eqinit_predicate: dict[int, int] = dict()
    all_latch: set[int] = set()

    for l in latches:
        var_to_latch[(l.signal_name, l.bit_index)] = l
        latch_to_var[l.id] = l
        all_latch.add(l.id)

    # Regex patterns for matching copy1/copy2 prefixed signal names
    copy_prefix1 = r"copy1."
    copy_prefix2 = r"copy2."
    copy_pre = re.compile(rf"({re.escape(copy_prefix1)}|{re.escape(copy_prefix2)})")
    is_copy = re.compile(rf"{copy_pre.pattern}(?P<name>.+)")

    for l in latches:

        match = is_copy.match(l.signal_name)
        if match:
            # Find the symmetric latch (copy1 <-> copy2)
            symmetric_prefix = (
                copy_prefix1 if l.signal_name.startswith(copy_prefix2)
                else copy_prefix2
            )
            symmetric_name = symmetric_prefix + match.group("name")
            if (symmetric_name, l.bit_index) in var_to_latch.keys():
                latch_symmetry[l.id] = var_to_latch[(symmetric_name, l.bit_index)].id
            else:
                latch_symmetry[l.id] = l.id

            # Map latch to its equivalence predicate
            equiv_predicate_name = f"shortcut.neq_{match.group('name')}_copy2"
            try:
                latch_to_equiv_predicate[l.id] = var_to_latch[(equiv_predicate_name, 0)].id
            except KeyError:
                if l.id not in latch_to_equiv_predicate.keys():
                    latch_to_equiv_predicate[l.id] = -1

            # Map latch to its equal-initial-state predicate
            eqinit_predicate_name = f"shortcut.neqinit.{l.signal_name}"
            try:
                latch_to_eqinit_predicate[l.id] = var_to_latch[(eqinit_predicate_name, 0)].id
            except KeyError:
                if l.id not in latch_to_eqinit_predicate.keys():
                    latch_to_eqinit_predicate[l.id] = -1
        elif l.signal_name.startswith("shortcut.neqinit"):
            symmetric_name = (
                l.signal_name.replace("copy1.", "copy2.") if l.signal_name.find("copy1.") >= 0
                else l.signal_name.replace("copy2.", "copy1.")
            )
            latch_symmetry[l.id] = var_to_latch[(symmetric_name, l.bit_index)].id

    # Write the relation file: latch, symmetry, predicates, and variable names
    with open(map_path.replace(".map", ".relation"), "w") as file:
        file.write("latch symmetry predicate var_name symmetry_name\n")
        for latch in sorted(list(all_latch)):
            sym_latch = latch_symmetry.get(latch, latch)
            neq_pred = latch_to_equiv_predicate.get(latch, -1)
            neqinit_pred = latch_to_eqinit_predicate.get(latch, -1)
            name = latch_to_var[latch].var_name()
            sym_name = latch_to_var[sym_latch].var_name()
            file.write(
                f"{latch} {sym_latch} {neq_pred} {neqinit_pred} {name} {sym_name} \n"
            )


def create_miter(input_path, output_path, top):
    # Create a miter circuit, i.e. creating two copies for equivalence checking
    with open("scripts/create_miter.ys", "r") as file:
        content = file.read()
    content = content.replace("input_path", input_path)
    content = content.replace("output_path", output_path)
    content = content.replace("top_module", top)
    content = content.replace("miter_top", f"{top}_miter")

    file = open("scripts/create_miter_temp.ys", "w")
    file.write(content)
    file.close()

    # Run yosys with the temporary script and remove the temporary script
    os.system("yosys -s scripts/create_miter_temp.ys")
    os.system("rm scripts/create_miter_temp.ys")


def remove_f_blocks(input_path, output_path):
    """
    Removes all $fatal and $fwrite blocks (including multi-line ones) from a Verilog file.

    Parameters:
        input_file (str): Path to the input Verilog file.
        output_file (str): Path to save the modified Verilog file.
    """
    # Read the content of the input file
    with open(input_path, "r") as file:
        content = file.read()

    # Regex patterns to match $fatal and $fwrite blocks (including multiline)
    fatal_pattern = re.compile(r"\$fatal.*?; //", re.DOTALL)
    fwrite_pattern = re.compile(r"\$fwrite.*?; //", re.DOTALL)

    # Remove all matches of $fatal and $fwrite blocks
    content = fatal_pattern.sub("//", content)
    content = fwrite_pattern.sub("//", content)

    # Write the modified content to the output file
    with open(output_path, "w") as file:
        file.write(content)

    print(f"Processed file saved to: {output_path}")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input", dest="input_path", required=True, help="input file path"
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help="output path",
    )
    parser.add_argument("--top", dest="top", required=True, help="top module")
    parser.add_argument(
        "--option",
        dest="option",
        required=True,
        help="available options: flatten verilog_to_aig create_miter",
    )
    parser.add_argument(
        "--no_assumption",
        dest="no_assumption",
        action="store_true",
        default=False,
        help="Disable to assumption macro in verilog"
    )
    args = parser.parse_args()
    if not args.input_path.endswith(".v") and not args.input_path.endswith(".sv"):
        print("Invalid input file, must be a .v or .sv file")
        sys.exit(1)

    if args.option == "flatten":
        flatten_verilog(args.input_path, args.output_path, args.top, args.no_assumption)
    elif args.option == "verilog_to_aig":
        if not args.output_path.endswith(".aig"):
            print("Invalid output file, must be a .aig file")
            sys.exit(1)
        verilog_to_aig(args.input_path, args.output_path, args.top)
    elif args.option == "create_miter":
        create_miter(args.input_path, args.output_path, args.top)
    elif args.option == "remove_f_blocks":
        remove_f_blocks(args.input_path, args.output_path)
