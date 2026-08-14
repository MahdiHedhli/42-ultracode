# Plugin skill boundary

The project-level planner, worker, and control Skills are maintained in
`../../.agents/skills/` so they can be used both with and without this local
plugin.  This directory is kept as the plugin’s future skill-extension point;
no wrappers are duplicated here because duplicated workflow instructions could
drift from the authoritative project Skills.
