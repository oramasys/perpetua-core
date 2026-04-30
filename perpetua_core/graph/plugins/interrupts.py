"""HITL interrupt plugin — raise Interrupt() from any node to pause the graph."""


class Interrupt(Exception):
    def __init__(self, prompt: str, payload: dict | None = None):
        self.prompt = prompt
        self.payload = payload or {}
        super().__init__(prompt)
