"""Recoverable inference failures that must never be typed as recognition text."""


class RecognitionFailure(RuntimeError):
    pass
