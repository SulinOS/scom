#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2006-2007, TUBITAK/UEKAE
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version. Please read the COPYING file.
#

import os
import subprocess
import fcntl
import termios
import pwd
import grp
import signal
import time

from comar.utility import *

# utility functions

def is_on():
    state = "off"
    s = get_profile("System.Service.setState")
    if s:
        state = s["state"]
    else:
        try:
            from csl import serviceDefault
            state = serviceDefault
        except:
            pass
    return state

def loadConfig():
    conf = {}
    try:
        from csl import serviceConf
    except ImportError:
        serviceConf = script()[0]
    filename = "/etc/conf.d/%s" % serviceConf
    if not os.path.exists(filename):
        return
    for line in file(filename):
        if line != "" and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if value.startswith('"') or value.startswith("'"):
                value = value[1:-1]
            conf[key] = value
    return conf

def loadEnvironment():
    basePath = "/bin:/sbin:/usr/bin:/usr/sbin:/usr/local/bin:/usr/local/sbin:"
    if os.path.exists("/etc/profile.env"):
        for line in file("/etc/profile.env"):
            if line.startswith("export "):
                key, value = line[7:].strip().split("=", 1)
                os.environ[key] = value[1:-1]
    # PATH in profile.env doesn't have some default paths
    os.environ["PATH"] = basePath + os.environ.get("PATH", "")


class Config(dict):
    def __init__(self):
        self.first_time = True
    
    def __load(self):
        self.first_time = False
        conf = loadConfig()
        for item in conf:
            self[item] = conf[item]
    
    def __check(self, func, args):
        if self.first_time:
            self.__load()
        return func(self, *args)
    
    __getitem__ = lambda self, item: self.__check(dict.__getitem__, [item])
    __str__ = lambda self: self.__check(dict.__str__, [])
    __len__ = lambda self: self.__check(dict.__len__, [])
    __iter__ = lambda self: self.__check(dict.__iter__, [])
    __contains__ = lambda self, item: self.__check(dict.__contains__, [item])
    get = lambda self, *args: self.__check(dict.get, args)


config = Config()

def startDependencies(*services):
    """Start other needed services.
    
    Arguments are service names.
    """
    for service in services:
        call("System.Service.start", service)

# Service control utilities

def _getPid(pidfile):
    """Read process ID from a .pid file."""
    try:
        pid = file(pidfile).read()
    except IOError, e:
        if e.errno != 2:
            raise
        return None
    # Some services put custom data after the first line
    pid = pid.split("\n")[0]
    # Non-pid data is also seen when stopped state in some services :/
    if len(pid) == 0 or len(filter(lambda x: not x in "0123456789", pid)) > 0:
        return None
    return int(pid)

def _checkPid(pid, user_uid=None, command=None):
    """Check that given process ID matches our criteria."""
    if not pid:
        return False
    path = "/proc/%d" % pid
    # Check that process is running
    if not os.path.exists(path):
        return False
    # Check that process belongs to correct user
    if user_uid:
        try:
            st = os.stat(path)
        except OSError, e:
            if e.errno != 2:
                raise
            return False
        if st.st_uid != user_uid:
            return False
    # Check that process is an instance of the correct binary
    if command:
        try:
            cmdline = file("%s/cmdline" % path).read()
        except IOError, e:
            if e.errno != 2:
                raise
            return False
        if cmdline.split("\0")[0] != command:
            return False
    return True

def _findProcesses(command=None, user=None):
    """Return the list of process IDs matching our criteria."""
    pids = []
    user_uid = None
    if user:
        pw = pwd.getpwnam(user)
        user_uid = pw.pw_uid
    for entry in os.listdir("/proc"):
        if entry[0] in "0123456789":
            pid = int(entry)
            if _checkPid(pid, user_uid=user_uid, command=command):
                pids.append(pid)
    if len(pids) > 0:
        return pids
    return None

def _changeUID(chuid):
    """Change to this chuid (user:group)"""
    c_user = chuid
    c_group = None
    if ":" in c_user:
        c_user, c_group = c_user.split(":", 1)
    cpw = pwd.getpwnam(c_user)
    c_uid = cpw.pw_uid
    if c_group:
        cgr = grp.getgrnam(c_group)
        c_gid = cgr.gr_gid
    else:
        c_gid = cpw.pw_gid
        c_group = grp.getgrgid(cpw.pw_gid).gr_name
    
    c_groups = []
    for item in grp.getgrall():
        if c_user in item.gr_mem:
            c_groups.append(item.gr_gid)
    if c_gid not in c_groups:
        c_groups.append(c_gid)
    
    os.setgid(c_gid)
    os.setgroups(c_groups)
    os.setuid(c_uid)

# Service control API

