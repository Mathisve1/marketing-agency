"""Service layer.

Thin coordination modules that own multi-step business operations the
Streamlit UI and the MCP server BOTH need to perform. Keeps the view
layer (ui/app.py) and the protocol layer (mcp_server.py) free of
duplicated SQL transitions and graph-resume bookkeeping.

Intentionally minimal: only operations that have at least two callers
or that compose multiple ClientContext / graph operations belong here.
Single-call wrappers around ClientContext methods do NOT belong here.
"""
