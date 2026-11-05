# Backtest

Ratio = monthly paper rate in months 7 to 12 after the cutoff, divided by the rate in the 6 months before it, divided by the growth of all arXiv CS papers between the same two windows. A ratio of 1.0 means the item only kept pace with the field.

## Phrases (S3)

Flagged = the top 50 phrases by score at each cutoff. Baseline = every phrase that passed the filters.

| group | n | grew (>=1.5x) | flat | faded (<0.75x) | median ratio |
|---|---|---|---|---|---|
| top 50, all cutoffs | 200 | 36% | 42% | 22% | 1.23 |
| baseline, all cutoffs | 595 | 27% | 50% | 22% | 1.11 |
| top 50, 2025-01-01 | 50 | 26% | 46% | 28% | 0.95 |
| baseline, 2025-01-01 | 72 | 24% | 47% | 29% | 0.90 |
| top 50, 2025-04-01 | 50 | 44% | 42% | 14% | 1.40 |
| baseline, 2025-04-01 | 132 | 33% | 52% | 14% | 1.23 |
| top 50, 2025-07-01 | 50 | 40% | 46% | 14% | 1.31 |
| baseline, 2025-07-01 | 189 | 31% | 51% | 17% | 1.17 |
| top 50, 2025-10-01 | 50 | 32% | 36% | 32% | 1.07 |
| baseline, 2025-10-01 | 202 | 21% | 50% | 29% | 0.99 |

## Category pairs (S2)

Flagged = pairs that pass the S2 rule. Baseline = every pair with at least 30 papers in the 6 months before the cutoff.

| group | n | grew (>=1.5x) | flat | faded (<0.75x) | median ratio |
|---|---|---|---|---|---|
| flagged, all cutoffs | 65 | 2% | 57% | 42% | 0.83 |
| baseline, all cutoffs | 1222 | 4% | 68% | 28% | 0.90 |
| flagged, 2025-01-01 | 24 | 0% | 46% | 54% | 0.74 |
| baseline, 2025-01-01 | 298 | 3% | 66% | 31% | 0.87 |
| flagged, 2025-04-01 | 20 | 5% | 50% | 45% | 0.82 |
| baseline, 2025-04-01 | 309 | 2% | 63% | 34% | 0.86 |
| flagged, 2025-07-01 | 8 | 0% | 100% | 0% | 1.09 |
| baseline, 2025-07-01 | 300 | 3% | 76% | 21% | 0.94 |
| flagged, 2025-10-01 | 13 | 0% | 62% | 38% | 0.81 |
| baseline, 2025-10-01 | 315 | 6% | 68% | 26% | 0.94 |

Flagged pairs faded at least as often as the baseline. Read the S2 table as a description of the last 6 months, not as a forecast.

## Details

### Top 50 phrases flagged before 2025-01-01

| phrase | before/mo | later/mo | ratio |
|---|---|---|---|
| kolmogorov-arnold networks | 19.8 | 15.8 | 0.63 |
| sparse autoencoders | 9.0 | 21.8 | 1.92 |
| visual generation | 9.2 | 13.5 | 1.17 |
| retrieval-augmented generation | 94.5 | 186.3 | 1.56 |
| visual token | 7.0 | 12.3 | 1.40 |
| social impact | 5.7 | 4.2 | 0.58 |
| resource-constrained edge devices | 9.0 | 14.7 | 1.29 |
| rag systems | 20.8 | 40.5 | 1.54 |
| video generation | 55.2 | 91.3 | 1.31 |
| gaussian splatting | 88.0 | 110.8 | 1.00 |
| mask generation | 7.0 | 5.5 | 0.62 |
| flow matching | 20.5 | 70.5 | 2.73 |
| vision-language models | 136.8 | 290.5 | 1.68 |
| continuous data | 8.3 | 6.3 | 0.60 |
| penetration testing | 5.0 | 5.3 | 0.85 |
| rectified flow | 5.7 | 12.3 | 1.73 |
| multimodal large language models | 87.2 | 163.0 | 1.48 |
| multimodal large language | 87.2 | 187.0 | 1.70 |
| adaptive data | 5.0 | 5.0 | 0.79 |
| diffusion transformers | 12.7 | 23.0 | 1.44 |
| multimodal ai | 6.8 | 12.0 | 1.39 |
| multimodal retrieval | 5.0 | 9.3 | 1.48 |
| frontier models | 6.7 | 28.2 | 3.35 |
| ai framework | 5.7 | 19.5 | 2.73 |
| segmentation framework | 8.7 | 17.0 | 1.56 |
| multi-agent framework | 13.2 | 44.7 | 2.69 |
| evaluating llm | 5.5 | 11.2 | 1.61 |
| attention transformer | 7.8 | 5.3 | 0.54 |
| integrating ai | 6.8 | 7.3 | 0.85 |
| secure data | 6.8 | 5.0 | 0.58 |
| small language model | 7.3 | 7.8 | 0.85 |
| improving detection | 7.7 | 8.0 | 0.83 |
| model merging | 15.2 | 17.0 | 0.89 |
| edge networks | 6.7 | 5.2 | 0.61 |
| vision foundation models | 14.7 | 28.0 | 1.51 |
| legged robots | 10.5 | 10.8 | 0.82 |
| generation systems | 10.0 | 12.3 | 0.98 |
| spatial awareness | 7.8 | 10.2 | 1.03 |
| haptic feedback | 9.0 | 4.7 | 0.41 |
| gaussian splats | 7.5 | 5.2 | 0.55 |
| ai safety | 12.0 | 21.2 | 1.40 |
| high-quality synthetic | 10.5 | 10.8 | 0.82 |
| in-memory computing | 8.3 | 7.2 | 0.68 |
| inverse rendering | 8.0 | 5.0 | 0.50 |
| slide image | 9.3 | 5.3 | 0.45 |
| data retrieval | 9.2 | 8.0 | 0.69 |
| video diffusion models | 18.0 | 20.8 | 0.92 |
| unit tests | 8.7 | 9.7 | 0.88 |
| estimation using | 22.2 | 18.8 | 0.67 |
| foundational model | 9.2 | 9.2 | 0.79 |

