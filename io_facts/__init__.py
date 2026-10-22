"""Passive facts about what is plugged into this machine.

Everything here reads only kernel-published state: sysfs, /dev/serial/by-id, /proc (fds and
locks) and the kernel journal. Nothing opens a serial port, a camera or a bus. The five
prototype directions all sit on this layer; they differ in surface, plugin model and health
policy, not in how they learn that something was plugged in.
"""
