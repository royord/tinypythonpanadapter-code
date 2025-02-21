#!/usr/bin/env python

# Program iq.py - spectrum displays from quadrature sampled IF data.
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
# Our goal is to display a zero-centered spectrum and waterfall on small
# computers, such as the BeagleBone Black or the Raspberry Pi, 
# spanning up to +/- 48 kHz (96 kHz sampling) with input from audio card
# or +/- 1.024 MHz from RTL dongle. 
#
# We use pyaudio, pygame, and pyrtlsdr Python libraries, which depend on
# underlying C/C++ libraries PortAudio, SDL, and rtl-sdr.
#

# HISTORY
# 01-04-2014 Initial release (QST article 4/2014)
# 05-17-2014 Improvements for RPi timing, etc.
#            Add REV, skip, sp_max/min, v_max/min options
# 05-31-2014 Add Si570 freq control option (DDS chip provided in SoftRock, eg.)
#           Note: Use of Si570 requires libusb-1.0 wrapper from 
#           https://pypi.python.org/pypi/libusb1/1.2.0

# Note for directfb use (i.e. without X11/Xorg):
# User must be a member of the following Linux groups:
#   adm dialout audio video input (plus user's own group, e.g., pi)

import os
import subprocess
import sys
import threading
import time
import psutil
import math

import numpy as np
import argparse
import pygame as pg

import lib.iq_dsp as dsp
import lib.iq_opt as options
import lib.iq_wf as wf

# Some colors in PyGame style
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
RED = (255, 0, 0)
YELLOW = (192, 192, 0)
DARK_RED = (128, 0, 0)
LITE_RED = (255, 100, 100)
BGCOLOR = (255, 230, 200)
BLUE_GRAY = (100, 100, 180)
ORANGE = (255, 150, 0)
GRAY = (192, 192, 192)
# RGBA colors - with alpha
TRANS_YELLOW = (255, 255, 0, 150)

# Adjust for best graticule color depending on display gamma, resolution, etc.
GRAT_COLOR = DARK_RED  # Color of graticule (grid)
GRAT_COLOR_2 = WHITE  # Color of graticule text
TRANS_OVERLAY = TRANS_YELLOW  # for info overlay
TCOLOR2 = ORANGE  # text color on info screen

INFO_CYCLE = 8  # Display frames per help info update

opt = options.opt  # Get option object from options module

# print list of parameters to console.
print("identification:", opt.ident)
print("source        :", opt.source)
print("freq control  :", opt.control)
print("waterfall     :", opt.waterfall)
print("rev i/q       :", opt.rev_iq)
print("sample rate   :", opt.sample_rate)
print("size          :", opt.size)
print("buffers       :", opt.buffers)
print("skipping      :", opt.skip)
print("hamlib        :", opt.hamlib)
print("hamlib rigtype:", opt.hamlib_rigtype)
print("hamlib device :", opt.hamlib_device)
if opt.source == "rtl":
    print("rtl frequency :", opt.rtl_frequency)
    print("rtl gain      :", opt.rtl_gain)
if opt.control == "si570":
    print("si570 frequency :", opt.si570_frequency)
print("pulse         :", opt.pulse)
print("fullscreen    :", opt.fullscreen)
print("hamlib intvl  :", opt.hamlib_interval)
print("cpu load intvl:", opt.cpu_load_interval)
print("wf accum.     :", opt.waterfall_accumulation)
print("wf palette    :", opt.waterfall_palette)
print("sp_min, max   :", opt.sp_min, opt.sp_max)
print("v_min, max    :", opt.v_min, opt.v_max)
# print "max queue dept:", opt.max_queue
print("PCM290x lagfix:", opt.lagfix)
if opt.lcd4:
    print("LCD4 brightnes:", opt.lcd4_brightness)


def quit_all():
    """ Quit pygames and close std outputs somewhat gracefully.
        Minimize console error messages.
    """
    pg.quit()
    try:
        sys.stdout.close()
    except:
        pass
    try:
        sys.stderr.close()
    except:
        pass
    sys.exit()


class LED(object):
    """ Make an LED indicator surface in pygame environment. 
        Does not include title
    """

    def __init__(self, width):
        """ width = pixels width (& height)
            colors = dictionary with color_values and PyGame Color specs
        """
        self.surface = pg.Surface((width, width))
        self.wd2 = width / 2
        return

    def get_LED_surface(self, color):
        """ Set LED surface to requested color
            Return square surface ready to blit
        """
        self.surface.fill(BGCOLOR)
        # Always make full-size black circle with no fill.
        pg.draw.circle(self.surface, BLACK, (self.wd2, self.wd2), self.wd2, 2)
        if color is None:
            return self.surface
        # Make inset filled color circle.
        pg.draw.circle(self.surface, color, (self.wd2, self.wd2), self.wd2 - 2, 0)
        return self.surface


