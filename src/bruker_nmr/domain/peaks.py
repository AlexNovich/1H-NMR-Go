"""Manual peaks, independent visibility and bounded nearest-extremum selection."""
from dataclasses import dataclass, replace

import numpy as np

from bruker_nmr.domain.integrals import Peak

MAX_PEAKS = 20000


@dataclass(frozen=True)
class PeakState:
    records: tuple[Peak, ...] = ()
    markers_visible: bool = True
    label_mode: str = 'auto'

    def __post_init__(self):
        if (len(self.records) > MAX_PEAKS or len({p.id for p in self.records}) != len(self.records)
                or self.label_mode not in ('auto', 'all', 'hidden')):
            raise ValueError('Invalid peak count, identities or label mode')
        if any(not np.isfinite([p.ppm, p.intensity]).all() for p in self.records):
            raise ValueError('Peak coordinates and amplitudes must be finite')

    def by_id(self, id):
        peak = next((p for p in self.records if p.id == id), None)
        if peak is None:
            raise ValueError('Select a peak')
        return peak

    def changed(self, peak):
        self.by_id(peak.id)
        return replace(self, records=tuple(peak if p.id == peak.id else p for p in self.records))

    def review(self):
        return replace(self, records=tuple(replace(p, needs_review=True) for p in self.records))


def nearest_extremum(ppm, real, position, radius):
    if (not np.isfinite([position, radius]).all() or radius <= 0
            or not min(ppm[0], ppm[-1]) <= position <= max(ppm[0], ppm[-1])):
        raise ValueError('Peak position must be within the measured axis; choose a positive search radius')
    # Search full-resolution local data; display decimation never picks a peak.
    step = abs(float(ppm[-1]-ppm[0]))/(len(ppm)-1)
    index = int(np.rint((position-ppm[0])/(ppm[1]-ppm[0])))
    reach = max(2, int(np.ceil(radius/step)))
    first, last = max(1, index-reach), min(len(real)-1, index+reach+1)
    y, left, right = real[first:last], real[first-1:last-1], real[first+1:last+1]
    extrema = (((y >= left) & (y > right)) | ((y > left) & (y >= right)) |
               ((y <= left) & (y < right)) | ((y < left) & (y <= right)))
    candidates = np.flatnonzero(extrema)+first
    candidates = candidates[np.abs(ppm[candidates]-position) <= radius]
    if not len(candidates):
        raise ValueError('No local extremum in the search window; zoom or enter a marker position')
    return int(min(candidates, key=lambda k: (abs(ppm[k]-position), -abs(real[k]), k)))


def rectangle_maxima(ppm, real, low, high, bottom, top):
    """Select full-grid local maxima, using one midpoint for each flat top."""
    from scipy.signal import find_peaks

    indices, _ = find_peaks(real)
    x, y = ppm[indices], real[indices]
    return indices[(x >= low) & (x <= high) & (y >= bottom) & (y <= top)]
