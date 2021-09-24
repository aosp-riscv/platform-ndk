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
"""Check that strip is not used."""
from pathlib import Path
from typing import Optional

from ndk.test.spec import BuildConfiguration
from ndk.testing.flag_verifier import FlagVerifier


def run_test(ndk_path: str,
             config: BuildConfiguration) -> tuple[bool, Optional[str]]:
    """Checks ndk-build V=1 output for lack of strip."""
    verifier = FlagVerifier(Path('project'), Path(ndk_path), config)
    # TODO: Fix this test.
    # This test has always been wrong, since it was only doing whole word
    # search for 'strip' and we call strip with its full path.
    verifier.expect_not_flag('strip')
    return verifier.verify_ndk_build().make_test_result_tuple()
