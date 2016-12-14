#!/bin/bash
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
# gen-platforms.sh
#
# This tool is used when packaging a new release, or when developing
# the NDK itself. It will populate DST ($NDK/platforms by default)
# with the content of SRC ($NDK/../development/ndk/platforms/ by default).
#
# The idea is that the content of $SRC/android-N/ only contains stuff
# that is relevant to API level N, and not contain anything that is already
# provided by API level N-1, N-2, etc..
#
# More precisely, for each architecture A:
#  $SRC/android-N/include        --> $DST/android-N/arch-A/usr/include
#  $SRC/android-N/arch-A/include --> $DST/android-N/arch-A/usr/include
#  $SRC/android-N/arch-A/lib     --> $DST/android-N/arch-A/usr/lib
#
# Also, we generate on-the-fly shared dynamic libraries from list of symbols:
#
#  $SRC/android-N/arch-A/symbols --> $DST/android-N/arch-A/usr/lib
#
# Repeat after that for N+1, N+2, etc..
#

PROGDIR=$(dirname "$0")
. "$PROGDIR/prebuilt-common.sh"

# Return the list of platform supported from $1/platforms
# as a single space-separated sorted list of levels. (e.g. "3 4 5 8 9 14")
# $1: source directory
extract_platforms_from ()
{
    if [ -d "$1" ] ; then
        (cd "$1/platforms" && ls -d android-*) | sed -e "s!android-!!" | sort -g | tr '\n' ' '
    else
        echo ""
    fi
}

# Override tmp file to be predictable
TMPC=$TMPDIR/tmp/tests/tmp-platform.c
TMPO=$TMPDIR/tmp/tests/tmp-platform.o
TMPE=$TMPDIR/tmp/tests/tmp-platform$EXE

SRCDIR="../development/ndk"
DSTDIR="$TMPDIR"

ARCHS="$DEFAULT_ARCHS"
PLATFORMS=`extract_platforms_from "$SRCDIR"`
NDK_DIR=$ANDROID_NDK_ROOT
NDK_BUILD_NUMBER=0

OPTION_HELP=no
OPTION_PLATFORMS=
OPTION_SRCDIR=
OPTION_DSTDIR=
OPTION_FAST_COPY=
OPTION_ARCH=
OPTION_ABI=
OPTION_DEBUG_LIBS=
OPTION_OVERLAY=
OPTION_GCC_VERSION="default"
OPTION_LLVM_VERSION=$DEFAULT_LLVM_VERSION
OPTION_CASE_INSENSITIVE=no
PACKAGE_DIR=

VERBOSE=no

for opt do
  optarg=`expr "x$opt" : 'x[^=]*=\(.*\)'`
  case "$opt" in
  --help|-h|-\?) OPTION_HELP=yes
  ;;
  --verbose)
    VERBOSE=yes
    ;;
  --src-dir=*)
    OPTION_SRCDIR="$optarg"
    ;;
  --dst-dir=*)
    OPTION_DSTDIR="$optarg"
    ;;
  --ndk-dir=*)
    NDK_DIR=$optarg
    ;;
  --build-number=*)
    NDK_BUILD_NUMBER=$optarg
    ;;
  --platform=*)
    OPTION_PLATFORM=$optarg
    ;;
  --arch=*)
    OPTION_ARCH=$optarg
    ;;
  --abi=*)  # We still support this for backwards-compatibility
    OPTION_ABI=$optarg
    ;;
  --fast-copy)
    OPTION_FAST_COPY=yes
    ;;
  --package-dir=*)
    PACKAGE_DIR=$optarg
    ;;
  --debug-libs)
    OPTION_DEBUG_LIBS=true
    ;;
  --overlay)
    OPTION_OVERLAY=true
    ;;
  --gcc-version=*)
    OPTION_GCC_VERSION=$optarg
    ;;
  --llvm-version=*)
    OPTION_LLVM_VERSION=$optarg
    ;;
  --case-insensitive)
    OPTION_CASE_INSENSITIVE=yes
    ;;
  *)
    echo "unknown option '$opt', use --help"
    exit 1
  esac
done

