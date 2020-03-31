#!/usr/bin/env python
#
# Copyright (C) 2015 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Verifies that the build is sane.

Cleans old build artifacts, configures the required environment, determines
build goals, and invokes the build scripts.
"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import collections
import contextlib
import copy
# pylint: disable=import-error,no-name-in-module
# https://github.com/PyCQA/pylint/issues/73
from distutils.dir_util import copy_tree
# pylint: enable=import-error,no-name-in-module
import glob
import inspect
import json
import logging
import multiprocessing
import os
from pathlib import Path
import pipes
import pprint
import re
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import textwrap
import traceback
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Set,
    TextIO,
    Tuple,
    Union,
)

from build.lib import build_support
import ndk.abis
import ndk.ansi
import ndk.autoconf
import ndk.builds
import ndk.config
import ndk.deps
import ndk.ext.shutil
import ndk.file
import ndk.hosts
import ndk.notify
import ndk.paths
import ndk.test.builder
import ndk.test.printers
import ndk.test.spec
import ndk.timer
import ndk.toolchains
import ndk.ui
import ndk.workqueue


def _make_tar_package(package_path: str, base_dir: str, path: str) -> str:
    """Creates a tarball package for distribution.

    Args:
        package_path (string): Path (without extention) to the output archive.
        base_dir (string): Path to the directory from which to perform the
                           packaging (identical to tar's -C).
        path (string): Path to the directory to package.
    """
    has_pbzip2 = shutil.which('pbzip2') is not None
    if has_pbzip2:
        compress_arg = '--use-compress-prog=pbzip2'
    else:
        compress_arg = '-j'

    package_path = package_path + '.tar.bz2'
    cmd = ['tar', compress_arg, '-cf', package_path, '-C', base_dir, path]
    subprocess.check_call(cmd)
    return package_path


def _make_zip_package(package_path: str, base_dir: str, path: str) -> str:
    """Creates a zip package for distribution.

    Args:
        package_path (string): Path (without extention) to the output archive.
        base_dir (string): Path to the directory from which to perform the
                           packaging (identical to tar's -C).
        path (string): Path to the directory to package.
    """
    cwd = os.getcwd()
    package_path = os.path.realpath(package_path) + '.zip'
    os.chdir(base_dir)
    try:
        subprocess.check_call(['zip', '-9qr', package_path, path])
        return package_path
    finally:
        os.chdir(cwd)


def purge_unwanted_files(ndk_dir: Path) -> None:
    """Removes unwanted files from the NDK install path."""

    for path, _dirs, files in os.walk(ndk_dir):
        for file_name in files:
            file_path = Path(path) / file_name
            if file_name.endswith('.pyc'):
                file_path.unlink()
            elif file_name == 'Android.bp':
                file_path.unlink()


def package_ndk(ndk_dir: str, dist_dir: str, host_tag: str,
                build_number: str) -> str:
    """Packages the built NDK for distribution.

    Args:
        ndk_dir (string): Path to the built NDK.
        dist_dir (string): Path to place the built package in.
        host_tag (string): Host tag to use in the package name,
        build_number (printable): Build number to use in the package name. Will
                                  be 'dev' if the argument evaluates to False.
    """
    package_name = 'android-ndk-{}-{}'.format(build_number, host_tag)
    package_path = os.path.join(dist_dir, package_name)

    purge_unwanted_files(Path(ndk_dir))

    base_dir = os.path.dirname(ndk_dir)
    package_files = os.path.basename(ndk_dir)
    if host_tag.startswith('windows'):
        return _make_zip_package(package_path, base_dir, package_files)
    else:
        return _make_tar_package(package_path, base_dir, package_files)


def build_ndk_tests(out_dir: str, dist_dir: str,
                    args: argparse.Namespace) -> bool:
    """Builds the NDK tests.

    Args:
        out_dir: Build output directory.
        dist_dir: Preserved artifact directory.
        args: Parsed command line arguments.

    Returns:
        True if all tests pass, else False.
    """
    # The packaging step extracts all the modules to a known directory for
    # packaging. This directory is not cleaned up after packaging, so we can
    # reuse that for testing.
    ndk_dir = ndk.paths.get_install_path(out_dir)
    test_src_dir = ndk.paths.ndk_path('tests')
    test_out_dir = os.path.join(out_dir, 'tests')

    site.addsitedir(os.path.join(ndk_dir, 'python-packages'))

    test_options = ndk.test.spec.TestOptions(
        test_src_dir, ndk_dir, test_out_dir, clean=True)

    printer = ndk.test.printers.StdoutPrinter()
    with open(ndk.paths.ndk_path('qa_config.json')) as config_file:
        test_config = json.load(config_file)

    if args.arch is not None:
        test_config['abis'] = ndk.abis.arch_to_abis(args.arch)

    test_spec = ndk.test.builder.test_spec_from_config(test_config)
    builder = ndk.test.builder.TestBuilder(
        test_spec, test_options, printer)

    report = builder.build()
    printer.print_summary(report)

    if report.successful:
        print('Packaging tests...')
        package_path = os.path.join(dist_dir, 'ndk-tests')
        _make_tar_package(package_path, out_dir, 'tests/dist')
    else:
        # Write out the result to logs/build_error.log so we can find the
        # failure easily on the build server.
        log_path = os.path.join(dist_dir, 'logs/build_error.log')
        with open(log_path, 'a') as error_log:
            error_log_printer = ndk.test.printers.FilePrinter(error_log)
            error_log_printer.print_summary(report)

    return report.successful


def install_file(file_name: str, src_dir: str, dst_dir: str) -> None:
    src_file = os.path.join(src_dir, file_name)
    dst_file = os.path.join(dst_dir, file_name)

    print('Copying {} to {}...'.format(src_file, dst_file))
    if os.path.isdir(src_file):
        _install_dir(src_file, dst_file)
    elif os.path.islink(src_file):
        _install_symlink(src_file, dst_file)
    else:
        _install_file(src_file, dst_file)


def _install_dir(src_dir: str, dst_dir: str) -> None:
    parent_dir = os.path.normpath(os.path.join(dst_dir, '..'))
    if not os.path.exists(parent_dir):
        os.makedirs(parent_dir)
    shutil.copytree(src_dir, dst_dir, symlinks=True)


def _install_symlink(src_file: str, dst_file: str) -> None:
    dirname = os.path.dirname(dst_file)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    link_target = os.readlink(src_file)
    os.symlink(link_target, dst_file)


def _install_file(src_file: str, dst_file: str) -> None:
    dirname = os.path.dirname(dst_file)
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    # copy2 is just copy followed by copystat (preserves file metadata).
    shutil.copy2(src_file, dst_file)


class Clang(ndk.builds.Module):
    name = 'clang'
    path = 'toolchains/llvm/prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN

    @property
    def notices(self) -> List[str]:
        # TODO: Why every host?
        return [
            str(ndk.toolchains.ClangToolchain.path_for_host(h) / 'NOTICE')
            for h in ndk.hosts.Host
        ]

    def build(self) -> None:
        pass

    def install(self) -> None:
        install_path = self.get_install_path()

        install_parent = os.path.dirname(install_path)
        if os.path.exists(install_path):
            shutil.rmtree(install_path)
        if not os.path.exists(install_parent):
            os.makedirs(install_parent)
        shutil.copytree(
            ndk.toolchains.ClangToolchain.path_for_host(self.host),
            install_path)

        # clang-4053586 was patched in the prebuilts directory to add the
        # libc++ includes. These are almost certainly a different revision than
        # the NDK libc++, and may contain local changes that the NDK's don't
        # and vice versa. Best to just remove them for the time being since
        # that returns to the previous behavior.
        # https://github.com/android-ndk/ndk/issues/564#issuecomment-342307128
        cxx_includes_path = os.path.join(install_path, 'include')
        shutil.rmtree(cxx_includes_path)

        if not self.host.is_windows:
            # The Linux and Darwin toolchains have Python compiler wrappers
            # that currently do nothing. We don't have these for Windows and we
            # want to make sure Windows behavior is consistent with the other
            # platforms, so just unwrap the compilers until they do something
            # useful and are available on Windows.
            os.rename(os.path.join(install_path, 'bin/clang.real'),
                      os.path.join(install_path, 'bin/clang'))
            os.rename(os.path.join(install_path, 'bin/clang++.real'),
                      os.path.join(install_path, 'bin/clang++'))

            # The prebuilts have symlinks pointing at a clang-MAJ.MIN binary,
            # but we replace symlinks with standalone copies, so remove this
            # copy to save space.
            bin_dir = os.path.join(install_path, 'bin')
            (clang_maj_min,) = glob.glob(os.path.join(bin_dir, 'clang-?'))
            os.remove(clang_maj_min)

        # Remove LLD duplicates. We only need ld.lld.
        # http://b/74250510
        #
        # Note that lld is experimental in the NDK. It is not the default for
        # any architecture and has received only minimal testing in the NDK.
        bin_ext = '.exe' if self.host.is_windows else ''
        os.remove(os.path.join(install_path, 'bin/ld64.lld' + bin_ext))
        os.remove(os.path.join(install_path, 'bin/lld' + bin_ext))
        os.remove(os.path.join(install_path, 'bin/lld-link' + bin_ext))

        # Remove LLDB before it is ready for use.
        os.remove(os.path.join(install_path, 'bin/lldb' + bin_ext))

        install_clanglib = os.path.join(install_path, 'lib64', 'clang')
        linux_prebuilt_path = ndk.toolchains.ClangToolchain.path_for_host(
            ndk.hosts.Host.Linux)

        if self.host != ndk.hosts.Host.Linux:
            # We don't build target binaries as part of the Darwin or Windows
            # build. These toolchains need to get these from the Linux
            # prebuilts.
            #
            # The headers and libraries we care about are all in lib64/clang
            # for both toolchains, and those two are intended to be identical
            # between each host, so we can just replace them with the one from
            # the Linux toolchain.
            linux_clanglib = linux_prebuilt_path / 'lib64/clang'
            shutil.rmtree(install_clanglib)
            shutil.copytree(linux_clanglib, install_clanglib)

        # The Clang prebuilts have the platform toolchain libraries in
        # lib64/clang. The libraries we want are in runtimes_ndk_cxx.
        ndk_runtimes = linux_prebuilt_path / 'runtimes_ndk_cxx'
        versions = os.listdir(install_clanglib)
        for version in versions:
            version_dir = os.path.join(install_clanglib, version)
            dst_lib_dir = os.path.join(version_dir, 'lib/linux')
            shutil.rmtree(dst_lib_dir)
            shutil.copytree(ndk_runtimes, dst_lib_dir)

        # Also remove the other libraries that we installed, but they were only
        # installed on Linux.
        if self.host == ndk.hosts.Host.Linux:
            shutil.rmtree(os.path.join(install_path, 'runtimes_ndk_cxx'))

        # Remove CMake package files that should not be exposed.
        # For some reason the LLVM install includes CMake modules that expose
        # its internal APIs. We want to purge these so apps don't accidentally
        # depend on them. See http://b/142327416 for more info.
        cmake_modules_dir = os.path.join(install_path, 'lib64', 'cmake')
        shutil.rmtree(cmake_modules_dir)


def get_gcc_prebuilt_path(host: ndk.hosts.Host, arch: ndk.abis.Arch) -> str:
    """Returns the path to the GCC prebuilt for the given host/arch."""
    host_tag = ndk.hosts.host_to_tag(host)
    toolchain = ndk.abis.arch_to_toolchain(arch) + '-4.9'
    rel_prebuilt_path = os.path.join(
        'prebuilts/ndk/current/toolchains', host_tag, toolchain)
    prebuilt_path = ndk.paths.android_path(rel_prebuilt_path)
    if not os.path.isdir(prebuilt_path):
        raise RuntimeError(
            'Could not find prebuilt GCC at {}'.format(prebuilt_path))
    return prebuilt_path


def get_binutils_prebuilt_path(host: ndk.hosts.Host,
                               arch: ndk.abis.Arch) -> str:
    if host == ndk.hosts.Host.Windows64:
        host_dir_name = 'win64'
    else:
        host_dir_name = host.value

    binutils_name = f'binutils-{arch}-{host_dir_name}'
    prebuilt_path = ndk.paths.android_path('prebuilts/ndk', 'binutils',
                                           host_dir_name, binutils_name)
    if not os.path.isdir(prebuilt_path):
        raise RuntimeError(
            f'Could not find prebuilt binutils at {prebuilt_path}')
    return prebuilt_path


def versioned_so(host: ndk.hosts.Host, lib: str, version: str) -> str:
    """Returns the formatted versioned library for the given host.

    >>> versioned_so(ndk.hosts.Host.Darwin, 'libfoo', '0')
    'libfoo.0.dylib'
    >>> versioned_so(ndk.hosts.Host.Linux, 'libfoo', '0')
    'libfoo.so.0'
    """
    if host == ndk.hosts.Host.Darwin:
        return f'{lib}.{version}.dylib'
    elif host == ndk.hosts.Host.Linux:
        return f'{lib}.so.{version}'
    raise ValueError(f'Unsupported host: {host}')


