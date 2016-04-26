#
# Copyright (C) 2011 The Android Open Source Project
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
#  This file contains various shell function definitions that can be
#  used to either build a static and shared libraries from sources, or
#  generate a Makefile to do it in parallel.
#

_BUILD_TAB=$(echo " " | tr ' ' '\t')

builder_command ()
{
    if [ -z "$_BUILD_MK" ]; then
        echo "$@"
        "$@"
    else
        echo "${_BUILD_TAB}$@" >> $_BUILD_MK
    fi
}


builder_log ()
{
    if [ "$_BUILD_MK" ]; then
        echo "${_BUILD_TAB}echo $@" >> $_BUILD_MK
    else
        log "$@"
    fi
}

# $1: Build directory
# $2: Optional Makefile name
builder_begin ()
{
    _BUILD_DIR_NEW=
    _BUILD_DIR=$1
    if [ ! -d "$_BUILD_DIR" ]; then
        mkdir -p "$_BUILD_DIR"
        fail_panic "Can't create build directory: $_BUILD_DIR"
        _BUILD_DIR_NEW=true
    else
        rm -rf "$_BUILD_DIR/*"
        fail_panic "Can't cleanup build directory: $_BUILD_DIR"
    fi
    _BUILD_TARGETS=
    _BUILD_PREFIX=
    _BUILD_MK=$2
    if [ -n "$_BUILD_MK" ]; then
        log "Creating temporary build Makefile: $_BUILD_MK"
        rm -f $_BUILD_MK &&
        echo "# Auto-generated by $0 - do not edit!" > $_BUILD_MK
        echo ".PHONY: all" >> $_BUILD_MK
        echo "all:" >> $_BUILD_MK
    fi

    builder_begin_module
}

# $1: Variable name
# out: Variable value
_builder_varval ()
{
    eval echo "\$$1"
}

_builder_varadd ()
{
    local _varname="$1"
    local _varval="$(_builder_varval $_varname)"
    shift
    if [ -z "$_varval" ]; then
        eval $_varname=\"$@\"
    else
        eval $_varname=\$$_varname\" $@\"
    fi
}


builder_set_prefix ()
{
    _BUILD_PREFIX="$@"
}

builder_begin_module ()
{
    _BUILD_CC=
    _BUILD_CXX=
    _BUILD_AR=
    _BUILD_C_INCLUDES=
    _BUILD_CFLAGS=
    _BUILD_CXXFLAGS=
    _BUILD_LDFLAGS_BEGIN_SO=
    _BUILD_LDFLAGS_END_SO=
    _BUILD_LDFLAGS_BEGIN_EXE=
    _BUILD_LDFLAGS_END_EXE=
    _BUILD_LDFLAGS=
    _BUILD_BINPREFIX=
    _BUILD_DSTDIR=
    _BUILD_SRCDIR=.
    _BUILD_OBJECTS=
    _BUILD_STATIC_LIBRARIES=
    _BUILD_SHARED_LIBRARIES=
    _BUILD_COMPILER_RUNTIME_LDFLAGS=-lgcc
}

builder_set_binprefix ()
{
    _BUILD_BINPREFIX=$1
    _BUILD_CC=${1}gcc
    _BUILD_CXX=${1}g++
    _BUILD_AR=${1}ar
}

builder_set_binprefix_llvm ()
{
    _BUILD_BINPREFIX=$1
    _BUILD_CC=${1}/clang
    _BUILD_CXX=${1}/clang++
    _BUILD_AR=${2}ar
}

builder_set_builddir ()
{
    _BUILD_DIR=$1
}

builder_set_srcdir ()
{
    _BUILD_SRCDIR=$1
}

builder_set_dstdir ()
{
    _BUILD_DSTDIR=$1
}

builder_ldflags ()
{
    _builder_varadd _BUILD_LDFLAGS "$@"
}

