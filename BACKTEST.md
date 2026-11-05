# Backtest: S3 phrases flagged with data before 2025-04-01

Measured: monthly paper rate in 2025-10..2026-03 divided by the monthly rate in the 6 months before the cutoff, then divided by 1.26, the growth of all arXiv CS papers between the same two windows. A ratio of 1.0 means the phrase only kept pace with the field.

| group | n | grew (>=1.5x) | flat | faded (<0.75x) | median ratio |
|---|---|---|---|---|---|
| top 50 flagged | 50 | 42% | 40% | 18% | 1.41 |
| all candidates (baseline) | 156 | 35% | 48% | 17% | 1.25 |

## Top 50 flagged phrases

| phrase | before/mo | later/mo | ratio |
|---|---|---|---|
| deepseek r1 | 8.2 | 4.5 | 0.44 |
| large reasoning models | 7.2 | 44.0 | 4.87 |
| test-time scaling | 9.2 | 50.3 | 4.36 |
| openai o1 | 14.3 | 2.0 | 0.11 |
| reasoning models | 25.8 | 109.0 | 3.35 |
| process reward models | 5.0 | 8.8 | 1.40 |
| inference-time scaling | 5.3 | 13.5 | 2.01 |
| inference scaling | 6.0 | 3.0 | 0.40 |
| sparse autoencoders | 16.8 | 26.5 | 1.25 |
| process reward | 8.7 | 17.0 | 1.56 |
| inference-time compute | 5.3 | 5.5 | 0.82 |
| test-time compute | 8.2 | 14.5 | 1.41 |
| vision-language models | 182.0 | 334.8 | 1.46 |
| structured reasoning | 9.2 | 37.2 | 3.22 |
| video generation | 77.8 | 113.7 | 1.16 |
| sparse autoencoder | 5.2 | 9.8 | 1.51 |
| reasoning trajectories | 5.2 | 33.2 | 5.09 |
| agentic ai | 7.3 | 70.7 | 7.65 |
| llm reasoning | 29.7 | 85.0 | 2.27 |
| llm agents | 46.5 | 140.5 | 2.40 |
| token reduction | 6.2 | 10.5 | 1.35 |
| multimodal reasoning | 13.8 | 59.0 | 3.38 |
| video large language models | 6.8 | 8.0 | 0.93 |
| enhanced reasoning | 5.8 | 5.5 | 0.75 |
| reasoning process | 33.2 | 76.5 | 1.83 |
| agentic systems | 5.0 | 42.0 | 6.67 |
| autoregressive modeling | 6.8 | 10.2 | 1.18 |
| reasoning llms | 19.2 | 33.8 | 1.40 |
| process supervision | 5.7 | 5.2 | 0.72 |
| agentic workflow | 5.0 | 10.8 | 1.72 |
| gui agents | 6.0 | 18.0 | 2.38 |
| retrieval-augmented generation | 128.5 | 195.3 | 1.21 |
| similarity analysis | 5.2 | 7.8 | 1.20 |
| enhancing llm | 12.0 | 7.3 | 0.49 |
| visual understanding | 26.3 | 35.2 | 1.06 |
| flow matching | 29.5 | 96.2 | 2.59 |
| chain-of-thought reasoning | 18.8 | 39.5 | 1.66 |
| code translation | 6.3 | 5.7 | 0.71 |
| visual token | 9.8 | 16.8 | 1.36 |
| token compression | 5.7 | 12.2 | 1.70 |
| action generation | 5.3 | 18.3 | 2.73 |
| kv cache | 28.5 | 52.5 | 1.46 |
| diffusion transformers | 19.7 | 32.8 | 1.32 |
| federated fine-tuning | 5.2 | 4.2 | 0.64 |
| gaussian splatting | 108.2 | 109.8 | 0.81 |
| step-by-step reasoning | 7.7 | 13.8 | 1.43 |
| gemini flash | 5.5 | 27.3 | 3.94 |
| visual generation | 12.2 | 19.0 | 1.24 |
| data alignment | 5.5 | 2.7 | 0.38 |
| advanced reasoning | 13.0 | 15.8 | 0.97 |
