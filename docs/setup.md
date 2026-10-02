# Setup and troubleshooting

Follow the [README](../README.md#installation) for the main installation sequence. Commands below run from the repository root in Linux or WSL2.

## Model runtime

Install and authenticate Claude Code using its [official setup guide](https://code.claude.com/docs/en/setup). Verify that it can execute a simple request in your local environment before running the hydraulic demo.

The runtime must support the supplied skill, custom agents, tool calls, and session continuation. A model identifier alone does not configure a service endpoint or credentials. Keep service configuration in the CLI environment. The demo inherits that environment and does not accept API keys in its web UI.

The manuscript identifies `Qwen3.6-35B-A3B-UD-Q6_K_XL.gguf`; the demo does not assume that this model is installed. Set `T2H_MODEL` only to a model your configured CLI service exposes.

## OpenModelica and OpenHydraulics

Install OpenModelica using the [official instructions](https://openmodelica.org/download/), including its compilation dependencies and a compatible Modelica Standard Library. The manuscript environment uses OpenModelica 1.26.4 and OpenHydraulics 2.0.0.

Obtain OpenHydraulics from the [upstream repository](https://github.com/modelica-3rdparty/OpenHydraulics) or your Modelica library manager. Point to the root `package.mo`, not a component file:

```bash
export T2H_OPENHYDRAULICS=/absolute/path/to/OpenHydraulics/package.mo
```

The application also recognizes an optional local installation at `bundle/OpenHydraulics/package.mo`. External libraries are configured separately from the distributed source.

For a fresh installation, the following example obtains the tagged library in a directory beside this repository:

```bash
git clone --depth 1 --branch v2.0.0 https://github.com/modelica-3rdparty/OpenHydraulics.git ../OpenHydraulics
export T2H_OPENHYDRAULICS="$(cd ../OpenHydraulics && pwd)/OpenHydraulics/package.mo"
```

Use your existing installation instead if that destination already exists. The upstream repository has an inner `OpenHydraulics/` directory, hence the extra path level. [OpenHydraulics 2.0.0](https://github.com/modelica-3rdparty/OpenHydraulics/releases/tag/v2.0.0) is based on Modelica Standard Library 4.0.0; make that library available to OpenModelica.

## Renderer

The optional diagram renderer uses the versions pinned in [requirements-renderer.txt](../requirements-renderer.txt): OMPython, svgwrite, and CairoSVG. CairoSVG also needs the Cairo system library. On Ubuntu/Debian, install missing system prerequisites using your package manager:

```bash
sudo apt-get update
sudo apt-get install python3-venv libcairo2
python3 setup_renderer.py --install
```

The install command creates `.venv-renderer` and installs the pinned packages. `--check`, startup, and **Auto-detect** only diagnose dependencies.

The renderer discovers OpenModelica's `generate_icons.py` under paths such as `share/doc/omc/testmodels/` or `share/omc/scripts/`. When necessary, set `icon_exporter` to the actual script path. The application also recognizes an optional local fallback at `bundle/tools/generate_icons.py`.

## Persistent configuration

Startup creates or updates local `config.json`. For manual configuration, copy [config.example.json](../config.example.json) only if `config.json` does not already exist, then edit the required fields.

| Field | Meaning |
| --- | --- |
| `claude_command` | CLI executable name or absolute path; no appended shell arguments |
| `omc_command` | OpenModelica compiler executable |
| `model` | Model identifier; empty uses the local runtime default |
| `openhydraulics_package` | Path to the hydraulic library's root `package.mo` |
| `timeout_seconds` | Overall task time limit, default 1800 seconds |
| `allowed_tools` | Rules for noninteractive tool execution |
| `permission_mode` | Runtime permission mode, default `inherit` |
| `terminal_permission_mode` | Permission mode for resumed interactive sessions |
| `renderer_python` | Python interpreter with rendering dependencies |
| `icon_exporter` | OpenModelica `generate_icons.py` path |
| `render_timeout_seconds` | Rendering timeout, default 120 seconds |

Environment overrides are `T2H_CLAUDE`, `T2H_OMC`, `T2H_OPENHYDRAULICS`, and `T2H_MODEL`. They apply to the current service process. Changing environment variables requires restarting the server.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Environment not ready | Click **Auto-detect**, then inspect configured CLI, compiler and library paths |
| CLI installed but task fails | Inspect task stdout/stderr; check model service, authentication and runtime permissions |
| Permission prevents a tool call | Inspect the denial and resume using **Open session** to review the request |
| Missing diagram | Check renderer imports, Cairo, `icon_exporter`, and the model's placement annotations |
| Missing curves | Inspect simulator logs and selected CSV; verify actual `time`, `worktable.s`, and `worktable.v` signals |
| Port occupied | Use `bash start.sh --no-browser --port 8877` |
| Service restarted during a task | Inspect the interrupted run; a restart does not automatically rerun the task |

The launcher and process-group management target Linux. Use Linux under WSL2 for the full workflow rather than launching it directly in a native Windows shell.