builder_ldflags_exe ()
{
    _builder_varadd _BUILD_LDFLAGS_EXE "$@"
}

builder_cflags ()
{
    _builder_varadd _BUILD_CFLAGS "$@"
}

builder_cxxflags ()
{
    _builder_varadd _BUILD_CXXFLAGS "$@"
}

builder_c_includes ()
{
    _builder_varadd _BUILD_C_INCLUDES "$@"
}

# $1: optional var to hold the original cflags before reset
builder_reset_cflags ()
{
    local _varname="$1"
    if [ -n "$_varname" ] ; then
        eval $_varname=\"$_BUILD_CFLAGS\"
    fi
    _BUILD_CFLAGS=
}

# $1: optional var to hold the original cxxflags before reset
builder_reset_cxxflags ()
{
    local _varname="$1"
    if [ -n "$_varname" ] ; then
        eval $_varname=\"$_BUILD_CXXFLAGS\"
    fi
    _BUILD_CXXFLAGS=
}

# $1: optional var to hold the original c_includes before reset
builder_reset_c_includes ()
{
    local _varname="$1"
    if [ -n "$_varname" ] ; then
        eval $_varname=\"$_BUILD_C_INCLUDES\"
    fi
    _BUILD_C_INCLUDES=
}

builder_compiler_runtime_ldflags ()
{
    _BUILD_COMPILER_RUNTIME_LDFLAGS=$1
}

builder_link_with ()
{
    local LIB
    for LIB; do
        case $LIB in
            *.a)
                _builder_varadd _BUILD_STATIC_LIBRARIES $LIB
                ;;
            *.so)
                _builder_varadd _BUILD_SHARED_LIBRARIES $LIB
                ;;
            *)
                echo "ERROR: Unknown link library extension: $LIB"
                exit 1
        esac
    done
}

builder_sources ()
{
    local src srcfull obj cc cflags text
    if [ -z "$_BUILD_DIR" ]; then
        panic "Build directory not set!"
    fi
    if [ -z "$_BUILD_CC" ]; then
        _BUILD_CC=${CC:-gcc}
    fi
    if [ -z "$_BUILD_CXX" ]; then
        _BUILD_CXX=${CXX:-g++}
    fi
    for src in "$@"; do
        srcfull=$_BUILD_SRCDIR/$src
        if [ ! -f "$srcfull" ]; then
            echo "ERROR: Missing source file: $srcfull"
            exit 1
        fi
        obj=$src
        cflags=""
        for inc in $_BUILD_C_INCLUDES; do
            cflags=$cflags" -I$inc"
        done
        cflags=$cflags" -I$_BUILD_SRCDIR"
        case $obj in
            *.c)
                obj=${obj%%.c}
                text="C"
                cc=$_BUILD_CC
                cflags="$cflags $_BUILD_CFLAGS"
                ;;
            *.cpp)
                obj=${obj%%.cpp}
                text="C++"
                cc=$_BUILD_CXX
                cflags="$cflags $_BUILD_CXXFLAGS"
                ;;
            *.cc)
                obj=${obj%%.cc}
                text="C++"
                cc=$_BUILD_CXX
                cflags="$cflags $_BUILD_CXXFLAGS"
                ;;
            *.S|*.s)
                obj=${obj%%.$obj}
                text="ASM"
                cc=$_BUILD_CC
                cflags="$cflags $_BUILD_CFLAGS"
                ;;
            *)
                echo "Unknown source file extension: $obj"
                exit 1
                ;;
        esac

        # Source file path can include ../ path items, ensure
        # that the generated object do not back up the output
        # directory by translating them to __/
        obj=$(echo "$obj" | tr '../' '__/')

        # Ensure we have unwind tables in the generated machine code
        # This is useful to get good stack traces
        cflags=$cflags" -funwind-tables"

        obj=$_BUILD_DIR/$obj.o
        if [ "$_BUILD_MK" ]; then
            echo "$obj: $srcfull" >> $_BUILD_MK
        fi
        builder_log "${_BUILD_PREFIX}$text: $src"
        builder_command mkdir -p $(dirname "$obj")
        builder_command $NDK_CCACHE $cc -c -o "$obj" "$srcfull" $cflags
        fail_panic "Could not compile ${_BUILD_PREFIX}$src"
        _BUILD_OBJECTS=$_BUILD_OBJECTS" $obj"
    done
}

