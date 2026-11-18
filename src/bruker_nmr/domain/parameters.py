"""Immutable local source parameters, separate from current processing recipes."""

from dataclasses import dataclass, field


def freeze(value):
    if isinstance(value, (tuple, list)):
        return tuple(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class SourceParameters:
    acquisition: tuple = field(default=(), repr=False)
    processing: tuple = field(default=(), repr=False)

    @classmethod
    def capture(cls, acquisition, processing=None):
        return cls(
            tuple((k, freeze(v)) for k, v in sorted(acquisition.items())),
            tuple((k, freeze(v)) for k, v in sorted((processing or {}).items())),
        )