if [ $OPTION_HELP = "yes" ] ; then
    echo "Collect files from an Android NDK development tree and assemble"
    echo "the platform files appropriately into a final release structure."
    echo ""
    echo "options:"
    echo ""
    echo "  --help                    Print this message"
    echo "  --verbose                 Enable verbose messages"
    echo "  --src-dir=<path>          Source directory for development platform files [$SRCDIR]"
    echo "  --dst-dir=<path>          Destination directory [$DSTDIR]"
    echo "  --ndk-dir=<path>          Use toolchains from this NDK directory [$NDK_DIR]"
    echo "  --platform=<list>         List of API levels [$PLATFORMS]"
    echo "  --arch=<list>             List of CPU architectures [$ARCHS]"
    echo "  --fast-copy               Don't create symlinks, copy files instead"
    echo "  --package-dir=<path>      Package platforms archive in specific path."
    echo "  --debug-libs              Also generate C source file for generated libraries."
    echo "  --build-number=<number>   NDK build number."
    exit 0
fi

if [ -n "$OPTION_SRCDIR" ] ; then
    SRCDIR="$OPTION_SRCDIR";
    if [ ! -d "$SRCDIR" ] ; then
        echo "ERROR: Source directory $SRCDIR does not exist !"
        exit 1
    fi
    if [ ! -d "$SRCDIR/platforms/android-3" ] ; then
        echo "ERROR: Invalid source directory: $SRCDIR"
        echo "Please make sure it contains platforms/android-3 etc..."
        exit 1
    fi
else
    SRCDIR=`dirname $ANDROID_NDK_ROOT`/development/ndk
    log "Using source directory: $SRCDIR"
fi

if [ -n "$OPTION_PLATFORM" ] ; then
    PLATFORMS=$(commas_to_spaces $OPTION_PLATFORM)
else
    # Build the list from the content of SRCDIR
    PLATFORMS=`extract_platforms_from "$SRCDIR"`
    log "Using platforms: $PLATFORMS"
fi

# Remove the android- prefix of any platform name
PLATFORMS=$(echo $PLATFORMS | tr ' ' '\n' | sed -e 's!^android-!!g' | tr '\n' ' ')

if [ -n "$OPTION_DSTDIR" ] ; then
    DSTDIR="$OPTION_DSTDIR"
else
    log "Using destination directory: $DSTDIR"
fi

# Handle architecture list
#
# We support both --arch and --abi for backwards compatibility reasons
# --arch is the new hotness, --abi is deprecated.
#
if [ -n "$OPTION_ARCH" ]; then
    OPTION_ARCH=$(commas_to_spaces $OPTION_ARCH)
fi

if [ -n "$OPTION_ABI" ] ; then
    echo "WARNING: --abi=<names> is deprecated. Use --arch=<names> instead!"
    OPTION_ABI=$(commas_to_spaces $OPTION_ABI)
    if [ -n "$OPTION_ARCH" -a "$OPTION_ARCH" != "$OPTION_ABI" ]; then
        echo "ERROR: You can't use both --abi and --arch with different values!"
        exit 1
    fi
    OPTION_ARCH=$OPTION_ABI
fi

if [ -n "$OPTION_ARCH" ] ; then
    ARCHS="$OPTION_ARCH"
fi
log "Using architectures: $(commas_to_spaces $ARCHS)"

log "Checking source platforms."
for PLATFORM in $PLATFORMS; do
    DIR="$SRCDIR/platforms/android-$PLATFORM"
    if [ ! -d $DIR ] ; then
        echo "ERROR: Directory missing: $DIR"
        echo "Please check your --platform=<list> option and try again."
        exit 2
    else
        log "  $DIR"
    fi
done

log "Checking source platform architectures."
BAD_ARCHS=
for ARCH in $ARCHS; do
    eval CHECK_$ARCH=no
done
for PLATFORM in $PLATFORMS; do
    for ARCH in $ARCHS; do
        DIR="$SRCDIR/platforms/android-$PLATFORM/arch-$ARCH"
        if [ -d $DIR ] ; then
            log "  $DIR"
            eval CHECK_$ARCH=yes
        fi
    done
done

BAD_ARCHS=
for ARCH in $ARCHS; do
    CHECK=`var_value CHECK_$ARCH`
    log "  $ARCH check: $CHECK"
    if [ "$CHECK" = no ] ; then
        if [ -z "$BAD_ARCHS" ] ; then
            BAD_ARCHS=$ARCH
        else
            BAD_ARCHS="$BAD_ARCHS $ARCH"
        fi
    fi
done

if [ -n "$BAD_ARCHS" ] ; then
    echo "ERROR: Source directory doesn't support these ARCHs: $BAD_ARCHS"
    exit 3
fi

