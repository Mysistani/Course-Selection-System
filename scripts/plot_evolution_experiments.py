"""Optional matplotlib figure from actual A/B/C JSON reports; no GA evaluations."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    matrix = json.loads(args.matrix.read_text(encoding='utf-8'))
    if args.output.resolve() in {args.matrix.resolve()} | {Path(row['report_path']).resolve() for row in matrix['runs']}:
        parser.error('output must differ from input reports')
    colors = {'A': '#2563eb', 'B': '#d97706', 'C': '#059669'}
    seeds = sorted({r['seed'] for r in matrix['runs']})
    styles = dict(zip(seeds, ['-', '--', ':', '-.'] * (len(seeds) // 4 + 1)))
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8), sharey=True, layout='constrained')
    for row in matrix['runs']:
        report = json.loads(Path(row['report_path']).read_text(encoding='utf-8'))
        g = report['generations']
        y = [item['best_hard_penalty'] for item in g]
        xs = ([item['generation_index'] for item in g],
              [item['cumulative_fitness_evaluation_count'] for item in g],
              [item['cumulative_elapsed_seconds'] - g[0]['cumulative_elapsed_seconds'] for item in g])
        for ax, x in zip(axes, xs):
            ax.step(x, y, where='post', color=colors[row['name'][0]], linestyle=styles[row['seed']], linewidth=1.7, alpha=.85)
    for ax, xlabel in zip(axes, ('Completed generation', 'Cumulative fitness calls (incl. initialization)', 'Evolution elapsed seconds')):
        ax.set_xlabel(xlabel)
        ax.grid(alpha=.2)
    axes[0].set_ylabel('Best population HardPenalty (lower is better)')
    legend = [Line2D([0], [0], color=color, label=label) for label, color in colors.items()]
    legend += [Line2D([0], [0], color='gray', linestyle=style, label=f'Seed {seed}') for seed, style in styles.items()]
    axes[0].legend(handles=legend, fontsize=8)
    fig.suptitle('Phase 6: measured A/B/C trajectories by generation, evaluation cost and elapsed time', fontsize=12)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    main()