builder_static_library ()
{
    local lib libname arflags
    libname=$1
    if [ -z "$_BUILD_DSTDIR" ]; then
        panic "Destination directory not set"
    fi
    lib=$_BUILD_DSTDIR/$libname
    lib=${lib%%.a}.a
    if [ "$_BUILD_MK" ]; then
        _BUILD_TARGETS=$_BUILD_TARGETS" $lib"
        echo "$lib: $_BUILD_OBJECTS" >> $_BUILD_MK
    fi
    if [ -z "${_BUILD_AR}" ]; then
        _BUILD_AR=${AR:-ar}
    fi
    builder_log "${_BUILD_PREFIX}Archive: $libname"
    rm -f "$lib"
    arflags="crs"
    case $HOST_TAG in
        darwin*)
            # XCode 'ar' doesn't support D flag
            ;;
        *)
            arflags="${arflags}D"
            ;;
    esac
    builder_command ${_BUILD_AR} $arflags "$lib" "$_BUILD_OBJECTS"
    fail_panic "Could not archive ${_BUILD_PREFIX}$libname objects!"
}

builder_host_static_library ()
{
    local lib libname
    libname=$1
    if [ -z "$_BUILD_DSTDIR" ]; then
        panic "Destination directory not set"
    fi
    lib=$_BUILD_DSTDIR/$libname
    lib=${lib%%.a}.a
    if [ "$_BUILD_MK" ]; then
        _BUILD_TARGETS=$_BUILD_TARGETS" $lib"
        echo "$lib: $_BUILD_OBJECTS" >> $_BUILD_MK
    fi
    if [ -z "$BUILD_AR" ]; then
        _BUILD_AR=${AR:-ar}
    fi
    builder_log "${_BUILD_PREFIX}Archive: $libname"
    rm -f "$lib"
    builder_command ${_BUILD_AR} crsD "$lib" "$_BUILD_OBJECTS"
    fail_panic "Could not archive ${_BUILD_PREFIX}$libname objects!"
}

builder_shared_library ()
{
    local lib libname suffix libm
    libname=$1
    suffix=$2
    armeabi_v7a_float_abi=$3

    if [ -z "$suffix" ]; then
        suffix=".so"
    fi
    libm="-lm"
    if [ "$armeabi_v7a_float_abi" = "hard" ]; then
        libm="-lm_hard"
    fi
    lib=$_BUILD_DSTDIR/$libname
    lib=${lib%%${suffix}}${suffix}
    if [ "$_BUILD_MK" ]; then
        _BUILD_TARGETS=$_BUILD_TARGETS" $lib"
        echo "$lib: $_BUILD_OBJECTS" >> $_BUILD_MK
    fi
    builder_log "${_BUILD_PREFIX}SharedLibrary: $libname"

    # Important: -lgcc must appear after objects and static libraries,
    #            but before shared libraries for Android. It doesn't hurt
    #            for other platforms.
    #            Also $libm must come before -lc because bionic libc
    #            accidentally exports a soft-float version of ldexp.
    builder_command ${_BUILD_CXX} \
        -Wl,-soname,$(basename $lib) \
        -Wl,-shared \
        $_BUILD_LDFLAGS_BEGIN_SO \
        $_BUILD_OBJECTS \
        $_BUILD_STATIC_LIBRARIES \
        $_BUILD_COMPILER_RUNTIME_LDFLAGS \
        $_BUILD_SHARED_LIBRARIES \
        $libm -lc \
        $_BUILD_LDFLAGS \
        $_BUILD_LDFLAGS_END_SO \
        -o $lib
    fail_panic "Could not create ${_BUILD_PREFIX}shared library $libname"
}

