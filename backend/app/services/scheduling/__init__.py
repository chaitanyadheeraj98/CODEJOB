"""Scheduling: the timezone foundation, schedule computation, and the sweep.

Deliberately a package of plain functions taking a Session. Startup is skipped
under pytest (`startup_service.py`), so anything reachable only through the
background thread would be untestable.
"""
