"""Snapshot keys shared by the writers and the recovery reader."""

KEY_TENSION = "tension.current"
KEY_GAIN = "tension.gain"
KEY_ACID = "pickle.acid"
KEY_PUMP = "pump.state"
KEY_TRACK = "strip.track"
KEY_RINSE = "pickle.rinse"
KEY_LATCH = "weld.latch"
KEY_WELD = "weld.record"
SEAM_PREFIX = "weld.seam."
KEY_EVENTS = "audit.events"
KEY_HOURS = "pump.hours"

#: Field readings uploaded at start-up for the recovery health check.
KEY_FIELD = "field.report"
#: Outcome of the latest start-up health check, including pending items.
KEY_HEALTH = "recover.health"
