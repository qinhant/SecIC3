"""Parse ABC PDR solver logs and produce human-readable interpreted output.

Uses a .map file to translate numeric latch and input IDs to their
corresponding signal names.  Outputs an interpreted log file and,
optionally, an interpreted counterexample file.
"""

import sys
import argparse


def pdr_interpret(log_path, map_path, inv_path, output_path, cex_path):
    # ---- Parse the .map file into latch and input dictionaries ----
    with open(map_path, "r") as file:
        mapping = [line.strip() for line in file.readlines()]
    latch_map = dict()
    input_map = dict()
    for line in mapping:
        if line.find("latch") < 0 and line.find("input") < 0:
            continue
        elif line.find("latch") >= 0:
            split = line.split(" ")
            latch_num = split[1]
            bit = split[2]
            signal_name = split[3]
            if latch_num not in latch_map.keys():
                latch_map[latch_num] = signal_name + "[" + bit + "]"
        elif line.find("input") >= 0:
            split = line.split(" ")
            input_num = split[1]
            bit = split[2]
            signal_name = split[3]
            if input_num not in input_map.keys():
                input_map[input_num] = signal_name + "[" + bit + "]"

    # ---- Interpret the invariant (.pla) file, if provided ----
    if inv_path is not None:
        with open(inv_path, "r") as file:
            content = file.read()
        latch_list = [
            latch_map[str(int(e[2:]))]
            for e in content[content.find(".ilb") + 4 : content.find(".ob")]
            .strip()
            .split(" ")
            if len(e) > 0
        ]
        log_output = "---------------Invariant Clauses-------------------- \n"
        invariant = content[content.find(".ob inv") + 7 : content.find(".e")].strip()
        for line in invariant.split("\n"):
            line = line[: line.find(" ")]
            literals = []
            for i in range(len(line)):
                if line[i] == "0":
                    literals.append("!" + latch_list[i])
                elif line[i] == "1":
                    literals.append(latch_list[i])
            clause = " && ".join(literals)
            log_output += "!(" + clause + ")\n"
        log_output += "\n\n\n"
    else:
        log_output = "---------------No Invariant File-------------------- \n"

    # ---- Interpret the PDR log file ----
    log_output += "---------------PDR Log--------------------\n"
    with open(log_path, "r") as file:
        log = [line.strip() for line in file.readlines()]
    for line in log:
        if line.find("cube") >= 0:
            if line.find("<prop=fail>") >= 0:
                log_output += line + "\n"
                continue
            cube = line[line.find("cube ") + 5 :].strip()
            cube = cube[: cube.find(" ")].strip()
            literals = []
            if len(cube) > len(latch_map.keys()):
                print("invalid cube: ", cube)
            for i in range(len(cube)):
                if cube[i] == "0":
                    literals.append("!" + latch_map[str(i)])
                elif cube[i] == "1":
                    literals.append(latch_map[str(i)])
            line = line.replace(cube, "(" + " && ".join(literals) + ")")
            log_output += line + "\n"
        elif len(line) == (len(latch_map) + len(input_map)):
            literals = []
            for i in range(len(input_map)):
                if line[i] == "0":
                    literals.append("!" + input_map[str(i)])
                elif line[i] == "1":
                    literals.append(input_map[str(i)])
            for i in range(len(input_map), len(line)):
                if line[i] == "0":
                    literals.append("!" + latch_map[str(i - len(input_map))])
                elif line[i] == "1":
                    literals.append(latch_map[str(i - len(input_map))])
            line = line.replace(line, "(" + " && ".join(literals) + ")")
            log_output += line + "\n"
        elif line.find("C=") >= 0 and line.find("F=") >= 0:
            cube = line[line.find("F=") + 5: ].strip()
            cube = cube[cube.find(" ") + 1: ].strip()
            literals = []
            for i in range(len(cube)):
                if cube[i] == "0":
                    literals.append("!" + latch_map[str(i)])
                elif cube[i] == "1":
                    literals.append(latch_map[str(i)])
            line = line.replace(cube, "(" + " && ".join(literals) + ")")
            log_output += line + "\n"
        else:
            log_output += line + "\n"

    # ---- Write the interpreted log ----
    if output_path is None:
        output_path = log_path.replace(".log", "_interpreted.log")
    with open(output_path, "w") as file:
        file.write(log_output)

    # ---- Write the interpreted counterexample, if requested ----
    if cex_path is None:
        return
    cex_output = "---------------Counterexample-------------------- \n"
    try:
        with open(cex_path, "r") as file:
            cex = file.read().strip().split(" ")
    except FileNotFoundError:
        print("Counterexample file not found!")
        return
    for line in cex:
        if line.find("pi") >= 0:
            input_num = line[line.find("pi") + 2 : line.find("@")].strip()
            line = line.replace(
                "pi" + input_num + "@", input_map[str(int(input_num))] + "@"
            )
            cex_output += line + "\n"
        elif line.find("lo") >= 0:
            latch_num = line[line.find("lo") + 2 : line.find("@")].strip()
            line = line.replace(
                "lo" + latch_num + "@", latch_map[str(int(latch_num))] + "@"
            )
            cex_output += line + "\n"
    cex_file = cex_path.replace(".cex", "_interpreted.cex")
    with open(cex_file, "w") as file:
        file.write(cex_output)


if __name__ == "__main__":
    parse = argparse.ArgumentParser()
    parse.add_argument("--log", dest="log_path", required=True, help="input .log path")
    parse.add_argument("--map", dest="map_path", required=True, help="input .map path")
    parse.add_argument(
        "--inv", dest="inv_path", default=None, help="input invariant (.pla) path"
    )
    parse.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help="output log path",
    )
    parse.add_argument(
        "--cex",
        dest="cex_path",
        default=None,
        help="input counterexample(.cex) file path",
    )
    args = parse.parse_args()

    if not args.log_path.endswith(".log"):
        print("Invalid log file, must be a .log file")
        sys.exit(1)
    if not args.map_path.endswith(".map"):
        print("Invalid map file, must be a .map file")
        sys.exit(1)
    if args.inv_path is not None and not args.inv_path.endswith(".pla"):
        print("Invalid invariant file, must be a .pla file")
        sys.exit(1)
    if args.output_path is not None and not args.output_path.endswith(".log"):
        print("Invalid output file, must be a .log file")
        sys.exit(1)
    if args.cex_path is not None and not args.cex_path.endswith(".cex"):
        print("Invalid counterexample file, must be a .cex file")
        sys.exit(1)

    pdr_interpret(
        args.log_path, args.map_path, args.inv_path, args.output_path, args.cex_path
    )
