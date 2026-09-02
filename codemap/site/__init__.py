"""The explorer: DB -> a single self-contained ``explore.html`` (spec M10-M14).

A statically generated artifact, not a server. ``model.py`` turns the graph into
one JSON-serializable dict; ``render.py`` inlines it into ``assets/shell.html``
alongside the frozen ``explore.css`` / ``explore.js``. No process ever runs.
"""
