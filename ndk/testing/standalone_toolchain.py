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
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, List, Tuple

import ndk.abis


def logger() -> logging.Logger:
    return logging.getLogger(__name__)


def call_output(cmd: List[str], *args: Any, **kwargs: Any) -> Tuple[int, Any]:
    logger().info('COMMAND: %s', ' '.join(cmd))
    kwargs.update({
        'stdout': subprocess.PIPE,
        'stderr': subprocess.STDOUT,
    })
    with subprocess.Popen(cmd, *args, **kwargs) as proc:
        out, _ = proc.communicate()
        return proc.returncode, out


def make_standalone_toolchain(ndk_path: str, arch: str, api: int,
                              extra_args: List[str],
                              install_dir: str) -> Tuple[bool, str]:
    make_standalone_toolchain_path = os.path.join(
        ndk_path, 'build/tools/make_standalone_toolchain.py')

    cmd = [make_standalone_toolchain_path, '--force',
           '--install-dir=' + install_dir, '--arch=' + arch,
           '--api={}'.format(api)] + extra_args

    if os.name == 'nt':
        # Windows doesn't process shebang lines, and we wouldn't be pointing at
        # the right Python if it did. Explicitly invoke the NDK's Python for on
        # Windows.
        prebuilt_dir = os.path.join(ndk_path, 'prebuilt/windows-x86_64')
        if not os.path.exists(prebuilt_dir):
            prebuilt_dir = os.path.join(ndk_path, 'prebuilt/windows')
        if not os.path.exists(prebuilt_dir):
            raise RuntimeError('Could not find prebuilts in {}'.format(
                os.path.join(ndk_path, 'prebuilt')))

        python_path = os.path.join(prebuilt_dir, 'bin/python.exe')
        cmd = [python_path] + cmd

    rc, out = call_output(cmd)
    return rc == 0, out.decode('utf-8')


def test_standalone_toolchain(install_dir: str, test_source: str,
                              flags: List[str]) -> Tuple[bool, str]:
    compiler_name = 'clang++'

    compiler = os.path.join(install_dir, 'bin', compiler_name)
    cmd = [compiler, test_source, '-Wl,--no-undefined', '-Wl,--fatal-warnings']
    cmd += flags
    if os.name == 'nt':
        # The Windows equivalent of exec doesn't know file associations so it
        # tries to load the batch file as an executable. Invoke it with cmd.
        cmd = ['cmd', '/c'] + cmd
    rc, out = call_output(cmd)
    return rc == 0, out.decode('utf-8')


def run_test(ndk_path: str, abi: ndk.abis.Abi, api: int, test_source: str,
             extra_args: List[str], flags: List[str]) -> Tuple[bool, str]:
    arch = ndk.abis.abi_to_arch(abi)

    install_dir = tempfile.mkdtemp()
    try:
        success, out = make_standalone_toolchain(
            ndk_path, arch, api, extra_args, install_dir)
        if not success:
            return success, out
        return test_standalone_toolchain(install_dir, test_source, flags)
    finally:
        shutil.rmtree(install_dir)
