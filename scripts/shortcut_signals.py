"""Inject shortcut signals and equivalence/eq-init predicates into a two-copy
miter Verilog design to assist IC3-based formal verification.

The script rewrites a Verilog netlist by:
  1. Parsing register and wire declarations from the miter.
  2. Creating shortcut (inequality) signals that track whether corresponding
     registers in the two copies differ.
  3. Optionally generating equivalence predicates or eq-init predicates that
     are wired into the design as additional outputs or assumptions.
"""

import itertools
import re
import textwrap
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
import json


RegId = str
WireId = str
CopyId = str


@dataclass
class RegCopy:
    id: RegId
    copy_id: CopyId
    full_name: str
    width: str = ""
    n_elements: int | None = None


@dataclass
class WireCopy:
    id: WireId
    copy_id: CopyId
    full_name: str
    width: str = ""


@dataclass
class ShortcutSignalsConfig:
    """Settings for shortcut signals."""

    parse_sct_reg: Callable  # [[str], RegCopy | None]
    prefix1: CopyId
    prefix2: CopyId
    shortcut_prefix: str
    assume_violate_sig: str = "assume_violate"
    prepend_shortcut_regs: bool = False
    wire_only: bool = False
    predicate_only: bool = False
    enable_eqinit: bool = False
    fanout_file: str = None
    design: str = None


@dataclass
class ImplicationSignalsConfig:
    """Settings for implication signals."""
    parse_wire: Callable  # [[str], RegCopy | None]
    prefix1: CopyId


