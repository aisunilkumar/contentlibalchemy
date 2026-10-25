from dataclasses import dataclass


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    critical: bool = False   # hard rule: a failure caps the score below the quality threshold
