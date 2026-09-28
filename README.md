# Multi-Agent Data Science Pipeline

A LangGraph-based agentic analytics system that turns a raw CSV into reproducible
machine-learning artifacts and an evidence-backed business report. LLM agents plan
and select actions; deterministic Python tools execute data processing, feature
engineering, model training, evaluation, retrieval, and report generation.

The project can be used from the CLI or through a FastAPI Web MVP with queued jobs,
SSE progress updates, report rendering, chart previews, and artifact downloads.

## Architecture

```text
CSV input
  -> Split Manager (optional task inference and train/test split)
  -> Agent Manager (profile, requirement context, cleaning plans)
  -> Data Clean Agent (train and test stages)
  -> EDA Agent (analysis, charts, metrics, three candidate models)
  -> Feature Agent (model-specific train/test preprocessing)
  -> Model Agent (candidate tuning, best-model training, held-out evaluation)
  -> Evaluation gate (requires best_model.final_test_metrics)
  -> Business Translator / BAT (RAG evidence and business report)
```

The graph uses a shared typed state, explicit stage routing, supervisor checks, and
structured tools. Each agent is restricted to its stage-specific tool set. Tool-call
histories are isolated between stages and between train/test or per-model rounds to
reduce context leakage and provider-specific function-call errors.

### Report ownership

| Component | Primary output |
|---|---|
| Split Manager | Train/test CSV files and split metadata |
| Agent Manager | Dataset profile and cleaning plans |
| Data Clean Agent | Cleaned train/test CSV files |
| EDA Agent | EDA report, charts, candidate models, evaluation metrics |
| Feature Agent | Per-model feature datasets and fitted preprocessors |
| Model Agent | CV comparison, persisted best model, held-out test metrics |
| Business Translator | `business_insight_report.md` and optional HTML/PDF |

Business recommendations are owned exclusively by BAT. Non-BAT reports pass through
a save-time filter that removes leaked recommendation sections.

BAT runs only when the selected model has valid held-out test metrics. If evaluation
fails, the run remains failed and preserves its partial artifacts, but no business
report is generated. This prevents unvalidated model output from becoming a business
recommendation.

## Quick Start

### Requirements

- Python 3.12+
- An API key for at least one supported LLM backend
- Optional: Quarto for HTML/PDF report rendering

Install the project from this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

For macOS or Linux, activate with `source .venv/bin/activate` and copy the environment
file with `cp .env.example .env`.

### LLM configuration

Configure provider credentials and routing in `.env`. The global route is
`PIPELINE_LLM`; an agent-specific route takes precedence:

```dotenv
PIPELINE_LLM=gemini_flash

PIPELINE_LLM_AGENT_MANAGER=gemini_flash
PIPELINE_LLM_SPLIT=gemini_flash
PIPELINE_LLM_DATA_CLEAN=gemini_flash
PIPELINE_LLM_EDA=gemini_flash
PIPELINE_LLM_FEATURE=gemini_flash
PIPELINE_LLM_MODEL=gemini_flash
PIPELINE_LLM_SUPERVISOR=gemini_flash
PIPELINE_LLM_BAT=gemini_flash

GEMINI_FLASH_API_KEY=your_key
GEMINI_FLASH_MODEL=gemini-2.5-flash
```

Supported route names are `deepseek`, `openai`, `openai_strong`, `gemini`,
`gemini_flash`, `zhipu`, `qwen`, `minimax`, and `kimi`. See `.env.example` for
provider URLs, model names, RAG settings, and optional Manager RAP retrieval.
Never commit live credentials.

### Web MVP

Start one Uvicorn process from the project directory:

```powershell
python -m uvicorn webapp.api:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Stop the service with `Ctrl+C`.



### CLI

The repository includes one redistributable demo dataset. See
[`datasets/README.md`](datasets/README.md) for its source, license, and citation.

Run the full pipeline from one raw CSV:

```powershell
python main.py --data "datasets/bank+marketing/bank/bank.csv" --description-file "datasets/bank+marketing/bank/bank-names.txt" --request "Analyze term-deposit subscription, train candidate classifiers, and produce an evidence-backed business report" --target y --task-type classification
```

Pre-split mode is also supported:

```powershell
python main.py --train path/to/train.csv --test path/to/test.csv --description-file path/to/data_description.txt --request "Run the full pipeline" --target target_column --task-type classification
```

Use `python main.py --help` for graph export, recursion limits, and benchmark recording
arguments.

### Python API

```python
from main import run_pipeline

result = run_pipeline(
    data_path="datasets/bank+marketing/bank/bank.csv",
    description="Binary classification: predict term-deposit subscription (target: y)",
    user_request="Train candidate classifiers and produce a business report",
    target_column="y",
    task_type="classification",
)

print(result["best_model"])
```

## RAG and Business Report

BAT retrieves evidence from a local Chroma knowledge base using metadata-aware
buckets and optional cross-encoder reranking. arXiv is used as a fallback when local
evidence is insufficient.

Prepare and build the local knowledge base before relying on domain retrieval:

```powershell
python scripts/prepare_mckinsey_md.py
python scripts/build_mckinsey_kb.py
```

The repository stores only source URLs and metadata in
`data/mckinsey_kb/manifest.yaml`. Retrieved Markdown, local PDFs, lock files, and
Chroma data are generated locally and ignored by Git. Add private or licensed
documents only to your local `source_pdf/` or `source_md/` directory.

See [`rag/README.md`](rag/README.md) and `.env.example` for chunking, embedding,
reranking, retrieval, and fallback settings.

`business_insight_report.md` is always the canonical BAT artifact after a successful
run. When Quarto is installed, the pipeline also attempts to render HTML and PDF.

## Run Artifacts

Every run is isolated under `artifacts/workspace/<run_id>/`:

```text
artifacts/workspace/<run_id>/
  clean/          cleaned train/test datasets
  features/       per-model feature datasets, encoders, and scalers
  models/         selected model and feature-column contract
  plots/          EDA visualizations
  predictions/    held-out predictions and probabilities
  reports/        stage reports, state snapshot, token usage, business report
  train_*.csv     run-scoped training split
  test_*.csv      run-scoped held-out split
```

For failed runs, inspect these files first:

- `reports/model_agent_report.md`
- `reports/supervisor_report.md`
- `reports/*_state.json`, especially `errors`, `current_stage`, and `best_model`
- `reports/token_usage.json`


## Project Layout

```text
agent/        LangGraph agent subgraphs and stage routing
core/         global state, pipeline graph, LLM routing, reporting, dataset store
tools/        deterministic data, feature, model, EDA, and business tools
rag/          document processing, vector retrieval, and reranking
webapp/       FastAPI API, SQLite job manager, SSE, and static UI
scripts/      knowledge-base and report utilities
tests/        unit, routing, integration, and Web job tests
main.py       CLI and Python entry point
```


## License

This repository originated as a course project created by
**our team (Liam, Leslie, Xiangshan, Mia, Jack, Justin, Tina, et)** and has since been independently maintained and
substantially extended by the repository owner. Sincere thanks to every member
of the original course team for their shared effort. The project is provided for
educational and portfolio purposes; no separate open-source license is granted
unless explicitly stated.

---
