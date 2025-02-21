#!/usr/bin/env python

# Program iq_dsp.py - Compute spectrum from I/Q data.
# Copyright (C) 2013-2014 Martin Ewing
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Contact the author by e-mail: aa6e@arrl.net
#
# Part of the iq.py program.

# HISTORY
# 01-04-2014 Initial Release

import math
import numpy as np
import numpy.fft as fft  # FFT module from NumPy


class DSP(object):
    """Digital Signal Processing class to compute power spectrum from I/Q data.

    This class processes complex I/Q (in-phase/quadrature) samples to produce
    a log power spectrum in dB, with noise rejection and windowing.

    Args:
        opt: Options object with attributes like size (FFT size), buffers (number of buffers),
             and pulse (threshold multiplier for pulse rejection)
    """

    def __init__(self, opt):
        """Initialize DSP object with options and precompute window function."""
        self.opt = opt  # Store options object for configuration
        self.stats = list()  # Placeholder for statistics (not used in this code)

        # Calibration factor: dB adjustment for full-scale 16-bit input
        # For 16-bit signed int, max amplitude is 2^15, scaled by FFT size
        self.db_adjust = 20. * math.log10(self.opt.size * 2 ** 15)

        self.rejected_count = 0  # Counter for rejected buffers due to noise pulses
        self.led_clip_ct = 0  # Counter for clipping indicator (e.g., for GUI LED)

        # Precompute Hanning window to reduce spectral leakage
        # Hanning: 0.5 * (1 - cos(2πi / (N-1))) for i = 0 to N-1
        self.w = np.empty(self.opt.size)  # Array for window coefficients
        for i in range(self.opt.size):
            self.w[i] = 0.5 * (1. - math.cos((2 * math.pi * i) / (self.opt.size - 1)))

        return  # Explicit return not needed, kept for original code fidelity

    def get_log_power_spectrum(self, data):
        """Compute the log power spectrum from a chunk of I/Q data.

        Processes multiple buffers within the data, rejects noise pulses,
        applies a window function, computes FFT, and returns the log power spectrum.

        Args:
            data (np.array): Complex I/Q samples (length >= opt.size * opt.buffers)

        Returns:
            np.array: Log power spectrum in dB, adjusted so max signal = 0 dB
        """
        size = self.opt.size  # FFT size (number of samples per buffer)
        power_spectrum = np.zeros(size)  # Initialize power spectrum accumulator

        # --- Noise Pulse Rejection ---
        # Analyze time-domain data to reject buffers with large pulses
        # Use median of absolute values from first buffer as "normal" signal level
        td_median = np.median(np.abs(data[:size]))  # Median of first buffer
        td_threshold = self.opt.pulse * td_median  # Threshold for pulse detection
        nbuf_taken = 0  # Count of accepted buffers

        # Process each buffer in the chunk
        for ic in range(self.opt.buffers):
            # Extract one buffer’s worth of data
            td_segment = data[ic * size:(ic + 1) * size]

            # Remove DC offset (0 Hz spike) by subtracting mean
            td_segment = np.subtract(td_segment, np.average(td_segment))

            # Check for noise pulses by finding max absolute value
            td_max = np.amax(np.abs(td_segment))
            if td_max < td_threshold:  # Accept buffer if below threshold
                # Apply Hanning window to reduce spectral leakage
                td_segment *= self.w

                # Compute FFT to transform to frequency domain
                fd_spectrum = fft.fft(td_segment)

                # Shift FFT so 0 Hz is in the center (originally at index 0)
                fd_spectrum_rot = np.fft.fftshift(fd_spectrum)

                # Compute power spectrum: |z|^2 = z * z conjugate
                # Accumulate into power_spectrum
                power_spectrum += np.real(fd_spectrum_rot * fd_spectrum_rot.conj())
                nbuf_taken += 1
            else:  # Reject buffer if noise pulse detected
                self.rejected_count += 1  # Increment rejection counter
                self.led_clip_ct = 1  # Set clipping indicator for GUI
                # Optional debug output (commented out)
                # if DEBUG: print "REJECT! %d" % self.rejected_count

        # --- Normalize and Convert to dB ---
        if nbuf_taken > 0:
            # Average power spectrum over accepted buffers
            power_spectrum = power_spectrum / nbuf_taken
        else:
            # If all buffers rejected, return flat spectrum (1’s)
            power_spectrum = np.ones(size)

        # Convert power to dB: 10 * log10(power)
        # Note: log(0) results in -inf, which can occur if ADC fails
        log_power_spectrum = 10. * np.log10(power_spectrum)

        # Adjust so max possible signal (full-scale input) is 0 dB
        return log_power_spectrum - self.db_adjust