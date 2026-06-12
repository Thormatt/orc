"""Static assets (trace.css, trace.js) inlined into generated reports.

A real package (not bare data files) so importlib.resources can locate the
assets from a wheel, a zipapp, or an editable install alike. trace.css and
trace.js are verbatim copies of site/trace.css and site/trace.js — the report
artifact and the public site must render traces identically.
"""
