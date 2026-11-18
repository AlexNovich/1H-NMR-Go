"""Captured immutable print state; private header text is transient and repr-hidden."""

import unicodedata
from dataclasses import dataclass, field

from bruker_nmr.domain.document import DocumentState
from bruker_nmr.domain.integrals import integral_snapshot


@dataclass(frozen=True)
class ViewSnapshot:
    state: DocumentState = field(repr=False)
    caption: str = field(default="",repr=False)
    selected_peak: str | None = None

    integral_bands: int = 1
    show_grid: bool = False

    def __post_init__(self):
        if self.integral_bands != 1:
            raise ValueError("Use one aligned integral row; zoom to fewer ranges.")
        if (
            not isinstance(self.caption, str)
            or len(self.caption) > 1024
            or any(unicodedata.category(c).startswith("C") and c != "\n" for c in self.caption)
        ):
            raise ValueError("Use a caption of at most 1024 printable characters.")
        if self.selected_peak is not None:
            self.state.peaks.by_id(self.selected_peak)

    @property
    def integrals(self):
        return integral_snapshot(self.state.integrals, self.state.grid)
