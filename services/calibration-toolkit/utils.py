# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Copyright (c) 2019, NVIDIA CORPORATION.  All rights reserved.
"""Helper utils for packaging."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import glob
import os


def up_directory(dir_path, n=1):
    """Go up n directories from dir_path."""
    dir_up = dir_path
    for _ in range(n):
        dir_up = os.path.split(dir_up)[0]
    return dir_up


def remove_prefix(dir_path):
    """Remove a certain prefix from path."""
    max_path = 8
    prefix = dir_path
    while max_path > 0:
        prefix = os.path.split(prefix)[0]
        if prefix.endswith('ai_infra'):
            return dir_path[len(prefix)+1:]
        max_path -= 1
    return dir_path


def get_subdirs(path):
    """Get all subdirs of given path."""
    dirs = os.walk(path)
    return [remove_prefix(x[0]) for x in dirs]


def rename_py_files(path, ext, new_ext, ignore_files):
    """Rename all .ext files in a path to .new_ext except __init__ files."""
    files = glob.glob(path + '/*' + ext)
    for ignore_file in ignore_files:
        files = [f for f in files if ignore_file not in f]

    for filename in files:
        os.rename(filename, filename.replace(ext, new_ext))

def rename_pyc_files(path, ext, new_ext, ignore_files):
    """Rename all .ext files in a path to .new_ext except __init__ files."""
    files = glob.glob(path + '/*' + ext)
    for ignore_file in ignore_files:
        files = [f for f in files if ignore_file not in f]

    for filename in files:
        print (filename)
        basedir,ext_pyc = os.path.split(filename)

        dir_array = basedir.split("/")
        print ("Dir", dir_array)
        temp = "/".join(dir_array[:-1]) + "/" + ext_pyc
        print ("new",temp)
        os.rename(filename, temp.replace(ext, new_ext))
