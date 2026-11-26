"""Draw measured A/B convergence without reading any feasibility-certificate file."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads(args.summary.read_text(encoding='utf-8'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), layout='constrained')
    seeds = sorted({r['seed'] for r in summary['runs']})
    colors = dict(zip(seeds, ['#2563eb', '#d97706', '#059669']))
    for run in summary['runs']:
        report = run if 'generations' in run else json.loads(Path(run['report_path']).read_text(encoding='utf-8'))
        g = report['generations']
        row = 0 if run['dataset'] == 'A' else 1
        xs = ([s['generation_index'] for s in g],
              [s['cumulative_fitness_evaluation_count'] for s in g],
              [s['cumulative_elapsed_seconds'] - g[0]['cumulative_elapsed_seconds'] for s in g])
        for axis, x in zip(axes[row], xs):
            axis.step(x, [s['best_hard_penalty'] for s in g], where='post',
                      label=f'Seed {run["seed"]}', color=colors[run['seed']], linewidth=1.7)
    for i, label in enumerate(('Dataset A (feasibility unverified)', 'Dataset B (certified feasible)')):
        for j, xlabel in enumerate(('Completed generation', 'Cumulative fitness calls', 'Evolution elapsed seconds')):
            axes[i,j].set_xlabel(xlabel)
            axes[i,j].grid(alpha=.2)
            axes[i,j].legend(fontsize=8)
            axes[i,j].set_ylim(bottom=0)
        axes[i,0].set_ylabel(label+'\nBest population HardPenalty')
    for axis in axes[1]:
        axis.axhline(0, color='black', linestyle='--', linewidth=1)
    fig.suptitle('Unmodified Phase 6 engine: same 4000-call ceiling; datasets differ in structure and size')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    main()