def add_eqinit_predicate(filein, fileout, *, cfg: ShortcutSignalsConfig):

    commentp = re.compile(r"\(\*.*\*\)")
    comments = re.compile(r"/\*.*\*/")
    comment = re.compile(rf"({commentp.pattern}|{comments.pattern})")
    skip = re.compile(rf"(\s|{comment.pattern})*")
    skipline = re.compile(rf"^{skip.pattern}$")

    decl = re.compile(r"\s*(module|wire|reg|input|output|inout).*")
    decl_reg = re.compile(r"\s*reg(?:\s+\[[^]]+\])?\s[^\[]*;")

    assign = re.compile(
        r"(?P<lhs>\s*(assign )?(?P<var>[\[\w\.\\\d\]]+)\s*(\[[0-9:\s]*\])?\s*(=|<=))"
        rf"\s*(?P<rhs>.+);(?P<comments>{skip.pattern})"
    )
    endmodule = re.compile(r"\s*endmodule")

    with open(filein, "r") as fin, open(fileout, "w") as fout:
        fout.write("/* Added shortcut signals via shortcut_signals.py */\n")
        lines = iter(fin)

        neqinit_regs: dict[RegId, RegCopy] = {}

        # --- Parse module header ---
        module_started = False
        for l in lines:

            if match := decl.match(l):
                assert match.group(1) == "module", "module declaration must be first"
                module_started = True
                if cfg.predicate_only:
                    if l.find("(dummy_o") < 0:
                        l = l.replace("(", "(dummy_o, ")

            fout.write(l)

            if module_started and ";" in l:
                break

        # --- Collect declarations and identify multi-bit registers ---
        decls = []
        for l in lines:
            if skipline.match(l):
                decls.append(l)
                continue

            if not decl.match(l):
                lines = itertools.chain([l], lines)
                break

            if decl_reg.match(l) and (reg := cfg.parse_sct_reg(l)):
                if len(reg.width) > 0:
                    temp_name = reg.full_name.replace('\\', '')
                    neqinit_regs[reg.full_name] = f"{cfg.shortcut_prefix}neqinit.{temp_name}"

            decls.append(l)

        # --- Create shortcut declarations and wire/reg assignments ---
        neq_registers: dict[str, str] = {}  # copy name -> neq register
        shortcuts: dict[str, str] = {}  # copy name -> shortcut
        shortcut_decls = []
        shortcut_assigns = []
        sematics = []
        for id, signal in neqinit_regs.items():
            if cfg.wire_only:
                signal_wire = f"{signal}_wire"
                shortcut_decls.append(f"  wire {signal_wire} ;\n")
                shortcut_assigns.append(
                        f"  assign {signal_wire} = !{cfg.assume_violate_sig} && {id} != 0 ;\n"
                    )
                neq_registers[id] = signal_wire
            else:
                signal_wire = signal
                neq_registers[id] = signal
                shortcut_decls.append(f"  reg {signal} = 0 ;\n")

        if cfg.prepend_shortcut_regs:
            decls = shortcut_decls + decls + shortcut_assigns
        else:
            decls = decls + shortcut_decls + shortcut_assigns
        fout.write("".join(decls))

        # --- Process assignments and substitute shortcut signals ---
        reg_assigns: dict[str, str] = {}
        dummy_o_exists = False
        for l in lines:
            if l.find("assign dummy_o = ") >= 0:
                dummy_o_exists = True
                l = l.replace("assign dummy_o = ", f"assign dummy_o = semantics_enforce_z && ")
            if endmodule.match(l):
                lines = itertools.chain([l], lines)
                break
            if match := assign.match(l):
                lhs = match.group("lhs")
                var = match.group("var")
                rhs = " " + match.group("rhs") + " "
                comments = match.group("comments")
                for copy, shortcut in shortcuts.items():
                    updated = rhs.replace(f" {copy} ", f" {shortcut} ")
                    if rhs != updated:
                        rhs = updated
                if "<=" in lhs:  # var is a register
                    assert (
                        var not in reg_assigns
                    ), f"not implemented: handle repeated register assignment (of {var})"
                    reg_assigns[var] = rhs
                    l = f"{lhs} {rhs} ;{comments}"
            fout.write(l)

        # --- Set inequality registers and collect semantic information ---
        sematics = []
        for id, r in neqinit_regs.items():
            orig_assign = reg_assigns[id]
            neq_signal = neq_registers[id]

            if not cfg.wire_only:
                fout.write(
                    textwrap.indent(
                        textwrap.dedent(f"""
                            always @(posedge in_clk) begin
                                {neq_signal} <= !{cfg.assume_violate_sig} && {orig_assign} != 0 ;
                            end
                        """).removeprefix("\n"),
                        "  ",
                    )
                )
            sematics.append(f"( {neq_signal} == ( {id} != 0 ))")

        if cfg.predicate_only:
            fout.write("wire semantics_enforce_z;\n")
            fout.write(f"assign semantics_enforce_z = {' && '.join(sematics)} ;\n")
            if not dummy_o_exists:
                fout.write("output dummy_o;\n")
                fout.write("assign dummy_o = semantics_enforce_z ;\n")
        for l in lines:
            fout.write(l)

