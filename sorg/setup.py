#!/usr/bin/python
#-*- coding: utf-8 -*-

import os

from distutils.core import setup
from distutils.command.install import install

import sorg
from sorg import consts

class Install(install):
    def run(self):
        install.run(self)

        if not self.root:
            self.root = "/"

        target = os.path.join(self.root, consts.config_dir.lstrip("/"))
        if not os.path.exists(target):
            os.makedirs(target, 0o755)

setup(name="sorg",
    version=sorg.versionString(),
    description="Python Modules for sorg",
    license="GNU GPL2",
    url="http://www.pardus.org.tr/",
    packages = ["sorg"],
    scripts = ["sorg-cli", "inf2mondb"],
    data_files = [
        (consts.data_dir, ["data/DriversDB", "data/MonitorsDB"]),
        ("/sbin", ["sorg-loadmodule"]),
    ],
    cmdclass = {"install": Install}
)
