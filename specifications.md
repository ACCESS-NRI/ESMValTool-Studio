# ESMValTool Studio — Concept and Specifications

## Motivation

Many users find writing ESMValTool recipes difficult. Recipes are YAML files that
combine data sources, a chain of ESMValCore preprocessors, and one or more
diagnostics — a structure that is conceptually a pipeline, but which users have to
express and debug as hand-written text.

The proposal is a graphical interface for **building, validating, and running**
ESMValTool recipes.

## Core concept: a visual pipeline builder

The main window is a node-based, drag-and-drop canvas:

- Each **ESMValCore preprocessor** is a box that can be connected to others to form
  a processing chain.
- **Data sources** feed into the pipeline.
- **Diagnostics** run on the processed data at the end of the chain.
- The output of one box becomes the input of the next.

Connecting boxes implies **compatibility checks** between the output of one step and
the input of the next. On top of per-connection checks, a **"validate pipeline"**
action would check the graph as a whole before the user commits to a run.

## Catalogues

Two browsable, documented catalogues backing the canvas:

- **Preprocessor catalogue** — every available ESMValCore preprocessor with its
  documentation and parameters.
- **Diagnostics catalogue** — same, for diagnostics.

**Open question:** can these catalogues be generated automatically by introspecting
the ESMValCore / ESMValTool Python packages, or does this need a separate,
hand-maintained description layer? Automatic generation would keep the GUI in step
with upstream; the alternative is a GUI pinned to a specific ESMValTool/ESMValCore
version.

## Recipe library

A catalogue of the recipes already shipped with ESMValTool, so that users can:

1. Browse the existing recipes.
2. Load one and have its YAML rendered as a visual pipeline.
3. Edit that pipeline in place — rewire steps, change parameters, add data sources.
4. Export it back to YAML.

This makes "start from something that works and adapt it" the default workflow,
rather than starting from a blank file.

## Data sources

Data sources are defined from the **configuration file of the target system**: once
the GUI is connected to a machine, it inspects that configuration to discover what
data is actually available there and offers it as pipeline inputs.

## Execution and HPC connectivity

The GUI should be able to connect to an HPC system such as **Gadi**.

Two modes, deliberately separable:

- **Offline** — the pipeline builder works with no connection at all.
- **Connected** — a connection is only required to run recipes (and to discover the
  data available on the system).

Running on HPC means going through the machine's **batch scheduler**. On Gadi that is
**PBS**, with restrictions on which queues may be used — the GUI has to model those
constraints rather than just submitting blindly.

**Open question:** what is the right connection architecture? Options include
installing a server-side component of the app on the HPC and talking to it over SSH,
in the style of VS Code Remote.

## Open questions summary

1. Auto-generate the preprocessor/diagnostic catalogues from the Python packages, or
   maintain them separately (and thus pin the GUI to a version)?
2. Connection architecture: SSH-only client, or a server-side component installed on
   the HPC?
3. How far should pipeline validation go before submission — connection-level type
   checks, full recipe validation, or a dry run through ESMValCore?

## Implementation

See [implementation-plan.md](implementation-plan.md) for the proposed stack,
architecture, and delivery phases.

## Resources

- [ESMValTool documentation](https://docs.esmvaltool.org/en/latest/) — user and
  developer docs for both ESMValTool and ESMValCore. Primary reference for the
  preprocessor and diagnostic descriptions the catalogues need.
- [ESMValGroup/ESMValTool](https://github.com/ESMValGroup/ESMValTool) — recipes and
  diagnostics. Source for the recipe library and the diagnostics catalogue.
- [ESMValGroup/ESMValCore](https://github.com/ESMValGroup/ESMValCore) — the recipe
  engine and the preprocessor implementations. Source for the preprocessor catalogue,
  and the package to introspect if the catalogues are auto-generated (open question 1).