def add_equiv_predicate(filein, fileout, *, cfg: ShortcutSignalsConfig):
    assert (not ((cfg.fanout_file == None and cfg.design != None) or (cfg.fanout_file != None and cfg.design == None)))
    secret_fanout = set()
    if cfg.fanout_file:
        fanout_file = cfg.fanout_file
        with open(fanout_file, "r") as f:
            data = json.load(f)

        design = parse.parse_args().design
        filtered = [b for b in data if b.get("design") == design]
        secret_fanout = []
        for block in filtered:
            fanout_signals = block.get("fanout", [])
            secret_fanout.extend(fanout_signals)

        secret_fanout = set([signal.replace(cfg.prefix1[1:], "".replace(cfg.prefix2[1:], "")) for signal in secret_fanout])


    commentp = re.compile(r"\(\*.*\*\)")
    comments = re.compile(r"/\*.*\*/")
    comment = re.compile(rf"({commentp.pattern}|{comments.pattern})")
    skip = re.compile(rf"(\s|{comment.pattern})*")
    skipline = re.compile(rf"^{skip.pattern}$")

    decl = re.compile(r"\s*(module|wire|reg|input|output|inout).*")
    decl_reg = re.compile(r"\s*reg .*")

    assign = re.compile(
        r"(?P<lhs>\s*(assign )?(?P<var>[\[\w\.\\\d\]]+)\s*(\[[0-9:\s]*\])?\s*(=|<=))"
        rf"\s*(?P<rhs>.+);(?P<comments>{skip.pattern})"
    )
    endmodule = re.compile(r"\s*endmodule")

    with open(filein, "r") as fin, open(fileout, "w") as fout:
        fout.write("/* Added shortcut signals via shortcut_signals.py */\n")
        lines = iter(fin)

        orig_regs: dict[RegId, RegCopy] = {}
        copy_regs: dict[RegId, list[RegCopy]] = defaultdict(list)

        # --- Parse module header ---
        module_started = False
        for l in lines:

            if match := decl.match(l):
                assert match.group(1) == "module", "module declaration must be first"
                module_started = True
                if cfg.predicate_only:
                    l = l.replace("(", "(dummy_o, property_o, ")
                else:
                    l = l.replace("(", "(property_o, ")
            fout.write(l)

            if module_started and ";" in l:
                break

        # --- Collect declarations and classify registers by copy ---
        decls = []
        for l in lines:
            if skipline.match(l):
                decls.append(l)
                continue

            if not decl.match(l):
                lines = itertools.chain([l], lines)
                break

            if decl_reg.match(l) and (reg := cfg.parse_sct_reg(l)):
                if cfg.fanout_file and reg.id not in secret_fanout:
                    continue
                if reg.copy_id == cfg.prefix1:
                    orig_regs[reg.id] = reg
                else:
                    copy_regs[reg.id].append(reg)

            decls.append(l)

        # --- Create shortcut signals and inequality registers ---
        neq_registers: dict[str, str] = {}  # copy name -> neq register
        shortcuts: dict[str, str] = {}  # copy name -> shortcut
        shortcut_decls = []
        shortcut_assigns = []

        for id, r in orig_regs.items():
            for rc in copy_regs[id]:
                copy_id = re.sub(r"(\\|\.)", "", rc.copy_id)
                signal = f"{cfg.shortcut_prefix}neq_{id}_{copy_id}"
                if cfg.wire_only:
                    signal_wire = f"{signal}_wire"
                    shortcut_decls.append(f"  wire {signal_wire} ;\n")
                    shortcut_assigns.append(
                        f"  assign {signal_wire} = !{cfg.assume_violate_sig} && {r.full_name} != {rc.full_name} ;\n"
                    )
                    neq_registers[rc.full_name] = signal_wire
                else:
                    signal_wire = signal
                    neq_registers[rc.full_name] = signal
                    shortcut_decls.append(f"  reg {signal} = 0 ;\n")
                shortcut = f"{cfg.shortcut_prefix}{id}.{copy_id}"
                shortcutc = f"{cfg.shortcut_prefix}{copy_id}.{id}"
                shortcuts[r.full_name] = shortcut
                shortcuts[rc.full_name] = shortcutc
                width = f" {rc.width}" if rc.width else ""
                shortcut_decls.append(f"  wire{width} {shortcut} ;\n")
                shortcut_decls.append(f"  wire{width} {shortcutc} ;\n")
                if cfg.predicate_only:
                    shortcut_assigns.append(
                        f"  assign {shortcut} = {r.full_name} ;\n"
                        f"  assign {shortcutc} = {rc.full_name} ;\n"
                    )
                else:
                    shortcut_assigns.append(
                        f"  assign {shortcut} = {signal_wire} ? {r.full_name} : ( {r.full_name} & {rc.full_name} ) ;\n"
                        f"  assign {shortcutc} = {signal_wire} ? {rc.full_name} : ( {r.full_name} & {rc.full_name} ) ;\n"
                    )

        if cfg.prepend_shortcut_regs:
            decls = shortcut_decls + decls + shortcut_assigns
        else:
            decls = decls + shortcut_decls + shortcut_assigns
        fout.write("".join(decls))

        # --- Process assignments and substitute shortcut signals ---
        reg_assigns: dict[str, str] = {}
        for l in lines:
            if l.find("assert(") >= 0:
                match = re.search(r'\bassert\s*\(\s*([^)]+?)\s*\)', l)
                property = match.group(1)
                fout.write("output property_o;\n")
                fout.write(f"assign property_o = ~( {property} ) ;\n")
            if endmodule.match(l):
                lines = itertools.chain([l], lines)
                break
            if match := assign.match(l):
                lhs = match.group("lhs")
                var = match.group("var")
                rhs = " " + match.group("rhs") + " "
                comments = match.group("comments")
                for copy, shortcut in shortcuts.items():
                    updated = rhs.replace(f" {copy} ", f" {shortcut} ")
                    if rhs != updated:
                        rhs = updated
                if "<=" in lhs:  # var is a register
                    assert (
                        var not in reg_assigns
                    ), f"not implemented: handle repeated register assignment (of {var}) in line {l}"
                    reg_assigns[var] = rhs
                    l = f"{lhs} {rhs} ;{comments}"
            fout.write(l)

        # --- Set inequality registers and collect semantic information ---
        sematics = []
        for id, r in orig_regs.items():
            for rc in copy_regs[id]:
                try:
                    orig_assign = reg_assigns[r.full_name]
                except KeyError:
                    print(reg_assigns.items())
                copy_assign = reg_assigns[rc.full_name]
                neq_signal = neq_registers[rc.full_name]
                # FIXME, track each copy's clock
                if not cfg.wire_only:
                    fout.write(
                        textwrap.indent(
                            textwrap.dedent(f"""
                                always @(posedge in_clk) begin
                                    {neq_signal} <= !{cfg.assume_violate_sig} && {orig_assign} != {copy_assign} ;
                                end
                            """).removeprefix("\n"),
                            "  ",
                        )
                    )
                sematics.append(f"( ! {neq_signal} || ( {r.full_name} == {rc.full_name} ) )")

        if cfg.predicate_only:
            fout.write("wire semantics_enforce;\n")
            fout.write(f"assign semantics_enforce = {' && '.join(sematics)} ;\n")
            fout.write("output dummy_o;\n")
            fout.write("assign dummy_o = semantics_enforce ;\n")
        for l in lines:
            fout.write(l)