### Top 50 phrases flagged before 2025-04-01

| phrase | before/mo | later/mo | ratio |
|---|---|---|---|
| large reasoning models | 7.2 | 44.0 | 4.81 |
| test-time scaling | 9.2 | 50.3 | 4.30 |
| reasoning models | 25.8 | 109.0 | 3.30 |
| process reward models | 5.0 | 8.8 | 1.38 |
| inference-time scaling | 5.3 | 13.5 | 1.98 |
| inference scaling | 6.0 | 3.0 | 0.39 |
| sparse autoencoders | 16.8 | 26.5 | 1.23 |
| inference-time compute | 5.3 | 5.5 | 0.81 |
| test-time compute | 8.2 | 14.5 | 1.39 |
| vision-language models | 182.0 | 334.8 | 1.44 |
| structured reasoning | 9.2 | 37.2 | 3.17 |
| video generation | 77.8 | 113.7 | 1.14 |
| reasoning trajectories | 5.2 | 33.2 | 5.02 |
| agentic ai | 7.3 | 71.0 | 7.58 |
| llm reasoning | 29.7 | 85.0 | 2.24 |
| llm agents | 46.5 | 140.5 | 2.37 |
| token reduction | 6.2 | 10.5 | 1.33 |
| multimodal reasoning | 13.8 | 59.0 | 3.34 |
| video large language models | 6.8 | 8.0 | 0.92 |
| enhanced reasoning | 5.8 | 5.5 | 0.74 |
| reasoning process | 33.2 | 76.5 | 1.81 |
| agentic systems | 5.0 | 42.2 | 6.60 |
| autoregressive modeling | 6.8 | 10.2 | 1.16 |
| process supervision | 5.7 | 5.2 | 0.71 |
| agentic workflow | 5.0 | 10.8 | 1.70 |
| gui agents | 6.0 | 18.0 | 2.35 |
| retrieval-augmented generation | 128.5 | 195.3 | 1.19 |
| similarity analysis | 5.2 | 7.8 | 1.19 |
| enhancing llm | 12.0 | 7.3 | 0.48 |
| visual understanding | 26.3 | 35.2 | 1.05 |
| flow matching | 29.5 | 96.2 | 2.55 |
| chain-of-thought reasoning | 18.8 | 39.5 | 1.64 |
| code translation | 6.3 | 5.7 | 0.70 |
| visual token | 9.8 | 16.8 | 1.34 |
| token compression | 5.7 | 12.2 | 1.68 |
| action generation | 5.3 | 18.3 | 2.69 |
| kv cache | 28.5 | 52.5 | 1.44 |
| diffusion transformers | 19.7 | 32.8 | 1.31 |
| federated fine-tuning | 5.2 | 4.2 | 0.63 |
| gaussian splatting | 108.2 | 110.0 | 0.80 |
| step-by-step reasoning | 7.7 | 13.8 | 1.41 |
| visual generation | 12.2 | 19.0 | 1.22 |
| video large language | 7.2 | 8.7 | 0.95 |
| data alignment | 5.5 | 2.7 | 0.38 |
| advanced reasoning | 13.0 | 15.8 | 0.95 |
| multimodal large language models | 96.0 | 188.5 | 1.54 |
| reducing hallucinations | 6.5 | 7.8 | 0.94 |
| multi-agent framework | 20.0 | 55.0 | 2.15 |
| multi-step reasoning | 22.3 | 44.7 | 1.57 |
| multimodal large language | 109.7 | 213.7 | 1.53 |