# Same as builder_shared_library, but do not link the default libs
builder_nostdlib_shared_library ()
{
    local lib libname suffix
    libname=$1
    suffix=$2
    if [ -z "$suffix" ]; then
        suffix=".so"
    fi
    lib=$_BUILD_DSTDIR/$libname
    lib=${lib%%${suffix}}${suffix}
    if [ "$_BUILD_MK" ]; then
        _BUILD_TARGETS=$_BUILD_TARGETS" $lib"
        echo "$lib: $_BUILD_OBJECTS" >> $_BUILD_MK
    fi
    builder_log "${_BUILD_PREFIX}SharedLibrary: $libname"

    builder_command ${_BUILD_CXX} \
        -Wl,-soname,$(basename $lib) \
        -Wl,-shared \
        $_BUILD_LDFLAGS_BEGIN_SO \
        $_BUILD_OBJECTS \
        $_BUILD_STATIC_LIBRARIES \
        $_BUILD_SHARED_LIBRARIES \
        $_BUILD_LDFLAGS \
        $_BUILD_LDFLAGS_END_SO \
        -o $lib
    fail_panic "Could not create ${_BUILD_PREFIX}shared library $libname"
}

builder_host_shared_library ()
{
    local lib libname
    libname=$1
    lib=$_BUILD_DSTDIR/$libname
    lib=${lib%%.so}.so
    if [ "$_BUILD_MK" ]; then
        _BUILD_TARGETS=$_BUILD_TARGETS" $lib"
        echo "$lib: $_BUILD_OBJECTS" >> $_BUILD_MK
    fi
    builder_log "${_BUILD_PREFIX}SharedLibrary: $libname"

    if [ -z "$_BUILD_CXX" ]; then
        _BUILD_CXX=${CXX:-g++}
    fi

    # Important: -lgcc must appear after objects and static libraries,
    #            but before shared libraries for Android. It doesn't hurt
    #            for other platforms.
    builder_command ${_BUILD_CXX} \
        -shared -s \
        $_BUILD_OBJECTS \
        $_BUILD_STATIC_LIBRARIES \
        $_BUILD_SHARED_LIBRARIES \
        $_BUILD_LDFLAGS \
        -o $lib
    fail_panic "Could not create ${_BUILD_PREFIX}shared library $libname"
}

builder_host_executable ()
{
    local exe exename
    exename=$1
    exe=$_BUILD_DSTDIR/$exename$HOST_EXE
    if [ "$_BUILD_MK" ]; then
        _BUILD_TARGETS=$_BUILD_TARGETS" $exe"
        echo "$exe: $_BUILD_OBJECTS" >> $_BUILD_MK
    fi
    builder_log "${_BUILD_PREFIX}Executable: $exename$HOST_EXE"

    if [ -z "$_BUILD_CXX" ]; then
        _BUILD_CXX=${CXX:-g++}
    fi

    # Important: -lgcc must appear after objects and static libraries,
    #            but before shared libraries for Android. It doesn't hurt
    #            for other platforms.
    builder_command ${_BUILD_CXX} \
        -s \
        $_BUILD_OBJECTS \
        $_BUILD_STATIC_LIBRARIES \
        $_BUILD_SHARED_LIBRARIES \
        $_BUILD_LDFLAGS \
        -o $exe
    fail_panic "Could not create ${_BUILD_PREFIX}executable $libname"
}


builder_end ()
{
    if [ "$_BUILD_MK" ]; then
        echo "all: $_BUILD_TARGETS" >> $_BUILD_MK
        run make -j$NUM_JOBS -f $_BUILD_MK
        fail_panic "Could not build project!"
    fi

    if [ "$_BUILD_DIR_NEW" ]; then
        log "Cleaning up build directory: $_BUILD_DIR"
        rm -rf "$_BUILD_DIR"
        _BUILD_DIR_NEW=
    fi
}