def install_gcc_lib(install_path: str, host: ndk.hosts.Host,
                    arch: ndk.abis.Arch, subarch: str, lib_subdir: str,
                    libname: str) -> None:
    gcc_prebuilt = get_gcc_prebuilt_path(host, arch)
    lib_install_dir = os.path.join(install_path, lib_subdir, subarch)
    if not os.path.exists(lib_install_dir):
        os.makedirs(lib_install_dir)
    shutil.copy2(
        os.path.join(gcc_prebuilt, lib_subdir, subarch, libname),
        os.path.join(lib_install_dir, libname))


def install_gcc_crtbegin(install_path: str, host: ndk.hosts.Host,
                         arch: ndk.abis.Arch, subarch: str) -> None:
    triple = ndk.abis.arch_to_triple(arch)
    subdir = os.path.join('lib/gcc', triple, '4.9.x')
    install_gcc_lib(install_path, host, arch, subarch, subdir, 'crtbegin.o')


def install_libgcc(install_path: str,
                   host: ndk.hosts.Host,
                   arch: ndk.abis.Arch,
                   subarch: str,
                   new_layout: bool = False) -> None:
    triple = ndk.abis.arch_to_triple(arch)
    subdir = os.path.join('lib/gcc', triple, '4.9.x')
    install_gcc_lib(install_path, host, arch, subarch, subdir, 'libgcc.a')

    if new_layout:
        # For all architectures, we want to ensure that libcompiler_rt-extras
        # is linked when libgcc is linked. Some day this will be entirely
        # replaced by compiler-rt, but for now we are still dependent on libgcc
        # but still need some things from compiler_rt-extras.
        #
        # For ARM32 we need to use LLVM's libunwind rather than libgcc.
        # Unfortunately we still use libgcc for the compiler builtins, so we we
        # have to link both. To make sure that the LLVM unwinder gets used, add
        # a linker script for libgcc to make sure that libunwind is placed
        # first whenever libgcc is used. This also necessitates linking libdl
        # since libunwind makes use of dl_iterate_phdr.
        #
        # Historically we dealt with this in the libc++ linker script, but
        # since the new toolchain setup has the toolchain link the STL for us
        # the correct way to use the static libc++ is to use
        # `-static-libstdc++' which will expand to `-Bstatic -lc++ -Bshared`,
        # which results in the static libdl being used. The stub implementation
        # of libdl.a causes the unwind to fail, so we can't link libdl there.
        # If we don't link it at all, linking fails when building a static
        # executable since the driver does not link libdl when building a
        # static executable.
        #
        # We only do this for the new toolchain layout since build systems
        # using the legacy toolchain already needed to handle this, and
        # -lunwind may not be valid in those configurations (it could have been
        # linked by a full path instead).
        libgcc_base_path = os.path.join(install_path, subdir, subarch)
        libgcc_path = os.path.join(libgcc_base_path, 'libgcc.a')
        libgcc_real_path = os.path.join(libgcc_base_path, 'libgcc_real.a')
        shutil.move(libgcc_path, libgcc_real_path)
        if arch == 'arm':
            libs = '-lunwind -lcompiler_rt-extras -lgcc_real -ldl'
        else:
            libs = '-lcompiler_rt-extras -lgcc_real'
        with open(libgcc_path, 'w') as script:
            script.write('INPUT({})'.format(libs))


def install_libatomic(install_path: str, host: ndk.hosts.Host,
                      arch: ndk.abis.Arch, subarch: str) -> None:
    triple = ndk.abis.arch_to_triple(arch)
    subdir = os.path.join(triple, 'lib64' if arch.endswith('64') else 'lib')
    install_gcc_lib(install_path, host, arch, subarch, subdir, 'libatomic.a')


def get_subarches(arch: ndk.abis.Arch) -> List[str]:
    if arch != ndk.abis.Arch('arm'):
        return ['']

    return [
        '',
        'thumb',
        'armv7-a',
        'armv7-a/thumb'
    ]


class Binutils(ndk.builds.Module):
    name = 'binutils'
    path = 'toolchains/{toolchain}-4.9/prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN

    # TODO: Move GCC wrapper generation to Clang?
    deps = {
        'clang',
    }

    @property
    def notices(self) -> List[str]:
        notices = []
        for host in ndk.hosts.ALL_HOSTS:
            for arch in ndk.abis.ALL_ARCHITECTURES:
                prebuilt_path = get_gcc_prebuilt_path(host, arch)
                notices.append(os.path.join(prebuilt_path, 'NOTICE'))
        return notices

    def build(self) -> None:
        pass

    def install(self) -> None:
        for arch in self.arches:
            self.install_arch(arch)

    def install_arch(self, arch: ndk.abis.Arch) -> None:
        install_path = self.get_install_path(arch=arch)
        toolchain_path = get_binutils_prebuilt_path(self.host, arch)
        ndk.builds.install_directory(toolchain_path, install_path)

        # We still need libgcc/libatomic. Copy them from the old GCC prebuilts.
        for subarch in get_subarches(arch):
            install_libgcc(install_path, self.host, arch, subarch)
            install_libatomic(install_path, self.host, arch, subarch)

            # We don't actually want this, but Clang won't recognize a
            # -gcc-toolchain without it.
            install_gcc_crtbegin(install_path, self.host, arch, subarch)

        # Copy the LLVMgold plugin into the binutils plugin directory so ar can
        # use it.
        if self.host == ndk.hosts.Host.Linux:
            so = '.so'
        elif self.host == ndk.hosts.Host.Darwin:
            so = '.dylib'
        else:
            so = '.dll'

        clang_prebuilts = self.get_dep('clang').get_install_path()
        clang_bin = os.path.join(clang_prebuilts, 'bin')
        clang_libs = os.path.join(clang_prebuilts, 'lib64')
        llvmgold = os.path.join(clang_libs, 'LLVMgold' + so)

        bfd_plugins = os.path.join(install_path, 'lib/bfd-plugins')
        os.makedirs(bfd_plugins)
        shutil.copy2(llvmgold, bfd_plugins)

        if not self.host.is_windows:
            libcxx_1 = os.path.join(
                clang_libs, versioned_so(self.host, 'libc++', '1'))
            libcxx_abi_1 = os.path.join(
                clang_libs, versioned_so(self.host, 'libc++abi', '1'))

            # The rpath on LLVMgold.so is ../lib64, so we have to install to
            # lib/lib64 to have it be in the right place :(
            lib_dir = os.path.join(install_path, 'lib/lib64')
            os.makedirs(lib_dir)
            shutil.copy2(libcxx_1, lib_dir)
            shutil.copy2(libcxx_abi_1, lib_dir)
        else:
            libwinpthread = os.path.join(clang_bin, 'libwinpthread-1.dll')
            shutil.copy2(libwinpthread, bfd_plugins)


class ShaderTools(ndk.builds.InvokeBuildModule):
    name = 'shader-tools'
    path = 'shader-tools/{host}'
    script = 'build-shader-tools.py'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN

    @property
    def notices(self) -> List[str]:
        base = ndk.paths.android_path('external/shaderc')
        shaderc_dir = os.path.join(base, 'shaderc')
        spirv_dir = os.path.join(base, 'spirv-headers')
        return [
            os.path.join(shaderc_dir, 'LICENSE'),
            os.path.join(shaderc_dir, 'third_party', 'LICENSE.spirv-tools'),
            os.path.join(shaderc_dir, 'third_party', 'LICENSE.glslang'),
            os.path.join(spirv_dir, 'LICENSE')
        ]


class Make(ndk.builds.AutoconfModule):
    name = 'make'
    path = 'prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN
    src: Path = ndk.paths.ANDROID_DIR / 'toolchain/make'

    @property
    def notices(self) -> List[str]:
        return [str(self.src / 'COPYING')]


class Yasm(ndk.builds.AutoconfModule):
    name = 'yasm'
    path = 'prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN
    src: Path = ndk.paths.ANDROID_DIR / 'toolchain/yasm'

    @property
    def notices(self) -> List[str]:
        files = ['Artistic.txt', 'BSD.txt', 'COPYING', 'GNU_GPL-2.0',
                 'GNU_LGPL-2.0']
        return [str(self.src / f) for f in files]


class NdkWhich(ndk.builds.FileModule):
    name = 'ndk-which'
    path = 'prebuilt/{host}/bin'
    src = ndk.paths.ndk_path('ndk-which')


class HostTools(ndk.builds.Module):
    name = 'host-tools'
    path = 'prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN

    @property
    def notices(self) -> List[str]:
        return [
            ndk.paths.android_path('toolchain/python/Python-2.7.5/LICENSE'),
            ndk.paths.ndk_path('sources/host-tools/toolbox/NOTICE'),
        ]

    def build(self) -> None:
        build_args = ndk.builds.common_build_args(self.out_dir, self.dist_dir,
                                                  self.host)

        if self.host.is_windows:
            print('Building toolbox...')
            ndk.builds.invoke_external_build(
                'ndk/sources/host-tools/toolbox/build.py', build_args)

        print('Building Python...')
        ndk.builds.invoke_external_build(
            'toolchain/python/build.py', build_args)

    def install(self) -> None:
        install_dir = self.get_install_path()
        ndk.ext.shutil.create_directory(install_dir)

        packages = [
            'ndk-python'
        ]

        if self.host.is_windows:
            packages.append('toolbox')

        host_tag = ndk.hosts.host_to_tag(self.host)

        package_names = [p + '-' + host_tag + '.tar.bz2' for p in packages]
        for package_name in package_names:
            package_path = os.path.join(self.out_dir, self.host.value,
                                        package_name)
            subprocess.check_call(
                ['tar', 'xf', package_path, '-C', install_dir,
                 '--strip-components=1'])


def install_exe(out_dir: str, install_dir: str, name: str,
                host: ndk.hosts.Host) -> None:
    ext = '.exe' if host.is_windows else ''
    exe_name = name + ext
    src = os.path.join(out_dir, exe_name)
    dst = os.path.join(install_dir, exe_name)

    ndk.ext.shutil.create_directory(install_dir)
    shutil.copy2(src, dst)


def make_linker_script(path: str, libs: List[str]) -> None:
    ndk.file.write_file(path, 'INPUT({})\n'.format(' '.join(libs)))


def create_libcxx_linker_scripts(lib_dir: str, abi: ndk.abis.Abi,
                                 api: int) -> None:
    static_libs = ['-lc++_static', '-lc++abi']
    is_arm = abi == 'armeabi-v7a'
    needs_android_support = api < 21
    if needs_android_support:
        static_libs.append('-landroid_support')
    if is_arm:
        static_libs.extend(['-lunwind', '-latomic'])
    make_linker_script(
        os.path.join(lib_dir, 'libc++.a.{}'.format(api)), static_libs)

    shared_libs = []
    if needs_android_support:
        shared_libs.append('-landroid_support')
    if is_arm:
        shared_libs.extend(['-lunwind', '-latomic'])
    shared_libs.append('-lc++_shared')
    make_linker_script(
        os.path.join(lib_dir, 'libc++.so.{}'.format(api)), shared_libs)


class Libcxx(ndk.builds.Module):
    name = 'libc++'
    path = 'sources/cxx-stl/llvm-libc++'
    script = 'ndk/sources/cxx-stl/llvm-libc++/build.py'
    notice = ndk.paths.android_path('external/libcxx/NOTICE')
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN
    arch_specific = True
    deps = {
        'base-toolchain',
        'libandroid_support',
        'ndk-build',
        'ndk-build-shortcut',
    }

    libcxx_path = ndk.paths.android_path('external/libcxx')

    @property
    def obj_out(self) -> str:
        return os.path.join(self.out_dir, 'libcxx/obj')

    @property
    def lib_out(self) -> str:
        return os.path.join(self.out_dir, 'libcxx/libs')

    @property
    def abis(self) -> List[ndk.abis.Abi]:
        abis = []
        for arch in self.arches:
            abis.extend(ndk.abis.arch_to_abis(arch))
        return abis

    def build(self) -> None:
        ndk_build = os.path.join(
            self.get_dep('ndk-build').get_build_host_install(), 'ndk-build')
        bionic_path = ndk.paths.android_path('bionic')

        android_mk = os.path.join(self.libcxx_path, 'Android.mk')
        application_mk = os.path.join(self.libcxx_path, 'Application.mk')

        build_cmd = [
            'bash', ndk_build, build_support.jobs_arg(), 'V=1',
            'APP_ABI=' + ' '.join(self.abis),

            # Since nothing in this build depends on libc++_static, we need to
            # name it to force it to build.
            'APP_MODULES=c++_shared c++_static',

            'BIONIC_PATH=' + bionic_path,

            # Tell ndk-build where all of our makefiles are and where outputs
            # should go. The defaults in ndk-build are only valid if we have a
            # typical ndk-build layout with a jni/{Android,Application}.mk.
            'NDK_PROJECT_PATH=null',
            'APP_BUILD_SCRIPT=' + android_mk,
            'NDK_APPLICATION_MK=' + application_mk,
            'NDK_OUT=' + self.obj_out,
            'NDK_LIBS_OUT=' + self.lib_out,

            # Make sure we don't pick up a cached copy.
            'LIBCXX_FORCE_REBUILD=true',
        ]

        print('Running: ' + ' '.join([pipes.quote(arg) for arg in build_cmd]))
        subprocess.check_call(build_cmd)

    def install(self) -> None:
        install_root = self.get_install_path()

        if os.path.exists(install_root):
            shutil.rmtree(install_root)
        os.makedirs(install_root)

        shutil.copy2(
            os.path.join(self.libcxx_path, 'Android.mk'), install_root)
        shutil.copy2(
            os.path.join(self.libcxx_path, 'NOTICE'), install_root)
        shutil.copytree(
            os.path.join(self.libcxx_path, 'include'),
            os.path.join(install_root, 'include'))
        shutil.copytree(self.lib_out, os.path.join(install_root, 'libs'))

        for abi in self.abis:
            lib_dir = os.path.join(install_root, 'libs', abi)

            # The static libraries installed to the obj dir, not the lib dir.
            self.install_static_libs(lib_dir, abi)

            # Create linker scripts for the libraries we use so that we link
            # things properly even when we're not using ndk-build. The linker
            # will read the script in place of the library so that we link the
            # unwinder and other support libraries appropriately.
            platforms_meta = json.loads(
                ndk.file.read_file(ndk.paths.ndk_path('meta/platforms.json')))
            for api in range(platforms_meta['min'], platforms_meta['max'] + 1):
                if api < ndk.abis.min_api_for_abi(abi):
                    continue

                create_libcxx_linker_scripts(lib_dir, abi, api)

    def install_static_libs(self, lib_dir: str, abi: ndk.abis.Abi) -> None:
        static_lib_dir = os.path.join(self.obj_out, 'local', abi)

        shutil.copy2(os.path.join(static_lib_dir, 'libc++abi.a'), lib_dir)
        shutil.copy2(os.path.join(static_lib_dir, 'libc++_static.a'), lib_dir)

        if abi == 'armeabi-v7a':
            shutil.copy2(os.path.join(static_lib_dir, 'libunwind.a'), lib_dir)

        if abi in ndk.abis.LP32_ABIS:
            shutil.copy2(
                os.path.join(static_lib_dir, 'libandroid_support.a'), lib_dir)


