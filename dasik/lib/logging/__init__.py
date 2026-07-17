"""dasik run-logging: a single observability chokepoint for shelled-out commands.

Every ``Command.execute`` records here so that (a) the full stdout/stderr/exit of
an install is written to a debug log file for later inspection, and (b) failures
surface in red on the console instead of vanishing into a captured pipe.
"""
