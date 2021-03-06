#!/usr/bin/env python
#
# Copyright (C) 2016 The Android Open Source Project
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
"""Generates an HTML table for the downloads page."""
from __future__ import print_function

import argparse
import logging
import operator
import os.path
import re
import sys


# pylint: disable=design


def get_lines():
    """Returns all stdin input until the first empty line."""
    lines = []
    while True:
        line = input()
        if line.strip() == '':
            return lines
        lines.append(line)


def parse_args():
    """Parses and returns command line arguments."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--beta', action='store_true',
        help='Generate content for a beta release.')

    return parser.parse_args()


def main():
    """Program entry point."""
    args = parse_args()
    print('Paste the contents of the "New files" section of the SDK update '
          'email here. Terminate with an empty line.')
    lines = get_lines()
    if not lines:
        sys.exit('No input.')

    # The user may have pasted the following header line:
    # SHA1                                              size  file
    if lines[0].startswith('SHA1') or lines[0].lstrip().startswith('Link'):
        lines = lines[1:]

    artifacts = []
    for line in lines:
        # Some lines are updates to the repository.xml files used by the SDK
        # manager. We don't care about these.
        # <sha>        12,345  path/to/repository.xml
        if line.endswith('.xml') or 'android-ndk' not in line:
            continue

        # Real entries look like this (the leading hex number is optional):
        # 0x1234 <sha>   123,456,789  path/to/android-ndk-r23-beta5-linux.zip
        match = re.match(
            r'^(?:0x[0-9a-f]+)?\s*(\w+)\s+([0-9,]+)\s+(.+)$', line)
        if match is None:
            logging.error('Skipping unrecognized line: %s', line)
            continue

        sha = match.group(1)

        size_str = match.group(2)
        size = int(size_str.replace(',', ''))

        path = match.group(3)
        package = os.path.basename(path)

        # android-ndk-$VERSION-$HOST.$EXT
        # $VERSION might contain a hyphen for beta/RC releases.
        # Split on all hyphens and join $HOST and $EXT to get the platform.
        package_name, package_ext = os.path.splitext(package)
        host = package_name.split('-')[-1] + '-' + package_ext[1:]
        pretty_host = {
            'darwin-zip': 'macOS',
            'darwin-dmg': 'macOS App Bundle',
            'linux-zip': 'Linux',
            'windows-zip': 'Windows',
        }[host]

        artifacts.append((host, pretty_host, package, size, sha))

    # Sort the artifacts by the platform name.
    artifacts = sorted(artifacts, key=operator.itemgetter(0))

    print('For GitHub:')
    print('<table>')
    print('  <tr>')
    print('    <th>Platform</th>')
    print('    <th>Package</th>')
    print('    <th>Size (bytes)</th>')
    print('    <th>SHA1 Checksum</th>')
    print('  </tr>')
    for host, pretty_host, package, size, sha in artifacts:
        url_base = 'https://dl.google.com/android/repository/'
        package_url = url_base + package
        link = '<a href="{}">{}</a>'.format(package_url, package)

        print('  <tr>')
        print('    <td>{}</td>'.format(pretty_host))
        print('    <td>{}</td>'.format(link))
        print('    <td>{}</td>'.format(size))
        print('    <td>{}</td>'.format(sha))
        print('  </tr>')
    print('</table>')
    print()
    print('For DAC:')

    var_prefix = 'ndk_beta' if args.beta else 'ndk'
    for host, pretty_host, package, size, sha in artifacts:
        dac_host = {
            'darwin-zip': 'mac64',
            'darwin-dmg': 'mac64_dmg',
            'linux-zip': 'linux64',
            'windows-zip': 'win64',
        }[host]
        print()
        print('{{# {} #}}'.format(pretty_host))
        print('{{% setvar {}_{}_download %}}{}{{% endsetvar %}}'.format(
            var_prefix, dac_host, package))
        print('{{% setvar {}_{}_bytes %}}{}{{% endsetvar %}}'.format(
            var_prefix, dac_host, size))
        print('{{% setvar {}_{}_checksum %}}{}{{% endsetvar %}}'.format(
            var_prefix, dac_host, sha))


if __name__ == '__main__':
    main()
