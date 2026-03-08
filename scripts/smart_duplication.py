"""Implement smart duplication for IC3 verification state-space reduction.

Given a two-copy miter design and secret fanout information, this script
replaces copy2 signals that are NOT in the secret's fanout cone with their
copy1 equivalents.  This collapses redundant state bits and shrinks the
state space that IC3 must explore.
"""

import re
import json


def smart_duplicate(fanout_signals, miter_file, output_file, prefix1 = r"\copy1.", prefix2 = r"\copy2."):
    commentp = re.compile(r"\(\*.*\*\)")
    comments = re.compile(r"/\*.*\*/")
    comment = re.compile(rf"({commentp.pattern}|{comments.pattern})")
    skip = re.compile(rf"(\s|{comment.pattern})*")

    assign = re.compile(
        r"(?P<lhs>\s*(assign )?(?P<var>[\[\w\.\\\d\]]+)\s*(\[[0-9:\s]*\])?\s*(=|<=))"
        rf"\s*(?P<rhs>.+);(?P<comments>{skip.pattern})"
    )
    copy2_reg = re.compile(rf"\b(?P<copy>{prefix2})(?P<name>[\w\.]+)?\b")

    with open(miter_file, "r") as f:
        miter_lines = f.readlines()
    with open(output_file, "w") as f:
        for l in miter_lines:
            if match := assign.match(l):
                lhs = match.group("lhs")
                var = match.group("var")
                rhs = match.group("rhs")
                comments = match.group("comments")
                rhs = re.sub(copy2_reg, lambda m: (prefix2 if m.group("name") in fanout_signals else prefix1) + m.group("name"), rhs)
                f.write(
                    f"{lhs} {rhs};{comments}"
                )
            else:
                f.write(l)

if __name__ == "__main__":
    import argparse
    parse = argparse.ArgumentParser()
    parse.add_argument(
        "--copy1_prefix",
        dest="prefix1",
        default="copy1.",
        help="prefix used to identify copy1",
    )

    parse.add_argument(
        "--copy2_prefix",
        dest="prefix2",
        default="copy2.",
        help="prefix used to identify copy2",
    )

    parse.add_argument(
        "--input",
        dest="input_file",
        help="input verilog file with two copies",
    )

    parse.add_argument(
        "--output",
        dest="output_file",
        help="output verilog file with unnecessary copy2 regs removed",
    )

    parse.add_argument(
        "--design",
        dest="design",
        help="name of the design, xx_miter",
    )

    parse.add_argument(
        "--fanout_signals_file",
        dest="fanout_signals_file",
        help="file containing fanout signals of the secret",
    )

    args = parse.parse_args()
    prefix1 = args.prefix1
    prefix2 = args.prefix2

    copy_re = f"(?P<copy>({prefix1}|{prefix2}))"
    width_re = r"\s*(?P<width>(\[\d+:\d+\]|))\s*"
    name_re = r"(?P<name>[\[\w\.\\\d\]]+)"
    fullname_re = rf"(?P<fullname>{copy_re}{name_re})"
    elems_re = r"\s*(?P<elems>(\[(?P<elems_start>\d+):(?P<elems_end>\d+)\]|))\s*"
    reg_re = re.compile(rf"\s*reg{width_re} {fullname_re}{elems_re}")
    wire_re = re.compile(rf"\s*(wire|input|output|inout){width_re} {fullname_re}")

    fanout_file = args.fanout_signals_file
    with open(fanout_file, "r") as f:
        data = json.load(f)

    design = args.design
    filtered = [b for b in data if b.get("design") == design]
    secret_fanout = []
    for block in filtered:
        fanout_signals = block.get("fanout", [])
        secret_fanout.extend(fanout_signals)

    for i in range(len(secret_fanout)):
        if secret_fanout[i].find('[') >= 0:
            secret_fanout[i] = secret_fanout[i][: secret_fanout[i].find('[')]
        secret_fanout[i] = secret_fanout[i].replace(prefix1, "").replace(prefix2, "")

    smart_duplicate(secret_fanout, args.input_file, args.output_file, prefix1, prefix2)
