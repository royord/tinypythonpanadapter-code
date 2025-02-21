#!/usr/bin/env python

# Program iq_wf.py - Create waterfall spectrum display.
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
# 01-04-2014 Initial release

import math
import sys

import numpy as np
import pygame as pg


def palette_color(palette, val, vmin0, vmax0):
    """Translate a data value into a color using different palette methods.

    Args:
        palette (int): Color scheme selection (1 or 2)
        val (float): Data value to convert to color
        vmin0 (float): Minimum value for color scale
        vmax0 (float): Maximum value for color scale

    Returns:
        tuple: RGB color values (r, g, b) between 0-255
    """
    # Normalize value to 0-1 range, then scale to 0-2
    f = (float(val) - vmin0) / (vmax0 - vmin0)  # btw 0 and 1.0
    f *= 2
    f = min(1., max(0., f))  # Clamp value between 0 and 1

    if palette == 1:  # Simple RGB stepped palette
        g, b = 0, 0
        if f < 0.333:  # Red phase
            r = int(f * 255 * 3)
        elif f < 0.666:  # Yellow phase (red + green)
            r = 200
            g = int((f - .333) * 255 * 3)
        else:  # White phase (red + green + blue)
            r = 200
            g = 200
            b = int((f - .666) * 255 * 3)
    elif palette == 2:  # Continuous rainbow palette
        bright = min(1.0, f + 0.15)  # Brightness adjustment
        tpi = 2 * math.pi
        # Use cosine waves with phase shifts for smooth color transitions
        r = bright * 128 * (1.0 + math.cos(tpi * f))
        g = bright * 128 * (1.0 + math.cos(tpi * f + tpi / 3))
        b = bright * 128 * (1.0 + math.cos(tpi * f + 2 * tpi / 3))
    else:
        print("Invalid palette requested!")
        sys.exit()

    # Ensure color values stay within valid RGB range (0-255)
    return max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))


class Wf(object):
    """Creates and manages a waterfall spectrum display showing power vs frequency and time.

    Attributes:
        opt: Options object containing waterfall_palette setting
        vmin: Minimum data value for color scaling
        vmax: Maximum data value for color scaling
        nsteps: Number of discrete color steps
        pxsz: Pixel size (width, height) for each data point
    """

    def __init__(self, opt, vmin, vmax, nsteps, pxsz):
        """Initialize waterfall display parameters and pre-calculate color palette."""
        self.opt = opt
        self.vmin = vmin
        self.vmin_rst = vmin  # Store reset value
        self.vmax = vmax
        self.vmax_rst = vmax  # Store reset value
        self.nsteps = nsteps
        self.pixel_size = pxsz
        self.firstcalc = True  # Flag for initial calculation
        self.initialize_palette()

    def initialize_palette(self):
        """Create a list of pre-rendered surfaces for each color step."""
        self.pixels = list()
        for istep in range(self.nsteps):
            # Create a new surface for each color step
            ps = pg.Surface(self.pixel_size)
            # Calculate corresponding data value for this step
            val = float(istep) * (self.vmax - self.vmin) / self.nsteps + self.vmin
            # Get RGB color for this value
            color = palette_color(self.opt.waterfall_palette, val, self.vmin, self.vmax)
            ps.fill(color)  # Fill surface with color
            self.pixels.append(ps)

    def set_range(self, vmin, vmax):
        """Update the data range and regenerate the color palette."""
        self.vmin = vmin
        self.vmax = vmax
        self.initialize_palette()

    def reset_range(self):
        """Restore original data range and regenerate palette."""
        self.vmin = self.vmin_rst
        self.vmax = self.vmax_rst
        self.initialize_palette()
        return self.vmin, self.vmax

    def calculate(self, datalist, nsum, surface):
        """Update and render the waterfall display.

        Args:
            datalist (np.array): Input spectral data
            nsum (int): Number of spectra to accumulate before updating
            surface: Pygame surface to draw on
        """
        if self.firstcalc:  # Initial setup
            self.datasize = len(datalist)  # Store data length
            self.wfacc = np.zeros(self.datasize)  # Accumulator array
            self.dx = float(surface.get_width()) / self.datasize  # Horizontal pixel spacing
            self.wfcount = 0
            self.firstcalc = False

        self.wfcount += 1
        self.wfacc += datalist  # Add new data to accumulator

        if self.wfcount % nsum != 0:  # Wait for nsum spectra before updating
            return

        # Shift existing waterfall down by one row
        surface.blit(surface, (0, self.pixel_size[1]))

        # Draw new row
        for ix in range(self.datasize):
            v = datalist[ix]  # Get data value (in dB)
            # Convert to palette index
            vi = int(self.nsteps * (v - self.vmin) / (self.vmax - self.vmin))
            vi = max(0, min(vi, self.nsteps - 1))  # Clamp to valid range
            px_surf = self.pixels[vi]  # Get pre-rendered color surface
            x = int(ix * self.dx)  # Calculate x position
            surface.blit(px_surf, (x, 0))  # Draw pixel

        # Reset for next accumulation cycle
        self.wfcount = 0
        self.wfacc.fill(0)