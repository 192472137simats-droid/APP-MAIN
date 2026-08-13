# Evaluation results

Papers evaluated: **2**

## Headline

| Metric | Result | What it measures |
|---|---|---|
| Year accuracy | 100% | Correct publication year extracted |
| Dataset recall | 100% | Expected datasets found |
| Method recall | 100% | Expected method terms present |
| Authors extracted | 50% | Papers with author list recovered |
| Summary faithfulness | 100% | Summaries with no invented numbers |
| Integrity clean | 100% | No pre-dating citations, no contradictory verdicts |

## Grounding effect (the contribution)

Across 10 gaps (5.0 per paper), literature grounding overturned **4** (**40%**) that single-paper analysis would have reported as open research directions.

Mean corpus checked per paper: 40.0 papers. Mean runtime: 51.5s per paper.

## Per paper

| Paper | Year | Datasets | Gaps | Open | Overturned | Faithful | Clean |
|---|---|---|---|---|---|---|---|
| Deep Residual Learning for Image Recogni | OK | 100% | 5 | 5 | 0 | yes | yes |
| Attention Is All You Need | OK | 100% | 5 | 1 | 4 | yes | yes |

## Gap precision (needs your judgement)

`gaps_to_review.csv` lists every gap marked still-open. Read the paper, put `y` or `n` in the `is_real_gap` column, and gap precision is the share of `y`. This is the one number a human has to supply - everything above is computed.
