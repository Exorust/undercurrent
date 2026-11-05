# Backtest: S3 phrases flagged with data before 2025-04-01

Measured: monthly paper rate in 2025-10..2026-03 divided by the monthly rate in the 6 months before the cutoff.

| group | n | grew (>=1.5x) | flat | faded (<0.75x) | median ratio |
|---|---|---|---|---|---|
| top 50 flagged | 50 | 38% | 44% | 18% | 1.29 |
| all candidates (baseline) | 156 | 31% | 51% | 19% | 1.20 |

## Top 50 flagged phrases

| phrase | before/mo | later/mo | ratio |
|---|---|---|---|
| deepseek r1 | 8.2 | 4.2 | 0.51 |
| large reasoning models | 7.2 | 30.3 | 4.23 |
| test-time scaling | 9.2 | 34.3 | 3.75 |
| openai o1 | 14.3 | 1.7 | 0.12 |
| reasoning models | 25.8 | 77.0 | 2.98 |
| process reward models | 5.0 | 6.0 | 1.20 |
| inference-time scaling | 5.3 | 9.7 | 1.81 |
| inference scaling | 6.0 | 2.5 | 0.42 |
| sparse autoencoders | 16.8 | 18.0 | 1.07 |
| process reward | 8.7 | 10.2 | 1.17 |
| inference-time compute | 5.3 | 3.2 | 0.59 |
| test-time compute | 8.2 | 11.0 | 1.35 |
| vision-language models | 182.0 | 254.5 | 1.40 |
| structured reasoning | 9.2 | 28.5 | 3.11 |
| video generation | 77.8 | 86.8 | 1.12 |
| sparse autoencoder | 5.2 | 7.3 | 1.42 |
| reasoning trajectories | 5.2 | 23.3 | 4.52 |
| agentic ai | 7.3 | 54.5 | 7.43 |
| llm reasoning | 29.7 | 62.5 | 2.11 |
| llm agents | 46.5 | 97.5 | 2.10 |
| token reduction | 6.2 | 7.7 | 1.24 |
| multimodal reasoning | 13.8 | 45.8 | 3.31 |
| video large language models | 6.8 | 6.8 | 1.00 |
| enhanced reasoning | 5.8 | 5.0 | 0.86 |
| reasoning process | 33.2 | 59.5 | 1.79 |
| agentic systems | 5.0 | 30.2 | 6.03 |
| autoregressive modeling | 6.8 | 8.3 | 1.22 |
| reasoning llms | 19.2 | 24.5 | 1.28 |
| process supervision | 5.7 | 3.2 | 0.56 |
| agentic workflow | 5.0 | 7.8 | 1.57 |
| gui agents | 6.0 | 13.0 | 2.17 |
| retrieval-augmented generation | 128.5 | 152.7 | 1.19 |
| similarity analysis | 5.2 | 6.3 | 1.23 |
| enhancing llm | 12.0 | 5.7 | 0.47 |
| visual understanding | 26.3 | 27.2 | 1.03 |
| flow matching | 29.5 | 65.3 | 2.21 |
| chain-of-thought reasoning | 18.8 | 30.2 | 1.60 |
| code translation | 6.3 | 4.0 | 0.63 |
| visual token | 9.8 | 12.7 | 1.29 |
| token compression | 5.7 | 9.5 | 1.68 |
| action generation | 5.3 | 12.3 | 2.31 |
| kv cache | 28.5 | 38.8 | 1.36 |
| diffusion transformers | 19.7 | 23.2 | 1.18 |
| federated fine-tuning | 5.2 | 3.7 | 0.71 |
| gaussian splatting | 108.2 | 85.7 | 0.79 |
| step-by-step reasoning | 7.7 | 10.7 | 1.39 |
| gemini flash | 5.5 | 20.5 | 3.73 |
| visual generation | 12.2 | 15.7 | 1.29 |
| data alignment | 5.5 | 1.5 | 0.27 |
| advanced reasoning | 13.0 | 13.5 | 1.04 |