### Top 50 phrases flagged before 2025-07-01

| phrase | before/mo | later/mo | ratio |
|---|---|---|---|
| reasoning models | 80.7 | 120.2 | 1.06 |
| large reasoning models | 30.2 | 46.7 | 1.10 |
| long chain-of-thought | 13.5 | 13.7 | 0.72 |
| model context protocol | 8.8 | 37.5 | 3.02 |
| test-time scaling | 29.3 | 50.8 | 1.23 |
| verifiable rewards | 12.3 | 90.5 | 5.21 |
| long cot | 8.8 | 3.7 | 0.29 |
| reasoning length | 5.7 | 7.3 | 0.92 |
| reinforcement fine-tuning | 9.7 | 14.7 | 1.08 |
| inference-time scaling | 11.5 | 13.3 | 0.82 |
| structured reasoning | 21.5 | 41.2 | 1.36 |
| llm reasoning | 52.2 | 96.2 | 1.31 |
| multimodal reasoning | 29.3 | 65.0 | 1.57 |
| agentic ai | 17.2 | 91.2 | 3.77 |
| reasoning efficiency | 5.3 | 8.7 | 1.15 |
| reasoning dataset | 9.3 | 8.3 | 0.63 |
| efficient reasoning | 8.8 | 16.5 | 1.33 |
| reasoning chains | 21.2 | 37.2 | 1.25 |
| agentic workflows | 6.2 | 27.8 | 3.21 |
| physically grounded | 6.5 | 42.0 | 4.59 |
| clinical reasoning | 9.0 | 26.2 | 2.06 |
| scaling test-time compute | 5.0 | 5.0 | 0.71 |
| test-time compute | 14.5 | 21.3 | 1.04 |
| long reasoning | 5.0 | 10.2 | 1.44 |
| agentic ai systems | 5.5 | 26.2 | 3.38 |
| process reward models | 8.3 | 11.7 | 0.99 |
| response length | 8.2 | 11.7 | 1.01 |
| general reasoning | 9.8 | 19.8 | 1.43 |
| mathematical reasoning | 61.0 | 105.0 | 1.22 |
| medical reasoning | 7.0 | 12.8 | 1.30 |
| world modeling | 8.2 | 30.0 | 2.61 |
| llm agents | 63.8 | 240.8 | 2.68 |
| ai systems | 165.2 | 257.7 | 1.11 |
| spatial reasoning | 33.3 | 71.8 | 1.53 |
| action generation | 10.3 | 33.0 | 2.27 |
| multi-agent coordination | 6.7 | 18.7 | 1.99 |
| ai agents | 64.5 | 165.0 | 1.82 |
| multi-agent ai | 5.2 | 12.2 | 1.67 |
| thinking process | 6.3 | 2.5 | 0.28 |
| agentic systems | 11.8 | 58.8 | 3.53 |
| enhancing large language models | 8.5 | 4.7 | 0.39 |
| chain-of-thought reasoning | 28.5 | 44.5 | 1.11 |
| adaptive reasoning | 6.3 | 9.2 | 1.03 |
| multimodal large language models | 123.0 | 217.3 | 1.25 |
| visual reasoning | 28.3 | 60.8 | 1.52 |
| hybrid retrieval | 5.2 | 12.0 | 1.65 |
| retrieval-augmented generation | 162.8 | 230.2 | 1.00 |
| improves reasoning | 9.7 | 24.5 | 1.80 |
| slow thinking | 5.5 | 1.8 | 0.24 |
| agentic framework | 9.0 | 50.2 | 3.96 |

### Top 50 phrases flagged before 2025-10-01

