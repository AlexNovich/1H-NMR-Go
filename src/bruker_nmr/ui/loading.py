"""Bounded I/O jobs and safe chooser records; no experimental titles in labels/logs."""
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from bruker_nmr.domain.document import DocumentData
from bruker_nmr.domain.parameters import SourceParameters
from bruker_nmr.io.errors import InputError
from bruker_nmr.io.source import Source


@dataclass(frozen=True)
class Choice:
    candidate_id: str
    experiment_number: int | None
    processing_number: int | None
    nucleus: str
    dimension: int | None
    processed_available: bool
    raw_available: bool
    issues: tuple[str, ...]
    locator: tuple[str, str | None] = field(repr=False)
    raw_points: int | None = None

    @classmethod
    def from_candidate(cls, candidate, root):
        return cls(candidate.candidate_id, candidate.experiment_number, candidate.processing_number,
                   candidate.nucleus, candidate.dimension, candidate.processed_available,
                   candidate.raw_available, candidate.issues,
                   (candidate.experiment.relative_to(root).as_posix(),
                    candidate.processing.relative_to(root).as_posix() if candidate.processing else None),
                   candidate.raw_points)


def discover_choices(path):
    with Source(path) as source:
        return tuple(Choice.from_candidate(c, source.root) for c in source.discover())


def load_choice(path, choice):
    with Source(path) as source:
        selected = next((c for c in source.discover() if c.candidate_id == choice.candidate_id), None)
        if selected is None or Choice.from_candidate(selected, source.root) != choice:
            raise InputError("source_changed_reopen")
        return replace(DocumentData.from_operator(source.read_processed(selected.candidate_id), selected),
                       raw_source=(Path(path).absolute(), choice))


class JobSignals(QObject):
    finished = Signal(object, object, object, object)


class OpenJob(QRunnable):
    def __init__(self, ticket, path, choice=None):
        super().__init__()
        self.ticket = ticket
        self.path = Path(path)
        self.choice = choice
        self.cancelled = threading.Event()
        self.signals = JobSignals()

    def run(self):
        result = error = None
        try:
            if not self.cancelled.is_set():
                result = discover_choices(self.path) if self.choice is None else load_choice(
                    self.path, self.choice)
        except InputError as failure:
            error = failure.code
        except Exception:
            error = "source_read_failed"
        if self.cancelled.is_set():
            result = error = None
        self.signals.finished.emit(self, self.ticket, result, error)


class RawJob(OpenJob):
    def __init__(self, ticket, path, choice, recipe, *, transition=False, cached=None, header="", source_parameters=None):
        super().__init__(ticket, path, choice)
        self.recipe, self.transition, self.cached = recipe, transition, cached
        self.header = header
        self.source_parameters = source_parameters

    def run(self):
        from bruker_nmr.io.fid import read_fid
        from bruker_nmr.processing.fid import automatic_si, process
        result = error = None
        try:
            if self.cached is not None:
                fid, params = self.cached
            else:
                with Source(self.path) as source:
                    selected = next((c for c in source.discover() if c.candidate_id == self.choice.candidate_id), None)
                    if selected is None or Choice.from_candidate(selected, source.root) != self.choice:
                        raise InputError('source_changed_reopen')
                    fid, params = read_fid(source, selected)
                    from bruker_nmr.io.print_header import read_header
                    self.header = read_header(source,selected)
                    from bruker_nmr.io.reader import _processed
                    processed = (_processed(selected.processing, source.limits, set(source.files))
                                 if selected.processed_available else {})
                    self.source_parameters = SourceParameters.capture(params, processed)
            recipe = self.recipe
            if recipe.si == 0:
                recipe = replace(recipe, si=automatic_si(len(fid)))
            output = process(fid, params, recipe, self.cancelled)
            result = DocumentData(output.ppm, output.real, 'raw', output, True,
                                  self.choice.experiment_number, self.choice.processing_number,
                                  (self.path, self.choice), print_header=self.header,
                                  source_parameters=self.source_parameters or SourceParameters.capture(params))
        except InputError as failure:
            error = failure.code
        except Exception:
            error = 'fid_processing_failed'
        if self.cancelled.is_set():
            result = error = None
        self.signals.finished.emit(self, self.ticket, result, error)

class SaveJob(QRunnable):
    def __init__(self, path, locator, state, revision, destination):
        super().__init__()
        self.path = path
        self.locator = locator
        self.state = state
        self.revision = revision
        self.destination = destination
        self.signals = JobSignals()

    def run(self):
        from bruker_nmr.io.save_markup import save_markup

        try:
            result = save_markup(self.path, self.locator, self.state, self.revision, self.destination)
        except InputError as failure:
            self.signals.finished.emit(self, None, None, failure.code)
        except Exception:
            self.signals.finished.emit(self, None, None, 'markup_save_failed')
        else:
            self.signals.finished.emit(self, None, result, None)