class Platforms(ndk.builds.Module):
    name = 'platforms'
    path = 'platforms'

    deps = {
        'clang',
        'binutils',
    }

    min_supported_api = 16

    # These API levels had no new native APIs. The contents of these platforms
    # directories would be identical to the previous extant API level, so they
    # are not included in the NDK to save space.
    skip_apis = (20, 25)

    # We still need a numeric API level for codenamed API levels because
    # ABI_ANDROID_API in crtbrand is an integer. We start counting the
    # codenamed releases from 9000 and increment for each additional release.
    # This is filled by get_apis.
    codename_api_map: Dict[str, int] = {}

    # Shared with the sysroot, though the sysroot NOTICE actually includes a
    # lot more licenses. Platforms and Sysroot are essentially a single
    # component that is split into two directories only temporarily, so this
    # will be the end state when we merge the two anyway.
    notice = ndk.paths.android_path('prebuilts/ndk/platform/sysroot/NOTICE')

    def prebuilt_path(self, *args: str) -> str:  # pylint: disable=no-self-use
        return ndk.paths.android_path('prebuilts/ndk/platform', *args)

    def src_path(self, *args: str) -> str:  # pylint: disable=no-self-use
        return ndk.paths.android_path('development/ndk/platforms', *args)

    def binutils_tool(self, tool: str, arch: ndk.abis.Arch) -> str:
        triple = build_support.arch_to_triple(arch)
        binutils = self.get_dep('binutils').get_build_host_install(arch)
        return os.path.join(binutils, 'bin', triple + '-' + tool)

    # pylint: disable=no-self-use
    def libdir_name(self, arch: ndk.abis.Arch) -> str:
        if arch == 'x86_64':
            return 'lib64'
        else:
            return 'lib'
    # pylint: enable=no-self-use

    def get_apis(self) -> List[Union[int, str]]:
        codenamed_apis = []
        apis: List[Union[int, str]] = []
        for name in os.listdir(self.prebuilt_path('platforms')):
            if not name.startswith('android-'):
                continue

            _, api_str = name.split('-')
            try:
                api = int(api_str)
                if api >= self.min_supported_api:
                    apis.append(api)
            except ValueError:
                # Codenamed release like android-O, android-O-MR1, etc.
                # TODO: Remove this code path.
                # I don't think we're actually using this. Since having
                # non-integer API directories breaks all kinds of tools, we
                # rename them when we check them in.
                apis.append(api_str)
                codenamed_apis.append(api_str)

        for api_num, api_str in enumerate(sorted(codenamed_apis), start=9000):
            self.codename_api_map[api_str] = api_num
        return sorted(apis)

    # pylint: disable=no-self-use
    def get_arches(self, api: Union[int, str]) -> List[ndk.abis.Arch]:
        arches = [ndk.abis.Arch('arm'), ndk.abis.Arch('x86')]
        # All codenamed APIs are at 64-bit capable.
        if isinstance(api, str) or api >= 21:
            arches.extend([ndk.abis.Arch('arm64'), ndk.abis.Arch('x86_64')])
        return arches
    # pylint: enable=no-self-use

    def get_build_cmd(self, dst: str, srcs: List[str], api: int,
                      arch: ndk.abis.Arch,
                      build_number: Union[int, str]) -> List[str]:
        bionic_includes = ndk.paths.android_path(
            'bionic/libc/arch-common/bionic')

        cc = os.path.join(
            self.get_dep('clang').get_build_host_install(), 'bin/clang')
        binutils = self.get_dep('binutils').get_build_host_install(arch)

        args = [
            cc,
            '-target',
            ndk.abis.clang_target(arch),
            '--sysroot',
            self.prebuilt_path('sysroot'),
            '-gcc-toolchain',
            binutils,
            '-I',
            bionic_includes,
            '-D__ANDROID_API__={}'.format(api),
            '-DPLATFORM_SDK_VERSION={}'.format(api),
            '-DABI_NDK_VERSION="{}"'.format(ndk.config.release),
            '-DABI_NDK_BUILD_NUMBER="{}"'.format(build_number),
            '-O2',
            '-fpic',
            '-Wl,-r',
            '-no-pie',
            '-nostdlib',
            '-Wa,--noexecstack',
            '-Wl,-z,noexecstack',
            '-o',
            dst,
        ] + srcs

        if arch == ndk.abis.Arch('arm'):
            args.append('-mfpu=vfpv3-d16')

        return args

    def check_elf_note(self, obj_file: str) -> None:
        # readelf is a cross platform tool, so arch doesn't matter.
        readelf = self.binutils_tool('readelf', ndk.abis.Arch('arm'))
        out = subprocess.check_output([readelf, '--notes', obj_file])
        if 'Android' not in out.decode('utf-8'):
            raise RuntimeError(
                '{} does not contain NDK ELF note'.format(obj_file))

    def build_crt_object(self, dst: str, srcs: List[str], api: Union[int, str],
                         arch: ndk.abis.Arch, build_number: Union[int, str],
                         defines: List[str]) -> None:
        try:
            # No-op for stable releases.
            api_num = int(api)
        except ValueError:
            # ValueError means this was a codenamed release. We need the
            # integer matching this release for ABI_ANDROID_API in crtbrand.
            assert isinstance(api, str)
            api_num = self.codename_api_map[api]

        cc_args = self.get_build_cmd(dst, srcs, api_num, arch, build_number)
        cc_args.extend(defines)

        print('Running: ' + ' '.join([pipes.quote(arg) for arg in cc_args]))
        subprocess.check_call(cc_args)

    def build_crt_objects(self, dst_dir: str, api: Union[int, str],
                          arch: ndk.abis.Arch,
                          build_number: Union[int, str]) -> None:
        src_dir = ndk.paths.android_path('bionic/libc/arch-common/bionic')
        crt_brand = ndk.paths.ndk_path('sources/crt/crtbrand.S')

        objects = {
            'crtbegin_dynamic.o': [
                os.path.join(src_dir, 'crtbegin.c'),
                crt_brand,
            ],
            'crtbegin_so.o': [
                os.path.join(src_dir, 'crtbegin_so.c'),
                crt_brand,
            ],
            'crtbegin_static.o': [
                os.path.join(src_dir, 'crtbegin.c'),
                crt_brand,
            ],
            'crtend_android.o': [
                os.path.join(src_dir, 'crtend.S'),
            ],
            'crtend_so.o': [
                os.path.join(src_dir, 'crtend_so.S'),
            ],
        }

        for name, srcs in objects.items():
            dst_path = os.path.join(dst_dir, name)
            defs = []
            if name == 'crtbegin_static.o':
                # libc.a is always the latest version, so ignore the API level
                # setting for crtbegin_static.
                defs.append('-D_FORCE_CRT_ATFORK')
            self.build_crt_object(
                dst_path, srcs, api, arch, build_number, defs)
            if name.startswith('crtbegin'):
                self.check_elf_note(dst_path)

    def build(self) -> None:
        build_dir = os.path.join(self.out_dir, self.path)
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)

        for api in self.get_apis():
            if api in self.skip_apis:
                continue

            platform = 'android-{}'.format(api)
            for arch in self.get_arches(api):
                arch_name = 'arch-{}'.format(arch)
                dst_dir = os.path.join(build_dir, platform, arch_name)
                os.makedirs(dst_dir)
                assert self.context is not None
                self.build_crt_objects(dst_dir, api, arch,
                                       self.context.build_number)

    def install(self) -> None:
        build_dir = os.path.join(self.out_dir, self.path)
        install_dir = self.get_install_path()

        if os.path.exists(install_dir):
            shutil.rmtree(install_dir)
        os.makedirs(install_dir)

        for api in self.get_apis():
            if api in self.skip_apis:
                continue

            # Copy shared libraries from prebuilts/ndk/platform/platforms.
            platform = 'android-{}'.format(api)
            platform_src = self.prebuilt_path('platforms', platform)
            platform_dst = os.path.join(install_dir, 'android-{}'.format(api))
            shutil.copytree(platform_src, platform_dst)

            for arch in self.get_arches(api):
                arch_name = 'arch-{}'.format(arch)
                triple = ndk.abis.arch_to_triple(arch)

                # Install static libraries from prebuilts/ndk/platform/sysroot.
                # TODO: Determine if we can change the build system to use the
                # libraries directly from the sysroot directory rather than
                # duplicating all the libraries in platforms.
                lib_dir = self.prebuilt_path('sysroot/usr/lib', triple)
                libdir_name = self.libdir_name(arch)
                lib_dir_dst = os.path.join(
                    install_dir, platform, arch_name, 'usr', libdir_name)
                for name in os.listdir(lib_dir):
                    lib_src = os.path.join(lib_dir, name)
                    lib_dst = os.path.join(lib_dir_dst, name)
                    shutil.copy2(lib_src, lib_dst)

                if libdir_name == 'lib64':
                    # The Clang driver won't accept a sysroot that contains
                    # only a lib64. An empty lib dir is enough to convince it.
                    os.makedirs(os.path.join(
                        install_dir, platform, arch_name, 'usr/lib'))

                # Install the CRT objects that we just built.
                obj_dir = os.path.join(build_dir, platform, arch_name)
                for name in os.listdir(obj_dir):
                    obj_src = os.path.join(obj_dir, name)
                    obj_dst = os.path.join(lib_dir_dst, name)
                    shutil.copy2(obj_src, obj_dst)

        # https://github.com/android-ndk/ndk/issues/372
        for root, dirs, files in os.walk(install_dir):
            if not files and not dirs:
                with open(os.path.join(root, '.keep_dir'), 'w') as keep_file:
                    keep_file.write(
                        'This file forces git to keep the directory.')