import pygame as pg

# Assuming BLACK is defined globally elsewhere in the codebase
BLACK = (0, 0, 0)


class Graticule(object):
    """Create a pygame surface with frequency/power (dB) grid and units.

    This class generates a grid for visualizing spectral data, with a vertical dB scale
    and a horizontal frequency scale.

    Args:
        opt: Options object containing sample_rate, sp_max, and sp_min attributes
        font: Pygame font object for rendering text labels
        h: Height of the graticule surface in pixels
        w: Width of the graticule surface in pixels
        color_l: RGB tuple specifying the color for grid lines
        color_t: RGB tuple specifying the color for text labels
    """

    def __init__(self, opt, font, h, w, color_l, color_t):
        """Initialize the graticule object with display and scale parameters."""
        self.opt = opt  # Store options object for accessing sample_rate, etc.
        self.sp_max = opt.sp_max  # Maximum dB value (default typically -20 dB)
        self.sp_min = opt.sp_min  # Minimum dB value (default typically -120 dB)
        self.font = font  # Font object for text rendering
        self.h = h  # Height of the graticule surface
        self.w = w  # Width of the graticule surface
        self.color_l = color_l  # Color for drawing grid lines
        self.color_t = color_t  # Color for rendering text labels
        self.surface = pg.Surface((self.w, self.h))  # Create pygame surface for drawing
        return  # Explicit return not needed, included for original code fidelity

    def make(self):
        """Make or re-make the graticule surface with dB and frequency scales.

        This method draws horizontal lines for dB levels and vertical lines for frequency
        ticks, along with corresponding labels.

        Returns:
            pygame.Surface: The rendered graticule surface
        """
        self.surface.fill(BLACK)  # Clear the surface with a black background

        # Calculate vertical scale: pixels per dB
        yscale = float(self.h) / (self.sp_max - self.sp_min)

        # Draw horizontal dB scale with lines every 10 dB
        for attn in range(self.sp_min, self.sp_max, 10):  # Iterate from min to max-10
            # Calculate y-position in pixel coordinates with a 3-pixel offset
            yattn = ((attn - self.sp_min) * yscale) + 3.
            yattnflip = self.h - yattn  # Invert y since screen y increases downward

            # Draw horizontal grid line across the full width
            pg.draw.line(self.surface, self.color_l, (0, yattnflip), (self.w, yattnflip))

            # Render and place dB label 12 pixels above the line, 5 pixels from left
            self.surface.blit(self.font.render("%3d" % attn, 1, self.color_t),
                              (5, yattnflip - 12))

        # Add "dB" unit label next to the last dB value (topmost label)
        ww, hh = self.font.size("%3d" % attn)  # Get width and height of last label
        self.surface.blit(self.font.render("dB", 1, self.color_t),
                          (5 + ww, yattnflip - 12))  # Position "dB" right of the number

        # --- Frequency Scale (Horizontal Axis) ---
        frq_range = float(self.opt.sample_rate) / 1000.  # Total bandwidth in kHz
        xscale = self.w / frq_range  # Pixels per kHz
        srate2 = frq_range / 2  # Half the bandwidth (for positive/negative range)

        # Determine the largest tick interval that fits within half the bandwidth
        for xtick_max in [800, 400, 200, 100, 80, 40, 20, 10]:
            if xtick_max < srate2:
                break  # Exit with the first tick value less than half bandwidth

        # Define frequency tick positions (symmetric around center)
        ticks = [-xtick_max, -xtick_max / 2, 0, xtick_max / 2, xtick_max]

        # Draw vertical frequency ticks and labels
        for offset in ticks:
            # Calculate x-position centered around the middle of the surface
            x = offset * xscale + self.w / 2
            # Draw vertical line from top to bottom
            pg.draw.line(self.surface, self.color_l, (x, 0), (x, self.h))
            # Format label: "0 kHz" for center, "+XXX" or "-XXX" for others
            fmt = "%d kHz" if offset == 0 else "%+3d"
            # Render and place label 2 pixels right of the line at the top
            self.surface.blit(self.font.render(fmt % offset, 1, self.color_t),
                              (x + 2, 0))

        return self.surface  # Return the completed graticule surface

    def set_range(self, sp_min, sp_max):
        """Set the desired range for the vertical dB scale.

        Updates the minimum and maximum dB values for the graticule.

        Args:
            sp_min (float): Minimum dB value for the scale
            sp_max (float): Maximum dB value for the scale (must be > sp_min)

        Note: 0 dB represents the maximum theoretical response for 16-bit sampling.
              Lines are fixed at 10 dB intervals.
        """
        # Validate that max is greater than min to ensure a valid scale
        if not sp_max > sp_min:
            print("Invalid dB scale setting requested!")
            quit_all()  # Exit program (assumes quit_all() is defined elsewhere)
        self.sp_max = sp_max  # Update maximum dB value
        self.sp_min = sp_min  # Update minimum dB value
        return  # No return value needed