def extract_operands(rhs_expression):
    """
    Extracts operands from a Verilog RHS expression in an assign statement.
    """
    # Define a regex pattern for matching variables and bit-selects
    variable_re = r"\\?[a-zA-Z_]\w*(?:\.\w+)?"
    bitselect_re = r"(?:\[\d+:\d+\]|\[\d+\])?"
    operand_pattern = rf"{variable_re}\s*{bitselect_re}"

    # Define a regex pattern for matching constants
    constant_pattern = r"\d+\'[b|h][0-9a-fA-F]+"

    rhs_expression = re.sub(constant_pattern, "", rhs_expression)

    # Find all matches
    matches = re.findall(operand_pattern, rhs_expression)

    # Return a list of unique operands and remove spaces
    matches = [match.strip() for match in list(sorted(set(matches)))]
    return matches


def add_implication_signals(filein, fileout, *, cfg: ImplicationSignalsConfig):

    commentp = re.compile(r"\(\*.*\*\)")
    comments = re.compile(r"/\*.*\*/")
    comment = re.compile(rf"({commentp.pattern}|{comments.pattern})")
    skip = re.compile(rf"(\s|{comment.pattern})*")
    skipline = re.compile(rf"^{skip.pattern}$")

    decl = re.compile(r"\s*(module|wire|reg|input|output|inout).*")
    decl_wire = re.compile(r"\s*(wire|input|output|inout) .*")

    assign = re.compile(
        r"(?P<lhs>\s*(assign )?(?P<var>[\w\.\\]+)\s*(\[[0-9:\s]*\])?\s*(=|<=))"
        rf"\s*(?P<rhs>.+);(?P<comments>{skip.pattern})"
    )

    with open(filein, "r") as fin, open(fileout, "w") as fout:
        fout.write("/* Added Implication signals via shortcut_signals.py */\n")
        lines = iter(fin)

        orig_wires: dict[WireId, WireCopy] = {}
        copy_wires: dict[WireId, list[WireCopy]] = defaultdict(list)

        # --- Parse module header ---
        module_started = False
        for l in lines:
            fout.write(l)

            if match := decl.match(l):
                assert match.group(1) == "module", "module declaration must be first"
                module_started = True

            if module_started and ";" in l:
                break

        # --- Collect wire declarations and classify by copy ---
        decls = []
        for l in lines:
            if skipline.match(l):
                decls.append(l)
                continue

            if not decl.match(l):
                lines = itertools.chain([l], lines)
                break

            if decl_wire.match(l) and (wire := cfg.parse_wire(l)):
                if wire.copy_id == cfg.prefix1:
                    orig_wires[wire.id] = wire
                else:
                    copy_wires[wire.id].append(wire)

            decls.append(l)
        fout.write("".join(decls))

        # --- Create equality signals and build support graph ---
        eq_signals: dict[str, str] = {}  # wire name (orig) -> eq signal
        supports: dict[str, list[str]] = defaultdict(
            list
        )  # wire name -> support signals

        src_comment = re.compile(
            r'\(\*\s*src\s*=\s*"(?P<path>[^"]+:\d+\.\d+-\d+\.\d+)"\s*\*\)'
        )
        lines = list(lines)
        for l in lines:
            if match := assign.match(l):
                lhs = match.group("lhs")
                rhs = " " + match.group("rhs") + " "
                # considering only wire in lhs
                if "<=" not in lhs:
                    operands = extract_operands(re.sub(src_comment.pattern, "", rhs))
                    lhs = re.sub(r"(assign\s+|\s*|=|<=)", "", lhs)
                    assert (
                        lhs not in supports
                    ), f"not implemented: handle repeated assignment (of {lhs})"
                    supports[lhs] = operands

        # --- Emit equality implication instrumentation ---
        eq_id = 1
        impl_id = 0
        fout.write(f"\n  /* Equality Implication Instrumentation Starts */\n\n")
        fout.write(f"  wire __IMPL{impl_id}__ ;\n")
        fout.write(f"  assign __IMPL{impl_id}__ = 1 ;\n")
        impl_id += 1
        for id, w in orig_wires.items():
            for wc in copy_wires[id]:
                stack = []
                stack.append(w.full_name)
                stack.append(wc.full_name)

                while stack:  # post order traversal
                    wc_name, w_name = stack[-1], stack[-2]

                    if w_name in eq_signals.keys():  # visited or redundant
                        stack = stack[:-2]
                        continue

                    if w_name not in supports.keys():  # leaf case
                        assert w_name not in eq_signals.keys()
                        if w_name == wc_name:
                            eq_signals[w_name] = "1"
                        else:
                            eq_signals[w_name] = f"__E{eq_id}__"
                            fout.write(f"  wire {eq_signals[w_name]} ;\n")
                            fout.write(
                                f"  assign {eq_signals[w_name]} = {w_name} == {wc_name} ;\n"
                            )
                        stack = stack[:-2]  # pop out
                        eq_id += 1

                    else:  # non-leaf case
                        ready = True
                        # check if all the supports are processed
                        for i in range(len(supports[w_name])):
                            s, sc = supports[w_name][i], supports[wc_name][i]
                            if s not in eq_signals.keys():
                                stack.append(s)
                                stack.append(sc)
                                ready = False

                        if ready:
                            assert w_name not in eq_signals.keys()
                            eq_signals[w_name] = f"__E{eq_id}__"
                            fout.write(f"  wire {eq_signals[w_name]} ;\n")
                            fout.write(
                                f"  assign {eq_signals[w_name]} = {w_name} == {wc_name} ;\n"
                            )
                            rhs = eq_signals[supports[w_name][0]]
                            for s in supports[w_name][1:]:
                                rhs += f" && {eq_signals[s]}"
                            fout.write(f"  wire __IMPL{impl_id}__ ;\n")
                            fout.write(
                                f"  assign __IMPL{impl_id}__ = (!({rhs}) || {eq_signals[w_name]}) && __IMPL{impl_id-1}__ ;\n"
                            )
                            stack = stack[:-2]  # pop out
                            eq_id += 1
                            impl_id += 1

        fout.write(f"\n  /* Equality Implication Instrumentation Ends */\n\n")

        # --- Wire implication result into the assumption signal ---
        # FIXME considering general assumption name
        # For now, assume it to be assume_1_violate_in
        assump_name = "assume_1_violate_in"
        for l in lines:
            assign_assump = re.compile(
                rf"(?P<lhs>\s*assign\s+{assump_name}\s*(=))"
                rf"\s*(?P<rhs>.+);(?P<comments>{skip.pattern})"
            )
            if match := assign_assump.match(l):
                rhs = match.group("rhs")
                l = f"  assign {assump_name} = !__IMPL{impl_id-1}__ || {rhs} ;\n"
            fout.write(l)