class Gdb(ndk.builds.Module):
    """Module for multi-arch host GDB.

    Note that the device side, gdbserver, is a separate module because it needs
    to be cross compiled for all four Android ABIs.
    """

    name = 'gdb'
    path = 'prebuilt/{host}'

    deps = {
        'make',
        'host-tools',
    }

    GDB_VERSION = '8.3'

    expat_src = ndk.paths.ANDROID_DIR / 'toolchain/expat/expat-2.0.1'
    lzma_src = ndk.paths.ANDROID_DIR / 'toolchain/xz'
    gdb_src = ndk.paths.ANDROID_DIR / f'toolchain/gdb/gdb-{GDB_VERSION}'

    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN

    _expat_builder: Optional[ndk.autoconf.AutoconfBuilder] = None
    _lzma_builder: Optional[ndk.autoconf.AutoconfBuilder] = None
    _gdb_builder: Optional[ndk.autoconf.AutoconfBuilder] = None

    @property
    def notices(self) -> List[str]:
        return [
            str(self.expat_src / 'COPYING'),
            str(self.gdb_src / 'COPYING'),
            str(self.lzma_src / 'COPYING'),
        ]

    @property
    def expat_builder(self) -> ndk.autoconf.AutoconfBuilder:
        """Returns the lazily initialized expat builder for this module."""
        if self._expat_builder is None:
            self._expat_builder = ndk.autoconf.AutoconfBuilder(
                self.expat_src / 'configure',
                self.intermediate_out_dir / 'expat',
                self.host,
                use_clang=True)
        return self._expat_builder

    @property
    def lzma_builder(self) -> ndk.autoconf.AutoconfBuilder:
        """Returns the lazily initialized lzma builder for this module."""
        if self._lzma_builder is None:
            self._lzma_builder = ndk.autoconf.AutoconfBuilder(
                self.lzma_src / 'configure',
                self.intermediate_out_dir / 'lzma',
                self.host,
                add_toolchain_to_path=True,
                use_clang=True)
        return self._lzma_builder

    @property
    def gdb_builder(self) -> ndk.autoconf.AutoconfBuilder:
        """Returns the lazily initialized gdb builder for this module."""
        if self._gdb_builder is None:
            no_build_or_host = False
            no_strip = False
            additional_flags = []
            if self.host == ndk.hosts.Host.Darwin:
                # Awful Darwin hack. For some reason GDB doesn't produce a gdb
                # executable when using --build/--host.
                no_build_or_host = True
                # -s caused mysterious linking error on old macOS like:
                # "ld: internal error: atom not found in symbolIndex(...)"
                # Seems old ld64 wrongly stripped some symbols.
                # Remove this when build server upgrades to xcode 7.3
                # (ld64-264.3.101) or above.
                no_strip = True
                additional_flags.append('-Wl,-rpath,@loader_path/../lib')
            if self.host == ndk.hosts.Host.Linux:
                additional_flags.append('-Wl,-rpath,$$$$\\ORIGIN/../lib')
            # Add path for libc++.
            clang_path = ndk.toolchains.ClangToolchain.path_for_host(self.host)
            additional_flags.append('-L' + str(clang_path / 'lib64'))
            self._gdb_builder = ndk.autoconf.AutoconfBuilder(
                self.gdb_src / 'configure',
                self.intermediate_out_dir / 'gdb',
                self.host,
                use_clang=True,
                no_build_or_host=no_build_or_host,
                no_strip=no_strip,
                additional_flags=additional_flags)
        return self._gdb_builder

    @property
    def gdb_stub_install_path(self) -> Path:
        """The gdb stub install path."""
        return self.gdb_builder.install_directory / 'bin/gdb-stub'

    def build_expat(self) -> None:
        """Builds the expat dependency."""
        self.expat_builder.build([
            '--disable-shared',
            '--enable-static',
        ])

    def build_lzma(self) -> None:
        """Builds the liblzma dependency."""
        self.lzma_builder.build([
            '--disable-shared',
            '--enable-static',
            '--disable-xz',
            '--disable-xzdec',
            '--disable-lzmadev',
            '--disable-scripts',
            '--disable-doc',
        ])

    def build_gdb(self) -> None:
        """Builds GDB itself."""
        targets = ' '.join(ndk.abis.ALL_TRIPLES)
        # TODO: Cleanup Python module so we don't need this explicit path.
        python_config = (Path(
            self.out_dir) / self.host.value / 'python' / ndk.hosts.host_to_tag(
                self.host) / 'install/host-tools/bin/python-config.sh')
        configure_args = [
            '--with-expat',
            f'--with-libexpat-prefix={self.expat_builder.install_directory}',
            f'--with-python={python_config}',
            f'--enable-targets={targets}',
            '--disable-shared',
            '--disable-werror',
            '--disable-nls',
            '--disable-docs',
            '--without-mpc',
            '--without-mpfr',
            '--without-gmp',
            '--without-isl',
            '--disable-sim',
            '--enable-gdbserver=no',
        ]

        configure_args.extend([
            '--with-lzma',
            f'--with-liblzma-prefix={self.lzma_builder.install_directory}',
        ])

        self.gdb_builder.build(configure_args)

    def build_gdb_stub(self) -> None:
        """Builds a gdb wrapper to setup PYTHONHOME.

        We need to use gdb with the Python it was built against, so we need to
        setup PYTHONHOME to point to the NDK's Python, not the host's.
        """
        if self.host.is_windows:
            # TODO: Does it really need to be an executable?
            # It's probably an executable because the original author wanted a
            # .exe rather than a .cmd. Not sure how disruptive this change
            # would be now. Presumably hardly at all because everyone needs to
            # use ndk-gdb for reasonable behavior anyway?
            self.build_exe_gdb_stub()
        else:
            self.build_sh_gdb_stub()

    def build_exe_gdb_stub(self) -> None:
        # Don't need to worry about extension here because it'll be renamed on
        # install anyway.
        gdb_stub_path = self.gdb_builder.install_directory / 'bin/gdb-stub'
        stub_src = ndk.paths.NDK_DIR / 'sources/host-tools/gdb-stub/gdb-stub.c'
        mingw_path = (ndk.paths.ANDROID_DIR /
                      'prebuilts/gcc/linux-x86/host/x86_64-w64-mingw32-4.8/bin'
                      / 'x86_64-w64-mingw32-gcc')

        cmd = [
            str(mingw_path),
            '-O2',
            '-s',
            '-DNDEBUG',
            str(stub_src),
            '-o',
            str(gdb_stub_path),
        ]
        pp_cmd = ' '.join([pipes.quote(arg) for arg in cmd])
        print('Running: {}'.format(pp_cmd))
        subprocess.run(cmd, check=True)

    def build_sh_gdb_stub(self) -> None:
        self.gdb_stub_install_path.write_text(
            textwrap.dedent("""\
            #!/bin/bash
            GDBDIR=$(cd $(dirname $0) && pwd)
            PYTHONHOME="$GDBDIR/.." "$GDBDIR/gdb-orig" "$@"
            """))
        self.gdb_stub_install_path.chmod(0o755)

    def build(self) -> None:
        """Builds GDB."""
        if self.intermediate_out_dir.exists():
            shutil.rmtree(self.intermediate_out_dir)

        self.build_expat()
        self.build_lzma()
        self.build_gdb()
        self.build_gdb_stub()

    def install(self) -> None:
        """Installs GDB."""
        install_dir = Path(self.get_install_path())
        copy_tree(
            str(self.gdb_builder.install_directory / 'bin'),
            str(install_dir / 'bin'))
        gdb_share_dir = self.gdb_builder.install_directory / 'share/gdb'
        gdb_share_install_dir = install_dir / 'share/gdb'
        if gdb_share_install_dir.exists():
            shutil.rmtree(gdb_share_install_dir)
        shutil.copytree(gdb_share_dir, gdb_share_install_dir)

        exe_suffix = '.exe' if self.host.is_windows else ''
        gdb_exe = install_dir / ('bin/gdb' + exe_suffix)

        # Strip is skipped when build. Strip the binary now.
        if self.host == ndk.hosts.Host.Darwin:
            cmd = [str(self.gdb_builder.toolchain.strip), str(gdb_exe)]
            subprocess.check_call(cmd)

        # gdb is currently gdb(.exe)? and the gdb stub is currently gdb-stub.
        # Make them gdb-orig(.exe)? and gdb(.exe)? respectively.
        gdb_exe.rename(install_dir / ('bin/gdb-orig' + exe_suffix))

        gdb_stub = install_dir / 'bin/gdb-stub'
        gdb_stub.rename(install_dir / ('bin/gdb' + exe_suffix))

        # Install libc++.
        clang_path = ndk.toolchains.ClangToolchain.path_for_host(self.host)
        libcxx_files = {
            ndk.hosts.Host.Darwin: ['libc++abi.1.dylib', 'libc++.1.dylib'],
            ndk.hosts.Host.Linux: ['libc++abi.so.1', 'libc++.so.1'],
            ndk.hosts.Host.Windows64: [],
        }
        for f in libcxx_files[self.host]:
            shutil.copy(clang_path / 'lib64' / f, install_dir / 'lib')


class GdbServer(ndk.builds.Module):
    name = 'gdbserver'
    path = 'prebuilt/android-{arch}/gdbserver'
    notice = ndk.paths.android_path(
        f'toolchain/gdb/gdb-{Gdb.GDB_VERSION}/gdb/COPYING')
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN
    arch_specific = True
    split_build_by_arch = True
    deps = {
        'toolchain',
    }
    max_api = Platforms().get_apis()[-1]

    libthread_db_src_dir = ndk.paths.ndk_path('sources/android/libthread_db')
    gdbserver_src_dir = ndk.paths.android_path(
        f'toolchain/gdb/gdb-{Gdb.GDB_VERSION}/gdb/gdbserver')

    @property
    def build_dir(self) -> str:
        """Returns the build directory for the current architecture."""
        assert self.build_arch is not None
        return os.path.join(self.out_dir, self.name, self.build_arch)

    @property
    def libthread_db_a_path(self) -> str:
        """Returns the path to the built libthread_db.a."""
        return os.path.join(self.build_dir, 'libthread_db.a')

    def get_tool(self, name: str, arch: Optional[ndk.abis.Arch] = None) -> str:
        """Returns the path to the given tool in the toolchain.

        Args:
            name: Name of the tool. e.g. 'ar'.
            arch: Optional architecture for architecture specific tools.

        Returns:
            Path to the specified tool.
        """
        toolchain_bin = os.path.join(
            self.get_dep('toolchain').get_build_host_install(), 'bin')

        if arch is not None:
            triple = ndk.abis.arch_to_triple(arch)
            name = f'{triple}-{name}'
        return os.path.join(toolchain_bin, name)

    def build_libthread_db(self, api_level: int) -> None:
        """Builds libthread_db.a for the current architecture."""
        assert self.build_arch is not None

        libthread_db_c = os.path.join(self.libthread_db_src_dir,
                                      'libthread_db.c')
        libthread_db_o = os.path.join(self.build_dir, 'libthread_db.o')
        cc_args = [
            self.get_tool('clang'),
            '-target',
            ndk.abis.clang_target(self.build_arch, api_level),
            '-I',
            self.libthread_db_src_dir,
            '-o',
            libthread_db_o,
            '-c',
            libthread_db_c,
        ]

        print('Running: {}'.format(' '.join(
            [pipes.quote(arg) for arg in cc_args])))
        subprocess.run(cc_args, check=True)

        ar_args = [
            self.get_tool('ar', self.build_arch),
            'rD',
            self.libthread_db_a_path,
            libthread_db_o,
        ]

        print('Running: {}'.format(' '.join(
            [pipes.quote(arg) for arg in ar_args])))
        subprocess.run(ar_args, check=True)

    def configure(self, api_level: int) -> None:
        """Configures the gdbserver build for the current architecture."""
        assert self.build_arch is not None
        gdbserver_host = {
            'arm': 'arm-eabi-linux',
            'arm64': 'aarch64-eabi-linux',
            'x86': 'i686-linux-android',
            'x86_64': 'x86_64-linux-android',
        }[self.build_arch]

        cflags = ['-O2', '-I' + self.libthread_db_src_dir]
        if self.build_arch.startswith('arm'):
            cflags.append('-fno-short-enums')
        if self.build_arch.endswith('64'):
            cflags.append('-DUAPI_HEADERS')

        ldflags = '-static -fuse-ld=gold -Wl,-z,nocopyreloc -Wl,--no-undefined'

        # Use --target as part of CC so it is used when linking as well.
        clang = '{} --target={}'.format(
            self.get_tool('clang'),
            ndk.abis.clang_target(self.build_arch, api_level))
        clangplusplus = '{} --target={}'.format(
            self.get_tool('clang++'),
            ndk.abis.clang_target(self.build_arch, api_level))
        configure_env = {
            'CC': clang,
            'CXX': clangplusplus,
            'AR': self.get_tool('ar', self.build_arch),
            'RANLIB': self.get_tool('ranlib', self.build_arch),
            'CFLAGS': ' '.join(cflags),
            'CXXFLAGS': ' '.join(cflags),
            'LDFLAGS': ldflags,
        }

        configure_args = [
            os.path.join(self.gdbserver_src_dir, 'configure'),
            '--build=x86_64-linux-gnu',
            f'--host={gdbserver_host}',
            f'--with-libthread-db={self.libthread_db_a_path}',
            '--disable-inprocess-agent',
            '--enable-werror=no',
        ]

        subproc_env = dict(os.environ)
        subproc_env.update(configure_env)
        print('Running: {} with env:\n{}'.format(
            ' '.join([pipes.quote(arg) for arg in configure_args]),
            pprint.pformat(configure_env, indent=4)))
        subprocess.run(configure_args, env=subproc_env, check=True)

    def make(self) -> None:
        """Runs make for the configured build."""
        subprocess.run(['make', build_support.jobs_arg()], check=True)

    def build(self) -> None:
        """Builds gdbserver."""
        if not os.path.exists(self.build_dir):
            os.makedirs(self.build_dir)

        max_api = Platforms().get_apis()[-1]
        # Make sure the max_api is not a codenamed release. It should never
        # happen since letters will sort before numbers.
        assert isinstance(max_api, int)
        with ndk.ext.os.cd(self.build_dir):
            self.build_libthread_db(max_api)
            self.configure(max_api)
            self.make()

    def install(self) -> None:
        """Installs gdbserver."""
        if os.path.exists(self.get_install_path()):
            shutil.rmtree(self.get_install_path())
        os.makedirs(self.get_install_path())

        objcopy_args = [
            self.get_tool('objcopy', self.build_arch),
            '--strip-unneeded',
            os.path.join(self.build_dir, 'gdbserver'),
            os.path.join(self.get_install_path(), 'gdbserver'),
        ]
        subprocess.run(objcopy_args, check=True)