# THREAD: Hamlib, checking Rx frequency, and changing if requested.
if opt.hamlib:
    import Hamlib

    rigfreq_request = None
    rigfreq = 7.0e6  # something reasonable to start


    def updatefreq(interval, rig):
        """ Read/set rig frequency via Hamlib.
            Interval defines repetition time (float secs)
            Return via global variable rigfreq (float kHz)
            To be run as thread.
            (All Hamlib I/O is done through this thread.)
        """
        global rigfreq, rigfreq_request
        rigfreq = float(rig.get_freq()) * 0.001  # freq in kHz
        while True:  # forever!
            # With KX3 @ 38.4 kbs, get_freq takes 100-150 ms to complete
            # If a new vfo setting is desired, we will have rigfreq_request
            # set to the new frequency, otherwise = None.
            if rigfreq_request:  # ordering of loop speeds up freq change
                if rigfreq_request != rigfreq:
                    rig.set_freq(rigfreq_request * 1000.)
                    rigfreq_request = None
            rigfreq = float(rig.get_freq()) * 0.001  # freq in kHz
            time.sleep(interval)

# THREAD: CPU load checking, monitoring cpu stats.
cpu_usage = [0., 0., 0.]


def cpu_load(interval):
    """ Check CPU user and system time usage, along with load average.
        User & system reported as fraction of wall clock time in
        global variable cpu_usage.
        Interval defines sleep time between checks (float secs).
        To be run as thread.
    """
    global cpu_usage
    times_store = np.array(os.times())
    # Will return: fraction usr time, sys time, and 1-minute load average
    # cpu_usage = [0., 0., os.getloadavg()[0]]
    cpu_usage = [0., 0., psutil.getloadavg()[0]]
    while True:
        time.sleep(interval)
        times = np.array(os.times())
        dtimes = times - times_store  # difference since last loop
        usr = None
        sys = None
        if dtimes[4] > 0 and dtimes!=None:
            usr = dtimes[0] / dtimes[4]  # fraction, 0 - 1
            sys = dtimes[1] / dtimes[4]
        times_store = times
        # cpu_usage = [usr, sys, os.getloadavg()[0]]
        cpu_usage = [usr, sys, psutil.getloadavg()[0]]

if opt.list_rigs or opt.search_rigs!=None:
    if opt.list_rigs:
        print("Listing rigs...")
        with open("hamlib_list.txt", 'r') as f:
            c = f.read()
            print(c)
        sys.exit(0)
    if opt.search_rigs:
        print(f"Searching rigs...{opt.search_rigs}")
        with open("hamlib_list.txt", 'r') as f:
            c = f.read().splitlines()
            for n, l in enumerate(c):
                if n == 0:
                    print(l)
                if opt.search_rigs in l and n > 0:
                    print(l)
            sys.exit(0)

# Screen setup parameters

if opt.lcd4:  # setup for directfb (non-X) graphics
    SCREEN_SIZE = (480, 272)  # default size for the 4" LCD (480x272)
    SCREEN_MODE = pg.FULLSCREEN
    # If we are root, we can set up LCD4 brightness.
    brightness = str(min(100, max(0, opt.lcd4_brightness)))  # validated string
    # Find path of script (same directory as iq.py) and append brightness value
    cmd = os.path.join(os.path.split(sys.argv[0])[0], "lcd4_brightness.sh") \
          + " %s" % brightness
    # (The subprocess script is a no-op if we are not root.)
    subprocess.call(cmd, shell=True)  # invoke shell script
else:
    SCREEN_MODE = pg.FULLSCREEN if opt.fullscreen else 0
    SCREEN_SIZE = (640, 512) if opt.waterfall \
        else (640, 310)  # NB: graphics may not scale well
WF_LINES = 50  # How many lines to use in the waterfall

# Initialize pygame (pg)
# We should not use pg.init(), because we don't want pg audio functions.
pg.display.init()
pg.font.init()

# Define the main window surface
surf_main = pg.display.set_mode(SCREEN_SIZE, SCREEN_MODE)
w_main = surf_main.get_width()

# derived parameters
w_spectra = w_main - 10  # Allow a small margin, left and right
w_middle = w_spectra / 2  # mid point of spectrum
x_spectra = (w_main - w_spectra) / 2.0  # x coord. of spectrum on screen

h_2d = 2 * SCREEN_SIZE[1] / 3 if opt.waterfall \
    else SCREEN_SIZE[1]  # height of 2d spectrum display
h_2d -= 25  # compensate for LCD4 overscan?
y_2d = 20.  # y position of 2d disp. (screen top = 0)

