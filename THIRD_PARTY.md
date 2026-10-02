# Third-party dependencies

The application uses these external projects:

| Dependency | Use | Upstream |
| --- | --- | --- |
| Claude Code | Skill and agent execution | [Documentation](https://code.claude.com/docs/en/overview) |
| OpenModelica | Compilation, simulation and icon tooling | [Project](https://openmodelica.org/) |
| Modelica Standard Library | Standard component models | [Repository](https://github.com/modelica/ModelicaStandardLibrary) |
| OpenHydraulics | Hydraulic component models | [Repository](https://github.com/modelica-3rdparty/OpenHydraulics) |
| OMPython | Python interface to OpenModelica | [Repository](https://github.com/OpenModelica/OMPython) |
| svgwrite | SVG generation | [Repository](https://github.com/mozman/svgwrite) |
| CairoSVG and Cairo | PNG rendering | [CairoSVG](https://cairosvg.org/) and [Cairo](https://www.cairographics.org/) |

External runtimes and model libraries are configured on the execution machine. The source ZIP contains project code, workflow resources and documentation; it excludes optional local installations under `bundle/OpenHydraulics/` and `bundle/tools/`.

The renderer uses OpenModelica's `generate_icons.py`. Preserve all original license and attribution notices when redistributing upstream libraries or scripts separately. Python renderer dependency versions are pinned in [requirements-renderer.txt](requirements-renderer.txt).

The frontend uses local HTML, CSS, JavaScript and inline SVG, with no external fonts or CDN assets. Licensing for external dependencies remains governed by their upstream terms.