class LibShaderc(ndk.builds.Module):
    name = 'libshaderc'
    path = 'sources/third_party/shaderc'
    src = ndk.paths.android_path('external/shaderc')
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN

    @property
    def notices(self) -> List[str]:
        shaderc_dir = os.path.join(self.src, 'shaderc')
        return [
            os.path.join(shaderc_dir, 'LICENSE'),
            os.path.join(shaderc_dir, 'third_party', 'LICENSE.glslang'),
            os.path.join(shaderc_dir, 'third_party', 'LICENSE.spirv-tools'),
        ]

    def build(self) -> None:
        copies = [
            {
                'source_dir': os.path.join(self.src, 'shaderc'),
                'dest_dir': 'shaderc',
                'files': [
                    'Android.mk', 'libshaderc/Android.mk',
                    'libshaderc_util/Android.mk',
                    'third_party/Android.mk',
                    'utils/update_build_version.py',
                    'CHANGES',
                ],
                'dirs': [
                    'libshaderc/include', 'libshaderc/src',
                    'libshaderc_util/include', 'libshaderc_util/src',
                ],
            },
            {
                'source_dir': os.path.join(self.src, 'spirv-tools'),
                'dest_dir': 'shaderc/third_party/spirv-tools',
                'files': [
                    'utils/generate_grammar_tables.py',
                    'utils/generate_language_headers.py',
                    'utils/generate_registry_tables.py',
                    'utils/update_build_version.py',
                    'Android.mk',
                    'CHANGES',
                ],
                'dirs': ['include', 'source'],
            },
            {
                'source_dir': os.path.join(self.src, 'spirv-headers'),
                'dest_dir':
                    'shaderc/third_party/spirv-tools/external/spirv-headers',
                'dirs': ['include'],
                'files': [
                    'include/spirv/1.0/spirv.py',
                    'include/spirv/1.1/spirv.py',
                    'include/spirv/1.2/spirv.py'
                    'include/spirv/uinified1/spirv.py'
                ],
            },
            {
                'source_dir': os.path.join(self.src, 'glslang'),
                'dest_dir': 'shaderc/third_party/glslang',
                'files': ['Android.mk', 'glslang/OSDependent/osinclude.h'],
                'dirs': [
                    'SPIRV',
                    'OGLCompilersDLL',
                    'glslang/GenericCodeGen',
                    'hlsl',
                    'glslang/Include',
                    'glslang/MachineIndependent',
                    'glslang/OSDependent/Unix',
                    'glslang/Public',
                ],
            },
        ]

        default_ignore_patterns = shutil.ignore_patterns(
            "*CMakeLists.txt",
            "*.py",
            "*test.h",
            "*test.cc")

        temp_dir = tempfile.mkdtemp()
        shaderc_path = os.path.join(temp_dir, 'shaderc')
        try:
            for properties in copies:
                source_dir = properties['source_dir']
                assert isinstance(source_dir, str)
                assert isinstance(properties['dest_dir'], str)
                dest_dir = os.path.join(temp_dir, properties['dest_dir'])
                for d in properties['dirs']:
                    assert isinstance(d, str)
                    src = os.path.join(source_dir, d)
                    dst = os.path.join(dest_dir, d)
                    print(src, " -> ", dst)
                    shutil.copytree(src, dst,
                                    ignore=default_ignore_patterns)
                for f in properties['files']:
                    print(source_dir, ':', dest_dir, ":", f)
                    # Only copy if the source file exists.  That way
                    # we can update this script in anticipation of
                    # source files yet-to-come.
                    assert isinstance(f, str)
                    if os.path.exists(os.path.join(source_dir, f)):
                        install_file(f, source_dir, dest_dir)
                    else:
                        print(source_dir, ':', dest_dir, ":", f, "SKIPPED")

            build_support.make_package('libshaderc', shaderc_path,
                                       self.dist_dir)
        finally:
            shutil.rmtree(temp_dir)


class CpuFeatures(ndk.builds.PackageModule):
    name = 'cpufeatures'
    path = 'sources/android/cpufeatures'
    src = ndk.paths.ndk_path('sources/android/cpufeatures')


class NativeAppGlue(ndk.builds.PackageModule):
    name = 'native_app_glue'
    path = 'sources/android/native_app_glue'
    src = ndk.paths.ndk_path('sources/android/native_app_glue')


class NdkHelper(ndk.builds.PackageModule):
    name = 'ndk_helper'
    path = 'sources/android/ndk_helper'
    src = ndk.paths.ndk_path('sources/android/ndk_helper')


class Gtest(ndk.builds.PackageModule):
    name = 'gtest'
    path = 'sources/third_party/googletest'
    src = ndk.paths.android_path('external/googletest/googletest')

    def install(self) -> None:
        super().install()

        # GTest renamed these files to be all lower case, but the SDK patcher
        # doesn't handle that properly. Rename them back to the old names so
        # the SDK patches apply properly.
        # http://b/122741472
        install_dir = self.get_install_path()
        docs_dir = os.path.join(install_dir, 'docs')
        rename_map = {
            'faq.md': 'FAQ.md',
            'primer.md': 'Primer.md',
            'samples.md': 'Samples.md',
        }
        for rename_from, rename_to in rename_map.items():
            os.rename(
                os.path.join(docs_dir, rename_from),
                os.path.join(docs_dir, rename_to))


class Sysroot(ndk.builds.Module):
    name = 'sysroot'
    path = 'sysroot'
    notice = ndk.paths.android_path('prebuilts/ndk/platform/sysroot/NOTICE')

    def build(self) -> None:
        temp_dir = tempfile.mkdtemp()
        try:
            path = ndk.paths.android_path('prebuilts/ndk/platform/sysroot')
            install_path = os.path.join(temp_dir, 'sysroot')
            shutil.copytree(path, install_path)
            if self.host != 'linux':
                # linux/netfilter has some headers with names that differ only
                # by case, which can't be extracted to a case-insensitive
                # filesystem, which are the defaults for Darwin and Windows :(
                #
                # There isn't really a good way to decide which of these to
                # keep and which to remove. The capitalized versions expose
                # different APIs, but we can't keep both. So far no one has
                # filed bugs about needing either API, so let's just dedup them
                # consistently and we can change that if we hear otherwise.
                remove_paths = [
                    'usr/include/linux/netfilter_ipv4/ipt_ECN.h',
                    'usr/include/linux/netfilter_ipv4/ipt_TTL.h',
                    'usr/include/linux/netfilter_ipv6/ip6t_HL.h',
                    'usr/include/linux/netfilter/xt_CONNMARK.h',
                    'usr/include/linux/netfilter/xt_DSCP.h',
                    'usr/include/linux/netfilter/xt_MARK.h',
                    'usr/include/linux/netfilter/xt_RATEEST.h',
                    'usr/include/linux/netfilter/xt_TCPMSS.h',
                ]
                for remove_path in remove_paths:
                    os.remove(os.path.join(install_path, remove_path))

            ndk_version_h_path = os.path.join(
                install_path, 'usr/include/android/ndk-version.h')
            with open(ndk_version_h_path, 'w') as ndk_version_h:
                major = ndk.config.major
                minor = ndk.config.hotfix
                beta = ndk.config.beta
                canary = '1' if ndk.config.canary else '0'
                assert self.context is not None
                build = self.context.build_number
                if build == 'dev':
                    build = '0'

                ndk_version_h.write(textwrap.dedent("""\
                    #ifndef ANDROID_NDK_VERSION_H
                    #define ANDROID_NDK_VERSION_H

                    /**
                     * Major version of this NDK.
                     *
                     * For example: 16 for r16.
                     */
                    #define __NDK_MAJOR__ {major}

                    /**
                     * Minor version of this NDK.
                     *
                     * For example: 0 for r16 and 1 for r16b.
                     */
                    #define __NDK_MINOR__ {minor}

                    /**
                     * Set to 0 if this is a release build, or 1 for beta 1,
                     * 2 for beta 2, and so on.
                     */
                    #define __NDK_BETA__ {beta}

                    /**
                     * Build number for this NDK.
                     *
                     * For a local development build of the NDK, this is -1.
                     */
                    #define __NDK_BUILD__ {build}

                    /**
                     * Set to 1 if this is a canary build, 0 if not.
                     */
                    #define __NDK_CANARY__ {canary}

                    #endif  /* ANDROID_NDK_VERSION_H */
                    """.format(
                        major=major,
                        minor=minor,
                        beta=beta,
                        build=build,
                        canary=canary)))

            build_support.make_package('sysroot', install_path, self.dist_dir)
        finally:
            shutil.rmtree(temp_dir)