# $1: source directory (relative to $SRCDIR)
# $2: destination directory (relative to $DSTDIR)
# $3: description of directory contents (e.g. "sysroot")
copy_src_directory ()
{
    local SDIR="$SRCDIR/$1"
    local DDIR="$DSTDIR/$2"
    if [ -d "$SDIR" ] ; then
        log "Copying $3 from \$SRC/$1 to \$DST/$2."
        mkdir -p "$DDIR" && (cd "$SDIR" && 2>/dev/null tar chf - *) | (tar xf - -C "$DDIR")
        if [ $? != 0 ] ; then
            echo "ERROR: Could not copy $3 directory $SDIR into $DDIR !"
            exit 5
        fi
    fi
}

# $1: source dir
# $2: destination dir
# $3: reverse path
#
symlink_src_directory_inner ()
{
    local files file subdir rev
    mkdir -p "$DSTDIR/$2"
    rev=$3
    files=$(cd $DSTDIR/$1 && ls -1p)
    for file in $files; do
        if [ "$file" = "${file%%/}" ]; then
            log "Link \$DST/$2/$file --> $rev/$1/$file"
            ln -s $rev/$1/$file $DSTDIR/$2/$file
        else
            file=${file%%/}
            symlink_src_directory_inner "$1/$file" "$2/$file" "$rev/.."
        fi
    done
}
# Create a symlink-copy of directory $1 into $2
# This function is recursive.
#
# $1: source directory (relative to $SRCDIR)
# $2: destination directory (relative to $DSTDIR)
symlink_src_directory ()
{
    symlink_src_directory_inner "$1" "$2" "$(reverse_path $1)"
}

# $1: Architecture
# Out: compiler command
get_default_compiler_for_arch()
{
    local ARCH=$1
    local TOOLCHAIN_PREFIX CC GCC_VERSION

    if [ -n "$OPTION_GCC_VERSION" -a "$OPTION_GCC_VERSION" != "default" ]; then
        GCC_VERSION=$OPTION_GCC_VERSION
    else
        GCC_VERSION=$(get_default_gcc_version_for_arch $ARCH)
    fi

    for TAG in $HOST_TAG $HOST_TAG32; do
        TOOLCHAIN_PREFIX="$ANDROID_BUILD_TOP/prebuilts/ndk/current/$(get_toolchain_binprefix_for_arch $ARCH $GCC_VERSION $TAG)"
        TOOLCHAIN_PREFIX=${TOOLCHAIN_PREFIX%-}
        CC="$TOOLCHAIN_PREFIX-gcc"
        if [ -f "$CC" ]; then
            break;
        fi
    done

    if [ ! -f "$CC" ]; then
        dump "ERROR: $ARCH toolchain not installed: $CC"
        exit 1
    fi
    echo "$CC"
}

