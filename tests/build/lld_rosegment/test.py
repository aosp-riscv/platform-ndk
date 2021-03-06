#
# Copyright (C) 2020 The Android Open Source Project
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
"""Check that -Wl,--no-rosegment is used when required.

https://github.com/android/ndk/issues/1196
"""
from pathlib import Path
from typing import Optional, Tuple

from ndk.abis import Abi
from ndk.testing.flag_verifier import FlagVerifier


def run_test(ndk_path: str, abi: Abi, _api: int) -> Tuple[bool, Optional[str]]:
    """Checks correct --no-rosegment use."""
    verifier = FlagVerifier(Path('project'), Path(ndk_path), abi, 28)
    verifier.expect_flag('-Wl,--no-rosegment')
    verifier.expect_not_flag('-Wl,--rosegment')
    result = verifier.verify()
    if result.failed():
        return result.make_test_result_tuple()

    verifier = FlagVerifier(Path('project'), Path(ndk_path), abi, 29)
    verifier.expect_not_flag('-Wl,--no-rosegment')
    return verifier.verify().make_test_result_tuple()
