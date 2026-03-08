"""Expand predicate-based invariants into bit-level invariants.

Reads an IC3 solver log (from ABC/PDR or rIC3), identifies predicate
variables (shortcut.neq_* and shortcut.neqinit.*), and expands each
predicate reference into the underlying bit-level comparisons.
Optionally generates symmetric (copy1 <-> copy2) invariant clauses.

Usage:
    python3 scripts/expand_inv.py --log LOG --map MAP --output OUT [--symmetry]
"""

import argparse
import re


def analyze_map_file(map_file_path):
    """Parse a .map file to extract latch mappings and predicate relationships.

    Returns:
        latch_map: dict mapping signal_name -> list of bit indices
        expanded_predicates: dict mapping predicate_name -> list of
            'signal_name[bit]' strings the predicate covers
    """
    latch_map = {}
    equiv_predicate_map = {}
    eqinit_predicate_map = {}

    with open(map_file_path, "r") as file:
        for line in file:
            if line.find("Invariant Clauses") >= 0:
                continue
            if line.find("PDR Log") >= 0:
                break
            if len(line.strip()) == 0:
                break
            parts = line.strip().split()
            if len(parts) >= 4:
                signal_type = parts[0]
                latch_number = int(parts[1])
                bit_index = int(parts[2])
                signal_name = parts[3]

                if signal_type in {"latch", "invlatch"}:
                    latch_map.setdefault(signal_name, []).append(bit_index)

                    # Classify predicate vs. data signals.
                    if signal_name.startswith("shortcut.neq"):
                        equiv_predicate_map.setdefault(signal_name, set())
                    elif signal_name.startswith("shortcut.neqinit"):
                        eqinit_predicate_map.setdefault(signal_name, set())
                    elif signal_name.startswith(("copy1", "copy2")):
                        word_name = signal_name[6:]
                        equiv_predicate_key = f"shortcut.neq_{word_name}_copy2"
                        equiv_predicate_map.setdefault(equiv_predicate_key, set()).add(signal_name)
                        eqinit_predicate_key = f"shortcut.neqinit.{signal_name}"
                        eqinit_predicate_map.setdefault(eqinit_predicate_key, set()).add(signal_name)

    # Expand each predicate to the full list of 'signal[bit]' entries.
    expanded_predicates = {}
    for predicate, signals in equiv_predicate_map.items():
        expanded_predicates[predicate] = []
        for signal in signals:
            if signal in latch_map:
                for bit in sorted(latch_map[signal]):
                    expanded_predicates[predicate].append(f"{signal}[{bit}]")
    for predicate, signals in eqinit_predicate_map.items():
        expanded_predicates[predicate] = []
        for signal in signals:
            if signal in latch_map:
                for bit in sorted(latch_map[signal]):
                    expanded_predicates[predicate].append(f"{signal}[{bit}]")

    return latch_map, expanded_predicates


def expand_inv(latch_map, predicate_map, log_path, output_path, symmetry):
    """Read invariant clauses from a solver log and expand predicate variables.

    Each predicate variable reference (e.g. shortcut.neq_X_copy2[0]) is
    replaced by the corresponding bit-level equality/inequality literals,
    producing purely bit-level invariant clauses.
    """
    with open(log_path, "r") as file_r:
        invariants = []
        final_invariants = []

        # Parse invariant clauses from the solver log.
        for line in file_r:
            if log_path.find("pdr_") >= 0:
                if line.find("Invariant Clauses") >= 0:
                    continue
                if line.find("PDR Log") >= 0:
                    break
                line = line.strip()
                if len(line) == 0:
                    break
                invariants.append(line)
            elif log_path.find("ric3_") >= 0:
                if line.find("inducive invariant:") >= 0:
                    line = line.replace(',', ' && ')
                    line = line.replace('inducive invariant:', '')
                    line = line.replace('-', '!')
                    line = line.strip()
                    line = line[1:-1]
                    line = "!(" + line + ")"
                    invariants.append(line)

        # Iteratively expand predicate references into bit-level literals.
        while len(invariants) > 0:
            inv = invariants.pop(0)

            # Ensure the assume-violation guard is present.
            if inv.find('!assume_1_violate') < 0:
                inv = inv.replace('(', '(!assume_1_violate[0] && ')

            # Negated predicate variables are not supported.
            error_match = re.search(r'!shortcut\.neq_[^ )]*', inv)
            if error_match:
                raise ValueError(f"Invalid usage of predicate variable: {error_match.group()}")

            match_equiv = re.search(r'shortcut\.neq_[^ )]+', inv)
            match_eqinit = re.search(r'shortcut\.neqinit\.[^ )]+', inv)

            if not match_equiv and not match_eqinit:
                # No predicate references remain — this clause is fully expanded.
                final_invariants.append(inv)
            elif match_equiv:
                # Expand equivalence predicate: neq means copies differ on some bit.
                equiv_pred = match_equiv.group(0)
                for signal in predicate_map[equiv_pred.replace('[0]', '')]:
                    if not signal.startswith('copy1'):
                        continue
                    temp_inv = inv.replace(equiv_pred, f"{signal} && !{signal.replace('copy1', 'copy2')}")
                    invariants.append(temp_inv)
                    temp_inv = inv.replace(equiv_pred, f"!{signal} && {signal.replace('copy1', 'copy2')}")
                    invariants.append(temp_inv)
            elif match_eqinit:
                # Expand eq-init predicate into individual signal references.
                eqinit_pred = match_eqinit.group(0)
                for signal in predicate_map[eqinit_pred.replace('[0]', '')]:
                    temp_inv = inv.replace(eqinit_pred, f"{signal}")
                    invariants.append(temp_inv)

        # Optionally add symmetric clauses (swap copy1 <-> copy2).
        if symmetry:
            symmetric_invariants = []
            for inv in final_invariants:
                sym_inv = inv.replace("copy1", "__TEMPTEMP__")
                sym_inv = sym_inv.replace("copy2", "copy1")
                sym_inv = sym_inv.replace("__TEMPTEMP__", "copy2")
                symmetric_invariants.append(sym_inv)
            final_invariants += symmetric_invariants

        with open(output_path, 'w') as file_w:
            file_w.write('\n'.join(final_invariants))


if __name__ == "__main__":
    parse = argparse.ArgumentParser()
    parse.add_argument("--log", dest="log_path", required=True, help="input .log path")
    parse.add_argument("--map", dest="map_path", required=True, help="input .map path")
    parse.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help="output inv path",
    )
    parse.add_argument(
        "--symmetry",
        dest="symmetry",
        action="store_true",
        default=False,
        help="generate symmetric clauses in addition to the original clauses",
    )

    args = parse.parse_args()
    latch_map, predicate_map = analyze_map_file(args.map_path)
    expand_inv(latch_map, predicate_map, args.log_path, args.output_path, args.symmetry)