# Copies the prebuilt shared library stubs into the NDK sysroot.
# $1: Destination sysroot
# $2: Architecture
# $3: API level
copy_shared_libraries ()
{
    local DEST=$DSTDIR/$1
    local ARCH=$2
    local API=$3

    PLATFORM_PREBUILTS=$NDK_DIR/../prebuilts/ndk/platform
    PREBUILT_SYSROOT=$PLATFORM_PREBUILTS/platforms/android-$API/arch-$ARCH
    dump "Copying prebuilt sysroot $PREBUILT_SYSROOT/usr/* -> `pwd`/$DEST"
    cp -r $PREBUILT_SYSROOT/usr/* $DEST
}

# $1: platform number
# $2: architecture name
# $3: common source directory (for crtbrand.c, etc)
# $4: source directory (for *.S files)
# $5: destination directory
# $6: flags for compiler (optional)
gen_crt_objects ()
{
    local API=$1
    local ARCH=$2
    local COMMON_SRC_DIR="$SRCDIR/$3"
    local SRC_DIR="$SRCDIR/$4"
    local DST_DIR="$DSTDIR/$5"
    local FLAGS="$6"
    local SRC_FILE DST_FILE
    local CC

    if [ ! -d "$SRC_DIR" ]; then
        return
    fi

    # Let's locate the toolchain we're going to use
    CC=$(get_default_compiler_for_arch $ARCH)" $FLAGS"
    if [ $? != 0 ]; then
        echo $CC
        exit 1
    fi

    # Substitute the "%NDK_VERSION%" and "%NDK_BUILD_NUMBER%" literal with the real version
    # and build number according to the configuration.
    NDK_VERSION=`python $NDK_DIR/config.py`
    NDK_RESERVED_SIZE=64
    CRTBRAND_S=$DST_DIR/crtbrand.s
    CRTBRAND_C=$DST_DIR/crtbrand.c
    log "Generating platform $API crtbrand assembly code: $CRTBRAND_S"
    (cd "$COMMON_SRC_DIR" && cat crtbrand.c | sed -e 's/%NDK_VERSION%/'"$NDK_VERSION"'/' | \
        sed -e 's/%NDK_BUILD_NUMBER%/'"$NDK_BUILD_NUMBER"'/' > "$CRTBRAND_C")
    (cd "$COMMON_SRC_DIR" && mkdir -p `dirname $CRTBRAND_S` && $CC -DPLATFORM_SDK_VERSION=$API -fpic -S -o - "$CRTBRAND_C" | \
        sed -e '/\.note\.ABI-tag/s/progbits/note/' > "$CRTBRAND_S")

    if [ $? != 0 ]; then
        dump "ERROR: Could not generate $CRTBRAND_S from $COMMON_SRC_DIR/crtbrand.c"
        exit 1
    fi

    for SRC_FILE in $(cd "$SRC_DIR" && ls crt*.[cS]); do
        DST_FILE=${SRC_FILE%%.c}
        DST_FILE=${DST_FILE%%.S}.o
        COPY_CRTBEGIN=false

        case "$DST_FILE" in
            "crtend.o")
                # Special case: crtend.S must be compiled as crtend_android.o
                # This is for long historical reasons, i.e. to avoid name conflicts
                # in the past with other crtend.o files. This is hard-coded in the
                # Android toolchain configuration, so switch the name here.
                DST_FILE=crtend_android.o
                ;;
            "crtbegin_dynamic.o"|"crtbegin_static.o")
                # Add .note.ABI-tag section
                SRC_FILE=$SRC_FILE" $CRTBRAND_S"
                ;;
            "crtbegin.o")
                # If we have a single source for both crtbegin_static.o and
                # crtbegin_dynamic.o we generate one and make a copy later.
                DST_FILE=crtbegin_dynamic.o
                # Add .note.ABI-tag section
                SRC_FILE=$SRC_FILE" $CRTBRAND_S"
                COPY_CRTBEGIN=true
                ;;
        esac

        log "Generating $ARCH C runtime object: $SRC_FILE -> $DST_FILE"
        (cd "$SRC_DIR" && $CC \
                 -I$SRCDIR/../../bionic/libc/include \
                 -I$SRCDIR/../../bionic/libc/arch-common/bionic \
                 -I$SRCDIR/../../bionic/libc/arch-$ARCH/include \
                 -DPLATFORM_SDK_VERSION=$API \
                 -O2 -fpic -Wl,-r -nostdlib -o "$DST_DIR/$DST_FILE" $SRC_FILE)
        if [ $? != 0 ]; then
            dump "ERROR: Could not generate $DST_FILE from $SRC_DIR/$SRC_FILE"
            exit 1
        fi
        if [ "$COPY_CRTBEGIN" = "true" ]; then
            dump "cp $DST_DIR/crtbegin_dynamic.o $DST_DIR/crtbegin_static.o"
            cp "$DST_DIR/crtbegin_dynamic.o" "$DST_DIR/crtbegin_static.o"
        fi
    done
    rm -f "$CRTBRAND_S"
}

# $1: platform number
# $2: architecture
# $3: target NDK directory
generate_api_level ()
{
    local API=$1
    local ARCH=$2
    local HEADER="platforms/android-$API/arch-$ARCH/usr/include/android/api-level.h"
    log "Generating: $HEADER"
    rm -f "$3/$HEADER"  # Remove symlink if any.
    cat > "$3/$HEADER" <<EOF
/*
 * Copyright (C) 2008 The Android Open Source Project
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *  * Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 *  * Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in
 *    the documentation and/or other materials provided with the
 *    distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
 * OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 */
#ifndef ANDROID_API_LEVEL_H
#define ANDROID_API_LEVEL_H

#define __ANDROID_API__ $API

#endif /* ANDROID_API_LEVEL_H */
EOF
}

# Copy platform sysroot into your destination
#

# if $SRC/android-$PLATFORM/arch-$ARCH exists
#   $SRC/android-$PLATFORM/include --> $DST/android-$PLATFORM/arch-$ARCH/usr/include
#   $SRC/android-$PLATFORM/arch-$ARCH/include --> $DST/android-$PLATFORM/arch-$ARCH/usr/include
#   $SRC/android-$PLATFORM/arch-$ARCH/lib --> $DST/android-$PLATFORM/arch-$ARCH/usr/lib
#
if [ -z "$OPTION_OVERLAY" ]; then
    rm -rf $DSTDIR/platforms && mkdir -p $DSTDIR/platforms
