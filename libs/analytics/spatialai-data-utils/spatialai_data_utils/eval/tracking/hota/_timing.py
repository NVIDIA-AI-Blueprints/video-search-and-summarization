# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT AND Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Portions of this file are adapted from TrackEval:
# https://github.com/kovalp/TrackEval/tree/1.3.0
#
# MIT License
#
# Copyright (c) 2020 Jonathon Luiten
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from functools import wraps
from time import perf_counter
import inspect

DO_TIMING = False
DISPLAY_LESS_PROGRESS = False
timer_dict = {}
counter = 0


def time(f):
    """
    Decorator function for timing the execution of a function.

    :param f: The function to be timed.
    :type f: function
    :return: A wrapped function that measures the execution time of the original function.
    :rtype: function

    The wrapped function measures the execution time of the original function `f`. If the `DO_TIMING` flag is set to
    `True`, the wrapped function records the accumulated time for each function and provides timing analysis when the
    code is finished. If the flag is set to `False` or certain conditions are met, the wrapped function runs the
    original function without timing.

    Note that the timing analysis is printed to the console. Modify the implementation to save the timing information
    in a different format or location if desired.
    """
    @wraps(f)
    def wrap(*args, **kw):
        if DO_TIMING:
            # Run function with timing
            ts = perf_counter()
            result = f(*args, **kw)
            te = perf_counter()
            tt = te-ts

            # Get function name
            arg_names = inspect.getfullargspec(f)[0]
            if arg_names[0] == 'self' and DISPLAY_LESS_PROGRESS:
                return result
            elif arg_names[0] == 'self':
                method_name = type(args[0]).__name__ + '.' + f.__name__
            else:
                method_name = f.__name__

            # Record accumulative time in each function for analysis
            if method_name in timer_dict.keys():
                timer_dict[method_name] += tt
            else:
                timer_dict[method_name] = tt

            # If code is finished, display timing summary
            if method_name == "Evaluator.evaluate":
                print("")
                print("Timing analysis:")
                for key, value in timer_dict.items():
                    print('%-70s %2.4f sec' % (key, value))
            else:
                # Get function argument values for printing special arguments of interest
                arg_titles = ['tracker', 'seq', 'cls']
                arg_vals = []
                for i, a in enumerate(arg_names):
                    if a in arg_titles:
                        arg_vals.append(args[i])
                arg_text = '(' + ', '.join(arg_vals) + ')'

                # Display methods and functions with different indentation.
                if arg_names[0] == 'self':
                    print('%-74s %2.4f sec' % (' '*4 + method_name + arg_text, tt))
                elif arg_names[0] == 'test':
                    pass
                else:
                    global counter
                    counter += 1
                    print('%i %-70s %2.4f sec' % (counter, method_name + arg_text, tt))

            return result
        else:
            # If config["TIME_PROGRESS"] is false, or config["USE_PARALLEL"] is true, run functions normally without timing.
            return f(*args, **kw)
    return wrap