if __name__ == "__main__":
    import argparse
    import os
    import sys

    parse = argparse.ArgumentParser()
    parse.add_argument(
        "--input", dest="input_path", required=True, help="input_log_path"
    )
    parse.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help="output path",
    )
    parse.add_argument("--top", dest="top", required=True, help="top module")

    parse.add_argument(
        "--copy1_prefix",
        dest="prefix1",
        default="\\copy1.",
        help="prefix used to identify copy1",
    )

    parse.add_argument(
        "--copy2_prefix",
        dest="prefix2",
        default="\\copy2.",
        help="prefix used to identify copy2",
    )

    parse.add_argument(
        "--shortcut_prefix",
        dest="prefix_sc",
        default="\\shortcut.",
        help="prefix used for new shortcut signals",
    )

    # Add short options: -s (shortcut only) and -se (shortcut and implication)
    parse.add_argument(
        "-s",
        "--shortcut",
        dest="enable_shortcut",
        action="store_true",
        default=False,
        help=f"Toggle adding shortcut signals [default=False]",
    )

    parse.add_argument(
        "-w",
        "--wireonly",
        dest="wire_only",
        action="store_true",
        default=False,
        help=f"Use only shortcut wires, not registers [default=False]",
    )

    parse.add_argument(
        "-i",
        "--implication",
        dest="enable_implication",
        action="store_true",
        default=False,
        help="Toggle adding implication signals [default=False]",
    )

    parse.add_argument(
        "--assume_violate_sig",
        dest="assume_violate_sig",
        default="assume_violate",
        help="name of the signal used to denote assume violation",
    )

    parse.add_argument(
        "--shortcut_regs",
        dest="sct_regs_file",
        default=None,
        help="a file of regular expressions specifying which registers are shortcut registers"
    )

    parse.add_argument(
        "-k",
        "--predicate_only",
        dest="predicate_only",
        action="store_true",
        default=False,
        help="add expressions to the property to enforce the semantics of predicate variables, do not use shortcut logic"
    )

    parse.add_argument(
        "-z",
        "--enable_eqinit",
        dest="enable_eqinit",
        action="store_true",
        default=False,
        help="enable eqinit predicate for every register that has more than 1 bit"
    )

    parse.add_argument(
        "--fanout_file",
        dest="fanout_file",
        default=None,
        help="a file of signals inside the secret's fanout"
    )

    parse.add_argument(
        "--design",
        dest="design",
        default=None,
        help="the design name, xxx_miter"
    )

    args = parse.parse_args()
    if not args.input_path.endswith(".v") and not args.input_path.endswith(".sv"):
        print("Invalid input file, must be a .v or .sv file")
        sys.exit(1)
    if not args.output_path.endswith(".v") and not args.output_path.endswith(".sv"):
        print("Invalid input file, must be a .v or .sv file")
        sys.exit(1)

    prefix1 = re.escape(str(args.prefix1))
    prefix2 = re.escape(str(args.prefix2))
    if args.sct_regs_file is not None:
        with open(args.sct_regs_file, 'r') as f:
            sct_regs = re.compile("|".join(s.strip() for s in f.readlines()))
    else:
        sct_regs = re.compile(r".*")

    copy_re = f"(?P<copy>({prefix1}|{prefix2}))"
    width_re = r"\s*(?P<width>(\[\d+:\d+\]|))\s*"
    name_re = r"(?P<name>[\[\w\.\\\d\]]+)"
    fullname_re = rf"(?P<fullname>{copy_re}{name_re})"
    elems_re = r"\s*(?P<elems>(\[(?P<elems_start>\d+):(?P<elems_end>\d+)\]|))\s*"
    reg_re = re.compile(rf"\s*reg{width_re} {fullname_re}{elems_re}")
    wire_re = re.compile(rf"\s*(wire|input|output|inout){width_re} {fullname_re}")

    def parse_sct_reg(reg: str):  # -> RegCopy | None:
        match = reg_re.match(reg)
        if match is None:
            return None
        try:
            name = match.group("name")
            if not sct_regs.fullmatch(name):
                return None
            if match.group("elems_end") is not None and match.group("elems_start") is not None:
                n_elems = abs(int(match.group("elems_end")) - int(match.group("elems_start"))) + 1
            else:
                n_elems = None
            return RegCopy(
                name,
                copy_id=match.group("copy"),
                width=match.group("width"),
                full_name=match.group("fullname"),
                n_elements=n_elems
            )
        except KeyError:
            return None

    def parse_wire(wire: str):  # -> WireCopy | None:
        match = wire_re.match(wire)
        if match is None:
            return None
        try:
            return WireCopy(
                match.group("name"),
                copy_id=match.group("copy"),
                width=match.group("width"),
                full_name=match.group("fullname"),
            )
        except KeyError:
            return None

    assert args.enable_shortcut or args.enable_implication or args.enable_eqinit

    # FIXME ugly fix for handling multiple modification of one file
    if args.enable_implication:
        cfg = ImplicationSignalsConfig(parse_wire=parse_wire, prefix1=args.prefix1)
        temp_path = args.output_path
        if args.enable_shortcut or args.enable_eqinit:
            temp_path = "temp.sv"
        add_implication_signals(args.input_path, temp_path, cfg=cfg)

    if args.enable_shortcut:
        cfg = ShortcutSignalsConfig(
            parse_sct_reg=parse_sct_reg,
            prefix1=args.prefix1,
            prefix2=args.prefix2,
            shortcut_prefix=str(args.prefix_sc),
            assume_violate_sig=args.assume_violate_sig,
            wire_only=args.wire_only,
            predicate_only=args.predicate_only,
            enable_eqinit=args.enable_eqinit,
            fanout_file=args.fanout_file,
            design=args.design
        )
        temp_path = args.input_path
        if args.enable_implication:
            temp_path = "temp.sv"
        if args.enable_eqinit:
            add_equiv_predicate(temp_path, "temp.sv", cfg=cfg)
        else:
            add_equiv_predicate(temp_path, args.output_path, cfg=cfg)

    if args.enable_eqinit:
        cfg = ShortcutSignalsConfig(
            parse_sct_reg=parse_sct_reg,
            prefix1=args.prefix1,
            shortcut_prefix=str(args.prefix_sc),
            assume_violate_sig=args.assume_violate_sig,
            wire_only=args.wire_only,
            predicate_only=args.predicate_only,
            enable_eqinit=args.enable_eqinit
        )
        temp_path = args.input_path
        if args.enable_implication or args.enable_shortcut:
            temp_path = "temp.sv"
        add_eqinit_predicate(temp_path, args.output_path, cfg=cfg)

    if temp_path == "temp.sv":
        os.remove(temp_path)
