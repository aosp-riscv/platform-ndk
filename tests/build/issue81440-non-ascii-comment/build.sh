# Check if clang crash on non-ascii comments
# See b.android.com/81440
#

result=$($NDK/ndk-build -B APP_ABI=armeabi-v7a 2>&1)
echo $result | grep -q "error: expected"
RET=$?
rm -rf obj

if [ $RET != 0 ]; then
  echo "Error: did not find 'error: expected'"
  echo "Output:"
  echo $result
  exit 1
fi