# Same as builder_begin, but to target Android with a specific ABI
# $1: ABI name (e.g. armeabi)
# $2: Build directory
# $3: Gcc version
# $4: Optional llvm version
# $5: Optional Makefile name
# $6: Platform (android-X)
builder_begin_android ()
{
    local ABI BUILDDIR LLVM_VERSION MAKEFILE
    local ARCH SYSROOT LDIR FLAGS
    local CRTBEGIN_SO_O CRTEND_SO_O CRTBEGIN_EXE_SO CRTEND_SO_O
    local BINPREFIX GCC_TOOLCHAIN LLVM_TRIPLE GCC_VERSION
    local SCRATCH_FLAGS PLATFORM
    local PREBUILT_NDK=$ANDROID_BUILD_TOP/prebuilts/ndk/current
    if [ -z "$ANDROID_BUILD_TOP" ]; then
        panic "ANDROID_BUILD_TOP is not defined!"
    elif [ ! -d "$PREBUILT_NDK/platforms" ]; then
        panic "Missing directory: $PREBUILT_NDK/platforms"
    fi
    ABI=$1
    BUILDDIR=$2
    GCC_VERSION=$3
    LLVM_VERSION=$4
    MAKEFILE=$5
    ARCH=$(convert_abi_to_arch $ABI)
    PLATFORM=$6

    if [ -n "$LLVM_VERSION" ]; then
        # override GCC_VERSION to pick $DEFAULT_LLVM_GCC??_VERSION instead
        if [ "$ABI" != "${ABI%%64*}" ]; then
            GCC_VERSION=$DEFAULT_LLVM_GCC64_VERSION
        else
            GCC_VERSION=$DEFAULT_LLVM_GCC32_VERSION
        fi
    fi
    for TAG in $HOST_TAG $HOST_TAG32; do
        BINPREFIX=$ANDROID_BUILD_TOP/prebuilts/ndk/current/$(get_toolchain_binprefix_for_arch $ARCH $GCC_VERSION $TAG)
        if [ -f ${BINPREFIX}gcc ]; then
            break;
        fi
    done
    if [ -n "$LLVM_VERSION" ]; then
        GCC_TOOLCHAIN=`dirname $BINPREFIX`
        GCC_TOOLCHAIN=`dirname $GCC_TOOLCHAIN`
        LLVM_BINPREFIX=$(get_llvm_toolchain_binprefix $TAG)
    fi

    if [ -z "$PLATFORM" ]; then
      SYSROOT=$PREBUILT_NDK/$(get_default_platform_sysroot_for_arch $ARCH)
    else
      SYSROOT=$PREBUILT_NDK/platforms/$PLATFORM/arch-$ARCH
    fi
    LDIR=$SYSROOT"/usr/"$(get_default_libdir_for_abi $ABI)

    CRTBEGIN_EXE_O=$LDIR/crtbegin_dynamic.o
    CRTEND_EXE_O=$LDIR/crtend_android.o

    CRTBEGIN_SO_O=$LDIR/crtbegin_so.o
    CRTEND_SO_O=$LDIR/crtend_so.o
    if [ ! -f "$CRTBEGIN_SO_O" ]; then
        CRTBEGIN_SO_O=$CRTBEGIN_EXE_O
    fi
    if [ ! -f "$CRTEND_SO_O" ]; then
        CRTEND_SO_O=$CRTEND_EXE_O
    fi

    builder_begin "$BUILDDIR" "$MAKEFILE"
    builder_set_prefix "$ABI "
    if [ -z "$LLVM_VERSION" ]; then
        builder_set_binprefix "$BINPREFIX"
    else
        builder_set_binprefix_llvm "$LLVM_BINPREFIX" "$BINPREFIX"
        case $ABI in
            armeabi)
                LLVM_TRIPLE=armv5te-none-linux-androideabi
                ;;
            armeabi-v7a)
                LLVM_TRIPLE=armv7-none-linux-androideabi
                ;;
            arm64-v8a)
                LLVM_TRIPLE=aarch64-none-linux-android
                ;;
            x86)
                LLVM_TRIPLE=i686-none-linux-android
                ;;
            x86_64)
                LLVM_TRIPLE=x86_64-none-linux-android
                ;;
            mips|mips32r6)
                LLVM_TRIPLE=mipsel-none-linux-android
                ;;
            mips64)
                LLVM_TRIPLE=mips64el-none-linux-android
                ;;
        esac
        SCRATCH_FLAGS="-target $LLVM_TRIPLE $FLAGS"
        builder_ldflags "$SCRATCH_FLAGS"
        if [ "$LLVM_VERSION" \> "3.4" ]; then
            # Turn off integrated-as for clang >= 3.5 due to ill-formed object it produces
            # involving inline-assembly .pushsection/.popsection which crashes ld.gold
            # BUG=18589643
            SCRATCH_FLAGS="$SCRATCH_FLAGS -fno-integrated-as"
        fi
        builder_cflags  "$SCRATCH_FLAGS"
        builder_cxxflags "$SCRATCH_FLAGS"
        if [ ! -z $GCC_TOOLCHAIN ]; then
            SCRATCH_FLAGS="-gcc-toolchain $GCC_TOOLCHAIN"
            builder_cflags "$SCRATCH_FLAGS"
            builder_cxxflags "$SCRATCH_FLAGS"
            builder_ldflags "$SCRATCH_FLAGS"
        fi
    fi

    SCRATCH_FLAGS="--sysroot=$SYSROOT"
    builder_cflags "$SCRATCH_FLAGS"
    builder_cxxflags "$SCRATCH_FLAGS"

    SCRATCH_FLAGS="--sysroot=$SYSROOT -nostdlib"
    _BUILD_LDFLAGS_BEGIN_SO="$SCRATCH_FLAGS $CRTBEGIN_SO_O"
    _BUILD_LDFLAGS_BEGIN_EXE="$SCRATCH_FLAGS $CRTBEGIN_EXE_O"

    _BUILD_LDFLAGS_END_SO="$CRTEND_SO_O"
    _BUILD_LDFLAGS_END_EXE="$CRTEND_EXE_O"

    case $ABI in
        armeabi)
            if [ -z "$LLVM_VERSION" ]; then
                # add -minline-thumb1-jumptable such that gabi++/stlport/libc++ can be linked
                # with compiler-rt where helpers __gnu_thumb1_case_* (in libgcc.a) don't exist
                SCRATCH_FLAGS="-minline-thumb1-jumptable"
                builder_cflags "$SCRATCH_FLAGS"
                builder_cxxflags "$SCRATCH_FLAGS"
            else
                builder_cflags ""
                builder_cxxflags ""
            fi
            ;;
        armeabi-v7a)
            SCRATCH_FLAGS="-march=armv7-a -mfpu=vfpv3-d16 -mfloat-abi=softfp"
            builder_cflags "$SCRATCH_FLAGS"
            builder_cxxflags "$SCRATCH_FLAGS"
            builder_ldflags "-march=armv7-a -Wl,--fix-cortex-a8"
            ;;
        mips)
            SCRATCH_FLAGS="-mips32"
            builder_cflags "$SCRATCH_FLAGS"
            builder_cxxflags "$SCRATCH_FLAGS"
            builder_ldflags "-mips32"
            ;;
    esac
}

# $1: Build directory
# $2: Optional Makefile name
builder_begin_host ()
{
    prepare_host_build
    builder_begin "$1" "$2"
    builder_set_prefix "$HOST_TAG "
}
