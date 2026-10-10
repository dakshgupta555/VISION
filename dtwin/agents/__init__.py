"""
Structured diagnostic agents. Deterministic: no language model, no agent
library. Each agent is a thin wrapper over modules that already exist, and
emits records in one JSON schema (schema.py):

  observation     dtwin.ingest report + the validator's channel quality flags
  physics         dtwin.physics.circuit (losses), .thermal (hot-spot), .magnetic (flux)
  diagnostic      dtwin.analytics.validation via dtwin.twin.components / .diagnose
  recommendation  rules on the diagnostic records + config/catalogue.yaml

Every natural-language field is a template filled with numbers the modules
computed. The output is advisory: the system never operates equipment and
never authorises a transformer to remain in service.
"""

from .run import run_agents  # noqa: F401
