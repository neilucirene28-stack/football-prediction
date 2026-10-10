"""Describe a frozen probability distribution without changing its probabilities."""
import math


def distribution_summary(matrix, wdl):
    names = ('home', 'draw', 'away')
    ranking = sorted(range(3), key=lambda i: (-wdl[i], i))
    scores = sorted(((f'{i}-{j}', float(p)) for i, row in enumerate(matrix)
                     for j, p in enumerate(row)), key=lambda item: (-item[1], item[0]))
    top5 = scores[:5]
    mass = math.fsum(p for _, p in top5)
    draw_score = max(((f'{i}-{i}', float(row[i])) for i, row in enumerate(matrix)),
                     key=lambda item: item[1])
    return {
        'score_summary': {
            'top5_probability': mass,
            'outside_top5_probability': max(0.0, 1.0 - mass),
            'top1_probability': scores[0][1],
            'best_draw_score': {'score': draw_score[0], 'prob': draw_score[1]},
        },
        'wdl_summary': {
            'ranking': [{'outcome': names[i], 'prob': float(wdl[i])} for i in ranking],
            'draw_rank': ranking.index(1) + 1,
            'draw_probability': float(wdl[1]),
            'runner_up': names[ranking[1]],
            'runner_up_probability': float(wdl[ranking[1]]),
            'first_second_gap': float(wdl[ranking[0]] - wdl[ranking[1]]),
            'top_two_probability': float(wdl[ranking[0]] + wdl[ranking[1]]),
        },
    }
