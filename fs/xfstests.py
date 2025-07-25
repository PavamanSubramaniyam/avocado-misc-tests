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
#
# Copyright: 2016 IBM
# Author: Praveen K Pandey <praveen@linux.vnet.ibm.com>
# Author: Harish <harish@linux.vnet.ibm.com>
#
# Based on code by Cleber Rosa <crosa@redhat.com>
#   copyright: 2011 Redhat
#   https://github.com/autotest/autotest-client-tests/tree/master/xfstests

import os
import re
import shutil

from avocado import Test
from avocado.utils import process, build, git, distro, partition
from avocado.utils import disk, pmem
from avocado.utils import genio
from avocado.utils.software_manager.manager import SoftwareManager


class Xfstests(Test):

    """
    xfstests - AKA FSQA SUITE, is set of filesystem tests

    :avocado: tags=fs,privileged
    """
    @staticmethod
    def get_size_alignval():
        """
        Return the size align restriction based on platform
        """
        if 'Hash' in genio.read_file('/proc/cpuinfo').rstrip('\t\r\n\0'):
            def_align = 16 * 1024 * 1024
        else:
            def_align = 2 * 1024 * 1024
        return def_align

    def get_half_region_size(self, region):
        size_align = self.get_size_alignval()
        region_size = self.plib.run_ndctl_list_val(self.plib.run_ndctl_list(
            '-r %s' % region)[0], 'size')

        namespace_size = region_size // 2
        namespace_size = (namespace_size // size_align) * size_align
        return namespace_size

    def setup_nvdimm(self):
        self.plib = pmem.PMem()
        self.plib.enable_region()
        regions = sorted(self.plib.run_ndctl_list('-R'),
                         key=lambda i: i['size'], reverse=True)
        if not regions:
            self.cancel("Nvdimm test with no region support")

        self.region = self.plib.run_ndctl_list_val(regions[0], 'dev')
        if self.plib.is_region_legacy(self.region):
            if not len(regions) > 1:
                self.cancel("Not supported with single legacy region")
            if self.logflag:
                self.log.info("Using loop devices as log devices")
                check = 2
                mount = True
                if disk.freespace('/') / 1073741824 > check:
                    self.disk_mnt = ''
                    mount = False
                else:
                    self.cancel('Need %s GB to create loop devices' % check)
                self._create_loop_device('2038M', mount)
                self.log_test = self.devices.pop()
                self.log_scratch = self.devices.pop()
            namespaces = self.plib.run_ndctl_list('-N -r %s' % self.region)
            pmem_dev = self.plib.run_ndctl_list_val(namespaces[0], 'blockdev')
            self.test_dev = "/dev/%s" % pmem_dev
            region_2 = self.plib.run_ndctl_list_val(regions[1], 'dev')
            namespaces = self.plib.run_ndctl_list('-N -r %s' % region_2)
            pmem_dev = self.plib.run_ndctl_list_val(namespaces[0], 'blockdev')
            self.scratch_dev = "/dev/%s" % pmem_dev
            self.devices.extend([self.test_dev, self.scratch_dev])
        else:
            if self.logflag:
                if not len(regions) > 1:
                    self.log.info('Using 10% space of device for logdev')
                    self.region_ldev = self.region
                    region_size = self.plib.run_ndctl_list_val(
                        self.plib.run_ndctl_list('-r %s' % self.region_ldev)[0], 'size')
                    logdev_size = int(region_size * 0.10)
                    dev_size = region_size - logdev_size
                    size_align = self.get_size_alignval()
                    dev_size = dev_size // 2
                    dev_size = (dev_size // size_align) * size_align
                    logdev_size = logdev_size // 2
                    logdev_size = (logdev_size // size_align) * size_align
                else:
                    dev_size = self.get_half_region_size(self.region)
                    self.region_ldev = self.plib.run_ndctl_list_val(
                        regions[1], 'dev')
                    logdev_size = self.get_half_region_size(
                        region=self.region_ldev)
                    self.plib.destroy_namespace(region=self.region, force=True)
                self.plib.destroy_namespace(
                    region=self.region_ldev, force=True)
                # XFS restrict max log size to 2136997888, which is 10M less
                # than 2GB, not 16M page-aligned, hence rounding-off to nearest
                # 16M align value 2130706432, which is 16M less than 2GiB
                logdev_size = min(logdev_size, 2130706432)
                # log device to be created in sector mode
                self.plib.create_namespace(region=self.region_ldev, mode='sector',
                                           sector_size='512', size=logdev_size)
                self.plib.create_namespace(region=self.region_ldev, mode='sector',
                                           sector_size='512', size=logdev_size)

                namespaces = self.plib.run_ndctl_list(
                    '-N -r %s -m sector' % self.region_ldev)
                log_dev = self.plib.run_ndctl_list_val(
                    namespaces[0], 'blockdev')
                self.log_test = "/dev/%s" % log_dev
                log_dev = self.plib.run_ndctl_list_val(
                    namespaces[1], 'blockdev')
                self.log_scratch = "/dev/%s" % log_dev
            else:
                self.plib.destroy_namespace(region=self.region, force=True)
                dev_size = self.get_half_region_size(self.region)
                self.log_test = None
                self.log_scratch = None
            self.plib.create_namespace(region=self.region, size=dev_size)
            self.plib.create_namespace(region=self.region, size=dev_size)
            namespaces = self.plib.run_ndctl_list(
                '-N -r %s -m fsdax' % self.region)
            pmem_dev = self.plib.run_ndctl_list_val(namespaces[0], 'blockdev')
            self.test_dev = "/dev/%s" % pmem_dev
            pmem_dev = self.plib.run_ndctl_list_val(namespaces[1], 'blockdev')
            self.scratch_dev = "/dev/%s" % pmem_dev
            self.devices.extend([self.test_dev, self.scratch_dev])

    def __setUp_packages(self):
        sm = SoftwareManager()

        self.detected_distro = distro.detect()

        packages = ['e2fsprogs', 'automake', 'gcc', 'quota', 'attr',
                    'make', 'xfsprogs', 'gawk', 'git']
        if self.detected_distro.name in ['Ubuntu', 'debian']:
            packages.extend(
                ['xfslibs-dev', 'uuid-dev', 'libuuid1',
                 'libattr1-dev', 'libacl1-dev', 'libgdbm-dev',
                 'uuid-runtime', 'libaio-dev', 'fio', 'dbench',
                 'gettext', 'libinih-dev', 'liburcu-dev', 'libblkid-dev',
                 'liblzo2-dev', 'zlib1g-dev', 'e2fslibs-dev',
                 'libzstd-dev', 'libudev-dev', 'bc', 'dump', 'acl',
                 'lvm2', 'sed'])
            if self.detected_distro.version in ['14']:
                packages.extend(['libtool'])
            elif self.detected_distro.version in ['18', '20']:
                packages.extend(['libtool-bin', 'libgdbm-compat-dev'])
            else:
                packages.extend(['libtool-bin'])

        elif self.detected_distro.name in ['centos', 'fedora', 'rhel', 'SuSE']:
            if self.dev_type == 'nvdimm':
                packages.extend(['ndctl', 'parted'])
                if self.detected_distro.name == 'rhel':
                    packages.extend(['daxctl'])
            packages.extend(['acl', 'bc', 'indent', 'libtool', 'lvm2',
                             'xfsdump', 'psmisc', 'sed', 'libacl-devel',
                             'libattr-devel', 'libaio-devel', 'libuuid-devel',
                             'libblkid-devel', 'lzo-devel', 'zlib-devel',
                             'e2fsprogs-devel', 'libzstd-devel',
                             'systemd-devel', 'meson',
                             'xfsprogs-devel', 'gcc-c++'])
            if self.detected_distro.name == 'rhel' and (
                    self.detected_distro.version.startswith('9')):
                packages.extend(['inih-devel'])

            if self.detected_distro.name == 'SuSE':
                packages.extend(['libbtrfs-devel', 'libcap-progs',
                                'liburcu-devel', 'libinih-devel',
                                 'libopenssl-devel', 'gettext-tools'])
            else:
                packages.extend(['btrfs-progs-devel', 'userspace-rcu-devel',
                                 'openssl-devel', 'gettext'])

            packages_remove = ['indent', 'btrfs-progs-devel']
            if self.detected_distro.name == 'rhel' and (
                    self.detected_distro.version.startswith('8') or
                    self.detected_distro.version.startswith('9') or
                    self.detected_distro.version.startswith('10')):
                packages = list(set(packages)-set(packages_remove))

            if self.detected_distro.name in ['centos', 'fedora']:
                packages.extend(['fio', 'dbench'])
        else:
            self.cancel("test not supported in %s" % self.detected_distro.name)

        for package in packages:
            if not sm.check_installed(package) and not sm.install(package):
                self.cancel("Fail to install %s required for this test." %
                            package)

    def setUp(self):
        """
        Build xfstest
        Source: git://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git
        """
        self.use_dd = False
        root_fs = process.system_output(
            "df -T / | awk 'END {print $2}'", shell=True).decode("utf-8")

        if root_fs in ['ext2', 'ext3']:
            self.use_dd = True

        self.dev_type = self.params.get('type', default='loop')
        self.logflag = self.params.get('logdev', default=False)
        self.fs_to_test = self.params.get('fs', default='ext4')
        self.args = self.params.get('args', default='-g quick')
        self.log.debug(f"FS: {self.fs_to_test}, args: {self.args}")
        self.base_disk = self.params.get('disk', default=None)
        self.scratch_mnt = self.params.get(
            'scratch_mnt', default='/mnt/scratch')
        self.test_mnt = self.params.get('test_mnt', default='/mnt/test')
        self.disk_mnt = self.params.get('disk_mnt', default='/mnt/loop_device')
        self.run_type = self.params.get('run_type', default='distro')
        self.log_test = self.params.get('log_test', default='')
        self.log_scratch = self.params.get('log_scratch', default='')
        self.test_dev = self.params.get('disk_test', default=None)
        self.scratch_dev = self.params.get('disk_scratch', default=None)
        self.mkfs_opt = self.params.get('mkfs_opt', default='')
        self.mount_opt = self.params.get('mount_opt', default='')
        self.logdev_opt = self.params.get('logdev_opt', default='')

        # If there is an existing results directory then just clean that up before running the test
        if os.path.exists(f"{self.teststmpdir}/results"):
            shutil.rmtree(f"{self.teststmpdir}/results")

        shutil.copyfile(self.get_data('local.config'),
                        os.path.join(self.teststmpdir, 'local.config'))

        self.devices = []
        self.part = None

        self.__setUp_packages()

        if self.run_type == 'upstream':
            prefix = "/usr/local"
            bin_prefix = "/usr/local/bin"

            if 'verity' in self.args:
                fsverity_dir = os.path.join(self.teststmpdir, 'fsverity-utils')
                if not os.path.exists(fsverity_dir):
                    os.makedirs(fsverity_dir)
                fsverity_url = self.params.get('fsverity_url')
                git.get_repo(fsverity_url, destination_dir=fsverity_dir)
                os.chdir(fsverity_dir)
                build.make(fsverity_dir)
                build.make(fsverity_dir, extra_args='install')

            if self.detected_distro.name == 'SuSE':
                # SuSE has /sbin at a higher priority than /usr/local/bin
                # in $PATH, so install all the binaries in /sbin to make
                # sure they are picked up correctly by xfstests.
                #
                # We still install in /usr/local but binaries are kept in
                # /sbin
                bin_prefix = "/sbin"

            if self.fs_to_test == "ext4":
                # Build e2fs progs
                e2fsprogs_dir = os.path.join(self.teststmpdir, 'e2fsprogs')
                if not os.path.exists(e2fsprogs_dir):
                    os.makedirs(e2fsprogs_dir)
                e2fsprogs_url = self.params.get('e2fsprogs_url')
                git.get_repo(e2fsprogs_url, destination_dir=e2fsprogs_dir)
                e2fsprogs_build_dir = os.path.join(e2fsprogs_dir, 'build')
                if not os.path.exists(e2fsprogs_build_dir):
                    os.makedirs(e2fsprogs_build_dir)
                os.chdir(e2fsprogs_build_dir)
                process.run("../configure --prefix=%s --bindir=%s --sbindir=%s"
                            % (prefix, bin_prefix, bin_prefix), verbose=True)
                build.make(e2fsprogs_build_dir)
                build.make(e2fsprogs_build_dir, extra_args='install')

            if self.fs_to_test == "xfs":
                if self.detected_distro.name in ['centos', 'fedora', 'rhel']:
                    libini_path = process.run("ldconfig -p | grep libini",
                                              verbose=True, ignore_status=True)
                    if not libini_path:
                        # Build libini.h as it is needed for xfsprogs
                        libini_dir = os.path.join(self.teststmpdir, 'libini')
                        if not os.path.exists(libini_dir):
                            os.makedirs(libini_dir)
                        git.get_repo('https://github.com/benhoyt/inih',
                                     destination_dir=libini_dir)
                        os.chdir(libini_dir)
                        process.run("meson build", verbose=True)
                        libini_build_dir = os.path.join(libini_dir, 'build')
                        if os.path.exists(libini_build_dir):
                            os.chdir(libini_build_dir)
                            process.run("meson install", verbose=True)
                        else:
                            self.fail('Something went wrong while building \
                                      libini. Please check the logs.')
                # Build xfs progs
                xfsprogs_dir = os.path.join(self.teststmpdir, 'xfsprogs')
                if not os.path.exists(xfsprogs_dir):
                    os.makedirs(xfsprogs_dir)
                xfsprogs_url = self.params.get('xfsprogs_url')
                git.get_repo(xfsprogs_url, destination_dir=xfsprogs_dir)
                os.chdir(xfsprogs_dir)
                build.make(xfsprogs_dir)
                process.run("./configure --prefix=%s --bindir=%s --sbindir=%s"
                            % (prefix, bin_prefix, bin_prefix), verbose=True)
                build.make(xfsprogs_dir, extra_args='install')

            if self.fs_to_test == "btrfs":
                # Build btrfs progs
                btrfsprogs_dir = os.path.join(self.teststmpdir, 'btrfsprogs')
                if not os.path.exists(btrfsprogs_dir):
                    os.makedirs(btrfsprogs_dir)
                btrfsprogs_url = self.params.get('btrfsprogs_url')
                git.get_repo(btrfsprogs_url, destination_dir=btrfsprogs_dir)
                os.chdir(btrfsprogs_dir)
                process.run("./autogen.sh", verbose=True)
                process.run("./configure --prefix=%s --bindir=%s --sbindir=%s --disable-documentation"
                            % (prefix, bin_prefix, bin_prefix), verbose=True)
                build.make(btrfsprogs_dir)
                build.make(btrfsprogs_dir, extra_args='install')

        # Check versions of fsprogs
        fsprogs_ver = process.system_output("mkfs.%s -V" % self.fs_to_test,
                                            ignore_status=True,
                                            shell=True).decode("utf-8")
        self.log.info(fsprogs_ver)

        if process.system('which mkfs.%s' % self.fs_to_test,
                          ignore_status=True):
            self.cancel('Unknown filesystem %s' % self.fs_to_test)
        mount = True
        self.log_devices = []

        # For btrfs we need minimum of 5 loop devices for SCRATCH_DEV_POOL
        self.num_loop_dev = 2
        if self.fs_to_test == "btrfs":
            self.num_loop_dev = 5

        if self.dev_type == 'loop':
            loop_size = self.params.get('loop_size', default='7GiB')
            if not self.base_disk:
                # Using root for file creation by default
                check = (int(loop_size.split('GiB')[
                         0]) * self.num_loop_dev) + 1
                if disk.freespace('/') / 1073741824 > check:
                    self.disk_mnt = ''
                    mount = False
                else:
                    self.cancel('Need %s GB to create loop devices' % check)
            self._create_loop_device(loop_size, mount)
        elif self.dev_type == 'nvdimm':
            self.setup_nvdimm()
        else:
            self.devices.extend([self.test_dev, self.scratch_dev])
        # mkfs for devices
        if self.devices:
            cfg_file = os.path.join(self.teststmpdir, 'local.config')
            with open(cfg_file, "r") as sources:
                lines = sources.readlines()
            with open(cfg_file, "w") as sources:
                for line in lines:
                    if line.startswith('export TEST_DEV'):
                        sources.write(
                            re.sub(r'export TEST_DEV=.*', 'export TEST_DEV=%s'
                                   % self.devices[0], line))
                    elif line.startswith('export TEST_DIR'):
                        sources.write(
                            re.sub(r'export TEST_DIR=.*', 'export TEST_DIR=%s'
                                   % self.test_mnt, line))
                    elif line.startswith('export SCRATCH_DEV'):
                        if self.fs_to_test == "btrfs":
                            scratch_dev_pool = ' '.join(
                                [(self.devices[i]) for i in range(1, self.num_loop_dev)])
                            sources.write(re.sub(r'export SCRATCH_DEV=.*',
                                                 'export SCRATCH_DEV_POOL="%s"'
                                                 % scratch_dev_pool, line))
                        else:
                            sources.write(re.sub(
                                r'export SCRATCH_DEV=.*', 'export SCRATCH_DEV=%s'
                                % self.devices[1], line))
                    elif line.startswith('export SCRATCH_MNT'):
                        sources.write(
                            re.sub(
                                r'export SCRATCH_MNT=.*',
                                'export SCRATCH_MNT=%s' %
                                self.scratch_mnt,
                                line))
                        break
            with open(cfg_file, "a") as sources:
                if self.log_test:
                    sources.write('export USE_EXTERNAL=yes\n')
                    sources.write('export TEST_LOGDEV="%s"\n' % self.log_test)
                    self.log_devices.append(self.log_test)
                if self.log_scratch:
                    sources.write('export SCRATCH_LOGDEV="%s"\n' %
                                  self.log_scratch)
                    self.log_devices.append(self.log_scratch)
                if self.mkfs_opt:
                    sources.write('MKFS_OPTIONS="%s"\n' % self.mkfs_opt)
                if self.mount_opt:
                    sources.write('MOUNT_OPTIONS="%s"\n' % self.mount_opt)
            for dev in self.log_devices:
                dev_obj = partition.Partition(dev)
                dev_obj.mkfs(fstype=self.fs_to_test, args=self.mkfs_opt)
            for ite, dev in enumerate(self.devices):
                dev_obj = partition.Partition(dev)
                if self.logdev_opt:
                    dev_obj.mkfs(fstype=self.fs_to_test, args='%s %s=%s' % (
                        self.mkfs_opt, self.logdev_opt, self.log_devices[ite]))
                else:
                    dev_obj.mkfs(fstype=self.fs_to_test, args=self.mkfs_opt)

        git.get_repo('https://git.kernel.org/pub/scm/fs/xfs/xfstests-dev.git',
                     destination_dir=self.teststmpdir)

        extra_args = f"-j{os.cpu_count()}"
        build.make(self.teststmpdir, extra_args=extra_args)

        if self.detected_distro.name is not 'SuSE':
            if process.system('useradd 123456-fsgqa', sudo=True, ignore_status=True):
                self.log.warn('useradd 123456-fsgqa failed')
            if process.system('useradd fsgqa', sudo=True, ignore_status=True):
                self.log.warn('useradd fsgqa failed')
        else:
            if process.system('useradd -m -U fsgqa', sudo=True, ignore_status=True):
                self.log.warn('useradd fsgqa failed')
            if process.system('groupadd sys', sudo=True, ignore_status=True):
                self.log.warn('groupadd sys failed')
        if not os.path.exists(self.scratch_mnt):
            os.makedirs(self.scratch_mnt)
        if not os.path.exists(self.test_mnt):
            os.makedirs(self.test_mnt)

    def test(self):
        failures = False
        os.chdir(self.teststmpdir)
        if self.args:
            cmd = f"./check {self.args}"
            result = process.run(cmd, ignore_status=True, verbose=True)
            if result.exit_status == 0:
                self.log.info("OK: All tests passed")
            else:
                msg = self._parse_error_message(result.stdout)
                self.log.info("FAIL: Test(s) failed %s" % msg)
                self.fail('One or more tests failed. Please check the logs.')

    def tearDown(self):

        srcdir = f"{self.teststmpdir}/results"
        if (os.path.exists(srcdir) and os.path.exists(self.outputdir)):
            new_outputdir = os.path.join(self.outputdir,
                                         os.path.basename(srcdir))
            shutil.copytree(srcdir, new_outputdir)

        job_dir = os.path.dirname(os.path.dirname(self.logdir))
        self.job_id = os.path.basename(job_dir)
        self.log.debug(" Job ID: %s, logdir: %s, srcdir: %s, outputdir: %s: " %
                       (self.job_id, self.logdir, srcdir, self.outputdir))

        user_exits = 0
        if not (process.system('id fsgqa', sudo=True, ignore_status=True)):
            process.system('userdel -r -f fsgqa', sudo=True)
            user_exits = 1
        if self.detected_distro.name is not 'SuSE':
            if not (process.system('id 123456-fsgqa', sudo=True, ignore_status=True)):
                process.system('userdel -f 123456-fsgqa', sudo=True)
        if user_exits and self.detected_distro.name is 'SuSE':
            process.system('groupdel fsgqa', sudo=True)
            process.system('groupdel sys', sudo=True)
        # In case if any test has been interrupted
        process.system('umount %s %s' % (self.scratch_mnt, self.test_mnt),
                       sudo=True, ignore_status=True)
        if os.path.exists(self.scratch_mnt):
            shutil.rmtree(self.scratch_mnt)
        if os.path.exists(self.test_mnt):
            shutil.rmtree(self.test_mnt)
        if os.path.exists(self.teststmpdir + "/libini"):
            shutil.rmtree(self.teststmpdir + "/libini")
        if self.dev_type == 'loop':
            for dev in self.devices:
                process.system('losetup -d %s' % dev, shell=True,
                               sudo=True, ignore_status=True)
            if self.part:
                self.part.unmount()
        elif self.dev_type == 'nvdimm':
            if hasattr(self, 'region'):
                self.plib.destroy_namespace(region=self.region, force=True)
            if hasattr(self, 'region_ldev'):
                self.plib.destroy_namespace(
                    region=self.region_ldev, force=True)
            if hasattr(self, 'region'):
                if self.plib.is_region_legacy(self.region):
                    if self.logflag:
                        for dev in [self.log_test, self.log_scratch]:
                            process.system('losetup -d %s' % dev, shell=True,
                                           sudo=True, ignore_status=True)

    def _create_fsimages(self, loop_size, i):
        dd_count = int(loop_size.split('GiB')[0])
        if self.use_dd:
            process.run('dd if=/dev/zero of=%s/file-%s.img bs=1G count=%s'
                        % (self.disk_mnt, i, dd_count), shell=True,
                        sudo=True)
        else:
            process.run('fallocate -o 0 -l %s %s/file-%s.img' %
                        (loop_size, self.disk_mnt, i), shell=True,
                        sudo=True)

    def _create_loop_device(self, loop_size, mount=True):
        if mount:
            self.part = partition.Partition(
                self.base_disk, mountpoint=self.disk_mnt)
            self.part.mount()

        # Creating [0 - num_loop_dev) loop devices
        for i in range(self.num_loop_dev):
            self._create_fsimages(loop_size, i)
            dev = process.system_output('losetup -f').decode("utf-8").strip()
            self.devices.append(dev)
            process.run('losetup %s %s/file-%s.img' %
                        (dev, self.disk_mnt, i), shell=True, sudo=True)

    @staticmethod
    def _parse_error_message(output):
        na_re = re.compile(r'Passed all 0 tests')
        na_detail_re = re.compile(r'(\d{3})\s*(\[not run\])\s*(.*)')
        failed_re = re.compile(r'Failed \d+ of \d+ tests')

        lines = output.decode("ISO-8859-1").split('\n')
        result_line = lines[-3]

        error_msg = None
        if na_re.match(result_line):
            detail_line = lines[-3]
            match = na_detail_re.match(detail_line)
            if match is not None:
                error_msg = match.groups()[2]
            else:
                error_msg = 'Test dependency failed, test will not run.'
        elif failed_re.match(result_line):
            error_msg = 'Test error. %s.' % result_line
        else:
            error_msg = 'Could not verify test result. Please check the logs.'

        return error_msg