fi
for ARCH in $ARCHS; do
    echo "## Generating arch: $ARCH"
    # Find first platform for this arch
    PREV_SYSROOT_DST=
    PREV_PLATFORM_SRC_ARCH=
    LIBDIR=$(get_default_libdir_for_arch $ARCH)

    for PLATFORM in $PLATFORMS; do
        echo "## Generating platform: $PLATFORM"
        PLATFORM_DST=platforms/android-$PLATFORM   # Relative to $DSTDIR
        PLATFORM_SRC=$PLATFORM_DST                 # Relative to $SRCDIR
        SYSROOT_DST=$PLATFORM_DST/arch-$ARCH/usr
        # Skip over if there is no arch-specific file for this platform
        # and no destination platform directory was created. This is needed
        # because x86 and MIPS don't have files for API levels 3-8.
        if [ -z "$PREV_SYSROOT_DST" -a \
           ! -d "$SRCDIR/$PLATFORM_SRC/arch-$ARCH" ]; then
            log "Skipping: \$SRC/$PLATFORM_SRC/arch-$ARCH"
            continue
        fi

        log "Populating \$DST/platforms/android-$PLATFORM/arch-$ARCH"

        # If this is not the first destination directory, copy over, or
        # symlink the files from the previous one now.
        if [ "$PREV_SYSROOT_DST" ]; then
            if [ "$OPTION_FAST_COPY" ]; then
                log "Copying \$DST/$PREV_SYSROOT_DST to \$DST/$SYSROOT_DST"
                copy_directory "$DSTDIR/$PREV_SYSROOT_DST" "$DSTDIR/$SYSROOT_DST"
            else
                log "Symlink-copying \$DST/$PREV_SYSROOT_DST to \$DST/$SYSROOT_DST"
                symlink_src_directory $PREV_SYSROOT_DST $SYSROOT_DST
            fi
        fi

        # If this is the first destination directory, copy the common
        # files from previous platform directories into this one.
        # This helps copy the common headers from android-3 to android-8
        # into the x86 and mips android-9 directories.
        if [ -z "$PREV_SYSROOT_DST" ]; then
            for OLD_PLATFORM in $PLATFORMS; do
                if [ "$OLD_PLATFORM" = "$PLATFORM" ]; then
                    break
                fi
                copy_src_directory platforms/android-$OLD_PLATFORM/include \
                                   $SYSROOT_DST/include \
                                   "common android-$OLD_PLATFORM headers"
            done
        fi

        # There are two set of bionic headers: the original ones haven't been updated since
        # gingerbread except for bug fixing, and the new ones in android-$FIRST_API64_LEVEL
        # with 64-bit support.  Before the old bionic headers are deprecated/removed, we need
        # to remove stale old headers when createing platform = $FIRST_API64_LEVEL
        if [ "$PLATFORM" = "$FIRST_API64_LEVEL" ]; then
            log "Removing stale bionic headers in \$DST/$SYSROOT_DST/include"
            nonbionic_files="android EGL GLES GLES2 GLES3 KHR media OMXAL SLES jni.h thread_db.h zconf.h zlib.h"
            if [ -d "$DSTDIR/$SYSROOT_DST/include/" ]; then
                files=$(cd "$DSTDIR/$SYSROOT_DST/include/" && ls)
                for file in $files; do
                    if [ "$nonbionic_files" = "${nonbionic_files%%${file}*}" ]; then
                        rm -rf "$DSTDIR/$SYSROOT_DST/include/$file"
                    fi
                done
            fi
        fi

        # Now copy over all non-arch specific include files
        copy_src_directory $PLATFORM_SRC/include $SYSROOT_DST/include "common system headers"
        copy_src_directory $PLATFORM_SRC/arch-$ARCH/include $SYSROOT_DST/include "$ARCH system headers"

        generate_api_level "$PLATFORM" "$ARCH" "$DSTDIR"

        # Copy the prebuilt static libraries.  We need full set for multilib compiler for some arch
        case "$ARCH" in
            x86_64)
                copy_src_directory $PLATFORM_SRC/arch-$ARCH/lib $SYSROOT_DST/lib "x86 sysroot libs"
                copy_src_directory $PLATFORM_SRC/arch-$ARCH/lib64 $SYSROOT_DST/lib64 "x86_64 sysroot libs"
                ;;
            mips64)
                copy_src_directory $PLATFORM_SRC/arch-$ARCH/lib64 $SYSROOT_DST/lib64 "mips -mabi=64 -mips64r6 sysroot libs"
                # create empty navigational dir expected by multilib clang
                mkdir -p "$DSTDIR/$SYSROOT_DST/lib"
                ;;
            mips)
                copy_src_directory $PLATFORM_SRC/arch-$ARCH/lib $SYSROOT_DST/lib "mips -mabi=32 -mips32 sysroot libs"
                copy_src_directory $PLATFORM_SRC/arch-$ARCH/libr6 $SYSROOT_DST/libr6 "mips -mabi=32 -mips32r6 sysroot libs"
                # create empty navigational dir expected by mips64el gcc
                mkdir -p "$DSTDIR/$SYSROOT_DST/lib64"
                ;;
            *)
                copy_src_directory $PLATFORM_SRC/arch-$ARCH/$LIBDIR $SYSROOT_DST/$LIBDIR "$ARCH sysroot libs"
                ;;
        esac

        # Generate C runtime object files when available
        PLATFORM_SRC_ARCH=$PLATFORM_SRC/arch-$ARCH/src
        if [ ! -d "$SRCDIR/$PLATFORM_SRC_ARCH" ]; then
            PLATFORM_SRC_ARCH=$PREV_PLATFORM_SRC_ARCH
        else
            PREV_PLATFORM_SRC_ARCH=$PLATFORM_SRC_ARCH
        fi

        # Genreate crt objects
        case "$ARCH" in
            x86_64)
                gen_crt_objects $PLATFORM $ARCH platforms/common/src $PLATFORM_SRC_ARCH $SYSROOT_DST/lib "-m32"
                gen_crt_objects $PLATFORM $ARCH platforms/common/src $PLATFORM_SRC_ARCH $SYSROOT_DST/lib64 "-m64"
                ;;
            mips64)
                gen_crt_objects $PLATFORM $ARCH platforms/common/src $PLATFORM_SRC_ARCH $SYSROOT_DST/lib64 "-mabi=64 -mips64r6"
                ;;
            mips)
                gen_crt_objects $PLATFORM $ARCH platforms/common/src $PLATFORM_SRC_ARCH $SYSROOT_DST/lib "-mabi=32 -mips32"
                gen_crt_objects $PLATFORM $ARCH platforms/common/src $PLATFORM_SRC_ARCH $SYSROOT_DST/libr6 "-mabi=32 -mips32r6"
                ;;
            *)
                gen_crt_objects $PLATFORM $ARCH platforms/common/src $PLATFORM_SRC_ARCH $SYSROOT_DST/$LIBDIR
                ;;
        esac

        copy_shared_libraries $SYSROOT_DST $ARCH $PLATFORM
        PREV_SYSROOT_DST=$SYSROOT_DST
    done
