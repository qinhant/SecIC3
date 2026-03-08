"""Generate a bar chart of per-design verification speedups.

Reads hardcoded benchmark results and saves a PDF plot to output/speedup.pdf.
"""

import matplotlib.pyplot as plt

plt.rcParams.update({'font.size': 10})

labels = ['Multiplier', 'Modexp', 'GCD', 'FP_ADD', 'SecEnclave', 'Cache', 'Sodor', 'Rocket']
values = [27.0, 1.8, 8.9, 5.6, 49.3, 32.3, 4.2, 2.3]

fig, ax = plt.subplots(figsize=(8, 3))
bars = ax.bar(labels, values, color='lightblue', edgecolor='lightblue')
ax.bar_label(bars, padding=3)

ax.set_xlabel('Design')
ax.set_ylabel('Speedup')
ax.set_ylim(0, max(values) * 1.12)

plt.tight_layout()
plt.savefig("output/speedup.pdf")