def startService(command, args=None, pidfile=None, makepid=False, nice=None, detach=False, chuid=None, donotify=False):
    """Start given service.
    
    command:  Path to the service executable.
    args:     Optional arguments to the service executable.
    pidfile:  Process ID of the service is kept in this file when running.
    nice:     This value is added to the service process' niceness value, which
              decreases its scheduling priority.
    chuid:    Change to this user:group before starting the service.
    detach:   If the service doesn't detach on its own, this option will fork
              and run it in the background.
    makepid:  Write the pid file if service does not create on its own. Mostly useful
              with the detach option.
    donotify: If True, automatically make Comar notification when service is run.
              Also automatically fail() if something goes wrong.
    """
    cmd = [ command ]
    if args:
        if isinstance(args, basestring):
            args = args.split()
        cmd.extend(args)
    
    if pidfile:
        pid = _getPid(pidfile)
        if _checkPid(pid, command=command):
            # Already running, no need to send notification, just return OK
            return None
    
    def fork_handler():
        if detach:
            # Set umask to a sane value
            # (other and group has no write permission by default)
            os.umask(022)
            # Detach from controlling terminal
            try:
                tty_fd = os.open("/dev/tty", os.O_RDWR)
                fcntl.ioctl(tty_fd, termios.TIOCNOTTY)
                os.close(tty_fd)
            except OSError:
                pass
            # Close IO channels
            devnull_fd = os.open("/dev/null", os.O_RDWR)
            os.dup2(devnull_fd, 0)
            os.dup2(devnull_fd, 1)
            os.dup2(devnull_fd, 2)
            # Detach from process group
            os.setsid()
        if nice is not None:
            os.nice(nice)
        if makepid and pidfile:
            file(pidfile, "w").write("%d\n" % os.getpid())
        if chuid:
            _changeUID(chuid)
    
    popen = subprocess.Popen(cmd, close_fds=True, preexec_fn=fork_handler, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if detach:
        if donotify:
            # We blindly send this, cause there is no way to track detached
            # process' return code.
            notify("System.Service.changed", "started")
        return execReply(0)
    else:
        ret = execReply(popen.wait())
        ret.stdout, ret.stderr = popen.communicate()
        if donotify:
            if ret == 0:
                notify("System.Service.changed", "started")
            else:
                err = "Unable to start service."
                if ret.stderr != "":
                    err = "Unable to start: " + str(ret.stderr)
                fail(err)
        return ret

def stopService(pidfile=None, command=None, args=None, chuid=None, user=None, signalno=None, donotify=False):
    """Stop given service.
    
    pidfile:   Process ID of the service is kept in this file when running.
    command:   Stop processes running this executable.
    args:      Execute command with these args instead of killing with [signalno]
    chuid:     Change to this user:group before stopping the service.
               Only used if both command and args are given.
    user:      Stop processes belonging to this user name.
    signalno:  Specify the signal to send to processes being stopped.
               Default is SIGTERM.
    donotify: If True, automatically make Comar notification when service is stopped.
              Also automatically fail() if something goes wrong.
    """
    if signalno is None:
        signalno = signal.SIGTERM
    
    if command and args is not None:
        cmd = [ command ]
        if args:
            if isinstance(args, basestring):
                args = args.split()
            cmd.extend(args)
        
        if chuid:
            _changeUID(chuid)
        
        popen = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ret = execReply(popen.wait())
        ret.stdout, ret.stderr = popen.communicate()
        if donotify:
            if ret == 0:
                notify("System.Service.changed", "stopped")
            else:
                err = "Unable to stop service."
                if ret.stderr != "":
                    err = "Unable to stop: " + str(ret.stderr)
                fail(err)
        return ret
    
    if pidfile:
        user_uid = None
        if user:
            pw = pwd.getpwnam(user)
            user_uid = pw.pw_uid
        pid = _getPid(pidfile)
        if _checkPid(pid, user_uid=user_uid, command=command):
            os.kill(pid, signalno)
            timeout = 5
            while True:
                if not _checkPid(pid, user_uid=user_uid, command=command):
                    break
                timeout -= 0.3
                time.sleep(0.3)
                if timeout <= 0:
                    fail("Unable to stop")
            if donotify:
                notify("System.Service.changed", "stopped")
        else:
            # Already stopped, no need to send notification.
            return None
    else:
        if not command and not user:
            raise TypeError("You should give a criteria to select service processes!")
        pids = _findProcesses(user=user, command=command)
        if pids is not None:
            for pid in pids:
                os.kill(pid, signalno)
            notify("System.Service.changed", "stopped")
    return None

def isServiceRunning(pidfile=None, command=None):
    """Return if given service is currently running.
    
    pidfile:   Process ID of the service is kept in this file when running.
    command:   Check processes running this executable.
    """
    if pidfile:
        pid = _getPid(pidfile)
        if pid is None:
            return False
        if not _checkPid(pid, command=command):
            return False
    else:
        pids = _findProcesses(command=command)
        if pids is None:
            return False
    return True

# Default Comar class methods

def info():
    from csl import serviceType
    from csl import serviceDesc
    state = is_on()
    try:
        from csl import status
        if status():
            if state == "off":
                state = "started"
        else:
            if state == "on":
                state = "stopped"
    except:
        pass
    return "\n".join([serviceType, state, serviceDesc])

def ready():
    if is_on() == "on":
        from csl import start
        start()

def setState(state=None):
    if state != "on" and state != "off":
        fail("Unknown state '%s'" % state)
    notify("System.Service.changed", state)
