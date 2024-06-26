#!/usr/bin/env python

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: 2016 IBM
# Author: Narasimhan V <sim@linux.vnet.ibm.com>

"""
Test to verify ppc64_cpu command.
"""

import os
from avocado import Test
from avocado.utils import process
from avocado.utils import cpu
from avocado.utils import distro, build, archive
from avocado.utils import genio
from avocado.utils.software_manager.manager import SoftwareManager
from math import ceil


class PPC64Test(Test):
    """
    Test to verify ppc64_cpu command for different supported values.

    :avocado: tags=cpu,power,privileged
    """

    def setUp(self):
        """
        Verifies if powerpc-utils is installed, and gets current SMT value.
        """
        if 'ppc' not in distro.detect().arch:
            self.cancel("Processor is not ppc64")
        self.sm = SoftwareManager()
        if not self.sm.check_installed("powerpc-utils"):
            if not self.sm.install("powerpc-utils"):
                self.cancel("Cannot install powerpc-utils, check the log!")

        self.loop = int(self.params.get('test_loop', default=100))
        self.run_type = self.params.get('type', default='distro')
        self.smt_str = "ppc64_cpu --smt"
        # Dynamically set max SMT specified at boot time
        process.system("%s=on" % self.smt_str, shell=True)
        # and get its value
        smt_op = process.system_output(self.smt_str, shell=True).decode()
        if "is not SMT capable" in smt_op:
            self.cancel("Machine is not SMT capable")
        if "Inconsistent state" in smt_op:
            self.cancel("Machine has mix of ST and SMT cores")

        self.curr_smt = smt_op.strip().split("=")[-1].split()[-1]
        self.smt_subcores = 0
        if os.path.exists("/sys/devices/system/cpu/subcores_per_core"):
            self.smt_subcores = 1
        self.failures = 0
        self.failure_message = "\n"
        self.smt_values = {1: "off"}
        self.key = 0
        self.value = ""
        self.max_smt_value = int(self.curr_smt)

    def test_build_upstream(self):
        """
        For upstream target download and compile source code
        Caution : This function will overwrite system installed
        lsvpd Tool binaries with upstream code.
        """
        if self.run_type == 'upstream':
            self.detected_distro = distro.detect()
            deps = ['gcc', 'make', 'automake', 'autoconf', 'bison', 'flex',
                    'libtool', 'zlib-devel', 'ncurses-devel', 'librtas-devel']
            if 'SuSE' in self.detected_distro.name:
                deps.extend(['libnuma-devel'])
            elif self.detected_distro.name in ['centos', 'fedora', 'rhel']:
                deps.extend(['numactl-devel'])
            else:
                self.cancel("Unsupported Linux distribution")
            for package in deps:
                if not self.sm.check_installed(package) and not \
                        self.sm.install(package):
                    self.cancel("Fail to install %s required for this test." %
                                package)
            url = self.params.get(
                'ppcutils_url', default='https://github.com/'
                'ibm-power-utilities/powerpc-utils/archive/refs/heads/'
                'master.zip')
            tarball = self.fetch_asset('ppcutils.zip', locations=[url],
                                       expire='7d')
            archive.extract(tarball, self.workdir)
            self.sourcedir = os.path.join(self.workdir, 'powerpc-utils-master')
            os.chdir(self.sourcedir)
            cmd_result = process.run('./autogen.sh', ignore_status=True,
                                     sudo=True, shell=True)
            if cmd_result.exit_status:
                self.fail('Upstream build: Pre configure step failed')
            cmd_result = process.run('./configure --prefix=/usr',
                                     ignore_status=True, sudo=True, shell=True)
            if cmd_result.exit_status:
                self.fail('Upstream build: Configure step failed')
            build.make(self.sourcedir)
            build.make(self.sourcedir, extra_args='install')
        else:
            self.cancel("This test is supported with upstream as target")

    def equality_check(self, test_name, cmd1, cmd2):
        """
        Verifies if the output of 2 commands are same, and sets failure
        count accordingly.

        :params test_name: Test Name
        :params cmd1: Command 1
        :params cmd2: Command 2
        """
        self.log.info("Testing %s", test_name)
        if str(cmd1) != str(cmd2):
            self.failures += 1
            self.failure_message += "%s test failed when SMT=%s\n" \
                % (test_name, self.key)

    def test_cmd_options(self):
        """
        Sets the SMT value, and calls each of the test, for each value.
        """
        for i in range(2, self.max_smt_value):
            self.smt_values[i] = str(i)
        for self.key, self.value in self.smt_values.items():
            process.system_output("%s=%s" % (self.smt_str,
                                             self.key), shell=True)
            process.system_output("ppc64_cpu --info")
            self.smt()
            self.core()
            if self.smt_subcores == 1:
                self.subcore()
            self.threads_per_core()
            self.dscr()

        if self.failures > 0:
            self.log.debug("Number of failures is %s", self.failures)
            self.log.debug(self.failure_message)
            self.fail()

    def smt(self):
        """
        Tests the SMT in ppc64_cpu command.
        """
        op1 = process.system_output(
            self.smt_str,
            shell=True).decode("utf-8").strip().split("=")[-1].split()[-1]
        self.equality_check("SMT", op1, self.value)

    def core(self):
        """
        Tests the core in ppc64_cpu command.
        """
        op1 = process.system_output(
            "ppc64_cpu --cores-present",
            shell=True).decode("utf-8").strip().split()[-1]
        op2 = cpu.online_cpus_count() / int(self.key)
        self.equality_check("Core", op1, ceil(op2))

    def subcore(self):
        """
        Tests the subcores in ppc64_cpu command.
        """
        op1 = process.system_output(
            "ppc64_cpu --subcores-per-core",
            shell=True).decode("utf-8").strip().split()[-1]
        op2 = genio.read_file(
            "/sys/devices/system/cpu/subcores_per_core").strip()
        self.equality_check("Subcore", op1, op2)

    def threads_per_core(self):
        """
        Tests the threads per core in ppc64_cpu command.
        """
        op1 = process.system_output(
            "ppc64_cpu --threads-per-core",
            shell=True).decode("utf-8").strip().split()[-1]
        op2 = process.system_output("ppc64_cpu --info",
                                    shell=True).decode("utf-8")
        op2 = len(op2.strip().splitlines()[0].split(":")[-1].split())
        self.equality_check("Threads per core", op1, ceil(op2))

    def dscr(self):
        """
        Tests the dscr in ppc64_cpu command.
        """
        op1 = process.system_output(
            "ppc64_cpu --dscr", shell=True).decode("utf-8").strip().split()[-1]
        op2 = int(genio.read_file(
            "/sys/devices/system/cpu/dscr_default").strip(), 16)
        self.equality_check("DSCR", op1, op2)

    def test_smt_loop(self):
        """
        Tests smt on/off in a loop
        """
        for _ in range(1, self.loop):
            if process.system("%s=off && %s=on" % (self.smt_str, self.smt_str),
                              shell=True):
                self.fail('SMT loop test failed')

    def test_single_core_smt(self):
        """
        Test smt level change when single core is online. This
        scenario was attempted to catch a regression.

        ppc64_cpu --cores-on=all
        ppc64_cpu —-smt=on
        ppc64_cpu --cores-on=1
        ppc64_cpu --cores-on
        ppc64_cpu --smt=2
        ppc64_cpu --smt=4
        ppc64_cpu --cores-on
           At this stage the number of online cores should be one.
           If not fail the test case

        """
        # online all cores
        process.system("ppc64_cpu --cores-on=all", shell=True)
        # Set highest SMT level
        process.system("ppc64_cpu --smt=on", shell=True)
        # online single core
        process.system("ppc64_cpu --cores-on=1", shell=True)
        # Record the output
        cores_on = process.system_output("ppc64_cpu --cores-on",
                                         shell=True).decode("utf-8")
        op1 = cores_on.strip().split("=")[-1]
        self.log.debug(op1)
        # Set 2 threads online
        process.system("ppc64_cpu --smt=2", shell=True)
        # Set 4 threads online
        process.system("ppc64_cpu --smt=4", shell=True)
        # Record the output
        cores_on = process.system_output("ppc64_cpu --cores-on",
                                         shell=True).decode("utf-8")
        op2 = cores_on.strip().split("=")[-1]
        self.log.debug(op2)
        if str(op1) != str(op2):
            self.fail("SMT with Single core test failed")

    def tearDown(self):
        """
        Sets back SMT to original value as was before the test.
        """
        if hasattr(self, 'smt_str'):
            process.system_output("%s=%s" % (self.smt_str,
                                             self.curr_smt), shell=True)
            process.system_output("dmesg")
