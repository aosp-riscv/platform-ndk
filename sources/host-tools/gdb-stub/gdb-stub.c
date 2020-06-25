/***************************************************************************
** The BSD 3-Clause License. http://www.opensource.org/licenses/BSD-3-Clause
**
** This file is part of 'mingw-builds' project.
** Copyright (c) 2011,2012,2013 by niXman (i dotty nixman doggy gmail dotty com)
** All rights reserved.
**
** Project: mingw-builds ( http://sourceforge.net/projects/mingwbuilds/ )
**
** Redistribution and use in source and binary forms, with or without 
** modification, are permitted provided that the following conditions are met:
** - Redistributions of source code must retain the above copyright 
**     notice, this list of conditions and the following disclaimer.
** - Redistributions in binary form must reproduce the above copyright 
**     notice, this list of conditions and the following disclaimer in 
**     the documentation and/or other materials provided with the distribution.
** - Neither the name of the 'mingw-builds' nor the names of its contributors may 
**     be used to endorse or promote products derived from this software 
**     without specific prior written permission.
**
** THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS 
** "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT 
** LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR 
** A PARTICULAR PURPOSE ARE DISCLAIMED.
** IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY 
** DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES 
** (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS 
** OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER 
** CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, 
** OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE 
** USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
**
***************************************************************************/

#include <windows.h>

#include <stdio.h>
#include <strings.h>

#ifdef _DEBUG
 #define dbg_printf(...) printf(__VA_ARGS__)
#else
 #define dbg_printf(...) do {} while(0)
#endif

#define GDB_TO_PYTHON_REL_DIR "."

#define GDB_EXECUTABLE_ORIG_FILENAME "gdb-orig.exe"

// The stub is installed to $PREBUILTS/bin, PYTHONHOME is $PREBUILTS.
#define PYTHONHOME_REL_DIR ".."

#define DIE_IF_FALSE(var) \
	do { \
		if ( !(var) ) { \
			fprintf(stderr, "%s(%d)[%d]: expression \"%s\" fail. terminate.\n" \
				,__FILE__ \
				,__LINE__ \
				,GetLastError() \
				,#var \
			); \
			exit(1); \
		} \
	} while (0)

int main(int argc, char** argv) {
	enum {
		 envbufsize = 1024*32
		,exebufsize = 1024
		,cmdbufsize = envbufsize
	};

	char *envbuf, *sep, *resbuf, *cmdbuf;
	DWORD len, exitCode;
	STARTUPINFO si;
	PROCESS_INFORMATION pi;

	DIE_IF_FALSE(
		(envbuf = (char *)malloc(envbufsize))
	);
	DIE_IF_FALSE(
		(cmdbuf = (char *)malloc(cmdbufsize))
	);
	*cmdbuf = 0;

	DIE_IF_FALSE(
		GetEnvironmentVariable("PATH", envbuf, envbufsize)
	);
	dbg_printf("env: %s\n", envbuf);

	DIE_IF_FALSE(
		GetModuleFileName(0, cmdbuf, exebufsize)
	);
	dbg_printf("curdir: %s\n", cmdbuf);

	DIE_IF_FALSE(
		(sep = strrchr(cmdbuf, '\\'))
	);
	*(sep+1) = 0;
	strcat(cmdbuf, GDB_TO_PYTHON_REL_DIR);
	dbg_printf("sep: %s\n", cmdbuf);

	len = strlen(envbuf)+strlen(cmdbuf)
		+1  /* for envronment separator */
		+1; /* for zero-terminator */

	DIE_IF_FALSE(
		(resbuf = (char *)malloc(len))
	);

	DIE_IF_FALSE(
		(snprintf(resbuf, len, "%s;%s", cmdbuf, envbuf) > 0)
	);
	dbg_printf("PATH: %s\n", resbuf);

	DIE_IF_FALSE(
		SetEnvironmentVariable("PATH", resbuf)
	);

	*(sep+1) = 0;
	strcat(cmdbuf, PYTHONHOME_REL_DIR);
	dbg_printf("PYTHONHOME: %s\n", cmdbuf);
	DIE_IF_FALSE(
		SetEnvironmentVariable("PYTHONHOME", cmdbuf)
	);

	*(sep+1) = 0;
	strcat(cmdbuf, GDB_EXECUTABLE_ORIG_FILENAME" ");

	if ( argc > 1 ) {
		for ( ++argv; *argv; ++argv ) {
			len = strlen(cmdbuf);
			snprintf(cmdbuf+len, cmdbufsize-len, "%s ", *argv);
		}
	}
	dbg_printf("cmd: %s\n", cmdbuf);

	HANDLE ghJob = CreateJobObject(NULL, "Gdb-Wrapper\0"/*NULL*/);
	if ( ghJob == NULL ) {
        fprintf(stderr, "Could not create job object\n");
	}
	else{
		JOBOBJECT_EXTENDED_LIMIT_INFORMATION jeli = { 0 };
		// Configure all child processes associated with the job to terminate when the last handle to the job is closed
		jeli.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
		if ( SetInformationJobObject(ghJob, JobObjectExtendedLimitInformation, &jeli, sizeof(jeli)) == 0 ) {
            fprintf(stderr, "Could not SetInformationJobObject\n");
		}
	}

	memset(&si, 0, sizeof(si));
	si.cb = sizeof(si);
	si.dwFlags |= STARTF_USESTDHANDLES;
	si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
	si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
	si.hStdError = GetStdHandle(STD_ERROR_HANDLE);

	memset(&pi, 0, sizeof(pi));
	// If current process is being monitored by the Program Compatibility Assistant (PCA), it is placed into a
	// compatibility job. Therefore, the child process must be created using CREATE_BREAKAWAY_FROM_JOB before it can be
	// placed in another job.
	DWORD creationFlags = CREATE_BREAKAWAY_FROM_JOB;
	DIE_IF_FALSE(
		CreateProcess(
			0					// exe name
			,cmdbuf				// command line
			,0					// process security attributes
			,0					// primary thread security attributes
			,TRUE				// handles are inherited
			,creationFlags		// creation flags
			,0					// use parent's environment
			,0					// use parent's current directory
			,&si				// STARTUPINFO pointer
			,&pi				// receives PROCESS_INFORMATION
		)
	);

	if ( ghJob != NULL )
		if ( AssignProcessToJobObject(ghJob, pi.hProcess) == 0 ) {
            fprintf(stderr, "Could not AssignProcessToObject\n");
		}

	// Do not handle Ctrl-C in the wrapper
	SetConsoleCtrlHandler(NULL, TRUE);

	WaitForSingleObject(pi.hProcess, INFINITE);

	DIE_IF_FALSE(
		GetExitCodeProcess(pi.hProcess, &exitCode)
	);

	if ( ghJob != NULL )
		CloseHandle(ghJob);
	CloseHandle( pi.hProcess );
	CloseHandle( pi.hThread );

	free(envbuf);
	free(resbuf);
	free(cmdbuf);

	dbg_printf("exiting with exitCode %d", exitCode);

	return exitCode;
}
