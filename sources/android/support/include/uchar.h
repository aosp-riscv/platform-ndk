/*
 * Copyright (C) 2013 The Android Open Source Project
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
#ifndef NDK_ANDROID_SUPPORT_UCHAR_H
#define NDK_ANDROID_SUPPORT_UCHAR_H

#include_next <uchar.h>

__BEGIN_DECLS

#if __ANDROID_API__ < __ANDROID_API_L__

size_t c16rtomb(char* __restrict, char16_t, mbstate_t* __restrict)
    __INTRODUCED_IN(21);
size_t c32rtomb(char* __restrict, char32_t, mbstate_t* __restrict)
    __INTRODUCED_IN(21);
size_t mbrtoc16(char16_t* __restrict, const char* __restrict, size_t,
                mbstate_t* __restrict) __INTRODUCED_IN(21);
size_t mbrtoc32(char32_t* __restrict, const char* __restrict, size_t,
                mbstate_t* __restrict) __INTRODUCED_IN(21);

#endif /* __ANDROID_API__ < __ANDROID_API_L__ */

__END_DECLS

#endif  // NDK_ANDROID_SUPPORT_UCHAR_H
