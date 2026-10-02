# Demo walkthrough

## Start a task

After [setup](setup.md), run `bash start.sh --no-browser` and open <http://127.0.0.1:8766>. Check **Environment settings**, choose **Worktable**, then select **Start design**. You can also paste [examples/worktable.txt](../examples/worktable.txt) or enter your own requirements.

The backend creates a task directory and copies the supplied skill and all three agent definitions into its `.claude/` folder. It initializes `memory.json` with the input hash and runtime paths, then starts the CLI.

The specialist roles operate sequentially: Design handles requirements, circuit selection and sizing; Simulation prepares the model; Review checks it; Simulation executes it and extracts responses. Findings can trigger revision. The main session coordinates and writes the final summary.

## Inspect the interface

- **Live activity:** concise progress notes and operation summaries.
- **Model:** the generated circuit assembled from component icons and Modelica annotations.
- **Data / Parameter:** select an actual CSV file and a numeric signal to plot against time.
- **Read final report:** inspect the report from the runtime.
- **Export run:** download the task's artifacts and original logs.

Diagram rendering does not call the model or rerun the simulation. It uses the model's existing placement and connection annotations, with cached output under `model-previews/`. It is not an OMEdit screenshot. Missing annotations and unsupported graphics can affect the preview.

CSV statistics use finite numeric values. Long curves may be downsampled for display; downloads retain the original file. Pressure and flow only appear when those signals were exported. Variable-name-based units should be checked against the model for custom signals.

## Output files

```text
runs/<run-id>/
├── .claude/                 Task-local skill and agents
├── prompt.txt               Original requirements
├── run.json                 Process and session metadata
├── events.jsonl             Timestamped interface events
├── claude.stdout.jsonl      Raw runtime stream
├── claude.stderr.log        Runtime diagnostics
├── memory.json              Shared engineering state
├── final-report.md          Report, when produced
├── model-previews/          Rendered diagrams, when produced
└── *.mo / *.mos / *.mat / *.csv / *.log
```

Engineering filenames depend on the generated model. Follow the registered paths in `memory.json`, including revision directories. Failed runs may have partial outputs.

The exported raw runtime stream includes more detail than the abbreviated browser activity, including tool inputs and outputs. Review it before sharing.

## Acceptance

A completed session and a successful engineering result are distinct. Inspect current checks, thresholds, issues, artifact paths, and actual motion together.

- `acceptance.done_allowed` records that the workflow can conclude.
- `acceptance.result_level_success` and `acceptance.strict_reproducible_success` record workflow acceptance under the skill's rules.
- The process state `completed` records that the runtime finished.

The browser displays these values; it does not independently validate the hydraulic design. See the [workflow contract](../bundle/.claude/skills/text2hydraulic/SKILL.md) for current acceptance and feedback rules.

## Stop and resume

**Stop** terminates the active process group and retains its files. After a task ends, **Open session** can resume its CLI session on the machine running the server. This requires that machine's CLI session history; a task ZIP does not contain the CLI's session database.

The resumed terminal session is not captured as a new web run. Refreshing the workspace can reread changed files, while the original backend status and report remain from the web run.

The service listens on localhost and allows one active design at a time.