done

if [ "$PACKAGE_DIR" ]; then
    # Remove "duplicate" files for case-insensitive platforms.
    if [ "$OPTION_CASE_INSENSITIVE" = "yes" ]; then
        find "$DSTDIR/platforms" | sort -f | uniq -di | xargs rm
    fi

    for PLATFORM in $PLATFORMS; do
        PLATFORM_NAME="android-$PLATFORM"
        make_repo_prop "$DSTDIR/platforms/$PLATFORM_NAME"

        NOTICE="$DSTDIR/platforms/$PLATFORM_NAME/NOTICE"
        cp "$ANDROID_BUILD_TOP/bionic/libc/NOTICE" $NOTICE
        echo >> $NOTICE
        cp "$ANDROID_BUILD_TOP/bionic/libm/NOTICE" $NOTICE
        echo >> $NOTICE
        cp "$ANDROID_BUILD_TOP/bionic/libdl/NOTICE" $NOTICE
        echo >> $NOTICE
        cp "$ANDROID_BUILD_TOP/bionic/libstdc++/NOTICE" $NOTICE

        mkdir -p "$PACKAGE_DIR"
        fail_panic "Could not create package directory: $PACKAGE_DIR"
        ARCHIVE=platform-$PLATFORM.zip
        dump "Packaging $ARCHIVE"
        pack_archive "$PACKAGE_DIR/$ARCHIVE" "$DSTDIR/platforms" "$PLATFORM_NAME"
        fail_panic "Could not package platform-$PLATFORM"
    done
fi

log "Done !"
