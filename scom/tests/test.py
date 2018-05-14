#!/usr/bin/python
# -*- coding: utf-8 -*-

import dbus
import time

bus = dbus.SystemBus()

obj = bus.get_object('tr.org.sulin.scom2', '/', introspect=False)
obj.setLocale('tr', dbus_interface='tr.org.sulin.scom2')

obj = bus.get_object('tr.org.sulin.scom2', '/package/apache', introspect=False)
print(obj.info(dbus_interface='tr.org.sulin.scom2.System.Service', timeout=60))
