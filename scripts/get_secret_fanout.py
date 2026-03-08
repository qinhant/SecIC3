"""Extract secret signal fanout information from JasperGold analysis logs.

Parses a JasperGold fanout analysis log file, extracts per-design fanout
blocks (target signal and its transitive fanout cone), and writes the
results to a JSON file.

Usage:
    python3 scripts/get_secret_fanout.py <input_log> [output.json]
"""

import sys, json, re

# Patterns to detect which design is being analyzed.
DESIGN_PATTERNS = [
    re.compile(r"Get fanout.*\b([\w\-]+)\.sv", re.IGNORECASE),
    re.compile(r"Analyzing Verilog file '.*?/([\w\-]+)\.sv'", re.IGNORECASE),
]


def detect_design(line, current):
    """Return the design name if the line matches a design pattern, else current."""
    s = line.strip()
    for pat in DESIGN_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(1)
    return current


def extract_blocks(text):
    """Parse fanout blocks delimited by '====....' / '----....' lines.

    Returns a list of (design, target_signal, fanout_signals) tuples.
    """
    lines = text.splitlines()
    results = []
    in_block = False
    collecting = False
    current_signals = []
    current_target = None
    current_design = None

    for raw in lines:
        s = raw.rstrip("\n")

        current_design = detect_design(s, current_design)

        # Start of a fanout block.
        if s.strip() == "=========================" and not in_block:
            in_block = True
            collecting = False
            current_signals = []
            current_target = None
            continue

        # End of the block — emit results.
        if in_block and s.strip().startswith("-------------------------"):
            if current_signals:
                seen = set()
                fanout = []
                for sig in current_signals:
                    if sig not in seen:
                        seen.add(sig)
                        fanout.append(sig)
                results.append((current_design, current_target, fanout))
            in_block = False
            collecting = False
            current_signals = []
            current_target = None
            continue

        if in_block:
            # Capture the target signal name.
            if s.strip().startswith("Target:"):
                parts = s.split("Target:", 1)[1].strip().split()
                current_target = parts[0] if parts else None

            # Start collecting once we see indented signal lines.
            if not collecting:
                if s.startswith((" ", "\t")) and s.strip():
                    collecting = True

            if collecting:
                if s.startswith((" ", "\t")) and s.strip():
                    token = s.strip().split()[0]
                    if token not in {"Task:", "Target:", "Result", "Type:", "signals"}:
                        current_signals.append(token)

    return results


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "input.txt"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "fanout_all.json"

    with open(in_path, "r", encoding="utf-8") as f:
        text = f.read()

    blocks = extract_blocks(text)

    out = [
        {"design": design or "unknown", "target": tgt, "fanout": fanout}
        for (design, tgt, fanout) in blocks
    ]

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
