"""
STServo Controller Package

Provides servo control for Waveshare ST-series bus servos (ST3020)
via USB serial adapter (Bus Servo Adapter A).
"""

from .controller import STServoController

__all__ = ['STServoController']
