"""
ai_flow_builder — testcase → codeless .flow generation pipeline.

The tool's own generation feature. Every flow it emits carries provenance: which
command generated it, which testcase input was processed, which registered actions
were available, what the mapper returned, which locators were reused vs unresolved,
and which validation gates passed.

Stages
------
  catalogue.py  — reads the REAL parser + action registry + locator DBs
  testcase.py   — testcase model; multi-sheet XLSX ingestion; prompt ingestion
  mapper.py     — manual step → codeless statement, with per-step classification
  emitter.py    — deterministic .flow rendering (only after mapping validates)
  pipeline.py   — orchestration + live progress events + batch control
"""

__all__ = ["catalogue", "testcase", "mapper", "emitter", "pipeline"]
__version__ = "0.1.0"
