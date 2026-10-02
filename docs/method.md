# Method and code

Section references refer to manuscript V1.15.

| Paper concept | Code entry | Purpose |
| --- | --- | --- |
| Candidate representation, Section III | [State template](../bundle/.claude/skills/text2hydraulic/reference/94_template.json) | Facts, assumptions, circuit, parameters, checks, results and artifact paths |
| Architecture, Section IV-A | [Workflow](../bundle/.claude/skills/text2hydraulic/SKILL.md) and [agents](../bundle/.claude/agents/) | Role handoffs, shared state and feedback |
| Domain knowledge, Section IV-B | [Reference index](reference-index.md) and [catalog](../bundle/.claude/skills/text2hydraulic/reference/component_catalog.json) | Circuit patterns, component descriptions and legal ports |
| Engineering operators, Section IV-C | [Scripts](../bundle/.claude/skills/text2hydraulic/scripts/) | Calculation, state handling, checking, layout and response extraction |
| Checking, Section IV-D | [Review role](../bundle/.claude/agents/hydraulic-review.md) and [validator](../bundle/.claude/skills/text2hydraulic/scripts/validate_memory.py) | Candidate-dependent engineering and artifact checks |
| Simulation, Section IV-E | [Simulation role](../bundle/.claude/agents/hydraulic-simulation.md) | Model preparation, numerical execution and evidence write-back |

## Nine engineering scripts

| Script | Operation |
| --- | --- |
| `convert_hydraulic_units.py` | Standardize engineering units |
| `calculate_cylinder_diameter.py` | Calculate theoretical bore and flow |
| `memory_view.py` | Expose stage-relevant state |
| `expand_memory_components.py` | Expand catalog-backed component definitions |
| `validate_memory.py` | Validate state and phase-specific checks |
| `generate_icon_placement_annotations.py` | Generate placement annotations |
| `generate_oil_line_annotations.py` | Generate connection annotations |
| `check_icon_overlap.py` | Check graphical overlaps |
| `extract_workpiece_metrics_from_csv.py` | Extract motion metrics from actual CSV output |

Scripts are under [bundle/.claude/skills/text2hydraulic/scripts](../bundle/.claude/skills/text2hydraulic/scripts/). Use `--help` to inspect arguments.

## From method to demo

The skill is the workflow contract and the three agent files define role responsibilities. Deterministic scripts perform calculations and checks. The shared `memory.json` retains the current task's inputs, decisions, paths and results.

`server.py` initializes task state, copies the workflow resources, starts the CLI, captures events, and exposes generated files to the local interface. `visualization.py` reads model and CSV information; `icon_preview.py` and `render_model.py` handle diagrams. The frontend under `static/` presents progress, files and curves.

To modify the workflow, edit the canonical files under `bundle/.claude/`. New tasks receive the updated snapshot. Existing tasks retain their task-local copy. Keep agent names, skill references, catalog paths, and state fields consistent.
