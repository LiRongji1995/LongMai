# Strategy spec: 龙脉 V2 (`SF_LONGMAI_V2`)

Status: frozen specification, committed before the first backtest of V2. Changes from V1
(docs/STRATEGY_LONGMAI_V1.md) are listed here; everything else is identical to V1. V1 stays reproducible
(`sf longmai --version 1`).

## Changes (owner decisions, 2026-11-10)

1. **Activity instead of turnover amount alone.** A 100-billion-yuan speculative stock and a
   10-trillion-yuan bank cannot be compared by traded amount. Activity = mean of the whole-market
   percentile ranks of traded amount and turnover rate (equal weight: turnover matters as much as
   amount). Leader condition: activity rank ≤ 30 in the whole market (was amount rank ≤ 30); inside a
   mainline, strong members are ranked by board height, then activity (was height, then amount).
2. **No veto for the 3-session abnormal-volatility rule.** It is an announcement-level event; the
   owner judges it not to matter in practice. Severe abnormal volatility, the post-severe window, the
   distance to the 10-session severe trigger and Lhasa retail net buying remain vetoes.

## Unchanged

Market gate, mainline definition, entry (gap > 0, not at limit-up), exits, slots, cost, evaluation and
forward-test criteria (40 trading days from 2026-11-11; average return per closed trade after 0.15% cost
above zero and realized max drawdown below 15% of capital).

## Post-list check (informational, not part of the decision)

After the list is built, every listed stock is checked for name links with today's strong stocks
(limit-up or +5%): shared character, two-character word, homophone, or code pattern. Links are shown
for human judgement; they do not change groups or ranks.