# NB: transform size must be <= w_spectra.  I.e., need at least one
# pixel of width per data point.  Otherwise, waterfall won't work, etc.
if opt.size > w_spectra:
    for n in [1024, 512, 256, 128]:
        if n <= w_spectra:
            print("*** Size was reset from %d to %d." % (opt.size, n))
            opt.size = n  # Force size to be 2**k (ok, reasonable choice?)
            break
chunk_size = opt.buffers * opt.size  # No. samples per chunk (pyaudio callback)
chunk_time = float(chunk_size) / opt.sample_rate

myDSP = dsp.DSP(opt)  # Establish DSP logic

# Surface for the 2d spectrum
surf_2d = pg.Surface((w_spectra, h_2d))  # Initialized to black
surf_2d_graticule = pg.Surface((w_spectra, h_2d))  # to hold fixed graticule

# define two LED widgets
led_urun = LED(10)
led_clip = LED(10)

# Waterfall geometry
h_wf = SCREEN_SIZE[1] / 3  # Height of waterfall (3d spectrum)
y_wf = y_2d + h_2d  # Position just below 2d surface

# Surface for waterfall (3d) spectrum
surf_wf = pg.Surface((w_spectra, h_wf))

pg.display.set_caption(opt.ident)  # Title for main window

# Establish fonts for screen text.
lgfont = pg.font.SysFont('sans', 16)
lgfont_ht = lgfont.get_linesize()  # text height
medfont = pg.font.SysFont('sans', 12)
medfont_ht = medfont.get_linesize()
smfont = pg.font.SysFont('mono', 9)
smfont_ht = smfont.get_linesize()

# Define the size of a unit pixel in the waterfall
wf_pixel_size = (w_spectra / opt.size, h_wf / WF_LINES)

# min, max dB for wf palette
v_min, v_max = opt.v_min, opt.v_max  # lower/higher end (dB)
nsteps = 50  # number of distinct colors

if opt.waterfall:
    # Instantiate the waterfall and palette data
    mywf = wf.Wf(opt, v_min, v_max, nsteps, wf_pixel_size)

if (opt.control == "si570") and opt.hamlib:
    print("Warning: Hamlib requested with si570.  Si570 wins! No Hamlib.")
if opt.hamlib and (opt.control != "si570"):
    import Hamlib

    # start up Hamlib rig connection
    Hamlib.rig_set_debug(Hamlib.RIG_DEBUG_NONE)
    rig = Hamlib.Rig(opt.hamlib_rigtype)
    rig.set_conf("rig_pathname", opt.hamlib_device)
    rig.set_conf("retry", "5")
    rig.open()

    # Create thread for Hamlib freq. checking.  
    # Helps to even out the loop timing, maybe.
    hl_thread = threading.Thread(target=updatefreq,
                                 args=(opt.hamlib_interval, rig))
    hl_thread.daemon = True
    hl_thread.start()
    print("Hamlib thread started.")
else:
    print("Hamlib not requested.")

# Create thread for cpu load monitor
lm_thread = threading.Thread(target=cpu_load, args=(opt.cpu_load_interval,))
lm_thread.daemon = True
lm_thread.start()
print("CPU monitor thread started.")

# Create graticule providing 2d graph calibration.
mygraticule = Graticule(opt, smfont, h_2d, w_spectra, GRAT_COLOR, GRAT_COLOR_2)
sp_min, sp_max = sp_min_def, sp_max_def = opt.sp_min, opt.sp_max
mygraticule.set_range(sp_min, sp_max)
surf_2d_graticule = mygraticule.make()

# Pre-formatx "static" text items to save time in real-time loop
# Useful operating parameters
parms_msg = "Fs = %d Hz; Res. = %.1f Hz;" \
            " chans = %d; width = %d px; acc = %.3f sec" % \
            (opt.sample_rate, float(opt.sample_rate) / opt.size, opt.size, w_spectra,
             float(opt.size * opt.buffers) / opt.sample_rate)
wparms, hparms = medfont.size(parms_msg)
parms_matter = pg.Surface((wparms, hparms))
parms_matter.blit(medfont.render(parms_msg, 1, TCOLOR2), (0, 0))

print("Update interval = %.2f ms" % float(1000 * chunk_time))

# Initialize input mode, RTL or AF
# This starts the input stream, so place it close to start of main loop.
if opt.source == "rtl":  # input from RTL dongle (and freq control)
    import lib.iq_rtl as rtl

    dataIn = rtl.RtlIn(opt)
elif opt.source == 'audio':  # input from audio card
    import lib.iq_af as af

    mainqueueLock = af.queueLock  # queue and lock only for soundcard
    dataIn = af.DataInput(opt)
else:
    print("unrecognized mode")
    quit_all()

if opt.control == "si570":
    import lib.si570control as si570control

    mysi570 = si570control.Si570control()
    mysi570.setFreq(opt.si570_frequency / 1000.)  # Set starting freq.

