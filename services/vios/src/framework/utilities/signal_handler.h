/*
 * SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#pragma once

#include <iostream>
#include <execinfo.h>
#include <signal.h>
#include <cstdlib>
#include <cxxabi.h>
#include "logger.h"

// Initialize the static member variable
struct sigaction sigact;
class SignalHandler
{
public:
    SignalHandler() {}
    SignalHandler(void (*handler)(int))
    {
        signal(SIGINT, handler);
        signal(SIGTERM, handler);
    }
    void initBacktraceGenerator()
    {
        atexit(cleanup);
        init_signals();
    }

private:
    //static struct sigaction sigact;
    static void init_signals()
    {
        sigact.sa_handler = crash_sig_handler;
        sigemptyset(&sigact.sa_mask);
        sigact.sa_flags = 0;

        sigaddset(&sigact.sa_mask, SIGSEGV);
        sigaction(SIGSEGV, &sigact, nullptr);

        sigaddset(&sigact.sa_mask, SIGABRT);
        sigaction(SIGABRT, &sigact, nullptr);

        sigaddset(&sigact.sa_mask, SIGBUS);
        sigaction(SIGBUS, &sigact, nullptr);
    }

    static void cleanup()
    {
        sigemptyset(&sigact.sa_mask);
        // Do any cleaning up chores here
    }

    static void dumpstack(unsigned int max_frames = 63)
    {
        LOG(error) << "stack trace:" << endl;
        // storage array for stack trace address data
        void* addrlist[max_frames+1];

        // retrieve current stack addresses
        int addrlen = backtrace(addrlist, sizeof(addrlist) / sizeof(void*));
        if (addrlen == 0)
        {
            LOG(error) << " <empty, possibly corrupt>" << endl;
            return;
        }

        // resolve addresses into strings containing "filename(function+address)",
        // this array must be free()-ed
        char** symbollist = backtrace_symbols(addrlist, addrlen);

        // allocate string which will be filled with the demangled function name
        size_t funcnamesize = 256;
        char* funcname = (char*)malloc(funcnamesize);

        // iterate over the returned symbol lines. skip the first, it is the
        // address of this function.
        for (int i = 1; i < addrlen; i++)
        {
            char *begin_name = nullptr, *begin_offset = nullptr, *end_offset = nullptr;
            // find parentheses and +address offset surrounding the mangled name:
            // ./module(function+0x15c) [0x8048a6d]
            for (char *p = symbollist[i]; *p; ++p)
            {
                if (*p == '(')
                    begin_name = p;
                else if (*p == '+')
                    begin_offset = p;
                else if (*p == ')' && begin_offset)
                {
                    end_offset = p;
                    break;
                }
            }

            if (begin_name && begin_offset && end_offset
                && begin_name < begin_offset)
            {
                *begin_name++ = '\0';
                *begin_offset++ = '\0';
                *end_offset = '\0';

                // mangled name is now in [begin_name, begin_offset) and caller
                // offset in [begin_offset, end_offset). now apply
                // __cxa_demangle():
                int status;
                char* ret = abi::__cxa_demangle(begin_name, funcname, &funcnamesize, &status);
                if (status == 0)
                {
                    funcname = ret;
                    LOG(error) << symbollist[i] << " : " << funcname << "+" << begin_offset << endl;
                }
                else
                {
                    // demangling failed. Output function name as a C function with no arguments.
                    LOG(error) << symbollist[i] << " : " << begin_name << "+" << begin_offset << endl;
                }
            }
            else
            {
                // couldn't parse the line? print the whole line.
                LOG(error) << " " << symbollist[i] << endl;
            }
        }
        free(funcname);
        free(symbollist);
        return;
    }

    static void crash_sig_handler(int sig)
    {
        std::cout << "Entered Signal Handler, sig: " << sig << std::endl;
        if (sig == SIGSEGV || sig == SIGBUS || sig == SIGABRT)
        {
            dumpstack();
            std::cout << "FATAL: " << ((sig == SIGSEGV) ? "Segmentation" : ((sig == SIGBUS) ? "Bus" : "Unknown"))
                      << " Fault, Logged StackTrace" << std::endl;

            // this is the trick: it will trigger the core dump as well
            signal(sig, SIG_DFL);
            kill(getpid(), sig);
        }
    }
};
