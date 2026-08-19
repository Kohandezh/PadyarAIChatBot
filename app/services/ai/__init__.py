"""Padyar AI — the only interface the rest of the application uses for AI.

Business code calls into this package. It never imports a vendor SDK, never
learns a provider's request shape, and never chooses a provider or model.
Everything below this line is the provider layer's problem.
"""