def write_clang_shell_script(wrapper_path: str, clang_name: str,
                             flags: List[str]) -> None:
    with open(wrapper_path, 'w') as wrapper:
        wrapper.write(textwrap.dedent("""\
            #!/bin/bash
            if [ "$1" != "-cc1" ]; then
                `dirname $0`/{clang} {flags} "$@"
            else
                # Target is already an argument.
                `dirname $0`/{clang} "$@"
            fi
        """.format(clang=clang_name, flags=' '.join(flags))))

    mode = os.stat(wrapper_path).st_mode
    os.chmod(wrapper_path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def write_clang_batch_script(wrapper_path: str, clang_name: str,
                             flags: List[str]) -> None:
    with open(wrapper_path, 'w') as wrapper:
        wrapper.write(textwrap.dedent("""\
            @echo off
            setlocal
            call :find_bin
            if "%1" == "-cc1" goto :L

            set "_BIN_DIR=" && %_BIN_DIR%{clang} {flags} %*
            if ERRORLEVEL 1 exit /b 1
            goto :done

            :L
            rem Target is already an argument.
            set "_BIN_DIR=" && %_BIN_DIR%{clang} %*
            if ERRORLEVEL 1 exit /b 1
            goto :done

            :find_bin
            rem Accommodate a quoted arg0, e.g.: "clang"
            rem https://github.com/android-ndk/ndk/issues/616
            set _BIN_DIR=%~dp0
            exit /b

            :done
        """.format(clang=clang_name, flags=' '.join(flags))))


def write_clang_wrapper(install_dir: str, api: int, triple: str,
                        is_windows: bool) -> None:
    """Writes a target-specific Clang wrapper.

    This wrapper can be used to target the given architecture/API combination
    without needing to specify -target. These obviate the need for standalone
    toolchains.

    Ideally these would be symlinks rather than wrapper scripts to avoid the
    unnecessary indirection (Clang will infer its default target based on
    argv[0]), but the SDK manager can't install symlinks and Windows only
    allows administrators to create them.
    """
    exe_suffix = '.exe' if is_windows else ''

    if triple.startswith('arm-linux'):
        triple = 'armv7a-linux-androideabi'

    wrapper_path = os.path.join(install_dir, '{}{}-clang'.format(triple, api))
    wrapperxx_path = wrapper_path + '++'

    flags = ['--target={}{}'.format(triple, api)]

    # TODO: Hoist into the driver.
    if triple.startswith('i686') and api < 24:
        flags.append('-mstackrealign')

    # Write shell scripts even for Windows to support WSL and Cygwin.
    write_clang_shell_script(wrapper_path, 'clang' + exe_suffix, flags)
    write_clang_shell_script(wrapperxx_path, 'clang++' + exe_suffix, flags)
    if is_windows:
        write_clang_batch_script(wrapper_path + '.cmd', 'clang' + exe_suffix,
                                 flags)
        write_clang_batch_script(wrapperxx_path + '.cmd',
                                 'clang++' + exe_suffix, flags)


class BaseToolchain(ndk.builds.Module):
    """The subset of the toolchain needed to build other toolchain components.

    libc++ is built using this toolchain, and the full toolchain requires
    libc++. The toolchain is split into BaseToolchain and Toolchain to break
    the cyclic dependency.
    """

    name = 'base-toolchain'
    # This is installed to the Clang location to avoid migration pain.
    path = 'toolchains/llvm/prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN
    deps = {
        'binutils',
        'clang',
        'libandroid_support',
        'make',
        'platforms',
        'sysroot',
        'system-stl',
        'yasm',
    }

    @property
    def notices(self) -> List[str]:
        return (Binutils().notices + Clang().notices + Yasm().notices
                + LibAndroidSupport().notices + Platforms().notices +
                Sysroot().notices + SystemStl().notices)

    def build(self) -> None:
        pass

    def install(self) -> None:
        install_dir = self.get_install_path()
        yasm_dir = self.get_dep('yasm').get_install_path()
        libandroid_support_dir = self.get_dep(
            'libandroid_support').get_install_path()
        platforms_dir = self.get_dep('platforms').get_install_path()
        sysroot_dir = self.get_dep('sysroot').get_install_path()
        system_stl_dir = self.get_dep('system-stl').get_install_path()

        copy_tree(sysroot_dir, os.path.join(install_dir, 'sysroot'))

        exe = '.exe' if self.host.is_windows else ''
        shutil.copy2(
            os.path.join(yasm_dir, 'bin', 'yasm' + exe),
            os.path.join(install_dir, 'bin'))

        for arch in self.arches:
            binutils_dir = self.get_dep('binutils').get_install_path(arch=arch)
            copy_tree(binutils_dir, install_dir)

        platforms = self.get_dep('platforms')
        assert isinstance(platforms, Platforms)
        for api in platforms.get_apis():
            if api in Platforms.skip_apis:
                continue

            platform = 'android-{}'.format(api)
            for arch in platforms.get_arches(api):
                triple = ndk.abis.arch_to_triple(arch)
                arch_name = 'arch-{}'.format(arch)
                lib_dir = 'lib64' if arch == 'x86_64' else 'lib'
                src_dir = os.path.join(platforms_dir, platform, arch_name,
                                       'usr', lib_dir)
                dst_dir = os.path.join(install_dir, 'sysroot/usr/lib', triple,
                                       str(api))
                shutil.copytree(src_dir, dst_dir)
                # TODO: Remove duplicate static libraries from this directory.
                # We already have them in the version-generic directory.

                assert isinstance(api, int)
                write_clang_wrapper(
                    os.path.join(install_dir, 'bin'), api, triple,
                    self.host.is_windows)

        # Clang searches for libstdc++ headers at $GCC_PATH/../include/c++. It
        # maybe be worth adding a search for the same path within the usual
        # sysroot location to centralize these, or possibly just remove them
        # from the NDK since they aren't particularly useful anyway.
        system_stl_hdr_dir = os.path.join(install_dir, 'include/c++')
        os.makedirs(system_stl_hdr_dir)
        system_stl_inc_src = os.path.join(system_stl_dir, 'include')
        system_stl_inc_dst = os.path.join(system_stl_hdr_dir, '4.9.x')
        shutil.copytree(system_stl_inc_src, system_stl_inc_dst)

        # $SYSROOT/usr/local/include comes before $SYSROOT/usr/include, so we
        # can use that for libandroid_support's headers. Puting them here
        # *does* mean that libandroid_support's headers get used even when
        # we're not using libandroid_support, but they should be a no-op for
        # android-21+ and in the case of pre-21 without libandroid_support
        # (libstdc++), we're only degrading the UX; the user will get a linker
        # error instead of a compiler error.
        support_hdr_dir = os.path.join(install_dir, 'sysroot/usr/local')
        os.makedirs(support_hdr_dir)
        support_inc_src = os.path.join(libandroid_support_dir, 'include')
        support_inc_dst = os.path.join(support_hdr_dir, 'include')
        shutil.copytree(support_inc_src, support_inc_dst)


class Vulkan(ndk.builds.Module):
    name = 'vulkan'
    path = 'sources/third_party/vulkan'

    @property
    def notices(self) -> List[str]:
        base = ndk.paths.android_path('external')
        headers_dir = os.path.join(base, 'vulkan-headers')
        layers_dir = os.path.join(base, 'vulkan-validation-layers')
        tools_dir = os.path.join(base, 'vulkan-tools')
        return [
            os.path.join(headers_dir, 'NOTICE'),
            os.path.join(layers_dir, 'NOTICE'),
            os.path.join(tools_dir, 'NOTICE')
        ]

    def build(self) -> None:
        print('Constructing Vulkan validation layer source...')
        vulkan_root_dir = ndk.paths.android_path(
            'external/vulkan-validation-layers')
        vulkan_headers_root_dir = ndk.paths.android_path(
            'external/vulkan-headers')
        vulkan_tools_root_dir = ndk.paths.android_path(
            'external/vulkan-tools')

        copies = [
            {
                'source_dir': vulkan_root_dir,
                'dest_dir': 'vulkan/src',
                'files': [
                ],
                'dirs': [
                    'layers', 'tests', 'scripts'
                ],
            },
            {
                'source_dir': vulkan_headers_root_dir,
                'dest_dir': 'vulkan/src',
                'files': [
                ],
                'dirs': [
                    'include', 'registry'
                ],
            },
            {
                'source_dir': vulkan_tools_root_dir,
                'dest_dir': 'vulkan/src',
                'files': [
                ],
                'dirs': [
                    'common'
                ],
            }
        ]

        default_ignore_patterns = shutil.ignore_patterns(
            "*CMakeLists.txt",
            "*test.cc",
            "linux",
            "windows")

        base_vulkan_path = os.path.join(self.out_dir, 'vulkan')
        vulkan_path = os.path.join(base_vulkan_path, 'src')
        for properties in copies:
            source_dir = properties['source_dir']
            assert isinstance(source_dir, str)
            assert isinstance(properties['dest_dir'], str)
            dest_dir = os.path.join(self.out_dir, properties['dest_dir'])
            for d in properties['dirs']:
                src = os.path.join(source_dir, d)
                dst = os.path.join(dest_dir, d)
                shutil.rmtree(dst, True)
                shutil.copytree(src, dst,
                                ignore=default_ignore_patterns)
            for f in properties['files']:
                install_file(f, source_dir, dest_dir)

        # Copy Android build components
        print('Copying Vulkan build components...')
        src = os.path.join(vulkan_root_dir, 'build-android')
        dst = os.path.join(vulkan_path, 'build-android')
        shutil.rmtree(dst, True)
        shutil.copytree(src, dst, ignore=default_ignore_patterns)
        print('Copying finished')

        # Copy binary validation layer libraries
        print('Copying Vulkan binary validation layers...')
        src = ndk.paths.android_path('prebuilts/ndk/vulkan-validation-layers')
        dst = os.path.join(vulkan_path, 'build-android/jniLibs')
        shutil.rmtree(dst, True)
        shutil.copytree(src, dst, ignore=default_ignore_patterns)
        print('Copying finished')

        # TODO: Verify source packaged properly
        print('Packaging Vulkan source...')
        src = os.path.join(self.out_dir, 'vulkan')
        build_support.make_package('vulkan', src, self.dist_dir)
        print('Packaging Vulkan source finished')


class Toolchain(ndk.builds.Module):
    """The complete toolchain.

    BaseToolchain installs the core of the toolchain. This module installs the
    STL to that toolchain.
    """

    name = 'toolchain'
    # This is installed to the Clang location to avoid migration pain.
    path = 'toolchains/llvm/prebuilt/{host}'
    notice_group = ndk.builds.NoticeGroup.TOOLCHAIN
    deps = {
        'base-toolchain',
        'libc++',
        'libc++abi',
        'platforms',
    }

    @property
    def notices(self) -> List[str]:
        return Libcxx().notices + Libcxxabi().notices

    def build(self) -> None:
        pass

    def install(self) -> None:
        install_dir = self.get_install_path()
        libcxx_dir = self.get_dep('libc++').get_install_path()
        libcxxabi_dir = self.get_dep('libc++abi').get_install_path()

        libcxx_hdr_dir = os.path.join(install_dir, 'sysroot/usr/include/c++')
        os.makedirs(libcxx_hdr_dir)
        libcxx_inc_src = os.path.join(libcxx_dir, 'include')
        libcxx_inc_dst = os.path.join(libcxx_hdr_dir, 'v1')
        shutil.copytree(libcxx_inc_src, libcxx_inc_dst)

        libcxxabi_inc_src = os.path.join(libcxxabi_dir, 'include')
        copy_tree(libcxxabi_inc_src, libcxx_inc_dst)

        for arch in self.arches:
            # We need to replace libgcc with linker scripts that also use
            # libunwind on arm32. We already get libgcc from copying binutils,
            # but re-install it so we get the linker scripts.
            #
            # This needs to be done here rather than in BaseToolchain because
            # libunwind isn't available until libc++ has been built.
            for subarch in get_subarches(arch):
                install_libgcc(
                    install_dir, self.host, arch, subarch, new_layout=True)

            triple = ndk.abis.arch_to_triple(arch)
            abi, = ndk.abis.arch_to_abis(arch)
            libcxx_lib_dir = os.path.join(libcxx_dir, 'libs', abi)
            sysroot_dst = os.path.join(install_dir, 'sysroot/usr/lib', triple)

            libs = [
                'libc++_shared.so',
                'libc++_static.a',
                'libc++abi.a',
            ]
            if arch == 'arm':
                libs.append('libunwind.a')
            if abi in ndk.abis.LP32_ABIS:
                libs.append('libandroid_support.a')

            for lib in libs:
                shutil.copy2(os.path.join(libcxx_lib_dir, lib), sysroot_dst)

        platforms = self.get_dep('platforms')
        assert isinstance(platforms, Platforms)
        for api in platforms.get_apis():
            if api in Platforms.skip_apis:
                continue

            for arch in platforms.get_arches(api):
                triple = ndk.abis.arch_to_triple(arch)
                dst_dir = os.path.join(install_dir, 'sysroot/usr/lib', triple,
                                       str(api))

                # Also install a libc++.so and libc++.a linker script per API
                # level. We need this to be done on a per-API level basis
                # because libandroid_support is only used on pre-21 API levels.
                static_script = ['-lc++_static', '-lc++abi']
                shared_script = ['-lc++_shared']
                assert isinstance(api, int)
                if api < 21:
                    static_script.append('-landroid_support')
                    shared_script.insert(0, '-landroid_support')

                libcxx_so_path = os.path.join(dst_dir, 'libc++.so')
                with open(libcxx_so_path, 'w') as script:
                    script.write('INPUT({})'.format(' '.join(shared_script)))

                libcxx_a_path = os.path.join(dst_dir, 'libc++.a')
                with open(libcxx_a_path, 'w') as script:
                    script.write('INPUT({})'.format(' '.join(static_script)))


def make_format_value(value: Any) -> Any:
    if isinstance(value, list):
        return ' '.join(value)
    return value


def var_dict_to_make(var_dict: Dict[str, Any]) -> str:
    lines = []
    for name, value in var_dict.items():
        lines.append('{} := {}'.format(name, make_format_value(value)))
    return os.linesep.join(lines)


def cmake_format_value(value: Any) -> Any:
    if isinstance(value, list):
        return ';'.join(value)
    return value


def var_dict_to_cmake(var_dict: Dict[str, Any]) -> str:
    lines = []
    for name, value in var_dict.items():
        lines.append('set({} "{}")'.format(name, cmake_format_value(value)))
    return os.linesep.join(lines)


def abis_meta_transform(metadata: Dict) -> Dict[str, Any]:
    default_abis = []
    deprecated_abis = []
    lp32_abis = []
    lp64_abis = []
    for abi, abi_data in metadata.items():
        bitness = abi_data['bitness']
        if bitness == 32:
            lp32_abis.append(abi)
        elif bitness == 64:
            lp64_abis.append(abi)
        else:
            raise ValueError('{} bitness is unsupported value: {}'.format(
                abi, bitness))

        if abi_data['default']:
            default_abis.append(abi)

        if abi_data['deprecated']:
            deprecated_abis.append(abi)

    meta_vars = {
        'NDK_DEFAULT_ABIS': sorted(default_abis),
        'NDK_DEPRECATED_ABIS': sorted(deprecated_abis),
        'NDK_KNOWN_DEVICE_ABI32S': sorted(lp32_abis),
        'NDK_KNOWN_DEVICE_ABI64S': sorted(lp64_abis),
    }

    return meta_vars


def platforms_meta_transform(metadata: Dict) -> Dict[str, Any]:
    meta_vars = {
        'NDK_MIN_PLATFORM_LEVEL': metadata['min'],
        'NDK_MAX_PLATFORM_LEVEL': metadata['max'],
    }

    for src, dst in metadata['aliases'].items():
        name = 'NDK_PLATFORM_ALIAS_{}'.format(src)
        value = 'android-{}'.format(dst)
        meta_vars[name] = value
    return meta_vars


def system_libs_meta_transform(metadata: Dict) -> Dict[str, Any]:
    # This file also contains information about the first supported API level
    # for each library. We could use this to provide better diagnostics in
    # ndk-build, but currently do not.
    return {'NDK_SYSTEM_LIBS': sorted(metadata.keys())}


class NdkBuild(ndk.builds.PackageModule):
    name = 'ndk-build'
    path = 'build'
    src = ndk.paths.ndk_path('build')
    notice = ndk.paths.ndk_path('NOTICE')

    deps = {
        'meta',
    }

    def install(self) -> None:
        super().install()

        self.install_ndk_version_makefile()

        self.generate_language_specific_metadata('abis', abis_meta_transform)

        self.generate_language_specific_metadata('platforms',
                                                 platforms_meta_transform)

        self.generate_language_specific_metadata('system_libs',
                                                 system_libs_meta_transform)

    def install_ndk_version_makefile(self) -> None:
        """Generates a version.mk for ndk-build."""
        version_mk = Path(self.get_install_path()) / 'core/version.mk'
        version_mk.write_text(textwrap.dedent(f"""\
            NDK_MAJOR := {ndk.config.major}
            NDK_MINOR := {ndk.config.hotfix}
            NDK_BETA := {ndk.config.beta}
            NDK_CANARY := {str(ndk.config.canary).lower()}
            """))

    def generate_language_specific_metadata(
            self, name: str, func: Callable[[Dict], Dict[str, Any]]) -> None:
        install_path = self.get_install_path()
        json_path = os.path.join(
            self.get_dep('meta').get_install_path(), name + '.json')
        meta = json.loads(ndk.file.read_file(json_path))
        meta_vars = func(meta)

        ndk.file.write_file(
            os.path.join(install_path, 'core/{}.mk'.format(name)),
            var_dict_to_make(meta_vars))
        ndk.file.write_file(
            os.path.join(install_path, 'cmake/{}.cmake'.format(name)),
            var_dict_to_cmake(meta_vars))


class PythonPackages(ndk.builds.PackageModule):
    name = 'python-packages'
    path = 'python-packages'
    src = ndk.paths.android_path('development/python-packages')


class SystemStl(ndk.builds.PackageModule):
    name = 'system-stl'
    path = 'sources/cxx-stl/system'
    src = ndk.paths.ndk_path('sources/cxx-stl/system')


class LibAndroidSupport(ndk.builds.PackageModule):
    name = 'libandroid_support'
    path = 'sources/android/support'
    src = ndk.paths.ndk_path('sources/android/support')


class Libcxxabi(ndk.builds.PackageModule):
    name = 'libc++abi'
    path = 'sources/cxx-stl/llvm-libc++abi'
    src = ndk.paths.android_path('external/libcxxabi')


class SimplePerf(ndk.builds.Module):
    name = 'simpleperf'
    path = 'simpleperf'
    notice = ndk.paths.android_path('prebuilts/simpleperf/NOTICE')

    def build(self) -> None:
        print('Building simpleperf...')
        install_dir = os.path.join(self.out_dir, 'simpleperf')
        if os.path.exists(install_dir):
            shutil.rmtree(install_dir)
        os.makedirs(install_dir)

        simpleperf_path = ndk.paths.android_path('prebuilts/simpleperf')
        dirs = ['doc', 'inferno', 'bin/android', 'app_api']
        host_bin_dir = 'windows' if self.host.is_windows else self.host.value
        dirs.append(os.path.join('bin/', host_bin_dir))
        for d in dirs:
            shutil.copytree(os.path.join(simpleperf_path, d),
                            os.path.join(install_dir, d))

        for item in os.listdir(simpleperf_path):
            should_copy = False
            if item.endswith('.py') and item not in ['update.py', 'test.py']:
                should_copy = True
            elif item == 'report_html.js':
                should_copy = True
            elif item == 'inferno.sh' and not self.host.is_windows:
                should_copy = True
            elif item == 'inferno.bat' and self.host.is_windows:
                should_copy = True
            if should_copy:
                shutil.copy2(os.path.join(simpleperf_path, item), install_dir)

        shutil.copy2(os.path.join(simpleperf_path, 'ChangeLog'), install_dir)
        build_support.make_package('simpleperf', install_dir, self.dist_dir)


class RenderscriptLibs(ndk.builds.PackageModule):
    name = 'renderscript-libs'
    path = 'sources/android/renderscript'
    src = ndk.paths.ndk_path('sources/android/renderscript')


class RenderscriptToolchain(ndk.builds.InvokeBuildModule):
    name = 'renderscript-toolchain'
    path = 'toolchains/renderscript/prebuilt/{host}'
    script = 'build-renderscript.py'

    @property
    def notices(self) -> List[str]:
        base = ndk.paths.android_path('prebuilts/renderscript/host')
        return [
            os.path.join(base, 'darwin-x86/current/NOTICE'),
            os.path.join(base, 'linux-x86/current/NOTICE'),
            os.path.join(base, 'windows-x86/current/NOTICE'),
        ]


class Changelog(ndk.builds.FileModule):
    name = 'changelog'
    path = 'CHANGELOG.md'
    src = ndk.paths.ndk_path('docs/changelogs/Changelog-r{}.md'.format(
        ndk.config.major))
    no_notice = True


class NdkGdb(ndk.builds.MultiFileModule):
    name = 'ndk-gdb'
    path = 'prebuilt/{host}/bin'
    notice = ndk.paths.ndk_path('NOTICE')

    @property
    def files(self) -> Iterable[str]:
        files = [
            ndk.paths.ndk_path('ndk-gdb'),
            ndk.paths.ndk_path('ndk-gdb.py'),
        ]

        if self.host.is_windows:
            files.append(ndk.paths.ndk_path('ndk-gdb.cmd'))

        return files


class NdkGdbShortcut(ndk.builds.ScriptShortcutModule):
    name = 'ndk-gdb-shortcut'
    path = 'ndk-gdb'
    script = 'prebuilt/{host}/bin/ndk-gdb'
    windows_ext = '.cmd'


class NdkStack(ndk.builds.MultiFileModule):
    name = 'ndk-stack'
    path = 'prebuilt/{host}/bin'
    notice = ndk.paths.ndk_path('NOTICE')

    @property
    def files(self) -> Iterable[str]:
        files = [
            ndk.paths.ndk_path('ndk-stack'),
            ndk.paths.ndk_path('ndk-stack.py'),
        ]

        if self.host.is_windows:
            files.append(ndk.paths.ndk_path('ndk-stack.cmd'))

        return files


class NdkStackShortcut(ndk.builds.ScriptShortcutModule):
    name = 'ndk-stack-shortcut'
    path = 'ndk-stack'
    script = 'prebuilt/{host}/bin/ndk-stack'
    windows_ext = '.cmd'


class NdkWhichShortcut(ndk.builds.ScriptShortcutModule):
    name = 'ndk-which-shortcut'
    path = 'ndk-which'
    script = 'prebuilt/{host}/bin/ndk-which'
    windows_ext = ''  # There isn't really a Windows ndk-which.


class NdkBuildShortcut(ndk.builds.ScriptShortcutModule):
    name = 'ndk-build-shortcut'
    path = 'ndk-build'
    script = 'build/ndk-build'
    windows_ext = '.cmd'


class Readme(ndk.builds.FileModule):
    name = 'readme'
    path = 'README.md'
    src = ndk.paths.ndk_path('UserReadme.md')


CANARY_TEXT = textwrap.dedent("""\
    This is a canary build of the Android NDK. It's updated almost every day.

    Canary builds are designed for early adopters and can be prone to breakage.
    Sometimes they can break completely. To aid development and testing, this
    distribution can be installed side-by-side with your existing, stable NDK
    release.
    """)


class CanaryReadme(ndk.builds.Module):
    name = 'canary-readme'
    path = 'README.canary'
    no_notice = True

    def build(self) -> None:
        pass

    def install(self) -> None:
        if ndk.config.canary:
            canary_path = self.get_install_path()
            with open(canary_path, 'w') as canary_file:
                canary_file.write(CANARY_TEXT)


class Meta(ndk.builds.PackageModule):
    name = 'meta'
    path = 'meta'
    src = ndk.paths.ndk_path('meta')
    no_notice = True

    deps = {
        'base-toolchain',
    }

    def install(self) -> None:
        super().install()
        self.create_system_libs_meta()

    def create_system_libs_meta(self) -> None:
        # Build system_libs.json based on what we find in the toolchain. We
        # only need to scan a single 32-bit architecture since these libraries
        # do not vary in availability across architectures.
        sysroot_base = os.path.join(
            self.get_dep('base-toolchain').get_install_path(),
            'sysroot/usr/lib/arm-linux-androideabi')

        system_libs: Dict[str, str] = {}
        for api_name in sorted(os.listdir(sysroot_base)):
            path = os.path.join(sysroot_base, api_name)

            # There are also non-versioned libraries in this directory.
            if not os.path.isdir(path):
                continue

            for lib in os.listdir(path):
                # Don't include CRT objects in the list.
                if not lib.endswith('.so'):
                    continue

                if not lib.startswith('lib'):
                    raise RuntimeError(
                        'Found unexpected file in sysroot: {}'.format(lib))

                # libc++.so is a linker script, not a system library.
                if lib == 'libc++.so':
                    continue

                # We're processing each version directory in sorted order, so
                # if we've already seen this library before it is an earlier
                # version of the library.
                if lib in system_libs:
                    continue

                system_libs[lib] = api_name

        system_libs = collections.OrderedDict(sorted(system_libs.items()))

        json_path = os.path.join(self.get_install_path(), 'system_libs.json')
        with open(json_path, 'w') as json_file:
            json.dump(system_libs, json_file, indent=2, separators=(',', ': '))


class WrapSh(ndk.builds.PackageModule):
    name = 'wrap.sh'
    path = 'wrap.sh'
    src = ndk.paths.ndk_path('wrap.sh')
    no_notice = True


class SourceProperties(ndk.builds.Module):
    name = 'source.properties'
    path = 'source.properties'
    no_notice = True

    def build(self) -> None:
        pass

    def install(self) -> None:
        path = self.get_install_path()
        with open(path, 'w') as source_properties:
            assert self.context is not None
            build = self.context.build_number
            if build == 'dev':
                build = '0'
            version = '{}.{}.{}'.format(
                ndk.config.major, ndk.config.hotfix, build)
            if ndk.config.beta > 0:
                version += '-beta{}'.format(ndk.config.beta)
            source_properties.writelines([
                'Pkg.Desc = Android NDK\n',
                'Pkg.Revision = {}\n'.format(version)
            ])


class AdbPy(ndk.builds.PythonPackage):
    name = 'adb.py'
    path = ndk.paths.android_path('development/python-packages/adb/setup.py')
    notice = ndk.paths.android_path('development/python-packages/NOTICE')


class Lit(ndk.builds.PythonPackage):
    name = 'lit'
    path = ndk.paths.android_path('external/llvm/utils/lit/setup.py')
    notice = ndk.paths.android_path('external/llvm/NOTICE')


class NdkPy(ndk.builds.PythonPackage):
    name = 'ndk.py'
    path = ndk.paths.ndk_path('setup.py')


def create_notice_file(path: str, for_group: ndk.builds.NoticeGroup) -> None:
    # Using sets here so we can perform some amount of duplicate reduction. In
    # a lot of cases there will be minor differences that cause lots of
    # "duplicates", but might as well catch what we can.
    notice_files = set()
    for module in ALL_MODULES:
        if module.notice_group == for_group:
            for notice in module.notices:
                notice_files.add(notice)

    licenses = set()
    for notice_path in notice_files:
        with open(notice_path, encoding='utf-8') as notice_file:
            licenses.add(notice_file.read())

    with open(path, 'w', encoding='utf-8') as output_file:
        # Sorting the contents here to try to make things deterministic.
        output_file.write(os.linesep.join(sorted(list(licenses))))


def launch_build(worker: ndk.workqueue.Worker, module: ndk.builds.Module,
                 log_dir: str) -> Tuple[bool, ndk.builds.Module]:
    result = do_build(worker, module, log_dir)
    if not result:
        return result, module
    do_install(worker, module)
    return True, module


def do_build(worker: ndk.workqueue.Worker, module: ndk.builds.Module,
             log_dir: str) -> bool:
    with open(module.log_path(log_dir), 'w') as log_file:
        os.dup2(log_file.fileno(), sys.stdout.fileno())
        os.dup2(log_file.fileno(), sys.stderr.fileno())
        try:
            worker.status = 'Building {}...'.format(module)
            module.build()
            return True
        except Exception:  # pylint: disable=broad-except
            traceback.print_exc()
            return False


def do_install(worker: ndk.workqueue.Worker,
               module: ndk.builds.Module) -> None:
    worker.status = 'Installing {}...'.format(module)
    module.install()


def split_module_by_arch(module: ndk.builds.Module, arches: List[ndk.abis.Arch]
                         ) -> Iterator[ndk.builds.Module]:
    if module.split_build_by_arch:
        for arch in arches:
            build_module = copy.deepcopy(module)
            build_module.build_arch = arch
            yield build_module
    else:
        yield module


def _get_transitive_module_deps(
        module: ndk.builds.Module, deps: Set[ndk.builds.Module],
        unknown_deps: Set[str], seen: Set[ndk.builds.Module]) -> None:
    seen.add(module)

    for name in module.deps:
        if name not in NAMES_TO_MODULES:
            unknown_deps.add(name)
            continue

        dep = NAMES_TO_MODULES[name]
        if dep in seen:
            # Cycle detection is already handled by ndk.deps.DependencyManager.
            # Just avoid falling into an infinite loop here and let that do the
            # work.
            continue

        deps.add(dep)
        _get_transitive_module_deps(dep, deps, unknown_deps, seen)


def get_transitive_module_deps(
        module: ndk.builds.Module) -> Tuple[Set[ndk.builds.Module], Set[str]]:
    seen: Set[ndk.builds.Module] = set()
    deps: Set[ndk.builds.Module] = set()
    unknown_deps: Set[str] = set()
    _get_transitive_module_deps(module, deps, unknown_deps, seen)
    return deps, unknown_deps


def get_modules_to_build(
        module_names: Iterable[str], arches: List[ndk.abis.Arch]
) -> Tuple[List[ndk.builds.Module], Set[ndk.builds.Module]]:
    """Returns a list of modules to be built given a list of module names.

    The module names are those given explicitly by the user or the full list.
    In the event that the user has passed a subset of modules, we need to also
    return the dependencies of that module.
    """
    unknown_modules = set()
    modules = set()
    deps_only = set()
    for name in module_names:
        if name not in NAMES_TO_MODULES:
            # Build a list of all the unknown modules rather than error out
            # immediately so we can provide a complete error message.
            unknown_modules.add(name)

        module = NAMES_TO_MODULES[name]
        modules.add(module)

        deps, unknown_deps = get_transitive_module_deps(module)
        modules.update(deps)

        # --skip-deps may be passed if the user wants to avoid rebuilding a
        # costly dependency. It's up to the user to guarantee that the
        # dependency has actually been built. Modules are skipped by
        # immediately completing them rather than sending them to the
        # workqueue. As such, we need to return a list of which modules are
        # *only* in the list because they are dependencies rather than being a
        # part of the requested set.
        for dep in deps:
            if dep.name not in module_names:
                deps_only.add(dep)
        unknown_modules.update(unknown_deps)

    if unknown_modules:
        sys.exit('Unknown modules: {}'.format(
            ', '.join(sorted(list(unknown_modules)))))

    build_modules = []
    for module in modules:
        for build_module in split_module_by_arch(module, arches):
            build_modules.append(build_module)

    return sorted(list(build_modules), key=str), deps_only


ALL_MODULES = [
    AdbPy(),
    BaseToolchain(),
    Binutils(),
    CanaryReadme(),
    Changelog(),
    Clang(),
    CpuFeatures(),
    Gdb(),
    GdbServer(),
    Gtest(),
    HostTools(),
    LibAndroidSupport(),
    LibShaderc(),
    Libcxx(),
    Libcxxabi(),
    Lit(),
    Make(),
    Meta(),
    NativeAppGlue(),
    NdkBuild(),
    NdkBuildShortcut(),
    NdkGdb(),
    NdkGdbShortcut(),
    NdkHelper(),
    NdkPy(),
    NdkStack(),
    NdkStackShortcut(),
    NdkWhich(),
    NdkWhichShortcut(),
    Platforms(),
    PythonPackages(),
    Readme(),
    RenderscriptLibs(),
    RenderscriptToolchain(),
    ShaderTools(),
    SimplePerf(),
    SourceProperties(),
    Sysroot(),
    SystemStl(),
    Toolchain(),
    Vulkan(),
    WrapSh(),
    Yasm(),
]


NAMES_TO_MODULES = {m.name: m for m in ALL_MODULES}


def get_all_module_names() -> List[str]:
    return [m.name for m in ALL_MODULES if m.enabled]


def build_number_arg(value: str) -> str:
    if value.startswith('P'):
        # Treehugger build. Treat as a local development build.
        return '0'
    return value


def parse_args() -> Tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(
        description=inspect.getdoc(sys.modules[__name__]))

    parser.add_argument(
        '--arch',
        choices=('arm', 'arm64', 'x86', 'x86_64'),
        help='Build for the given architecture. Build all by default.')
    parser.add_argument(
        '-j', '--jobs', type=int, default=multiprocessing.cpu_count(),
        help=('Number of parallel builds to run. Note that this will not '
              'affect the -j used for make; this just parallelizes '
              'checkbuild.py. Defaults to the number of CPUs available.'))

    parser.add_argument(
        '--skip-deps', action='store_true',
        help=('Assume that dependencies have been built and only build '
              'explicitly named modules.'))

    package_group = parser.add_mutually_exclusive_group()
    package_group.add_argument(
        '--package', action='store_true', dest='package', default=True,
        help='Package the NDK when done building (default).')
    package_group.add_argument(
        '--no-package', action='store_false', dest='package',
        help='Do not package the NDK when done building.')
    package_group.add_argument(
        '--force-package', action='store_true', dest='force_package',
        help='Force a package even if only building a subset of modules.')

    test_group = parser.add_mutually_exclusive_group()
    test_group.add_argument(
        '--build-tests', action='store_true', dest='build_tests', default=True,
        help=textwrap.dedent("""\
        Build tests when finished. --package is required. Not supported
        when targeting Windows.
        """))
    test_group.add_argument(
        '--no-build-tests', action='store_false', dest='build_tests',
        help='Skip building tests after building the NDK.')

    parser.add_argument(
        '--build-number', default='0', type=build_number_arg,
        help='Build number for use in version files.')
    parser.add_argument(
        '--release', help='Ignored. Temporarily compatibility.')

    parser.add_argument(
        '--system',
        choices=ndk.hosts.Host,
        type=ndk.hosts.Host,
        default=ndk.hosts.get_default_host(),
        help='Build for the given OS.')

    module_group = parser.add_mutually_exclusive_group()

    module_group.add_argument(
        '--module', dest='modules', action='append', default=[],
        choices=get_all_module_names(), help='NDK modules to build.')

    return parser.parse_known_args()


def log_build_failure(log_path: str, dist_dir: str) -> None:
    with open(log_path, 'r') as log_file:
        contents = log_file.read()
        print(contents)

        # The build server has a build_error.log file that is supposed to be
        # the short log of the failure that stopped the build. Append our
        # failing log to that.
        build_error_log = os.path.join(dist_dir, 'logs/build_error.log')
        with open(build_error_log, 'a') as error_log:
            error_log.write('\n')
            error_log.write(contents)


def launch_buildable(deps: ndk.deps.DependencyManager,
                     workqueue: ndk.workqueue.AnyWorkQueue, log_dir: str,
                     skip_deps: bool,
                     skip_modules: Set[ndk.builds.Module]) -> None:
    # If args.skip_deps is true, we could get into a case where we just
    # dequeued the only module that was still building and the only
    # items in get_buildable() are modules that will be skipped.
    # Without this outer while loop, we'd mark the skipped dependencies
    # as complete and then complete the outer loop.  The workqueue
    # would be out of work and we'd exit.
    #
    # Avoid this by making sure that we queue all possible buildable
    # modules before we complete the loop.
    while deps.buildable_modules:
        for module in deps.get_buildable():
            if skip_deps and module in skip_modules:
                deps.complete(module)
                continue
            workqueue.add_task(launch_build, module, log_dir)


def wait_for_build(deps: ndk.deps.DependencyManager,
                   workqueue: ndk.workqueue.AnyWorkQueue, dist_dir: str,
                   log_dir: str, skip_deps: bool,
                   skip_modules: Set[ndk.builds.Module]) -> None:
    console = ndk.ansi.get_console()
    ui = ndk.ui.get_build_progress_ui(console, workqueue)
    with ndk.ansi.disable_terminal_echo(sys.stdin):
        with console.cursor_hide_context():
            while not workqueue.finished():
                result, module = workqueue.get_result()
                if not result:
                    ui.clear()
                    print('Build failed: {}'.format(module))
                    log_build_failure(
                        module.log_path(log_dir), dist_dir)
                    sys.exit(1)
                elif not console.smart_console:
                    ui.clear()
                    print('Build succeeded: {}'.format(module))

                deps.complete(module)
                launch_buildable(deps, workqueue, log_dir, skip_deps,
                                 skip_modules)

                ui.draw()
            ui.clear()
            print('Build finished')


def build_ndk(modules: List[ndk.builds.Module],
              deps_only: Set[ndk.builds.Module], out_dir: str, dist_dir: str,
              args: argparse.Namespace) -> str:
    arches = list(ndk.abis.ALL_ARCHITECTURES)
    if args.arch is not None:
        arches = [args.arch]

    build_context = ndk.builds.BuildContext(
        out_dir, dist_dir, ALL_MODULES, args.system, arches, args.build_number)

    for module in modules:
        module.context = build_context

    log_dir = os.path.join(dist_dir, 'logs')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    ndk_dir = ndk.paths.get_install_path(out_dir, args.system)
    if not os.path.exists(ndk_dir):
        os.makedirs(ndk_dir)

    deps = ndk.deps.DependencyManager(modules)
    workqueue = ndk.workqueue.WorkQueue(args.jobs)
    try:
        launch_buildable(deps, workqueue, log_dir, args.skip_deps, deps_only)
        wait_for_build(
            deps, workqueue, dist_dir, log_dir, args.skip_deps, deps_only)

        if deps.get_buildable():
            raise RuntimeError(
                'Builder stopped early. Modules are still '
                'buildable: {}'.format(', '.join(str(deps.get_buildable()))))

        create_notice_file(
            os.path.join(ndk_dir, 'NOTICE'),
            ndk.builds.NoticeGroup.BASE)
        create_notice_file(
            os.path.join(ndk_dir, 'NOTICE.toolchain'),
            ndk.builds.NoticeGroup.TOOLCHAIN)
        return ndk_dir
    finally:
        workqueue.terminate()
        workqueue.join()


def build_ndk_for_cross_compile(out_dir: str, arches: List[ndk.abis.Arch],
                                args: argparse.Namespace) -> None:
    args = copy.deepcopy(args)
    args.system = ndk.hosts.get_default_host()
    if args.system != ndk.hosts.Host.Linux:
        raise NotImplementedError
    module_names = NAMES_TO_MODULES.keys()
    modules, deps_only = get_modules_to_build(module_names, arches)
    print('Building Linux modules: {}'.format(' '.join(
        [str(m) for m in modules])))
    build_ndk(modules, deps_only, out_dir, out_dir, args)


def create_ndk_symlink(out_dir: str) -> None:
    this_host_ndk = ndk.paths.get_install_path()
    ndk_symlink = os.path.join(out_dir, os.path.basename(this_host_ndk))
    if not os.path.exists(ndk_symlink):
        os.symlink(this_host_ndk, ndk_symlink)


def get_directory_size(path: str) -> int:
    du_str = subprocess.check_output(['du', '-sm', path])
    match = re.match(r'^(\d+)', du_str.decode('utf-8'))
    if match is None:
        raise RuntimeError(f'Could not determine the size of {path}')
    size_str = match.group(1)
    return int(size_str)


def main() -> None:
    logging.basicConfig()

    total_timer = ndk.timer.Timer()
    total_timer.start()

    args, module_names = parse_args()
    module_names.extend(args.modules)
    if not module_names:
        module_names = get_all_module_names()

    required_package_modules = set(get_all_module_names())
    have_required_modules = required_package_modules <= set(module_names)
    do_package = have_required_modules if args.package else False
    if args.force_package:
        do_package = True

    # TODO(danalbert): wine?
    # We're building the Windows packages from Linux, so we can't actually run
    # any of the tests from here.
    if args.system.is_windows or not do_package:
        args.build_tests = False

    os.chdir(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

    # Set ANDROID_BUILD_TOP.
    if 'ANDROID_BUILD_TOP' in os.environ:
        sys.exit(textwrap.dedent("""\
            Error: ANDROID_BUILD_TOP is already set in your environment.

            This typically means you are running in a shell that has lunched a
            target in a platform build. The platform environment interferes
            with the NDK build environment, so the build cannot continue.

            Launch a new shell before building the NDK."""))

    os.environ['ANDROID_BUILD_TOP'] = ndk.paths.android_path()

    arches = list(ndk.abis.ALL_ARCHITECTURES)
    if args.arch is not None:
        arches = [args.arch]

    out_dir = ndk.paths.get_out_dir()
    dist_dir = ndk.paths.get_dist_dir(out_dir)

    print('Machine has {} CPUs'.format(multiprocessing.cpu_count()))

    if args.system.is_windows and not args.skip_deps:
        # Since the Windows NDK is cross compiled, we need to build a Linux NDK
        # first so we can build components like libc++.
        build_ndk_for_cross_compile(out_dir, arches, args)

    modules, deps_only = get_modules_to_build(module_names, arches)
    print('Building modules: {}'.format(' '.join(
        [str(m) for m in modules
         if not args.skip_deps or m not in deps_only])))

    build_timer = ndk.timer.Timer()
    with build_timer:
        ndk_dir = build_ndk(modules, deps_only, out_dir, dist_dir, args)
    installed_size = get_directory_size(ndk_dir)

    # Create a symlink to the NDK usable by this host in the root of the out
    # directory for convenience.
    create_ndk_symlink(out_dir)

    package_timer = ndk.timer.Timer()
    with package_timer:
        if do_package:
            print('Packaging NDK...')
            host_tag = ndk.hosts.host_to_tag(args.system)
            # NB: Purging of unwanted files (.pyc, Android.bp, etc) happens as
            # part of packaging. If testing is ever moved to happen before
            # packaging, ensure that the directory is purged before and after
            # building the tests.
            package_path = package_ndk(
                ndk_dir, dist_dir, host_tag, args.build_number)
            packaged_size_bytes = os.path.getsize(package_path)
            packaged_size = packaged_size_bytes // (2 ** 20)

    good = True
    test_timer = ndk.timer.Timer()
    with test_timer:
        if args.build_tests:
            good = build_ndk_tests(out_dir, dist_dir, args)
            print()  # Blank line between test results and timing data.

    total_timer.finish()

    print('')
    print('Installed size: {} MiB'.format(installed_size))
    if do_package:
        print('Package size: {} MiB'.format(packaged_size))
    print('Finished {}'.format('successfully' if good else 'unsuccessfully'))
    print('Build: {}'.format(build_timer.duration))
    print('Packaging: {}'.format(package_timer.duration))
    print('Testing: {}'.format(test_timer.duration))
    print('Total: {}'.format(total_timer.duration))

    subject = 'NDK Build {}!'.format('Passed' if good else 'Failed')
    body = 'Build finished in {}'.format(total_timer.duration)
    ndk.notify.toast(subject, body)

    sys.exit(not good)


@contextlib.contextmanager
def _assign_self_to_new_process_group(fd: TextIO) -> Iterator[None]:
    # It seems the build servers run us in our own session, in which case we
    # get EPERM from `setpgrp`. No need to call this in that case because we
    # will already be the process group leader.
    if os.getpid() == os.getsid(os.getpid()):
        yield
        return

    if ndk.ansi.is_self_in_tty_foreground_group(fd):
        old_pgrp = os.tcgetpgrp(fd.fileno())
        os.tcsetpgrp(fd.fileno(), os.getpid())
        os.setpgrp()
        try:
            yield
        finally:
            os.tcsetpgrp(fd.fileno(), old_pgrp)
    else:
        os.setpgrp()
        yield


def _run_main_in_new_process_group() -> None:
    with _assign_self_to_new_process_group(sys.stdin):
        main()


if __name__ == '__main__':
    _run_main_in_new_process_group()