# ** MAIN PROGRAM LOOP **

run_flag = True  # Set to False to pause for help screen or other overlays
info_phase = 1  # > 0 shows info overlay
info_counter = 0  # Counter for info display timing
tloop = 0.  # Loop timing variable
t_last_data = 0.  # Timestamp of last data update
nframe = 0  # Frame counter
t_frame0 = time.time()  # Start time for frame rate calculation
led_overflow_ct = 0  # Overflow LED counter
startqueue = True  # Flag to start data queue


def get_gradient_color(y, y_min, y_max):
    """Map a y-value to a color gradient from green to yellow to red.

    Args:
        y (float): Current y-value (screen coordinate, higher values = lower on screen)
        y_min (float): Minimum y-value (top of graph)
        y_max (float): Maximum y-value (bottom of graph)

    Returns:
        tuple: RGB color (green at y_min, yellow in middle, red at y_max)
    """
    # Normalize y to 0-1 range (0 at top, 1 at bottom)
    f = (y - y_min) / (y_max - y_min) if y_max > y_min else 0
    f = max(0, min(1, f))  # Clamp to 0-1

    if f < 0.25:  # Red to Orange (0 to 0.25)
        t = f / 0.25  # Normalize within this segment (0 to 1)
        r = 255  # Red stays at max
        g = int(165 * t)  # Green increases from 0 to 165
        b = 0  # No blue
    elif f < 0.5:  # Orange to Yellow (0.25 to 0.5)
        t = (f - 0.25) / 0.25
        r = 255  # Red stays at max
        g = int(165 + 90 * t)  # Green increases from 165 to 255
        b = 0  # No blue
    elif f < 0.75:  # Yellow to Green (0.5 to 0.75)
        t = (f - 0.5) / 0.25
        r = int(255 * (1 - t))  # Red decreases from 255 to 0
        g = 255  # Green stays at max
        b = 0  # No blue
    else:  # Green to Blue (0.75 to 1)
        t = (f - 0.75) / 0.25
        r = 0  # Red stays at 0
        g = int(255 * (1 - t))  # Green decreases from 255 to 0
        b = int(255 * t)  # Blue increases from 0 to 255

    return (r, g, b)


def palette_color(palette, val, vmin0, vmax0):
    """Translate a data value into a color using different palette methods.

    Args:
        palette (int): 1 for stepped RGB (red-yellow-white), 2 for rainbow
        val (float): Value to map (e.g., y-position or dB)
        vmin0 (float): Minimum value for scaling
        vmax0 (float): Maximum value for scaling

    Returns:
        tuple: RGB color values (0-255)
    """
    # Normalize value to 0-1 range, then scale to 0-2
    f = (float(val) - vmin0) / (vmax0 - vmin0)  # Between 0 and 1.0
    f *= 2  # Scale to 0-2
    f = min(1., max(0., f))  # Clamp to 0-1 for palette range
    if palette == 0:
        # return 255, 255, 255
        return 0,0,0
    if palette == 1:  # Simple RGB stepped palette
        g, b = 0, 0
        if f < 0.333:  # Red phase
            r = int(f * 255 * 3)  # Red ramps up
        elif f < 0.666:  # Yellow phase (red + green)
            r = 200  # Red fixed
            g = int((f - .333) * 255 * 3)  # Green ramps up
        else:  # White phase (red + green + blue)
            r = 200  # Red fixed
            g = 200  # Green fixed
            b = int((f - .666) * 255 * 3)  # Blue ramps up
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


