"""prreview — a multi-agent pull-request reviewer.

Four specialists fan out over a diff, an aggregator merges and dedupes their
findings, and a human approves the draft before anything is posted.

Layering (INV-1, dependencies point inward only):
    cli            entry
    orchestrator   app
    agents/*       domain
    llm, github    adapter
    contracts, diff  core   ← import nothing from this package
"""

__version__ = "0.1.0"
