class ApiRejected(ValueError):
    def __init__(self, reason: str, message: str | None = None):
        super().__init__(message or reason)
        self.reason = reason


class ApiNotFound(LookupError):
    def __init__(self, *resources: str):
        super().__init__("Unknown resources: " + ", ".join(resources))
        self.resources = list(resources)


class ApiConflict(LookupError):
    def __init__(self, *resources: str):
        super().__init__("Existing resources: " + ", ".join(resources))
        self.resources = list(resources)
