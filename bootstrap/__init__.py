#
# Copyright (C) 2018 The Android Open Source Project
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
"""Tools for bootstrapping Python 3."""
import os
import sys


THIS_DIR = os.path.realpath(os.path.dirname(__file__))


def android_path(*args):
    """Returns the absolute path rooted within the top level source tree."""
    return os.path.normpath(os.path.join(THIS_DIR, '../..', *args))


def python_path():
    """Returns the absolute path to python executable."""
    if sys.platform.startswith('linux'):
        host_name = "linux"
    elif sys.platform.startswith('darwin'):
        host_name = "darwin"
    else:
        raise RuntimeError('Unsupported host: {}'.format(sys.platform))
    return android_path('prebuilts', 'python3', host_name, 'bin')


def bootstrap():
    """Creates a bootstrap Python 3 environment.

    Adds the directory containing the python3 binary to the first element in
    the PATH.
    """
    os.environ['PATH'] = os.pathsep.join([python_path(), os.environ['PATH']])
