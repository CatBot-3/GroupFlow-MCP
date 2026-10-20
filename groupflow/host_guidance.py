"""Portable guidance for optional host capabilities, without provider clients."""

WEATHER_POLICY = (
    "Look for a weather MCP when relevant; use it if available, otherwise ignore weather. "
    "Forecasts are advisory, not closures or consent."
)

OPTIONAL_WEATHER_GUIDANCE = WEATHER_POLICY + (
    " Inspect the host's connected tools, using its MCP discovery facility if available. "
    "Use an available forecast for the event's public location and date. "
    "If absent, unavailable or unsuitable, continue without weather; do not invent forecasts, require a connection or call a weather API directly. "
    "A forecast is not a venue closure or human consent; proposals still need normal constraints and personal confirmations."
)

LOCAL_HOST_CAPABILITIES = (
    "Demo: GroupFlow only; skip weather. No direct weather APIs."
)