| phrase | before/mo | later/mo | ratio |
|---|---|---|---|
| verifiable rewards | 32.5 | 90.3 | 1.94 |
| model context protocol | 18.7 | 41.5 | 1.55 |
| deep research | 10.8 | 26.7 | 1.72 |
| mcp servers | 5.0 | 8.3 | 1.16 |
| large reasoning models | 39.5 | 34.8 | 0.62 |
| agentic ai | 36.3 | 100.7 | 1.93 |
| clinically grounded | 5.2 | 17.3 | 2.34 |
| reasoning models | 101.8 | 104.2 | 0.71 |
| long chain-of-thought | 17.5 | 10.0 | 0.40 |
| reasoning length | 8.0 | 6.2 | 0.54 |
| test-time scaling | 39.7 | 42.5 | 0.75 |
| culturally grounded | 8.0 | 14.5 | 1.27 |
| agentic reasoning | 6.0 | 22.5 | 2.62 |
| semantic reasoning | 18.0 | 47.3 | 1.84 |
| diagnostic reasoning | 7.5 | 12.8 | 1.19 |
| general reasoning | 15.7 | 16.2 | 0.72 |
| agentic systems | 19.7 | 62.0 | 2.20 |
| efficient reasoning | 12.7 | 12.2 | 0.67 |
| agentic framework | 17.0 | 56.2 | 2.31 |
| structured prompting | 6.3 | 6.0 | 0.66 |
| physically grounded | 10.2 | 45.2 | 3.10 |
| hierarchical reasoning | 5.3 | 6.3 | 0.83 |
| multimodal reasoning | 42.5 | 65.2 | 1.07 |
| structured reasoning | 30.3 | 34.8 | 0.80 |
| grounded reasoning | 5.0 | 16.3 | 2.28 |
| reasoning efficiency | 7.2 | 6.2 | 0.60 |
| cross-modal reasoning | 8.5 | 9.8 | 0.81 |
| vision-language-action models | 14.2 | 58.7 | 2.89 |
| scalable foundation | 7.2 | 14.0 | 1.36 |
| coding agents | 5.5 | 114.8 | 14.57 |
| geometric consistency | 17.5 | 34.5 | 1.38 |
| long reasoning | 6.8 | 11.0 | 1.12 |
| static benchmarks | 7.8 | 12.3 | 1.10 |
| interpretable reasoning | 9.2 | 9.2 | 0.70 |
| scalable evaluation | 7.3 | 10.3 | 0.98 |
| structured prompts | 8.5 | 5.7 | 0.47 |
| reasoning language models | 12.3 | 11.0 | 0.62 |
| extensible framework | 6.7 | 7.2 | 0.75 |
| diffusion language models | 7.5 | 45.8 | 4.27 |
| real-world evaluation | 5.7 | 8.7 | 1.07 |
| dynamic workloads | 6.3 | 4.0 | 0.44 |
| long cot | 9.3 | 1.7 | 0.12 |
| agentic ai systems | 8.3 | 27.2 | 2.28 |
| unified evaluation framework | 5.5 | 8.0 | 1.02 |
| rl fine-tuning | 8.3 | 10.3 | 0.87 |
| tool integration | 7.0 | 3.2 | 0.32 |
| adaptive reasoning | 10.0 | 8.3 | 0.58 |
| tool use | 23.7 | 107.2 | 3.16 |
| multi-dimensional evaluation | 6.8 | 8.7 | 0.89 |
| semantic alignment | 40.5 | 68.5 | 1.18 |

### Pairs flagged before 2025-01-01

| pair | before/mo | later/mo | ratio |
|---|---|---|---|
| cs.CE+physics.comp-ph | 5.0 | 8.5 | 1.35 |
| cs.AI+econ.GN | 7.5 | 12.5 | 1.32 |
| cs.CY+cs.ET | 8.2 | 10.8 | 1.05 |
| cs.CV+cs.ET | 6.0 | 7.8 | 1.04 |
| cs.LG+q-bio.NC | 25.8 | 32.3 | 0.99 |
| cs.AI+q-bio.GN | 6.0 | 7.5 | 0.99 |
| cs.CC+quant-ph | 16.8 | 20.2 | 0.95 |
| cs.AI+cs.SD | 83.2 | 97.3 | 0.93 |
| cs.AI+cs.ET | 26.3 | 28.2 | 0.85 |
| cs.NA+physics.comp-ph | 20.5 | 20.8 | 0.81 |
| cs.CR+cs.ET | 10.7 | 10.5 | 0.78 |
| cs.LG+q-fin.CP | 11.7 | 11.0 | 0.75 |
| cs.AI+cs.AR | 26.7 | 24.8 | 0.74 |
| cs.ET+cs.HC | 10.8 | 10.0 | 0.73 |
| cs.AI+eess.IV | 71.8 | 65.5 | 0.72 |
| cs.MM+cs.SD | 32.0 | 29.2 | 0.72 |
| stat.AP+stat.ML | 15.3 | 13.7 | 0.71 |
| cs.GR+cs.HC | 13.5 | 11.7 | 0.69 |
| cs.LG+q-fin.TR | 5.3 | 4.3 | 0.64 |
| cs.AI+eess.AS | 91.2 | 72.2 | 0.63 |
| cs.CV+cs.MM | 80.5 | 62.8 | 0.62 |
| cs.DB+cs.SE | 5.7 | 4.2 | 0.58 |
| cs.MM+eess.AS | 32.3 | 17.0 | 0.42 |
| cs.CL+cs.ET | 5.8 | 2.8 | 0.39 |