while True:
    nframe += 1  # Increment frame counter for tracking loop iterations

    # Reconstruct the main screen each loop
    surf_main.fill(BGCOLOR)  # Clear main surface with background color

    # Process a chunk of audio/RTL data, compute log power spectrum, and update display
    # --- Display Receiver Center Frequency ---
    showfreq = True
    if opt.control == "si570":
        msg = "%.3f kHz" % (mysi570.getFreqByValue() * 1000.)  # Si570 frequency
    elif opt.hamlib:
        msg = "%.3f kHz" % rigfreq  # Hamlib frequency
    elif opt.control == 'rtl':
        msg = "%.3f MHz" % (dataIn.rtl.get_center_freq() / 1.e6)  # RTL-SDR frequency
    else:
        showfreq = False

    if showfreq:
        ww, hh = lgfont.size(msg)  # Text dimensions
        # Center frequency text above 2D display
        surf_main.blit(lgfont.render(msg, 1, BLACK, BGCOLOR),
                       (w_middle + x_spectra - ww / 2, y_2d - hh))

    # --- Audio Buffer Status Indicators ---
    if opt.source == 'audio':
        # Underrun indicator
        if af.led_underrun_ct > 0:
            sled = led_urun.get_LED_surface(RED)
            af.led_underrun_ct -= 1
        else:
            sled = led_urun.get_LED_surface(None)
        msg = "Buffer underrun"
        ww, hh = medfont.size(msg)
        ww1 = SCREEN_SIZE[0] - ww - 10
        surf_main.blit(medfont.render(msg, 1, BLACK, BGCOLOR), (ww1, y_2d - hh))
        surf_main.blit(sled, (ww1 - 15, y_2d - hh))

        # Clipping indicator
        if myDSP.led_clip_ct > 0:
            sled = led_clip.get_LED_surface(RED)
            myDSP.led_clip_ct -= 1
        else:
            sled = led_clip.get_LED_surface(None)
        msg = "Pulse clip"
        ww, hh = medfont.size(msg)
        surf_main.blit(medfont.render(msg, 1, BLACK, BGCOLOR), (25, y_2d - hh))
        surf_main.blit(sled, (10, y_2d - hh))

    # --- Data Acquisition ---
    if opt.source == 'rtl':
        iq_data_cmplx = dataIn.read_samples(chunk_size)  # Read RTL-SDR samples
        if opt.rev_iq:
            iq_data_cmplx = np.imag(iq_data_cmplx) + 1j * np.real(iq_data_cmplx)
        time.sleep(0.05)  # Delay for slower PCs
        stats = [0, 0]  # Placeholder stats
    else:  # Audio input
        my_in_data_s = dataIn.get_queued_data()  # Get queued audio data
        iq_local = np.frombuffer(my_in_data_s, dtype=np.int16).astype('float32')
        re_d = np.array(iq_local[1::2])  # I (right channel)
        im_d = np.array(iq_local[0::2])  # Q (left channel)
        if opt.lagfix:
            im_d = np.roll(im_d, 1)  # Fix PCM290x lag
        stats = [int(np.amax(re_d)), int(np.amax(im_d))]
        if opt.rev_iq:
            iq_data_cmplx = np.array(im_d + re_d * 1j)
        else:
            iq_data_cmplx = np.array(re_d + im_d * 1j)

    # --- Compute Spectrum ---
    sp_log = myDSP.get_log_power_spectrum(iq_data_cmplx)  # Get log power spectrum
    if opt.source == 'rtl':
        sp_log += 60  # Boost RTL spectrum levels

    # --- Draw 2D Spectrum Graph ---
    yscale = float(h_2d) / (sp_max - sp_min)  # Pixels per dB
    surf_2d.blit(surf_2d_graticule, (0, 0))  # Reset with graticule background

    # Scale spectrum to screen coordinates
    sp_scaled = ((sp_log - sp_min) * yscale) + 3.
    ylist = list(sp_scaled)
    ylist = [h_2d - x for x in ylist]  # Flip y for screen (lower dB = higher y)
    lylist = len(ylist)
    xlist = [x * w_spectra / lylist for x in range(lylist)]  # X coordinates

    # Color pixels under the spectrum curve using palette_color
    palette = 0  # Use stepped RGB palette (can change to 2 for rainbow)
    for i in range(lylist):
        x = int(xlist[i])  # Current x-position
        y_top = int(ylist[i])  # Top of the curve (lower y = higher power)
        y_bottom = h_2d  # Bottom of the graph

        # Draw vertical line from bottom to curve with reversed palette
        for y in range(int(y_top), int(y_bottom)):
            # Reverse the mapping: lower y (higher power) -> lower value in palette
            val = h_2d - y  # Invert y to map power (higher power = lower y)
            color = palette_color(palette, val, 0, h_2d)  # White (top) to red (bottom)
            surf_2d.set_at((x, y), color)

    # Optionally draw the spectrum line on top (white outline)
    pg.draw.lines(surf_2d, WHITE, False, list(zip(xlist, ylist)), 3)

    # Blit 2D spectrum onto main surface
    surf_main.blit(surf_2d, (x_spectra, y_2d))

    if opt.waterfall:
        # Calculate the new Waterfall line and blit it to main surface
        nsum = opt.waterfall_accumulation  # 2d spectra per wf line
        mywf.calculate(sp_log, nsum, surf_wf)
        surf_main.blit(surf_wf, (x_spectra, y_wf + 1))
    if opt.disable_onscreen_help:
        info_phase = 0
    if info_phase > 0:
        # Assemble and show semi-transparent overlay info screen
        # This takes cpu time, so don't recompute it too often. (DSP & graphics
        # are still running.)
        info_counter = (info_counter + 1) % INFO_CYCLE
        if info_counter == 1:
            # First time through, and every INFO_CYCLE-th time thereafter.
            # Some button labels to show at right of LCD4 window
            # Add labels for LCD4 buttons.
            place_buttons = False
            if opt.lcd4 or (w_main == 480):
                place_buttons = True
                button_names = [" LT", " RT ", " UP", " DN", "ENT"]
                button_vloc = [20, 70, 120, 170, 220]
                button_surfs = []
                for bb in button_names:
                    button_surfs.append(medfont.render(bb, 1, WHITE, BLACK))

            # Help info will be placed toward top of window.
            # Info comes in 4 phases (0 - 3), cycle among them with <return>
            if info_phase == 1:
                lines = ["KEYBOARD CONTROLS:",
                         "(R) Reset display; (Q) Quit program",
                         "Change upper plot dB limit:  (U) increase; (u) decrease",
                         "Change lower plot dB limit:  (L) increase; (l) decrease"
                        ]
                if opt.waterfall:
                    lines.append("Change WF palette upper limit: (B) increase; (b) decrease")
                    lines.append("Change WF palette lower limit: (D) increase; (d) decrease")
                if opt.control != "none":
                    lines.append("Change rcvr freq: (rt arrow) increase; (lt arrow) decrease")
                    lines.append("   Use SHIFT for bigger steps")
                lines.append("RETURN - Cycle to next Help screen")
            elif info_phase == 2:
                lines = ["SPECTRUM ADJUSTMENTS:",
                         "UP - upper screen level +10 dB",
                         "DOWN - upper screen level -10 dB",
                         "RIGHT - lower screen level +10 dB",
                         "LEFT - lower screen level -10 dB",
                         "RETURN - Cycle to next Help screen"]
            elif info_phase == 3:
                lines = ["WATERFALL PALETTE ADJUSTMENTS:",
                         "UP - upper threshold INCREASE",
                         "DOWN - upper threshold DECREASE",
                         "RIGHT - lower threshold INCREASE",
                         "LEFT - lower threshold DECREASE",
                         "RETURN - Cycle Help screen OFF"]
            else:
                lines = ["Invalid info phase!"]  # we should never arrive here.
                info_phase = 0
            wh = (0, 0)
            for il in lines:  # Find max line width, height
                wh = list(map(max, wh, medfont.size(il)))
            help_matter = pg.Surface((wh[0] + 24, len(lines) * wh[1] + 15))
            for ix, x in enumerate(lines):
                help_matter.blit(medfont.render(x, 1, TCOLOR2), (20, ix * wh[1] + 15))

            # "Live" info is placed toward bottom of window...
            # Width of this surface is a guess. (It should be computed.)
            live_surface = pg.Surface((430, 48), 0)
            # give live sp_min, sp_max, v_min, v_max
            msg = "dB scale min= %d, max= %d" % (sp_min, sp_max)
            live_surface.blit(medfont.render(msg, 1, TCOLOR2), (10, 0))
            if opt.waterfall:
                # Palette adjustments info
                msg = "WF palette min= %d, max= %d" % (v_min, v_max)
                live_surface.blit(medfont.render(msg, 1, TCOLOR2), (200, 0))
            live_surface.blit(parms_matter, (10, 16))
            if opt.source == 'audio':
                msg = "ADC max I:%05d; Q:%05d" % (stats[0], stats[1])
                live_surface.blit(medfont.render(msg, 1, TCOLOR2), (10, 32))
            # Show the live cpu load information from cpu_usage thread.
            msg = "Load usr=%3.2f; sys=%3.2f; load avg=%.2f" % \
                  (cpu_usage[0], cpu_usage[1], cpu_usage[2])
            live_surface.blit(medfont.render(msg, 1, TCOLOR2), (200, 32))
        # Blit newly formatted -- or old -- screen to main surface.
        if place_buttons:  # Do we have rt hand buttons to place?
            for ix, bb in enumerate(button_surfs):
                surf_main.blit(bb, (449, button_vloc[ix]))
        surf_main.blit(help_matter, (20, 20))
        surf_main.blit(live_surface, (20, SCREEN_SIZE[1] - 60))

    # Check for pygame events - keyboard, etc.
    # Note: A key press is not recorded as a PyGame event if you are 
    # connecting via SSH.  In that case, use --sp_min/max and --v_min/max
    # command line options to set scales.

    for event in pg.event.get():
        if event.type == pg.QUIT:
            quit_all()
        elif event.type == pg.KEYDOWN:
            if info_phase <= 1:  # Normal op. (0) or help phase 1 (1)
                # We usually want left or right shift treated the same!
                shifted = event.mod & (pg.KMOD_LSHIFT | pg.KMOD_RSHIFT)
                if event.key == pg.K_q:
                    quit_all()
                elif event.key == pg.K_u:  # 'u' or 'U' - chg upper dB
                    if shifted:  # 'U' move up
                        if sp_max < 0:
                            sp_max += 10
                    else:  # 'u' move dn
                        if sp_max > -130 and sp_max > sp_min + 10:
                            sp_max -= 10
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                elif event.key == pg.K_l:  # 'l' or 'L' - chg lower dB
                    if shifted:  # 'L' move up lower dB
                        if sp_min < sp_max - 10:
                            sp_min += 10
                    else:  # 'l' move down lower dB
                        if sp_min > -140:
                            sp_min -= 10
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                elif event.key == pg.K_b:  # 'b' or 'B' - chg upper pal.
                    if shifted:
                        if v_max < -10:
                            v_max += 10
                    else:
                        if v_max > v_min + 20:
                            v_max -= 10
                    mywf.set_range(v_min, v_max)
                elif event.key == pg.K_d:  # 'd' or 'D' - chg lower pal.
                    if shifted:
                        if v_min < v_max - 20:
                            v_min += 10
                    else:
                        if v_min > -130:
                            v_min -= 10
                    mywf.set_range(v_min, v_max)
                elif event.key == pg.K_r:  # 'r' or 'R' = reset levels
                    sp_min, sp_max = sp_min_def, sp_max_def
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                    if opt.waterfall:
                        v_min, v_max = mywf.reset_range()

                # Note that LCD peripheral buttons are Right, Left, Up, Down
                # arrows and "Enter".  (Same as keyboard buttons)

                elif event.key == pg.K_RIGHT:  # right arrow + freq
                    if opt.control == 'rtl':
                        finc = 100e3 if shifted else 10e3
                        dataIn.rtl.center_freq = dataIn.rtl.get_center_freq() + finc
                    elif opt.control == 'si570':
                        finc = 1.0 if shifted else 0.1
                        mysi570.setFreqByValue(mysi570.getFreqByValue() + finc * .001)
                    elif opt.hamlib:
                        finc = 1.0 if shifted else 0.1
                        rigfreq_request = rigfreq + finc
                    else:
                        print("Rt arrow ignored, no Hamlib")
                elif event.key == pg.K_LEFT:  # left arrow - freq
                    if opt.control == 'rtl':
                        finc = -100e3 if shifted else -10e3
                        dataIn.rtl.center_freq = dataIn.rtl.get_center_freq() + finc
                    elif opt.control == 'si570':
                        finc = -1.0 if shifted else -0.1
                        mysi570.setFreqByValue(mysi570.getFreqByValue() + finc * .001)
                    elif opt.hamlib:
                        finc = -1.0 if shifted else -0.1
                        rigfreq_request = rigfreq + finc
                    else:
                        print("Lt arrow ignored, no Hamlib")
                elif event.key == pg.K_UP:
                    print("Up")
                elif event.key == pg.K_DOWN:
                    print("Down")
                elif event.key == pg.K_RETURN:
                    info_phase += 1  # Jump to phase 1 or 2 overlay
                    info_counter = 0  # (next time)

            # We can have an alternate set of keyboard (LCD button) responses
            # for each "phase" of the on-screen help system.

            elif info_phase == 2:  # Listen for info phase 2 keys
                # Showing 2d spectrum gain/offset adjustments
                # Note: making graticule is moderately slow.  
                # Do not repeat range changes too quickly!
                if event.key == pg.K_UP:
                    if sp_max < 0:
                        sp_max += 10
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                elif event.key == pg.K_DOWN:
                    if sp_max > -130 and sp_max > sp_min + 10:
                        sp_max -= 10
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                elif event.key == pg.K_RIGHT:
                    if sp_min < sp_max - 10:
                        sp_min += 10
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                elif event.key == pg.K_LEFT:
                    if sp_min > -140:
                        sp_min -= 10
                    mygraticule.set_range(sp_min, sp_max)
                    surf_2d_graticule = mygraticule.make()
                elif event.key == pg.K_RETURN:
                    info_phase = 3 if opt.waterfall \
                        else 0  # Next is phase 3 unless no WF.
                    info_counter = 0

            elif info_phase == 3:  # Listen for info phase 3 keys
                # Showing waterfall pallette adjustments
                # Note: recalculating palette is quite slow.  
                # Do not repeat range changes too quickly! (1 per second max?)
                if event.key == pg.K_UP:
                    if v_max < -10:
                        v_max += 10
                    mywf.set_range(v_min, v_max)
                elif event.key == pg.K_DOWN:
                    if v_max > v_min + 20:
                        v_max -= 10
                    mywf.set_range(v_min, v_max)
                elif event.key == pg.K_RIGHT:
                    if v_min < v_max - 20:
                        v_min += 10
                    mywf.set_range(v_min, v_max)
                elif event.key == pg.K_LEFT:
                    if v_min > -130:
                        v_min -= 10
                    mywf.set_range(v_min, v_max)
                elif event.key == pg.K_RETURN:
                    info_phase = 0  # Turn OFF overlay
                    info_counter = 0
    # Finally, update display for user
    pg.display.update()

    # End of main loop

# END OF IQ.PY
