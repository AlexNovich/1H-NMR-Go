"""Independent synthetic demonstration, unrelated to experimental inputs."""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DemoSpectrum:
    ppm: np.ndarray
    amplitude: np.ndarray


def make_demo(points: int = 8192) -> DemoSpectrum:
    if not isinstance(points, int) or isinstance(points, bool) or not 256 <= points <= 65536:
        raise ValueError("Demo point count must be an integer from 256 to 65536")
    ppm = np.linspace(-1.0, 12.0, points)
    amplitude = np.zeros(points)
    # Independent Lorentzian parameters: center (ppm), height, half-width (ppm).
    for center, height, width in [(1.2, 0.65, 0.025), (3.6, 1.0, 0.035),
                                  (7.1, 0.8, 0.020), (7.3, 0.55, 0.020)]:
        amplitude += height * width**2 / ((ppm - center)**2 + width**2)
    ppm.flags.writeable = False
    amplitude.flags.writeable = False
    return DemoSpectrum(ppm, amplitude)