### Pairs flagged before 2025-04-01

| pair | before/mo | later/mo | ratio |
|---|---|---|---|
| cs.CY+cs.MA | 6.8 | 13.8 | 1.58 |
| cs.AI+cs.MA | 72.5 | 134.0 | 1.45 |
| cs.CL+cs.MA | 19.0 | 33.2 | 1.37 |
| cs.HC+cs.MA | 7.2 | 12.2 | 1.33 |
| cs.IT+quant-ph | 19.8 | 30.5 | 1.20 |
| cs.LG+cs.MA | 44.8 | 63.3 | 1.11 |
| cs.GT+econ.TH | 14.3 | 17.8 | 0.97 |
| cs.LG+physics.comp-ph | 30.0 | 36.2 | 0.94 |
| cond-mat.mtrl-sci+cs.LG | 28.5 | 33.0 | 0.91 |
| cs.LG+hep-ex | 8.8 | 9.5 | 0.84 |
| cs.RO+math.OC | 14.0 | 14.2 | 0.79 |
| cs.HC+cs.RO | 41.8 | 35.8 | 0.67 |
| cs.CV+stat.ML | 20.0 | 16.2 | 0.63 |
| cs.CE+math.OC | 5.2 | 4.2 | 0.63 |
| cs.LG+physics.geo-ph | 11.8 | 8.7 | 0.57 |
| cs.DS+stat.ML | 14.5 | 10.0 | 0.54 |
| cs.LG+hep-ph | 8.5 | 5.5 | 0.51 |
| hep-ex+hep-ph | 5.2 | 3.2 | 0.48 |
| cs.LG+physics.bio-ph | 5.8 | 3.0 | 0.40 |
| cs.CV+stat.AP | 6.8 | 3.3 | 0.38 |

### Pairs flagged before 2025-07-01

| pair | before/mo | later/mo | ratio |
|---|---|---|---|
| cs.AI+cs.MA | 85.8 | 175.0 | 1.45 |
| cs.CY+cs.MA | 10.2 | 20.0 | 1.40 |
| cs.CL+cs.MA | 24.2 | 44.3 | 1.30 |
| cs.IT+math.PR | 9.2 | 14.2 | 1.10 |
| cs.AI+math.ST | 6.8 | 10.3 | 1.07 |
| cs.HC+cs.MA | 9.0 | 13.5 | 1.07 |
| cs.LG+math.PR | 25.7 | 32.8 | 0.91 |
| cs.ET+cs.SE | 5.8 | 6.8 | 0.83 |

### Pairs flagged before 2025-10-01

| pair | before/mo | later/mo | ratio |
|---|---|---|---|
| cs.AI+physics.soc-ph | 6.2 | 11.0 | 1.25 |
| cs.MA+cs.NI | 5.3 | 8.8 | 1.16 |
| cs.MM+eess.IV | 10.8 | 17.2 | 1.11 |
| cs.AI+cs.IT | 20.7 | 31.5 | 1.06 |
| cs.DB+cs.DC | 8.8 | 11.7 | 0.92 |
| cs.PL+cs.SE | 22.3 | 28.5 | 0.89 |
| cs.AI+cs.ET | 39.0 | 45.5 | 0.81 |
| cs.DB+cs.IR | 13.7 | 14.8 | 0.76 |
| cs.AI+cs.SD | 104.5 | 109.5 | 0.73 |
| cs.CR+cs.SD | 7.8 | 8.0 | 0.71 |
| cs.CL+cs.SD | 74.8 | 70.2 | 0.65 |
| cs.HC+cs.MM | 12.5 | 10.8 | 0.60 |
| cs.CY+cs.ET | 13.8 | 10.8 | 0.55 |

