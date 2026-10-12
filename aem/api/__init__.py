"""HTTP layer: routing, dependency wiring, and error translation only.

No accounting logic lives here. Routers validate with the models in
``aem.schemas``, call a service, and return what it returns.
"""
