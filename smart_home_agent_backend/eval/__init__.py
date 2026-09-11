"""Offline, between-sessions evaluation utilities (Phase 10).

Kept entirely out of the live LangGraph runtime: these read the persisted
session artifacts (logs/chats/*.json, logs/sessions.jsonl, and — once Phase 8
lands — logs/evaluator.jsonl) and compute research metrics after the fact.
"""
