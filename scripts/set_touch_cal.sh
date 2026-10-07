#!/bin/bash
# Touch rotation is handled by the compositor (labwc mapToOutput in rc.xml +
# the wlr-randr output transform in labwc/autostart), NOT by a libinput
# calibration matrix. An earlier approach wrote a LIBINPUT_CALIBRATION_MATRIX
# udev rule, but libinput classifies our USB panel as a pointer and ignores
# the matrix, so it only ever conflicted with the compositor mapping.
#
# This script now just removes any leftover calibration rule so the two
# mechanisms never fight. Kept as cage.service ExecStartPre.

RULES_FILE=/etc/udev/rules.d/99-touchscreen-cal.rules

if [ -f "$RULES_FILE" ]; then
    rm -f "$RULES_FILE"
    udevadm control --reload-rules
    udevadm trigger
fi
